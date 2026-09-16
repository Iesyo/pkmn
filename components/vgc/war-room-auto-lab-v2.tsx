"use client";

import Image from "next/image";
import { useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  Swords,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { serializeShowdownPaste } from "@/lib/team-builder";
import type { TeamVersion } from "@/lib/types";
import {
  selectAutoLabOpponentCandidates,
  selectAutoLabRecentVgcPastesCandidates,
} from "@/lib/war-room-auto-lab";
import { inspectBattleReadyPaste } from "@/lib/war-room-sparring";
import type { WarRoomCorpusTeam } from "@/lib/war-room";
import { cn } from "@/lib/utils";

const POLL_MS = 900;
const MAX_POLL_BACKOFF_MS = 5_000;
const LOAD_BATCH = 6;

type RunPreset = "quick" | "standard" | "deep";

const PRESETS: Record<RunPreset, { label: string; opponents: number; battlesPerOpponent: number; description: string }> = {
  quick: { label: "Rápido", opponents: 18, battlesPerOpponent: 12, description: "18 rivales · 216 batallas. Auditoría rápida con muestra amplia." },
  standard: { label: "Normal", opponents: 24, battlesPerOpponent: 20, description: "24 rivales · 480 batallas. Auditoría intensiva con mayor repetición por matchup." },
  deep: { label: "Profundo · meta actual", opponents: 100, battlesPerOpponent: 10, description: "100 rivales recientes · 1,000 batallas. Solo los VGCPastes M-C más recientes por Date Shared." },
};

type RecordRow = { games: number; wins: number; losses: number; ties: number; scorePercent: number };
type MatchupRow = RecordRow & { id: string; label: string; roster: string[]; archetypes: string[] };
type AutoLabAudit = {
  signal: { games: number; level: "exploratory" | "directional" | "stronger"; note: string };
  goodMatchups: MatchupRow[];
  badMatchups: MatchupRow[];
  problematicOpponents: MatchupRow[];
  leadPerformance: Array<RecordRow & { lead: string }>;
  selectionUsage: Array<{ pokemon: string; selectedGames: number; selectedRate: number; leadGames: number; scoreWhenSelected: number; signal: "rarely-selected" | "review" | "ok" }>;
  moveSignals: Array<{ pokemon: string; move: string; gamesUsed: number; totalUses: number; wins: number; losses: number; ties: number; scoreWhenUsed: number; signal: "review" | "observed" }>;
  opponentPokemonPressure: Array<{ pokemon: string; lossGames: number; lossShare: number }>;
  opponentCorePressure: Array<{ core: string; lossGames: number; lossShare: number }>;
  archetypePerformance: Array<RecordRow & { archetype: string }>;
  recurringLossPatterns: Array<{ pattern: string; count: number; lossShare: number }>;
  replayCoverage: { expected: number; parsed: number; errors: Array<{ battleTag: string; error: string }> };
  limitations: string[];
};
type AutoLabResult = {
  schemaVersion: 2;
  benchmark: "light-mc-team-gauntlet";
  policy: string;
  totalBattles: number;
  battlesPerOpponent: number;
  opponents: Array<{ id: string; label: string; roster: string[]; archetypes: string[] }>;
  baseline: RecordRow & { id: string; label: string };
  audit: AutoLabAudit;
  caveat: string;
};
type AutoLabJob = {
  id: string;
  phase: "queued" | "preparing" | "running" | "finalizing" | "completed" | "error" | "cancelled";
  error: string;
  completedBattles: number;
  totalBattles: number;
  progress: number;
  elapsedSeconds: number;
  etaSeconds: number | null;
  currentCandidateId: string | null;
  currentCandidateLabel: string | null;
  currentOpponentId: string | null;
  events: string[];
  result: AutoLabResult | null;
};
type AutoLabPreflightVerdict = { id: string; label: string; valid: boolean; error: string };
type AutoLabPreflightStatus = { checked: number; valid: number; rejected: number; lastRejected: string };
type LoadedOpponent = { team: WarRoomCorpusTeam; paste: string; label: string };

function seconds(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.max(0, Math.round(value))}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

function signalLabel(level: AutoLabAudit["signal"]["level"]) {
  if (level === "stronger") return "Señal más fuerte";
  if (level === "directional") return "Señal direccional";
  return "Exploratoria";
}

async function readPayload(response: Response) {
  const text = await response.text();
  try { return text ? JSON.parse(text) as unknown : {}; } catch { return {}; }
}

function errorText(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    if ("detail" in payload && typeof payload.detail === "string") return payload.detail;
    if ("error" in payload && typeof payload.error === "string") return payload.error;
  }
  return fallback;
}

