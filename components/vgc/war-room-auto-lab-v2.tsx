"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
  Swords,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { OpponentMetaResponse } from "@/lib/opponent-meta-presets";
import { serializeShowdownPaste } from "@/lib/team-builder";
import { loadShowdownSnapshot, type ShowdownSnapshot } from "@/lib/showdown-data";
import type { TeamVersion } from "@/lib/types";
import {
  isWarRoomPasteEvidenceResponse,
  selectWarRoomPasteEvidenceCandidates,
  type WarRoomPasteEvidenceTeam,
} from "@/lib/war-room-paste-evidence";
import {
  auditTeam,
  optimizeTeam,
  warRoomMetaKey,
  type WarRoomAuditResult,
  type WarRoomCorpusTeam,
  type WarRoomOptimizationResult,
} from "@/lib/war-room";
import {
  buildAutoLabVariants,
  diagnoseAutoLab,
  selectAutoLabOpponentCandidates,
  type AutoLabVariant,
} from "@/lib/war-room-auto-lab";
import { inspectBattleReadyPaste } from "@/lib/war-room-sparring";
import { cn } from "@/lib/utils";

const POLL_MS = 900;

type RunPreset = "quick" | "standard" | "deep";
const PRESETS: Record<RunPreset, { label: string; opponents: number; battlesPerOpponent: number; description: string }> = {
  quick: { label: "Rápido", opponents: 4, battlesPerOpponent: 4, description: "16 partidas del baseline · muestra exploratoria para descubrir dónde mirar." },
  standard: { label: "Normal", opponents: 8, battlesPerOpponent: 6, description: "48 partidas del baseline · señal direccional por matchup, lead y arquetipo." },
  deep: { label: "Profundo", opponents: 12, battlesPerOpponent: 10, description: "120 partidas del baseline · señal más fuerte antes de promover un set." },
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
  setSignals: Array<{ pokemon: string; selectedGames: number; selectedRate: number; leadGames: number; scoreWhenSelected: number; signal: string }>;
  moveSignals: Array<{ pokemon: string; move: string; gamesUsed: number; totalUses: number; wins: number; losses: number; ties: number; scoreWhenUsed: number; signal: "review" | "observed" }>;
  opponentPokemonPressure: Array<{ pokemon: string; lossGames: number; lossShare: number }>;
  opponentCorePressure: Array<{ core: string; lossGames: number; lossShare: number }>;
  archetypePerformance: Array<RecordRow & { archetype: string }>;
  recurringLossPatterns: Array<{ pattern: string; count: number; lossShare: number }>;
  replayCoverage: { expected: number; parsed: number; errors: Array<{ battleTag: string; error: string }> };
  limitations: string[];
};
type VariantReport = RecordRow & {
  id: string;
  label: string;
  comparison: {
    deltaPercentagePoints: number;
    opponentsImproved: number;
    opponentsRegressed: number;
    opponentsTied: number;
    verdict: "improved" | "regressed" | "mixed";
    promotion: "candidate" | "hold";
    caveat: string;
  };
};
type AutoLabResult = {
  schemaVersion: 2;
  benchmark: "light-mc-team-gauntlet";
  policy: string;
  totalBattles: number;
  battlesPerOpponent: number;
  bestVariantId: string | null;
  baseline: RecordRow & { id: string; label: string };
  variants: VariantReport[];
  audit: AutoLabAudit;
  caveat: string;
};
type AutoLabJob = {
  id: string;
  phase: "queued" | "preparing" | "running" | "completed" | "error" | "cancelled";
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
type Preparation = {
  snapshot: ShowdownSnapshot;
  audit: WarRoomAuditResult;
  optimization: WarRoomOptimizationResult;
  variants: AutoLabVariant[];
  evidenceTeams: number;
  metaSets: number;
};

function isOpponentMetaResponse(value: unknown): value is OpponentMetaResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const root = value as Partial<OpponentMetaResponse>;
  return typeof root.pokemon === "string" && root.methodology === "marginal-frequency-composite" && Array.isArray(root.presets);
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
async function loadMeta(team: TeamVersion) {
  const rows = await Promise.all(team.pokemon.filter((set) => set.species.trim()).map(async (set) => {
    try {
      const response = await fetch(`/api/opponent-meta/${encodeURIComponent(set.species)}`, { cache: "no-store" });
      const payload = await readPayload(response);
      return response.ok && isOpponentMetaResponse(payload) ? [warRoomMetaKey(set.species), payload] as const : null;
    } catch { return null; }
  }));
  return Object.fromEntries(rows.filter((row): row is readonly [string, OpponentMetaResponse] => Boolean(row)));
}
async function prepareAutoLab(team: TeamVersion, corpusTeams: WarRoomCorpusTeam[]): Promise<Preparation> {
  const snapshot = await loadShowdownSnapshot();
  const audit = auditTeam(team.pokemon, corpusTeams, snapshot, { teamFormat: team.format });
  const metaBySpecies = await loadMeta(team);
  const initial = optimizeTeam(team.pokemon, {}, corpusTeams, snapshot, metaBySpecies);
  let evidence: WarRoomPasteEvidenceTeam[] = [];
  const candidates = selectWarRoomPasteEvidenceCandidates(team.pokemon.map((set) => set.species), [], initial.members.map((member) => member.species), corpusTeams, 24);
  if (candidates.length) {
    try {
      const response = await fetch("/api/war-room/paste-evidence", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ candidates }) });
      const payload = await readPayload(response);
      if (response.ok && isWarRoomPasteEvidenceResponse(payload)) evidence = payload.teams;
    } catch { /* deterministic fallback */ }
  }
  const optimization = optimizeTeam(team.pokemon, {}, corpusTeams, snapshot, metaBySpecies, { pasteEvidence: evidence });
  return { snapshot, audit, optimization, variants: buildAutoLabVariants(team.pokemon, optimization), evidenceTeams: evidence.length, metaSets: Object.keys(metaBySpecies).length };
}
function seconds(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.max(0, Math.round(value))}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}
function methodologyLabel(value: AutoLabVariant["methodology"]) {
  if (value === "observed-paste") return "Paste observado";
  if (value === "observed-paste-patched") return "Paste + parche";
  return "Battle Data";
}
function signalLabel(level: AutoLabAudit["signal"]["level"]) {
  if (level === "stronger") return "Señal más fuerte";
  if (level === "directional") return "Señal direccional";
  return "Exploratoria";
}
function MiniRecord({ row }: { row: RecordRow }) {
  return <span className="font-mono text-[8px] text-slate-500">{row.wins}-{row.losses}-{row.ties} · {row.scorePercent.toFixed(1)}%</span>;
}
function AuditList({ title, rows }: { title: string; rows: Array<{ label: string; detail: string }> }) {
  return <section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-slate-400">{title}</h4><div className="mt-3 space-y-2">{rows.length ? rows.map((row, index) => <div key={`${row.label}-${index}`} className="rounded-xl border border-white/6 bg-white/[0.02] px-3 py-2"><strong className="text-[10px] text-slate-200">{row.label}</strong><p className="mt-0.5 text-[9px] leading-4 text-slate-600">{row.detail}</p></div>) : <p className="text-[9px] text-slate-600">Sin señal suficiente todavía.</p>}</div></section>;
}