async function loadExactPaste(team: WarRoomCorpusTeam) {
  const response = team.savedPasteId
    ? await fetch(`/api/scouting-pastes/${encodeURIComponent(team.savedPasteId)}`, { cache: "no-store" })
    : await fetch("/api/pokepaste-import", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url: team.pokepasteUrl }) });
  const payload = await readPayload(response);
  if (!response.ok) throw new Error(errorText(payload, "No se pudo cargar el paste rival."));
  const paste = team.savedPasteId && payload && typeof payload === "object" && "item" in payload && payload.item && typeof payload.item === "object" && "paste" in payload.item && typeof payload.item.paste === "string"
    ? payload.item.paste
    : payload && typeof payload === "object" && "paste" in payload && typeof payload.paste === "string" ? payload.paste : "";
  if (!paste) throw new Error("El paste rival llegó vacío.");
  return paste;
}

function opponentLabel(team: WarRoomCorpusTeam, deep: boolean, fallback: string) {
  return [deep ? team.dateShared : "", team.playerName, team.tournament].filter(Boolean).join(" · ") || fallback;
}

async function validateBattleReadyBatch(teams: Array<{ id: string; label: string; teamPaste: string }>) {
  const response = await fetch("/api/battle-lab/auto-lab/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ teams }),
  });
  const payload = await readPayload(response);
  if (!response.ok) throw new Error(errorText(payload, "No se pudo prevalidar el corpus con Pokémon Showdown."));
  const results = payload && typeof payload === "object" && "results" in payload
    && Array.isArray((payload as { results?: unknown }).results)
    ? (payload as { results: AutoLabPreflightVerdict[] }).results
    : [];
  return new Map(results.map((result) => [result.id, result]));
}

function speciesFromPair(value: string) {
  if (!value || value === "No recuperado") return [];
  return value.split(" + ").map((item) => item.trim()).filter(Boolean);
}

function SpriteStrip({ species, size = 34 }: { species: string[]; size?: number }) {
  if (!species.length) return <span className="text-[9px] text-slate-700">Sin sprites recuperados</span>;
  return <div className="flex flex-wrap items-center gap-1">{species.map((name, index) => <div key={`${name}-${index}`} title={name} className="flex items-center justify-center rounded-lg border border-white/7 bg-slate-950/55 p-0.5"><Image src={getSpriteUrl(name)} alt={name} width={size} height={size} unoptimized className="object-contain" style={{ width: size, height: size }} /></div>)}</div>;
}

function MatchupCard({ row, tone }: { row: MatchupRow; tone: "good" | "bad" }) {
  return <article className={cn("rounded-2xl border bg-slate-950/50 p-4", tone === "good" ? "border-emerald-300/12" : "border-rose-300/12")}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h4 className="truncate text-xs font-black text-white">{row.label}</h4><p className="mt-1 font-mono text-[8px] text-slate-600">{row.wins}-{row.losses}-{row.ties} · {row.games} partidas</p></div><strong className={cn("text-base", tone === "good" ? "text-emerald-200" : "text-rose-200")}>{row.scorePercent.toFixed(1)}%</strong></div><div className="mt-3"><SpriteStrip species={row.roster} size={32} /></div><div className="mt-3 flex flex-wrap gap-1">{row.archetypes.map((tag) => <Badge key={tag} variant="outline" className="border-white/8 text-[7px] text-slate-500">{tag}</Badge>)}</div></article>;
}

function SelectionCard({ row }: { row: AutoLabAudit["selectionUsage"][number] }) {
  return <article className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><div className="flex items-center gap-3"><Image src={getSpriteUrl(row.pokemon)} alt={row.pokemon} width={52} height={52} unoptimized className="size-12 shrink-0 object-contain" /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><strong className="truncate text-[10px] text-white">{row.pokemon}</strong>{row.signal !== "ok" ? <Badge variant="outline" className={cn("text-[7px]", row.signal === "rarely-selected" ? "border-amber-300/15 text-amber-200" : "border-rose-300/15 text-rose-200")}>{row.signal === "rarely-selected" ? "Poco elegido" : "Revisar"}</Badge> : null}</div><div className="mt-2 flex items-center gap-2"><Progress value={row.selectedRate} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300" /><span className="w-11 text-right font-mono text-[8px] text-cyan-200">{row.selectedRate.toFixed(1)}%</span></div><p className="mt-1 text-[8px] text-slate-600">{row.selectedGames} selecciones · {row.leadGames} leads · score {row.scoreWhenSelected.toFixed(1)}%</p></div></div></article>;
}