export function WarRoomAutoLab({ team, corpusTeams }: { team: TeamVersion; corpusTeams: WarRoomCorpusTeam[] }) {
  const [preparation, setPreparation] = useState<Preparation | null>(null);
  const [preparing, setPreparing] = useState(true);
  const [prepareError, setPrepareError] = useState("");
  const [preset, setPreset] = useState<RunPreset>("quick");
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<AutoLabJob | null>(null);
  const [runError, setRunError] = useState("");
  const [copied, setCopied] = useState("");
  const pollRef = useRef<number | null>(null);
  const teamKey = useMemo(() => `${team.id}|${team.paste}|${team.pokemon.map((set) => `${set.species}:${set.item}:${set.ability}:${set.evs}:${set.moves.map((move) => move.name).join(",")}`).join("|")}`, [team]);

  async function refreshPreparation() {
    setPreparing(true); setPrepareError(""); setPreparation(null); setJob(null); setRunError("");
    try { setPreparation(await prepareAutoLab(team, corpusTeams)); }
    catch (error) { setPrepareError(error instanceof Error ? error.message : "No se pudo preparar Auto Lab."); }
    finally { setPreparing(false); }
  }
  useEffect(() => {
    void refreshPreparation();
    return () => { if (pollRef.current != null) window.clearTimeout(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamKey]);

  const diagnosis = preparation ? diagnoseAutoLab(preparation.audit) : null;
  const baselinePaste = useMemo(() => serializeShowdownPaste(team.pokemon, team.mechanics ?? ["mega"]), [team]);
  const baselineReady = inspectBattleReadyPaste(baselinePaste);

  async function poll(jobId: string) {
    try {
      const response = await fetch(`/api/battle-lab/auto-lab/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No se pudo consultar Auto Lab."));
      const next = payload as AutoLabJob;
      setJob(next);
      if (["completed", "error", "cancelled"].includes(next.phase)) return;
      pollRef.current = window.setTimeout(() => void poll(jobId), POLL_MS);
    } catch (error) { setRunError(error instanceof Error ? error.message : "Se perdió la conexión con Auto Lab."); }
  }

  async function startGauntlet() {
    if (!preparation || !preparation.variants.length || !baselineReady.ready) return;
    setStarting(true); setRunError(""); setJob(null);
    try {
      const spec = PRESETS[preset];
      const pool = selectAutoLabOpponentCandidates(corpusTeams, spec.opponents * 3, teamKey);
      const loaded: Array<{ team: WarRoomCorpusTeam; paste: string }> = [];
      for (const candidate of pool) {
        if (loaded.length >= spec.opponents) break;
        try {
          const paste = await loadExactPaste(candidate);
          if (inspectBattleReadyPaste(paste).ready) loaded.push({ team: candidate, paste });
        } catch { /* keep scanning */ }
      }
      if (loaded.length < 2) throw new Error("No hay suficientes rivales battle-ready entre VGCPastes, torneos y Mis pastes.");
      const response = await fetch("/api/battle-lab/auto-lab", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          baseline: { id: "baseline-current", label: `${team.name} · baseline`, teamPaste: baselinePaste },
          variants: preparation.variants.map((variant) => ({ id: variant.id, label: variant.label, teamPaste: serializeShowdownPaste(variant.pokemon, team.mechanics ?? ["mega"]) })),
          opponents: loaded.map(({ team: opponent, paste }, index) => ({ id: `opponent-${index + 1}`, label: [opponent.playerName, opponent.tournament].filter(Boolean).join(" · ") || `Rival ${index + 1}`, teamPaste: paste })),
          battlesPerOpponent: spec.battlesPerOpponent,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No se pudo iniciar Auto Lab."));
      const next = payload as AutoLabJob; setJob(next); void poll(next.id);
    } catch (error) { setRunError(error instanceof Error ? error.message : "No se pudo iniciar el Gauntlet."); }
    finally { setStarting(false); }
  }

  async function copyVariant(variantId: string) {
    const variant = preparation?.variants.find((item) => item.id === variantId);
    if (!variant) return;
    const text = serializeShowdownPaste(variant.pokemon, team.mechanics ?? ["mega"]);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else window.prompt("Copia el paste de la variante:", text);
      setCopied(variantId);
      window.setTimeout(() => setCopied((current) => current === variantId ? "" : current), 1600);
    } catch { window.prompt("Copia el paste de la variante:", text); }
  }

  const audit = job?.result?.audit;
  return <section className="rounded-[26px] border border-violet-300/14 bg-violet-300/[0.025] p-5">
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="max-w-3xl"><div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.16em] text-violet-300"><Sparkles className="size-3.5" />Auto Lab · auditoría empírica</div><h2 className="mt-1 text-lg font-black text-white">Tortura el Team contra el meta y convierte las partidas en diagnóstico</h2><p className="mt-1 text-[10px] leading-4 text-slate-500">LIGHT explora distintas selecciones/leads en Team Preview, juega los turnos con política fija y compara paquetes de set completos. Nana no participa.</p></div><Button type="button" variant="outline" size="sm" onClick={() => void refreshPreparation()} disabled={preparing || starting || job?.phase === "running" || job?.phase === "preparing"} className="gap-2 border-white/10 text-[9px]"><RefreshCw className={cn("size-3.5", preparing && "animate-spin")} />Recalcular</Button></div>

    {preparing ? <div className="mt-5 flex min-h-28 items-center justify-center gap-2 rounded-2xl border border-white/7 bg-slate-950/35 text-xs text-slate-400"><Loader2 className="size-4 animate-spin text-violet-300" />Auditando y armando paquetes de set completos…</div> : null}
    {prepareError ? <div className="mt-5 flex items-start gap-2 rounded-2xl border border-rose-300/15 bg-rose-300/[0.04] p-4 text-xs text-rose-200"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{prepareError}</div> : null}

    {preparation && diagnosis ? <>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">{[["Bloqueos", diagnosis.blockers], ["Amenazas altas+", diagnosis.criticalThreats + diagnosis.highThreats], ["Sin respuesta", diagnosis.unresolvedThreats], ["Huecos", diagnosis.gaps], ["Sets candidatos", preparation.variants.length]].map(([label, value]) => <div key={String(label)} className="rounded-2xl border border-white/7 bg-slate-950/45 p-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">{label}</p><p className="mt-1 text-xl font-black text-white">{value}</p></div>)}</div>
      <div className="mt-3 rounded-2xl border border-cyan-300/10 bg-cyan-300/[0.025] px-4 py-3"><p className="text-[8px] font-black uppercase tracking-[0.14em] text-cyan-300">Prioridad previa</p><p className="mt-1 text-xs font-bold text-slate-200">{diagnosis.priority}</p><p className="mt-1 text-[9px] text-slate-600">Evidencia: {preparation.evidenceTeams} pastes completos · {preparation.metaSets}/6 sets con Battle Data.</p></div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">{preparation.variants.length ? preparation.variants.map((variant) => <article key={variant.id} className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-[8px] font-black uppercase tracking-[0.13em] text-violet-300">Paquete de set completo</p><h3 className="mt-1 text-xs font-black text-white">{variant.label}</h3></div><Badge variant="outline" className="border-violet-300/15 text-[8px] text-violet-200">{methodologyLabel(variant.methodology)}</Badge></div><p className="mt-2 text-[9px] text-slate-500">{variant.changes.length} campos cambian juntos · fuente: {variant.sourceLabel}</p><div className="mt-2 flex flex-wrap gap-1">{variant.changes.slice(0, 7).map((change) => <Badge key={change.key} variant="outline" className="border-white/8 text-[7px] text-slate-500">{change.field}</Badge>)}</div>{variant.rationale[0] ? <p className="mt-2 text-[9px] leading-4 text-slate-600">{variant.rationale[0]}</p> : null}</article>) : <div className="lg:col-span-2 rounded-2xl border border-amber-300/15 bg-amber-300/[0.04] p-4 text-xs text-amber-100">No apareció un paquete de set completo distinto y battle-ready; Auto Lab no inventará uno.</div>}</div>
      <div className="mt-5 rounded-2xl border border-white/7 bg-slate-950/45 p-4"><div className="flex flex-wrap items-center justify-between gap-4"><div><p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-500">Intensidad del Gauntlet</p><div className="mt-2 flex flex-wrap gap-2">{(Object.keys(PRESETS) as RunPreset[]).map((value) => <button key={value} type="button" onClick={() => setPreset(value)} disabled={Boolean(job && !["completed", "error", "cancelled"].includes(job.phase))} className={cn("rounded-xl border px-3 py-2 text-left transition", preset === value ? "border-violet-300/30 bg-violet-300/[0.08]" : "border-white/8 bg-slate-950/30")}><span className="block text-[9px] font-black text-slate-200">{PRESETS[value].label}</span><span className="mt-0.5 block text-[8px] text-slate-600">{PRESETS[value].opponents} rivales × {PRESETS[value].battlesPerOpponent}</span></button>)}</div><p className="mt-2 text-[9px] text-slate-600">{PRESETS[preset].description}</p></div><Button type="button" onClick={() => void startGauntlet()} disabled={starting || !preparation.variants.length || !baselineReady.ready || Boolean(job && !["completed", "error", "cancelled"].includes(job.phase))} className="gap-2 bg-violet-300 text-slate-950 hover:bg-violet-200">{starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}{starting ? "Cargando meta…" : "Ejecutar Auto Lab"}</Button></div>{!baselineReady.ready ? <p className="mt-3 text-[9px] text-rose-300">El Team actual no es battle-ready: {baselineReady.issues[0]}</p> : null}</div>
    </> : null}

    {runError ? <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-300/15 bg-rose-300/[0.04] p-4 text-xs text-rose-200"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{runError}</div> : null}
    {job && !job.result ? <div className="mt-4 rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.025] p-4"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Swords className="size-4 text-cyan-300" /><strong className="text-xs text-white">{job.currentCandidateLabel || (job.phase === "preparing" ? "Preparando arena…" : "Gauntlet en curso")}</strong></div><span className="font-mono text-[9px] text-slate-500">{job.completedBattles}/{job.totalBattles}</span></div><Progress value={Math.max(0, Math.min(100, job.progress * 100))} className="mt-3 h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300" /><div className="mt-2 flex justify-between text-[8px] text-slate-600"><span>Transcurrido {seconds(job.elapsedSeconds)}</span><span>ETA {seconds(job.etaSeconds)}</span></div>{job.error ? <p className="mt-3 text-[9px] text-rose-300">{job.error}</p> : null}</div> : null}

    {job?.result ? <div className="mt-5 space-y-4">
      <section className="rounded-2xl border border-emerald-300/12 bg-emerald-300/[0.025] p-4"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-[8px] font-black uppercase tracking-[0.14em] text-emerald-300">Gauntlet A/B</p><h3 className="mt-1 text-base font-black text-white">Baseline {job.result.baseline.scorePercent.toFixed(1)}%</h3></div><Badge variant="outline" className="border-cyan-300/15 text-[8px] text-cyan-200">{job.result.totalBattles} batallas · Preview explorado</Badge></div><div className="mt-3 grid gap-2 lg:grid-cols-2">{job.result.variants.map((report) => { const source = preparation?.variants.find((variant) => variant.id === report.id); const delta = report.comparison.deltaPercentagePoints; return <article key={report.id} className="rounded-xl border border-white/7 bg-slate-950/35 p-3"><div className="flex justify-between gap-3"><div><strong className="text-[10px] text-white">{report.label}</strong><p className="mt-1 text-[8px] text-slate-600">mejora {report.comparison.opponentsImproved} · retrocede {report.comparison.opponentsRegressed}</p></div><div className="text-right"><p className="text-sm font-black text-white">{report.scorePercent.toFixed(1)}%</p><p className={cn("text-[8px] font-bold", delta > 0 ? "text-emerald-300" : delta < 0 ? "text-rose-300" : "text-slate-500")}>{delta > 0 ? "+" : ""}{delta.toFixed(1)} pp</p></div></div>{source ? <Button type="button" variant="outline" size="sm" onClick={() => void copyVariant(report.id)} className="mt-2 gap-2 border-white/10 text-[8px]"><Copy className="size-3" />{copied === report.id ? "Paste copiado" : "Copiar set candidato"}</Button> : null}</article>; })}</div></section>

      {audit ? <>
        <section className="rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.025] p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[8px] font-black uppercase tracking-[0.14em] text-cyan-300">Auditoría empírica del Team actual</p><h3 className="mt-1 text-base font-black text-white">Qué pasó cuando LIGHT lo llevó contra el meta</h3></div><Badge variant="outline" className="border-cyan-300/15 text-[8px] text-cyan-200">{signalLabel(audit.signal.level)} · {audit.signal.games} partidas</Badge></div><p className="mt-2 text-[9px] text-slate-500">{audit.signal.note}</p><p className="mt-1 text-[8px] text-slate-700">Replays analizados: {audit.replayCoverage.parsed}/{audit.replayCoverage.expected}</p></section>

        <div className="grid gap-3 xl:grid-cols-2"><AuditList title="Matchups más favorables" rows={audit.goodMatchups.map((row) => ({ label: `${row.label} · ${row.scorePercent.toFixed(1)}%`, detail: `${row.wins}-${row.losses}-${row.ties} · ${row.archetypes.join(" + ")}` }))} /><AuditList title="Matchups más duros" rows={audit.badMatchups.map((row) => ({ label: `${row.label} · ${row.scorePercent.toFixed(1)}%`, detail: `${row.wins}-${row.losses}-${row.ties} · ${row.archetypes.join(" + ")}` }))} /></div>

        <section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-rose-300">Ranking de rivales problemáticos</h4><div className="mt-3 grid gap-2 md:grid-cols-2">{audit.problematicOpponents.map((row, index) => <div key={row.id} className="rounded-xl border border-white/6 p-3"><div className="flex justify-between gap-2"><strong className="text-[10px] text-slate-200">#{index + 1} {row.label}</strong><span className="font-mono text-[9px] text-rose-200">{row.scorePercent.toFixed(1)}%</span></div><p className="mt-1 text-[8px] text-slate-600">{row.roster.join(" · ")}</p></div>)}</div></section>

        <div className="grid gap-3 xl:grid-cols-2"><AuditList title="Leads que mejor funcionan" rows={[...audit.leadPerformance].sort((a, b) => b.scorePercent - a.scorePercent || b.games - a.games).slice(0, 8).map((row) => ({ label: `${row.lead} · ${row.scorePercent.toFixed(1)}%`, detail: `${row.games} partidas · ${row.wins}-${row.losses}-${row.ties}` }))} /><AuditList title="Pokémon casi nunca seleccionados / sets a revisar" rows={audit.selectionUsage.map((row) => ({ label: `${row.pokemon} · seleccionado ${row.selectedRate.toFixed(1)}%`, detail: `${row.selectedGames} partidas · lead ${row.leadGames} · score al seleccionarlo ${row.scoreWhenSelected.toFixed(1)}%${row.signal === "rarely-selected" ? " · selección muy baja" : row.signal === "review" ? " · señal de revisión" : ""}` }))} /></div>

        <div className="grid gap-3 xl:grid-cols-2"><AuditList title="Pokémon rivales asociados a derrotas" rows={audit.opponentPokemonPressure.map((row) => ({ label: `${row.pokemon} · ${row.lossShare.toFixed(1)}% de derrotas`, detail: `${row.lossGames} derrotas donde apareció en campo` }))} /><AuditList title="Cores / leads rivales asociados a derrotas" rows={audit.opponentCorePressure.map((row) => ({ label: `${row.core} · ${row.lossShare.toFixed(1)}%`, detail: `${row.lossGames} derrotas con este lead/core observable` }))} /></div>

        <div className="grid gap-3 xl:grid-cols-2"><AuditList title="Moves a revisar (señal, no causalidad)" rows={audit.moveSignals.filter((row) => row.signal === "review").slice(0, 10).map((row) => ({ label: `${row.pokemon} · ${row.move}`, detail: `${row.gamesUsed} partidas · ${row.totalUses} usos · score cuando apareció ${row.scoreWhenUsed.toFixed(1)}%` }))} /><AuditList title="Patrones recurrentes en derrotas" rows={audit.recurringLossPatterns.map((row) => ({ label: row.pattern, detail: `${row.count} eventos/partidas · ${row.lossShare.toFixed(1)}% relativo a derrotas` }))} /></div>

        <section className="rounded-2xl border border-white/7 bg-slate-950/40 p-4"><h4 className="text-[9px] font-black uppercase tracking-[0.13em] text-violet-300">Rendimiento por arquetipo</h4><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{audit.archetypePerformance.map((row) => <div key={row.archetype} className="rounded-xl border border-white/6 p-3"><strong className="text-[10px] text-slate-200">{row.archetype}</strong><p className="mt-1 text-lg font-black text-white">{row.scorePercent.toFixed(1)}%</p><MiniRecord row={row} /></div>)}</div></section>

        <div className="flex items-start gap-2 rounded-2xl border border-white/7 bg-slate-950/35 p-3 text-[9px] leading-4 text-slate-500"><CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-cyan-300" /><div>{job.result.caveat}<ul className="mt-1 space-y-0.5">{audit.limitations.slice(0, 4).map((item) => <li key={item}>• {item}</li>)}</ul></div></div>
      </> : null}
    </div> : null}
  </section>;
}