export function WarRoomAutoLab({ team, corpusTeams }: { team: TeamVersion; corpusTeams: WarRoomCorpusTeam[] }) {
  const [preset, setPreset] = useState<RunPreset>("quick");
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<AutoLabJob | null>(null);
  const [runError, setRunError] = useState("");
  const [preflight, setPreflight] = useState<AutoLabPreflightStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const pollFailuresRef = useRef(0);
  const teamKey = useMemo(() => `${team.id}|${team.paste}`, [team.id, team.paste]);
  const baselinePaste = useMemo(() => serializeShowdownPaste(team.pokemon, team.mechanics ?? ["mega"]), [team]);
  const baselineReady = inspectBattleReadyPaste(baselinePaste);

  async function poll(jobId: string) {
    try {
      const response = await fetch(`/api/battle-lab/auto-lab/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No se pudo consultar Auto Lab."));
      const next = payload as AutoLabJob;
      pollFailuresRef.current = 0;
      setRunError("");
      setJob(next);
      if (["completed", "error", "cancelled"].includes(next.phase)) return;
      pollRef.current = window.setTimeout(() => void poll(jobId), POLL_MS);
    } catch (error) {
      pollFailuresRef.current += 1;
      const message = error instanceof Error ? error.message : "Se perdió la conexión con Auto Lab.";
      setRunError(`${message} · reintentando consulta de estado…`);
      const delay = Math.min(MAX_POLL_BACKOFF_MS, POLL_MS * (pollFailuresRef.current + 1));
      pollRef.current = window.setTimeout(() => void poll(jobId), delay);
    }
  }

  async function startAudit() {
    if (!baselineReady.ready) return;
    if (pollRef.current != null) window.clearTimeout(pollRef.current);
    pollFailuresRef.current = 0;
    setStarting(true); setRunError(""); setJob(null); setPreflight({ checked: 0, valid: 0, rejected: 0, lastRejected: "" });
    try {
      const spec = PRESETS[preset];
      const baselineVerdicts = await validateBattleReadyBatch([
        { id: "baseline-current", label: `${team.name} · Team actual`, teamPaste: baselinePaste },
      ]);
      const baselineVerdict = baselineVerdicts.get("baseline-current");
      if (!baselineVerdict?.valid) {
        throw new Error(`El Team actual no supera validate-team M-C: ${baselineVerdict?.error || "validación sin respuesta"}`);
      }

      const pool = preset === "deep"
        ? selectAutoLabRecentVgcPastesCandidates(corpusTeams, spec.opponents * 3)
        : selectAutoLabOpponentCandidates(corpusTeams, spec.opponents * 3, teamKey);
      const loaded: LoadedOpponent[] = [];
      let checked = 0;
      let rejected = 0;
      let lastRejected = "";

      for (let offset = 0; offset < pool.length && loaded.length < spec.opponents; offset += LOAD_BATCH) {
        const candidates = pool.slice(offset, offset + LOAD_BATCH);
        const fetched = await Promise.all(candidates.map(async (candidate, index) => {
          const id = `preflight-${offset + index + 1}`;
          const label = opponentLabel(candidate, preset === "deep", `Rival ${offset + index + 1}`);
          try {
            const paste = await loadExactPaste(candidate);
            const structural = inspectBattleReadyPaste(paste);
            if (!structural.ready) return { id, team: candidate, label, error: structural.issues[0] || "Paste incompleto." };
            return { id, team: candidate, label, paste, error: "" };
          } catch (error) {
            return { id, team: candidate, label, error: error instanceof Error ? error.message : "No se pudo cargar el paste rival." };
          }
        }));

        const readyToValidate = fetched.filter((item): item is LoadedOpponent & { id: string; error: string } => "paste" in item && Boolean(item.paste));
        for (const item of fetched) {
          if ("paste" in item && item.paste) continue;
          checked += 1;
          rejected += 1;
          lastRejected = `${item.label}: ${item.error}`;
        }

        if (readyToValidate.length) {
          const verdicts = await validateBattleReadyBatch(readyToValidate.map((item) => ({ id: item.id, label: item.label, teamPaste: item.paste })));
          for (const item of readyToValidate) {
            checked += 1;
            const verdict = verdicts.get(item.id);
            if (verdict?.valid) {
              if (loaded.length < spec.opponents) loaded.push({ team: item.team, paste: item.paste, label: item.label });
            } else {
              rejected += 1;
              lastRejected = `${item.label}: ${verdict?.error || "Showdown rechazó el paste."}`;
            }
          }
        }
        setPreflight({ checked, valid: loaded.length, rejected, lastRejected });
      }

      if (preset === "deep" && loaded.length < spec.opponents) {
        throw new Error(`Profundo necesita ${spec.opponents} VGCPastes M-C recientes validados por Showdown; solo encontramos ${loaded.length} después de revisar ${checked} y descartar ${rejected}.`);
      }
      if (loaded.length < Math.min(6, spec.opponents)) throw new Error("No hay suficientes rivales válidos en Showdown entre VGCPastes, torneos y Mis pastes.");
      const response = await fetch("/api/battle-lab/auto-lab", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          baseline: { id: "baseline-current", label: `${team.name} · Team actual`, teamPaste: baselinePaste },
          opponents: loaded.map(({ paste, label }, index) => ({
            id: `opponent-${index + 1}`,
            label,
            teamPaste: paste,
          })),
          battlesPerOpponent: spec.battlesPerOpponent,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No se pudo iniciar la auditoría empírica."));
      const next = payload as AutoLabJob; setJob(next); void poll(next.id);
    } catch (error) { setRunError(error instanceof Error ? error.message : "No se pudo iniciar la auditoría empírica."); }
    finally { setStarting(false); }
  }

  const audit = job?.result?.audit;
  return <section className="rounded-[26px] border border-cyan-300/12 bg-cyan-300/[0.02] p-5">
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="max-w-3xl"><div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300"><ShieldCheck className="size-3.5" />Auto Lab · auditoría empírica</div><h2 className="mt-1 text-lg font-black text-white">Tortura el Team actual contra un meta mucho más ancho</h2><p className="mt-1 text-[10px] leading-4 text-slate-500">Audit usa todo el presupuesto en este Team: LIGHT explora Team Preview, mantiene los turnos deterministas y convierte los replays en diagnóstico. Los paquetes de set se quedaron en <strong className="text-violet-200">Optimizar o construir</strong>.</p></div>{job ? <Button type="button" variant="outline" size="sm" onClick={() => { setJob(null); setRunError(""); setPreflight(null); }} disabled={job.phase === "running" || job.phase === "preparing" || job.phase === "finalizing"} className="gap-2 border-white/10 text-[9px]"><RefreshCw className="size-3.5" />Limpiar informe</Button> : null}</div>

    <div className="mt-5 rounded-2xl border border-white/7 bg-slate-950/45 p-4"><div className="flex flex-wrap items-center justify-between gap-4"><div><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-500">Cobertura del Gauntlet</p><div className="mt-2 flex flex-wrap gap-2">{(Object.keys(PRESETS) as RunPreset[]).map((value) => <button key={value} type="button" onClick={() => { setPreset(value); setPreflight(null); }} disabled={Boolean(job && !["completed", "error", "cancelled"].includes(job.phase))} className={cn("rounded-xl border px-3 py-2 text-left transition", preset === value ? "border-cyan-300/30 bg-cyan-300/[0.08]" : "border-white/8 bg-slate-950/30 hover:border-white/15")}><span className={cn("block text-[9px] font-black", preset === value ? "text-cyan-100" : "text-slate-300")}>{PRESETS[value].label}</span><span className="mt-0.5 block text-[8px] text-slate-600">{PRESETS[value].opponents} rivales × {PRESETS[value].battlesPerOpponent}</span></button>)}</div><p className="mt-2 text-[9px] text-slate-600">{PRESETS[preset].description}</p></div><Button type="button" onClick={() => void startAudit()} disabled={starting || !baselineReady.ready || Boolean(job && !["completed", "error", "cancelled"].includes(job.phase))} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}{starting ? "Validando meta…" : "Auditar Team con LIGHT"}</Button></div>{!baselineReady.ready ? <p className="mt-3 text-[9px] text-rose-300">El Team actual no es battle-ready: {baselineReady.issues[0]}</p> : null}{preflight ? <div className="mt-3 rounded-xl border border-cyan-300/10 bg-cyan-300/[0.025] px-3 py-2 text-[8px] text-slate-500"><strong className="text-cyan-200">Preflight Showdown M-C:</strong> {preflight.valid}/{PRESETS[preset].opponents} válidos · {preflight.rejected} descartados · {preflight.checked} revisados{preflight.lastRejected ? <div className="mt-1 truncate text-amber-200/70" title={preflight.lastRejected}>Último descarte: {preflight.lastRejected}</div> : null}</div> : null}</div>

    {runError ? <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-300/15 bg-rose-300/[0.04] p-4 text-xs text-rose-200"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{runError}</div> : null}
    {job && !job.result ? <div className="mt-4 rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.025] p-4"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Swords className="size-4 text-cyan-300" /><strong className="text-xs text-white">{job.phase === "finalizing" ? "Generando informe de combate…" : job.currentOpponentId ? `Probando ${job.currentOpponentId}` : job.phase === "preparing" ? "Preparando arena…" : "Gauntlet en curso"}</strong></div><span className="font-mono text-[9px] text-slate-500">{job.completedBattles}/{job.totalBattles}</span></div><Progress value={Math.max(0, Math.min(100, job.progress * 100))} className="mt-3 h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300" /><div className="mt-2 flex justify-between text-[8px] text-slate-600"><span>Transcurrido {seconds(job.elapsedSeconds)}</span><span>ETA {job.phase === "finalizing" ? "armando informe" : seconds(job.etaSeconds)}</span></div>{job.error ? <p className="mt-3 text-[9px] text-rose-300">{job.error}</p> : null}</div> : null}

    {job?.result && audit ? <div className="mt-5 space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><div className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">Score LIGHT-Team</p><p className="mt-1 text-xl font-black text-cyan-100">{job.result.baseline.scorePercent.toFixed(1)}%</p></div><div className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">Registro</p><p className="mt-1 text-xl font-black text-white">{job.result.baseline.wins}-{job.result.baseline.losses}-{job.result.baseline.ties}</p></div><div className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">Rivales</p><p className="mt-1 text-xl font-black text-white">{job.result.opponents.length}</p></div><div className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">Batallas</p><p className="mt-1 text-xl font-black text-white">{audit.signal.games}</p></div><div className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">Replays</p><p className="mt-1 text-xl font-black text-white">{audit.replayCoverage.parsed}/{audit.replayCoverage.expected}</p></div></section>
      <section className="rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.025] p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[8px] font-black uppercase tracking-[0.14em] text-cyan-300">Informe de combate</p><h3 className="mt-1 text-base font-black text-white">Qué hace bien y dónde se rompe este Team</h3></div><Badge variant="outline" className="border-cyan-300/15 text-[8px] text-cyan-200">{signalLabel(audit.signal.level)} · {audit.signal.games} partidas</Badge></div><p className="mt-2 text-[9px] text-slate-500">{audit.signal.note}</p></section>
      <div className="grid gap-3 xl:grid-cols-2"><section className="rounded-2xl border border-emerald-300/10 bg-emerald-300/[0.02] p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-emerald-300">Matchups favorables</h4><div className="mt-3 grid gap-2">{audit.goodMatchups.map((row) => <MatchupCard key={row.id} row={row} tone="good" />)}</div></section><section className="rounded-2xl border border-rose-300/10 bg-rose-300/[0.02] p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-rose-300">Matchups duros</h4><div className="mt-3 grid gap-2">{audit.badMatchups.map((row) => <MatchupCard key={row.id} row={row} tone="bad" />)}</div></section></div>
      <section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-rose-300">Ranking de rivales problemáticos</h4><div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">{audit.problematicOpponents.map((row, index) => <article key={row.id} className="rounded-xl border border-white/6 bg-white/[0.02] p-3"><div className="flex items-center justify-between gap-2"><strong className="truncate text-[9px] text-slate-200">#{index + 1} {row.label}</strong><span className="font-mono text-[8px] text-rose-200">{row.scorePercent.toFixed(1)}%</span></div><div className="mt-2"><SpriteStrip species={row.roster} size={28} /></div></article>)}</div></section>
      <div className="grid gap-3 xl:grid-cols-2"><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-cyan-300">Leads propios</h4><div className="mt-3 grid gap-2 sm:grid-cols-2">{[...audit.leadPerformance].sort((a, b) => b.scorePercent - a.scorePercent || b.games - a.games).map((row) => <article key={row.lead} className="rounded-xl border border-white/6 bg-white/[0.02] p-3"><div className="flex items-center justify-between gap-2"><SpriteStrip species={speciesFromPair(row.lead)} size={36} /><strong className="font-mono text-[9px] text-cyan-200">{row.scorePercent.toFixed(1)}%</strong></div><p className="mt-2 text-[8px] text-slate-600">{row.lead} · {row.games} partidas · {row.wins}-{row.losses}-{row.ties}</p></article>)}</div></section><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-violet-300">Uso del roster</h4><div className="mt-3 grid gap-2">{audit.selectionUsage.map((row) => <SelectionCard key={row.pokemon} row={row} />)}</div></section></div>
      <div className="grid gap-3 xl:grid-cols-2"><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-amber-300">Pokémon rivales ligados a derrotas</h4><div className="mt-3 grid gap-2 sm:grid-cols-2">{audit.opponentPokemonPressure.map((row) => <article key={row.pokemon} className="flex items-center gap-3 rounded-xl border border-white/6 bg-white/[0.02] p-3"><Image src={getSpriteUrl(row.pokemon)} alt={row.pokemon} width={48} height={48} unoptimized className="size-11 object-contain" /><div className="min-w-0"><strong className="text-[10px] text-slate-200">{row.pokemon}</strong><p className="mt-0.5 text-[8px] text-slate-600">{row.lossGames} derrotas · {row.lossShare.toFixed(1)}%</p></div></article>)}</div></section><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-amber-300">Cores / leads rivales ligados a derrotas</h4><div className="mt-3 grid gap-2">{audit.opponentCorePressure.map((row) => <article key={row.core} className="rounded-xl border border-white/6 bg-white/[0.02] p-3"><div className="flex items-center justify-between gap-3"><SpriteStrip species={speciesFromPair(row.core)} size={36} /><span className="font-mono text-[8px] text-amber-200">{row.lossShare.toFixed(1)}%</span></div><p className="mt-1 text-[8px] text-slate-600">{row.core} · {row.lossGames} derrotas</p></article>)}</div></section></div>
      <section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-slate-400">Moves que merecen revisión</h4><p className="mt-1 text-[8px] text-slate-700">Señal correlacional de uso; no significa que el move sea malo por sí mismo.</p><div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{audit.moveSignals.map((row) => <article key={`${row.pokemon}-${row.move}`} className="flex items-center gap-3 rounded-xl border border-white/6 bg-white/[0.02] p-3"><Image src={getSpriteUrl(row.pokemon)} alt={row.pokemon} width={42} height={42} unoptimized className="size-10 object-contain" /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><strong className="truncate text-[9px] text-slate-200">{row.pokemon} · {row.move}</strong>{row.signal === "review" ? <Badge variant="outline" className="border-amber-300/15 text-[7px] text-amber-200">Revisar</Badge> : null}</div><p className="mt-1 text-[8px] text-slate-600">{row.totalUses} usos · {row.gamesUsed} partidas · score {row.scoreWhenUsed.toFixed(1)}%</p></div></article>)}</div></section>
      <div className="grid gap-3 xl:grid-cols-2"><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-fuchsia-300">Rendimiento por arquetipo</h4><div className="mt-3 grid gap-2 sm:grid-cols-2">{audit.archetypePerformance.map((row) => <article key={row.archetype} className="rounded-xl border border-white/6 bg-white/[0.02] p-3"><div className="flex items-center justify-between gap-2"><strong className="text-[9px] text-slate-200">{row.archetype}</strong><span className="font-mono text-[8px] text-fuchsia-200">{row.scorePercent.toFixed(1)}%</span></div><p className="mt-1 text-[8px] text-slate-600">{row.games} partidas · {row.wins}-{row.losses}-{row.ties}</p></article>)}</div></section><section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-rose-300">Patrones recurrentes en derrotas</h4><div className="mt-3 space-y-2">{audit.recurringLossPatterns.map((row) => <div key={row.pattern} className="flex items-center gap-3 rounded-xl border border-white/6 bg-white/[0.02] px-3 py-2"><AlertTriangle className="size-4 shrink-0 text-rose-300" /><div className="min-w-0 flex-1"><strong className="text-[9px] text-slate-200">{row.pattern}</strong><p className="mt-0.5 text-[8px] text-slate-600">{row.count} veces · {row.lossShare.toFixed(1)}% de derrotas</p></div></div>)}</div></section></div>
      <div className="flex items-start gap-2 rounded-2xl border border-white/7 bg-slate-950/35 p-3 text-[9px] leading-4 text-slate-500"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-cyan-300" /><div><strong className="text-slate-300">Lectura correcta:</strong> {job.result.caveat}<div className="mt-1 text-[8px] text-slate-700">{audit.limitations.join(" · ")}</div></div></div>
    </div> : null}
  </section>;
}
