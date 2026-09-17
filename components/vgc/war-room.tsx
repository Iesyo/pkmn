"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Crosshair,
  Database,
  ExternalLink,
  Gamepad2,
  Hammer,
  Info,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Scale,
  Search,
  Settings2,
  Shield,
  Sparkles,
  Swords,
  Unlock,
  Users,
  X,
} from "lucide-react";

import { WarRoomAutoLab } from "@/components/vgc/war-room-auto-lab";
import { WarRoomSparring } from "@/components/vgc/war-room-sparring";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { OpponentMetaResponse } from "@/lib/opponent-meta-presets";
import { parseShowdownPaste } from "@/lib/paste";
import { getSpriteUrl, toId } from "@/lib/pokemon-data";
import { formatVersion, serializeShowdownPaste } from "@/lib/team-builder";
import { hydrateSetFromSnapshot, loadShowdownSnapshot, type ShowdownSnapshot } from "@/lib/showdown-data";
import type { PokemonSet, TeamGroup, TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  isWarRoomPasteEvidenceResponse,
  pasteEvidenceSpeciesKey,
  selectWarRoomPasteEvidenceCandidates,
  type WarRoomPasteEvidenceTeam,
} from "@/lib/war-room-paste-evidence";
import {
  WAR_ROOM_FORMAT_ID,
  MAX_WAR_ROOM_LOCKED_IDENTITIES,
  MAX_WAR_ROOM_MEMBER_SUGGESTIONS,
  MAX_WAR_ROOM_MEMBER_SUGGESTIONS_PER_SLOT,
  applyWarRoomSetSuggestion,
  auditTeam,
  buildWarRoomMemberReplacement,
  createWarRoomPokemonLocks,
  isWarRoomCorpusResponse,
  optimizeTeam,
  prepareMatchup,
  warRoomMetaKey,
  type WarRoomAuditResult,
  type WarRoomCorpusResponse,
  type WarRoomCorpusTeam,
  type WarRoomLockField,
  type WarRoomMatchupResult,
  type WarRoomMemberSuggestion,
  type WarRoomOptimizationLocks,
  type WarRoomOptimizationResult,
  type WarRoomPokemonLocks,
  type WarRoomSetSuggestion,
} from "@/lib/war-room";

type WarRoomMode = "audit" | "matchup" | "optimize" | "sparring";

type RivalSetState = {
  teamId: string;
  status: "idle" | "loading" | "exact" | "preview";
  sets: PokemonSet[] | null;
  error: string;
};

type MetaState = {
  teamId: string;
  status: "idle" | "loading" | "ready";
  values: Record<string, OpponentMetaResponse | undefined>;
  loaded: number;
  error: string;
};

type PasteEvidenceState = {
  teamKey: string;
  status: "idle" | "loading" | "ready";
  teams: WarRoomPasteEvidenceTeam[];
  loaded: number;
  failed: number;
  error: string;
};

type OptimizationChangeStep = {
  kind: "member" | "set";
  previousSet: PokemonSet;
  previousLocks: WarRoomPokemonLocks | null;
  excludedMemberSpecies: string[];
  changedFields: string[];
};

type OptimizationChangeHistory = Record<string, OptimizationChangeStep[]>;

type OptimizationComparisonSnapshot = {
  token: number;
  original: TeamVersion;
  optimized: TeamVersion;
};

type MemberApplyState = {
  species: string;
  setId: string;
  status: "idle" | "loading" | "ready" | "fallback" | "error";
  message: string;
};

const MODES: Array<{
  id: WarRoomMode;
  label: string;
  short: string;
  description: string;
  icon: typeof Shield;
}> = [
  {
    id: "audit",
    label: "Auditar mi Team",
    short: "Audit",
    description: "Legalidad M-C, amenazas, cores desfavorables y huecos reales.",
    icon: Shield,
  },
  {
    id: "matchup",
    label: "Preparar un matchup",
    short: "Matchup",
    description: "Mejores cuatro, lead, backline y líneas alternativas contra un rival.",
    icon: Crosshair,
  },
  {
    id: "optimize",
    label: "Optimizar o construir",
    short: "Optimize",
    description: "Bloquea el core y contrasta integrantes y sets con evidencia actual.",
    icon: Settings2,
  },
  {
    id: "sparring",
    label: "Sparring",
    short: "Sparring",
    description: "Juega contra LIGHT M-C con un rival completo de VGCPastes o Mis pastes.",
    icon: Gamepad2,
  },
];

const EMPTY_RIVAL_STATE: RivalSetState = {
  teamId: "",
  status: "idle",
  sets: null,
  error: "",
};

const EMPTY_MEMBER_APPLY_STATE: MemberApplyState = {
  species: "",
  setId: "",
  status: "idle",
  message: "",
};

const EMPTY_META_STATE: MetaState = {
  teamId: "",
  status: "idle",
  values: {},
  loaded: 0,
  error: "",
};

const EMPTY_PASTE_EVIDENCE_STATE: PasteEvidenceState = {
  teamKey: "",
  status: "idle",
  teams: [],
  loaded: 0,
  failed: 0,
  error: "",
};

function cloneTeamVersion(version: TeamVersion): TeamVersion {
  return {
    ...version,
    mechanics: version.mechanics ? [...version.mechanics] : undefined,
    pokemon: version.pokemon.map((set) => ({
      ...set,
      mechanics: set.mechanics ? { ...set.mechanics } : undefined,
      moves: set.moves.map((move) => ({ ...move })),
      performance: { ...set.performance },
    })),
  };
}

function pasteEvidenceTeamKey(team: TeamVersion) {
  return team.pokemon.map((set) => [
    toId(set.species),
    toId(set.item),
    toId(set.ability),
    toId(set.nature),
    set.evs.trim().toLowerCase(),
    ...set.moves.map((move) => toId(move.name)),
  ].join("|")).join("::");
}

function apiError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") return payload.error;
  return fallback;
}

async function readJson(response: Response) {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const text = await response.text();
  if (!contentType.includes("json")) throw new Error("El servidor respondió con una página en lugar de datos de War Room.");
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error("El servidor devolvió datos incompletos.");
  }
}

function isOpponentMetaResponse(value: unknown): value is OpponentMetaResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const root = value as Partial<OpponentMetaResponse>;
  return typeof root.pokemon === "string"
    && root.methodology === "marginal-frequency-composite"
    && Array.isArray(root.presets);
}

function sourceLabel(scope: "exact-set" | "team-preview" | "corpus") {
  if (scope === "exact-set") return "Set exacto";
  if (scope === "team-preview") return "Team preview";
  return "Corpus";
}

function PokemonPill({ species, tone = "slate" }: { species: string; tone?: "cyan" | "violet" | "amber" | "slate" }) {
  const tones = {
    cyan: "border-cyan-300/18 bg-cyan-300/7 text-cyan-100",
    violet: "border-violet-300/18 bg-violet-300/7 text-violet-100",
    amber: "border-amber-300/18 bg-amber-300/7 text-amber-100",
    slate: "border-white/8 bg-white/[0.025] text-slate-300",
  };
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5 rounded-full border py-1 pr-2.5 pl-1.5 text-[10px] font-bold", tones[tone])}>
      <Image src={getSpriteUrl(species)} alt="" width={24} height={24} unoptimized className="size-6 shrink-0 object-contain" />
      <span className="truncate">{species}</span>
    </span>
  );
}

function Metric({ label, value, detail, tone = "slate" }: { label: string; value: string | number; detail: string; tone?: "cyan" | "emerald" | "amber" | "rose" | "slate" }) {
  const tones = {
    cyan: "text-cyan-200",
    emerald: "text-emerald-200",
    amber: "text-amber-200",
    rose: "text-rose-200",
    slate: "text-white",
  };
  return (
    <div className="rounded-2xl border border-white/7 bg-slate-950/55 p-4">
      <p className="text-[9px] font-black uppercase tracking-[0.15em] text-slate-600">{label}</p>
      <p className={cn("mt-2 text-2xl font-black", tones[tone])}>{value}</p>
      <p className="mt-1 text-[10px] leading-4 text-slate-500">{detail}</p>
    </div>
  );
}

function EvidenceNote({ children }: { children: React.ReactNode }) {
  return <div className="flex items-start gap-2 rounded-xl border border-cyan-300/10 bg-cyan-300/[0.035] px-3 py-2.5 text-[10px] leading-4 text-slate-400"><Info className="mt-0.5 size-3.5 shrink-0 text-cyan-300" />{children}</div>;
}

function AuditView({ result }: { result: WarRoomAuditResult }) {
  const legalityTone = result.legality.status === "legal" ? "emerald" : result.legality.status === "review" ? "amber" : "rose";
  const legalityLabel = result.legality.status === "legal" ? "Legal" : result.legality.status === "review" ? "Revisar" : "Bloqueado";
  return (
    <div className="space-y-4">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Regulación M-C" value={legalityLabel} detail={`${result.legality.blockers} bloqueos · ${result.legality.warnings} avisos`} tone={legalityTone} />
        <Metric label="Corpus auditado" value={result.summary.corpusTeams.toLocaleString("es-MX")} detail="Teams públicos de Champions M-C" tone="cyan" />
        <Metric label="Amenazas altas" value={result.summary.criticalThreats + result.summary.highThreats} detail={`${result.summary.unresolvedThreats} sin cobertura directa`} tone="amber" />
        <Metric label="Huecos estructurales" value={result.gaps.length} detail="Defensa, cobertura y funciones visibles" tone={result.gaps.some((gap) => gap.severity === "high") ? "rose" : "slate"} />
      </section>

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Chequeo reglamentario</p><h2 className="mt-1 text-lg font-black text-white">Champions M-C, campo por campo</h2></div>
          <Badge variant="outline" className={cn("text-[9px]", result.legality.status === "legal" ? "border-emerald-300/20 bg-emerald-300/7 text-emerald-200" : result.legality.status === "review" ? "border-amber-300/20 bg-amber-300/7 text-amber-200" : "border-rose-300/20 bg-rose-300/7 text-rose-200")}>{legalityLabel}</Badge>
        </div>
        {result.legality.issues.length ? (
          <div className="mt-4 grid gap-2 lg:grid-cols-2">
            {result.legality.issues.map((issue) => (
              <div key={issue.id} className={cn("rounded-xl border px-3 py-2.5", issue.severity === "blocker" ? "border-rose-300/14 bg-rose-300/[0.045]" : "border-amber-300/14 bg-amber-300/[0.045]")}>
                <div className="flex items-center gap-2"><AlertTriangle className={cn("size-3.5", issue.severity === "blocker" ? "text-rose-300" : "text-amber-300")} /><strong className="text-[10px] text-slate-200">{issue.subject}</strong></div>
                <p className="mt-1 text-[10px] leading-4 text-slate-500">{issue.detail}</p>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-emerald-300/12 bg-emerald-300/[0.04] px-4 py-3 text-xs text-emerald-200"><CheckCircle2 className="size-4" />No se detectaron infracciones ni campos incompletos.</div>
        )}
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
        <div className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
          <div className="flex items-end justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-amber-300">Threat board</p><h2 className="mt-1 text-lg font-black text-white">Amenazas prioritarias</h2></div><Badge variant="outline" className="border-amber-300/15 bg-amber-300/7 text-[9px] text-amber-200">No es win rate</Badge></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {result.threats.slice(0, 10).map((threat) => (
              <article key={threat.species} className="rounded-2xl border border-white/7 bg-slate-950/55 p-4">
                <div className="flex items-start gap-3">
                  <Image src={getSpriteUrl(threat.species)} alt={threat.species} width={56} height={56} unoptimized className="size-14 shrink-0 object-contain" />
                  <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><h3 className="truncate text-sm font-black text-white">{threat.species}</h3><span className={cn("text-[9px] font-black uppercase", threat.tier === "crítica" ? "text-rose-300" : threat.tier === "alta" ? "text-amber-300" : "text-slate-500")}>{threat.tier}</span></div><div className="mt-2 flex items-center gap-2"><Progress value={threat.score} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-amber-300" /><span className="w-8 text-right font-mono text-[9px] text-amber-200">{threat.score}</span></div><p className="mt-1 text-[9px] text-slate-600">{threat.appearances} equipos · {threat.usageRate}% del corpus</p></div>
                </div>
                <p className="mt-3 text-[10px] leading-4 text-slate-400">{threat.reasons.slice(1, 3).join(" ")}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">{threat.answers.length ? threat.answers.slice(0, 3).map((species) => <PokemonPill key={species} species={species} tone="cyan" />) : <Badge variant="outline" className="border-rose-300/15 bg-rose-300/7 text-[9px] text-rose-200">Sin respuesta directa</Badge>}</div>
              </article>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
            <p className="text-[9px] font-black uppercase tracking-[0.16em] text-rose-300">Gap report</p><h2 className="mt-1 text-lg font-black text-white">Huecos reales</h2>
            <div className="mt-4 space-y-2">
              {result.gaps.length ? result.gaps.map((gap) => (
                <div key={gap.id} className="rounded-xl border border-white/7 bg-slate-950/55 p-3">
                  <div className="flex items-center justify-between gap-2"><strong className="text-[10px] text-slate-200">{gap.title}</strong><Badge variant="outline" className={cn("text-[8px] uppercase", gap.severity === "high" ? "border-rose-300/15 text-rose-200" : gap.severity === "medium" ? "border-amber-300/15 text-amber-200" : "border-white/10 text-slate-500")}>{gap.severity}</Badge></div>
                  <p className="mt-1 text-[10px] leading-4 text-slate-500">{gap.detail}</p><p className="mt-2 font-mono text-[8px] text-slate-700">{gap.evidence}</p>
                </div>
              )) : <p className="text-xs text-slate-500">No se detectaron huecos estructurales con estas reglas.</p>}
            </div>
          </section>
        </div>
      </section>

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">Core pressure</p><h2 className="mt-1 text-lg font-black text-white">Cores desfavorables recurrentes</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {result.cores.slice(0, 6).map((core) => (
            <article key={core.id} className="rounded-2xl border border-white/7 bg-slate-950/55 p-4">
              <div className="flex flex-wrap gap-2">{core.species.map((species) => <PokemonPill key={species} species={species} tone="violet" />)}</div>
              <div className="mt-3 flex items-center gap-2"><Progress value={core.score} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-violet-300" /><span className="font-mono text-[9px] text-violet-200">{core.score}</span></div>
              <p className="mt-2 text-[10px] leading-4 text-slate-500">{core.reasons.slice(0, 3).join(" ")}</p>
            </article>
          ))}
        </div>
      </section>

      <div className="grid gap-2 lg:grid-cols-3">{result.notes.map((note) => <EvidenceNote key={note}>{note}</EvidenceNote>)}</div>
    </div>
  );
}

function RivalPicker({
  teams,
  selected,
  query,
  onQueryChange,
  onSelect,
}: {
  teams: WarRoomCorpusTeam[];
  selected: WarRoomCorpusTeam | null;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (team: WarRoomCorpusTeam) => void;
}) {
  const normalized = query.trim().toLocaleLowerCase();
  const visible = teams.filter((team) => !normalized || `${team.playerName} ${team.tournament} ${team.rank} ${team.pokemon.join(" ")}`.toLocaleLowerCase().includes(normalized)).slice(0, 12);
  return (
    <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
      <div className="flex items-start gap-3"><div className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-violet-300/15 bg-violet-300/7"><Search className="size-4 text-violet-300" /></div><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">Rival target</p><h2 className="mt-1 text-lg font-black text-white">Selecciona un equipo publicado</h2></div></div>
      <Input type="search" value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Jugador, evento o Pokémon…" className="mt-4 border-white/10 bg-slate-950/70" />
      <div className="mt-3 max-h-72 space-y-1.5 overflow-y-auto pr-1">
        {visible.map((team) => (
          <button key={team.id} type="button" onClick={() => onSelect(team)} className={cn("w-full rounded-xl border px-3 py-2.5 text-left transition", selected?.id === team.id ? "border-violet-300/35 bg-violet-300/9" : "border-white/7 bg-slate-950/45 hover:border-violet-300/20 hover:bg-violet-300/[0.035]")}>
            <div className="flex items-center justify-between gap-2"><span className="flex min-w-0 items-center gap-2"><strong className="truncate text-[10px] text-slate-200">{team.playerName}</strong>{team.source === "scouting-library" ? <Badge variant="outline" className="shrink-0 border-cyan-300/15 bg-cyan-300/7 text-[7px] text-cyan-200">Mis pastes</Badge> : null}</span><span className="shrink-0 font-mono text-[8px] text-slate-700">{team.id}</span></div>
            <p className="mt-1 truncate text-[9px] text-slate-600">{[team.rank, team.tournament].filter((value) => value && value !== "-").join(" · ") || "Champions M-C"}</p>
            <div className="mt-2 flex gap-0.5">{team.pokemon.map((species, index) => <Image key={`${team.id}-${species}-${index}`} src={getSpriteUrl(species)} alt={species} title={species} width={28} height={28} unoptimized className="size-7 object-contain" />)}</div>
          </button>
        ))}
        {!visible.length ? <p className="px-3 py-8 text-center text-xs text-slate-600">No hay equipos con esa búsqueda.</p> : null}
      </div>
    </section>
  );
}

function PlanPokemon({ title, species, tone }: { title: string; species: string[]; tone: "cyan" | "violet" | "slate" }) {
  return (
    <div><p className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">{title}</p><div className="mt-2 flex flex-wrap gap-1.5">{species.map((name) => <PokemonPill key={name} species={name} tone={tone} />)}</div></div>
  );
}

function MatchupView({ result, rivalState }: { result: WarRoomMatchupResult | null; rivalState: RivalSetState }) {
  if (!result) {
    return <section className="rounded-[24px] border border-dashed border-white/10 bg-slate-900/30 px-6 py-20 text-center"><Crosshair className="mx-auto size-9 text-slate-700" /><h2 className="mt-4 text-lg font-black text-white">Elige un rival para preparar la hoja de partida</h2><p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-600">War Room intentará cargar su PokéPaste exacto; si no existe, trabajará con los seis del team preview y lo marcará explícitamente.</p></section>;
  }
  const plan = result.recommended;
  return (
    <div className="space-y-4">
      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div><div className="flex flex-wrap items-center gap-2"><p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">{result.rival.playerName}</p><Badge variant="outline" className={cn("text-[8px]", result.evidenceScope === "exact-set" ? "border-emerald-300/18 bg-emerald-300/7 text-emerald-200" : "border-amber-300/18 bg-amber-300/7 text-amber-200")}>{sourceLabel(result.evidenceScope)}</Badge>{rivalState.status === "loading" ? <Badge variant="outline" className="gap-1 border-cyan-300/15 text-[8px] text-cyan-200"><Loader2 className="size-2.5 animate-spin" />Cargando paste</Badge> : null}</div><h2 className="mt-1 text-xl font-black text-white">Plan de matchup</h2><p className="mt-1 text-[10px] text-slate-500">{[result.rival.rank, result.rival.tournament].filter((value) => value && value !== "-").join(" · ") || "Champions M-C"}</p></div>
          <div className="flex flex-wrap gap-1.5">{result.rival.pokemon.map((species) => <PokemonPill key={species} species={species} />)}</div>
        </div>
        {rivalState.error ? <p className="mt-3 rounded-xl border border-amber-300/15 bg-amber-300/7 px-3 py-2 text-[10px] text-amber-100">{rivalState.error} Se mantiene el análisis de team preview.</p> : null}
      </section>

      {plan ? (
        <section className="overflow-hidden rounded-[24px] border border-cyan-300/15 bg-gradient-to-br from-cyan-300/[0.07] via-slate-900/55 to-violet-300/[0.05]">
          <div className="h-px bg-gradient-to-r from-cyan-300 via-violet-300 to-transparent" />
          <div className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Recomendación principal</p><h2 className="mt-1 text-xl font-black text-white">{plan.intent}</h2></div><div className="text-right"><p className="font-mono text-2xl font-black text-cyan-200">{plan.fit}</p><p className="text-[8px] uppercase tracking-wide text-slate-600">índice de encaje · no win rate</p></div></div>
            <div className="mt-5 grid gap-5 md:grid-cols-2"><PlanPokemon title="Lead recomendado" species={plan.lead} tone="cyan" /><PlanPokemon title="Backline" species={plan.backline} tone="violet" /></div>
            <div className="mt-4 rounded-xl border border-white/7 bg-slate-950/35 px-4 py-3"><p className="text-[9px] font-black uppercase tracking-wide text-slate-600">Por qué</p><ul className="mt-2 space-y-1 text-[10px] leading-4 text-slate-400">{plan.reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul></div>
          </div>
        </section>
      ) : null}

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Selection matrix</p><h2 className="mt-1 text-lg font-black text-white">Los seis, ordenados para este rival</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {result.picks.map((pick) => {
            const selected = plan ? [...plan.lead, ...plan.backline].some((species) => toId(species) === toId(pick.species)) : false;
            const lead = plan?.lead.some((species) => toId(species) === toId(pick.species));
            return (
              <article key={pick.id} className={cn("rounded-2xl border p-4", selected ? "border-cyan-300/18 bg-cyan-300/[0.04]" : "border-white/7 bg-slate-950/45 opacity-70")}>
                <div className="flex items-center gap-3"><Image src={getSpriteUrl(pick.species)} alt={pick.species} width={52} height={52} unoptimized className="size-13 object-contain" /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><h3 className="truncate text-sm font-black text-white">{pick.species}</h3>{selected ? <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[8px] text-cyan-200">{lead ? "Lead" : "Back"}</Badge> : <span className="text-[8px] uppercase text-slate-700">Bench</span>}</div><div className="mt-2 flex items-center gap-2"><Progress value={pick.fit} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300" /><span className="font-mono text-[9px] text-cyan-200">{pick.fit}</span></div></div></div>
                <div className="mt-3 grid grid-cols-4 gap-1 text-center text-[8px]"><span className="rounded-lg bg-white/3 px-1 py-1.5 text-slate-500">ATK <strong className="block text-slate-300">{pick.offense}</strong></span><span className="rounded-lg bg-white/3 px-1 py-1.5 text-slate-500">SAFE <strong className="block text-slate-300">{pick.safety}</strong></span><span className="rounded-lg bg-white/3 px-1 py-1.5 text-slate-500">SPEED <strong className="block text-slate-300">{pick.speed}</strong></span><span className="rounded-lg bg-white/3 px-1 py-1.5 text-slate-500">UTIL <strong className="block text-slate-300">{pick.utility}</strong></span></div>
                <p className="mt-3 text-[9px] leading-4 text-slate-600">Cubre: {pick.covers.join(", ") || "sin cobertura supereficaz directa"}.</p>
                {pick.damage[0] ? <p className="mt-1 text-[9px] leading-4 text-emerald-300/70">Mejor daño neutral: {pick.damage[0].move} → {pick.damage[0].target}, hasta {pick.damage[0].maxPercent}%.</p> : null}
                {pick.incomingDamage[0] ? <p className="mt-1 text-[9px] leading-4 text-rose-300/70">Mayor presión recibida: {pick.incomingDamage[0].source} con {pick.incomingDamage[0].move}, hasta {pick.incomingDamage[0].maxPercent}%.</p> : null}
              </article>
            );
          })}
        </div>
      </section>

      {result.alternatives.length ? <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5"><p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">Fallback lines</p><h2 className="mt-1 text-lg font-black text-white">Planes alternativos</h2><div className="mt-4 grid gap-3 lg:grid-cols-2">{result.alternatives.map((alternative) => <article key={alternative.id} className="rounded-2xl border border-white/7 bg-slate-950/50 p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-[9px] font-black text-violet-200">{alternative.label}</p><h3 className="mt-1 text-sm font-black text-white">{alternative.intent}</h3></div><span className="font-mono text-sm font-bold text-violet-200">{alternative.fit}</span></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><PlanPokemon title="Lead" species={alternative.lead} tone="violet" /><PlanPokemon title="Backline" species={alternative.backline} tone="slate" /></div><p className="mt-3 text-[10px] leading-4 text-slate-500">{alternative.reasons.join(" ")}</p></article>)}</div></section> : null}

      <div className="grid gap-2 lg:grid-cols-3">{result.notes.map((note) => <EvidenceNote key={note}>{note}</EvidenceNote>)}</div>
    </div>
  );
}

function FieldLockButton({
  label,
  value,
  locked,
  disabled = false,
  onToggle,
}: {
  label: string;
  value: string;
  locked: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={locked}
      disabled={disabled}
      onClick={onToggle}
      title={`${locked ? "Liberar" : "Bloquear"} ${label}: ${value}`}
      className={cn(
        "w-full min-w-0 rounded-xl border px-2.5 py-2 text-left transition",
        locked
          ? "border-cyan-300/30 bg-cyan-300/[0.08] text-cyan-100"
          : "border-white/7 bg-slate-950/55 text-slate-500 hover:border-white/15 hover:text-slate-300",
        disabled && "cursor-not-allowed opacity-40",
      )}
    >
      <span className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-wide">
        {locked ? <Lock className="size-3 shrink-0 text-cyan-300" /> : <Unlock className="size-3 shrink-0 text-slate-700" />}
        {label}
      </span>
      <span className="mt-1 block truncate text-[9px]" title={value}>{value}</span>
    </button>
  );
}

function PokemonLockCard({
  set,
  locks,
  identityCount,
  changeSummary,
  undoTitle,
  onToggle,
  onToggleWholeSet,
  onUndo,
}: {
  set: PokemonSet;
  locks: WarRoomPokemonLocks;
  identityCount: number;
  changeSummary?: string;
  undoTitle?: string;
  onToggle: (field: WarRoomLockField) => void;
  onToggleWholeSet: (locked: boolean) => void;
  onUndo?: () => void;
}) {
  const fullyLocked = locks.item && locks.ability && locks.nature && locks.statPoints && locks.moves.every(Boolean);
  const lockedCount = Number(locks.identity)
    + Number(locks.item)
    + Number(locks.ability)
    + Number(locks.nature)
    + Number(locks.statPoints)
    + locks.moves.filter(Boolean).length;
  const moveFields = [
    { key: "move-0" as const, label: "Movimiento 1", value: set.moves[0]?.name || "Vacío", locked: locks.moves[0] },
    { key: "move-1" as const, label: "Movimiento 2", value: set.moves[1]?.name || "Vacío", locked: locks.moves[1] },
    { key: "move-2" as const, label: "Movimiento 3", value: set.moves[2]?.name || "Vacío", locked: locks.moves[2] },
    { key: "move-3" as const, label: "Movimiento 4", value: set.moves[3]?.name || "Vacío", locked: locks.moves[3] },
  ];

  return (
    <article className={cn("rounded-2xl border p-3", onUndo ? "border-violet-300/25 bg-violet-300/[0.045]" : lockedCount ? "border-cyan-300/20 bg-cyan-300/[0.035]" : "border-white/7 bg-slate-950/45")}>
      <div className="flex items-center gap-3">
        {set.species
          ? <Image src={getSpriteUrl(set.species)} alt={set.species} width={48} height={48} unoptimized className="size-12 shrink-0 object-contain" />
          : <div className="flex size-12 shrink-0 items-center justify-center rounded-full border border-dashed border-white/10 text-slate-700"><Plus className="size-5" /></div>}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-xs font-black text-white">{set.species || `Slot ${set.slot} libre`}</h3>
          <p className={cn("mt-0.5 text-[8px]", onUndo ? "text-violet-200" : "text-slate-600")}>{onUndo ? changeSummary : lockedCount ? `${lockedCount} ${lockedCount === 1 ? "campo protegido" : "campos protegidos"}` : "Sin restricciones"}</p>
        </div>
        <div className="flex items-center gap-1.5">
          {onUndo ? <button type="button" onClick={onUndo} aria-label={`Deshacer cambio de ${set.species || `Slot ${set.slot}`}`} title={undoTitle || "Deshacer último cambio"} className="flex size-7 items-center justify-center rounded-lg border border-violet-300/20 bg-violet-300/7 text-violet-200 transition hover:bg-violet-300/15"><X className="size-3.5" /></button> : null}
          <button type="button" onClick={() => onToggleWholeSet(!fullyLocked)} className="rounded-lg border border-white/8 px-2 py-1 text-[8px] font-bold text-slate-500 transition hover:border-cyan-300/20 hover:text-cyan-200">
            {fullyLocked ? "Liberar set" : "Bloquear set"}
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5">
        <FieldLockButton label="Identidad" value={set.species || "Slot libre"} locked={locks.identity} disabled={!set.species || (!locks.identity && identityCount >= MAX_WAR_ROOM_LOCKED_IDENTITIES)} onToggle={() => onToggle("identity")} />
        <FieldLockButton label="Objeto" value={set.item || "Sin objeto"} locked={locks.item} onToggle={() => onToggle("item")} />
        <FieldLockButton label="Habilidad" value={set.ability || "Sin declarar"} locked={locks.ability} onToggle={() => onToggle("ability")} />
        <FieldLockButton label="Naturaleza" value={set.nature || "Sin declarar"} locked={locks.nature} onToggle={() => onToggle("nature")} />
        <div className="col-span-2">
          <FieldLockButton label="Stat Points" value={set.evs || "0"} locked={locks.statPoints} onToggle={() => onToggle("statPoints")} />
        </div>
      </div>

      <p className="mt-3 text-[8px] font-black uppercase tracking-[0.12em] text-slate-700">Slots de movimiento</p>
      <div className="mt-1.5 grid grid-cols-2 gap-1.5">
        {moveFields.map((move) => <FieldLockButton key={move.key} label={move.label} value={move.value} locked={move.locked} onToggle={() => onToggle(move.key)} />)}
      </div>
      {fullyLocked ? <p className="mt-2 rounded-lg border border-cyan-300/12 bg-cyan-300/[0.04] px-2 py-1.5 text-[8px] leading-3 text-cyan-200">Set completo protegido: el motor no propondrá cambios para este Pokémon.</p> : null}
    </article>
  );
}

function SetSuggestionCard({ suggestion, onApply }: { suggestion: WarRoomSetSuggestion; onApply: () => void }) {
  const observed = suggestion.methodology !== "battle-data-fallback";
  const badge = suggestion.methodology === "observed-paste"
    ? "Paste observado"
    : suggestion.methodology === "observed-paste-patched"
      ? "Paste + parche"
      : "Battle Data · fallback";
  return (
    <article className="rounded-2xl border border-white/7 bg-slate-950/55 p-4">
      <div className="flex items-center gap-3">
        <Image src={getSpriteUrl(suggestion.species)} alt={suggestion.species} width={50} height={50} unoptimized className="size-12 object-contain" />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-black text-white">{suggestion.species}</h3>
          <p className={cn("mt-0.5 text-[9px] font-bold", suggestion.structuralDelta > 0 ? "text-emerald-300" : suggestion.structuralDelta < 0 ? "text-amber-300" : "text-slate-500")}>{suggestion.structuralDelta > 0 ? "+" : ""}{suggestion.structuralDelta} encaje estructural</p>
        </div>
        <Badge variant="outline" className={cn("text-[8px]", observed ? "border-emerald-300/15 bg-emerald-300/7 text-emerald-200" : "border-amber-300/15 bg-amber-300/7 text-amber-200")}>{badge}</Badge>
      </div>

      {suggestion.source ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-300/10 bg-emerald-300/[0.025] px-3 py-2 text-[8px] text-emerald-100">
          <span>{suggestion.source.label}{suggestion.source.rank ? ` · ${suggestion.source.rank}` : ""} · contexto {suggestion.source.contextFit}/100</span>
          {suggestion.source.historical ? <Badge variant="outline" className="border-amber-300/15 bg-amber-300/7 text-[8px] text-amber-200">Set histórico legal · {suggestion.source.formatLabel}</Badge> : null}
          {suggestion.source.url ? <a href={suggestion.source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-bold text-emerald-300 hover:text-emerald-200">Ver paste <ExternalLink className="size-3" /></a> : null}
        </div>
      ) : null}

      {suggestion.preservedFields.length ? (
        <div className="mt-3 rounded-xl border border-cyan-300/12 bg-cyan-300/[0.035] p-2.5">
          <p className="text-[8px] font-black uppercase tracking-wide text-cyan-300">Preservado por tus bloqueos</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {suggestion.preservedFields.map((field) => <Badge key={field.key} variant="outline" title={`${field.label}: ${field.value}`} className="max-w-full border-cyan-300/15 bg-cyan-300/[0.04] text-[8px] text-cyan-100"><span className="truncate">{field.label}: {field.value}</span></Badge>)}
          </div>
        </div>
      ) : null}

      <div className="mt-3 space-y-2">
        {suggestion.changes.map((change) => (
          <div key={change.key} className="rounded-xl border border-white/6 bg-white/[0.02] px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[8px] font-black uppercase tracking-wide text-slate-600">{change.field}</span>
              {change.source === "paste"
                ? <span className="text-[8px] font-bold text-emerald-300">Paste observado</span>
                : <span className="font-mono text-[8px] text-amber-200">{change.evidence !== null ? `${change.evidence}% marginal` : "Battle Data · estimado"}</span>}
            </div>
            <p className="mt-1 line-clamp-2 text-[9px] leading-4 text-slate-500"><span className="text-slate-700">Actual:</span> {change.current}</p>
            <p className="line-clamp-2 text-[9px] leading-4 text-slate-300"><span className="text-amber-300">Probar:</span> {change.suggested}</p>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[9px] leading-4 text-slate-600">{suggestion.reasons.join(" ")}</p>
      <Button type="button" variant="outline" size="sm" onClick={onApply} className="mt-3 w-full gap-2 border-amber-300/15 bg-amber-300/5 text-[9px] font-black text-amber-100 hover:bg-amber-300/10"><CheckCircle2 className="size-3.5" />Aplicar cambios al borrador</Button>
    </article>
  );
}

function MemberSuggestionCard({ member, loading, disabled, onApply }: { member: WarRoomMemberSuggestion; loading: boolean; disabled: boolean; onApply: () => void }) {
  return (
    <article className="flex flex-col rounded-2xl border border-white/7 bg-slate-950/55 p-4 transition hover:border-violet-300/20">
      <div className="flex items-center gap-3">
        <Image src={getSpriteUrl(member.observedAs)} alt={member.species} width={54} height={54} unoptimized className="size-13 object-contain" />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="truncate text-sm font-black text-white">{member.species}</h3>
            {member.isMega ? <Badge variant="outline" className="shrink-0 border-fuchsia-300/18 bg-fuchsia-300/7 px-1.5 text-[8px] text-fuchsia-200">Mega</Badge> : null}
            {member.evidenceMode === "historical" ? <Badge variant="outline" className="shrink-0 border-cyan-300/18 bg-cyan-300/7 px-1.5 text-[8px] text-cyan-200">Histórico · {member.evidenceRegulations.join("/")}</Badge> : null}
            {member.evidenceMode === "expanded" ? <Badge variant="outline" className="shrink-0 border-amber-300/18 bg-amber-300/7 px-1.5 text-[8px] text-amber-200">Corpus ampliado</Badge> : null}
          </div>
          <p className="mt-0.5 text-[9px] text-violet-200">por {member.replaces}</p>
          <div className="mt-2 flex items-center gap-2"><Progress value={member.score} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-violet-300" /><span className="whitespace-nowrap font-mono text-[9px] text-violet-200">Encaje {member.score}/100</span></div>
        </div>
      </div>
      <p className="mt-3 flex-1 text-[10px] leading-4 text-slate-500">{member.reasons.join(" ")}</p>
      {member.patchedTypes.length ? <div className="mt-3 flex flex-wrap gap-1">{member.patchedTypes.map((type) => <Badge key={type} variant="outline" className="border-emerald-300/12 bg-emerald-300/5 text-[8px] text-emerald-200">+ {type}</Badge>)}</div> : null}
      <Button type="button" variant="outline" size="sm" onClick={onApply} disabled={disabled} className="mt-3 w-full gap-2 border-violet-300/18 bg-violet-300/7 text-[9px] font-black text-violet-100 hover:bg-violet-300/14">{loading ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}{loading ? "Armando set viable…" : "Elegir y recalcular"}</Button>
    </article>
  );
}

function OptimizationView({
  team,
  result,
  optimizationLocks,
  metaState,
  pasteEvidenceState,
  memberApplyState,
  optimizationHistory,
  onToggleLock,
  onToggleWholeSet,
  onLoadMeta,
  onOpenBuilder,
  onCompare,
  canCompare,
  comparisonOpen,
  onApplySet,
  onApplyMember,
  onUndoChange,
}: {
  team: TeamVersion;
  result: WarRoomOptimizationResult;
  optimizationLocks: WarRoomOptimizationLocks;
  metaState: MetaState;
  pasteEvidenceState: PasteEvidenceState;
  memberApplyState: MemberApplyState;
  optimizationHistory: OptimizationChangeHistory;
  onToggleLock: (id: string, field: WarRoomLockField) => void;
  onToggleWholeSet: (id: string, locked: boolean) => void;
  onLoadMeta: () => void;
  onOpenBuilder: () => void;
  onCompare: () => void;
  canCompare: boolean;
  comparisonOpen: boolean;
  onApplySet: (suggestion: WarRoomSetSuggestion) => void;
  onApplyMember: (member: WarRoomMemberSuggestion) => void;
  onUndoChange: (setId: string) => void;
}) {
  const identityCount = result.lockedSpecies.length;
  const coreSampleLabel = result.coreSample.mode === "exact"
    ? "core exacto"
    : result.coreSample.mode === "partial"
      ? "coincidencia parcial"
      : "sin coincidencia directa";
  const usesExpandedCorpus = result.members.some((member) => member.evidenceMode === "expanded");
  const usesHistoricalCorpus = result.members.some((member) => member.evidenceMode === "historical");
  const allIdentitiesLocked = team.pokemon.every((set) => optimizationLocks[set.id]?.identity);
  return (
    <div className="space-y-4">
      <section id="war-room-optimization-locks" className="scroll-mt-4 rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Bloqueos por Pokémon</p>
            <h2 className="mt-1 text-lg font-black text-white">Protege solo lo que quieres conservar</h2>
            <p className="mt-1 max-w-3xl text-[10px] leading-4 text-slate-500">Identidad evita reemplazar al integrante y permite proteger los seis miembros del Team. Objeto, habilidad, naturaleza, Stat Points y cada movimiento restringen únicamente las propuestas de set.</p>
          </div>
          <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">{identityCount}/{MAX_WAR_ROOM_LOCKED_IDENTITIES} identidades</Badge>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {team.pokemon.map((set) => {
            const lastChange = optimizationHistory[set.id]?.at(-1);
            const previousSpecies = lastChange?.previousSet.species || `Slot ${set.slot} libre`;
            const changeSummary = lastChange?.kind === "set"
              ? `${lastChange.changedFields.length} ${lastChange.changedFields.length === 1 ? "ajuste de set aplicado" : "ajustes de set aplicados"}`
              : lastChange ? `Reemplazó a ${previousSpecies}` : undefined;
            const undoTitle = lastChange?.kind === "set"
              ? `Deshacer ajustes de ${set.species}`
              : lastChange ? `Volver a ${previousSpecies}` : undefined;
            return <PokemonLockCard
              key={set.id}
              set={set}
              locks={optimizationLocks[set.id] ?? createWarRoomPokemonLocks()}
              identityCount={identityCount}
              changeSummary={changeSummary}
              undoTitle={undoTitle}
              onToggle={(field) => onToggleLock(set.id, field)}
              onToggleWholeSet={(locked) => onToggleWholeSet(set.id, locked)}
              onUndo={lastChange ? () => onUndoChange(set.id) : undefined}
            />;
          })}
        </div>
      </section>

      {!identityCount ? <section className="rounded-[24px] border border-dashed border-cyan-300/15 bg-cyan-300/[0.025] px-6 py-14 text-center"><Lock className="mx-auto size-8 text-cyan-300/50" /><h3 className="mt-3 text-sm font-black text-white">Bloquea al menos una identidad para buscar partners</h3><p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-slate-600">Los bloqueos de set ya se respetan. Al proteger una identidad, el motor además buscará compañeros observados con ese núcleo sin proponer reemplazarla.</p></section> : (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">Partner search</p><h2 className="mt-1 text-lg font-black text-white">Integrantes que encajan con el core</h2><p className="mt-1 text-[10px] text-slate-600">Orden: coaparición en M-C → relación histórica ponderada → frecuencia general en M-C. Dentro de cada grupo gana el mayor Encaje, que combina evidencia contextual y balance defensivo.</p><p className="mt-1 text-[9px] text-slate-700">Hasta {MAX_WAR_ROOM_MEMBER_SUGGESTIONS_PER_SLOT} alternativas por identidad desbloqueada y {MAX_WAR_ROOM_MEMBER_SUGGESTIONS} por ronda; máximo dos Megas por Team. La legalidad de M-C se valida antes de mostrar cada tarjeta.</p></div><div className="flex flex-wrap items-center gap-2"><Badge variant="outline" className={cn("text-[9px]", result.megaPolicy.configured >= result.megaPolicy.maximum ? "border-fuchsia-300/18 bg-fuchsia-300/7 text-fuchsia-200" : "border-white/8 text-slate-400")}>{result.megaPolicy.configured >= result.megaPolicy.maximum ? `${result.megaPolicy.configured} Megas · sin extras` : `${result.megaPolicy.configured}/${result.megaPolicy.maximum} Megas`}</Badge><Badge variant="outline" className={cn("text-[9px]", result.coreSample.mode === "exact" ? "border-emerald-300/15 text-emerald-200" : "border-amber-300/15 text-amber-200")}>{result.coreSample.size} teams · {coreSampleLabel}</Badge>{usesHistoricalCorpus ? <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/5 text-[9px] text-cyan-200">Evidencia histórica</Badge> : null}{usesExpandedCorpus ? <Badge variant="outline" className="border-amber-300/15 bg-amber-300/5 text-[9px] text-amber-200">Búsqueda ampliada</Badge> : null}</div></div>
          {memberApplyState.message ? <p className={cn("mt-3 rounded-xl border px-3 py-2 text-[10px]", memberApplyState.status === "error" ? "border-rose-300/18 bg-rose-300/7 text-rose-100" : memberApplyState.status === "fallback" ? "border-amber-300/18 bg-amber-300/7 text-amber-100" : "border-emerald-300/15 bg-emerald-300/5 text-emerald-100")}>{memberApplyState.message}</p> : null}
          {result.members.length ? <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{result.members.map((member) => <MemberSuggestionCard key={`${member.replacesSetId}-${member.species}`} member={member} loading={memberApplyState.status === "loading" && memberApplyState.species === member.species && memberApplyState.setId === member.replacesSetId} disabled={memberApplyState.status === "loading"} onApply={() => onApplyMember(member)} />)}</div> : <p className="mt-5 rounded-xl border border-white/7 bg-slate-950/45 px-4 py-8 text-center text-xs text-slate-600">{allIdentitiesLocked ? "Los seis integrantes están bloqueados. Libera una identidad para buscar sustitutos." : "Ya agotaste las alternativas compatibles disponibles en el corpus para este estado del Team."}</p>}
        </section>
      )}

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-amber-300">Set search</p><h2 className="mt-1 text-lg font-black text-white">Objeto, moves, naturaleza y Stat Points</h2><p className="mt-1 max-w-2xl text-[10px] leading-4 text-slate-500">Busca primero sets completos en pastes comparables y conserva su contexto. Battle Data solo rellena campos ausentes o funciona como último recurso.</p></div><Button type="button" variant="outline" onClick={onLoadMeta} disabled={metaState.status === "loading" || pasteEvidenceState.status === "loading"} className="gap-2 border-amber-300/18 bg-amber-300/7 text-xs font-black text-amber-100 hover:bg-amber-300/12">{metaState.status === "loading" || pasteEvidenceState.status === "loading" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}{metaState.status === "loading" || pasteEvidenceState.status === "loading" ? "Revisando pastes…" : pasteEvidenceState.status === "ready" ? "Recalcular contexto" : "Buscar cambios de set"}</Button></div>
        {pasteEvidenceState.error || metaState.error ? <p className="mt-3 rounded-xl border border-amber-300/15 bg-amber-300/7 px-3 py-2 text-[10px] text-amber-100">{[pasteEvidenceState.error, metaState.error].filter(Boolean).join(" ")}</p> : null}
        {pasteEvidenceState.status === "ready" ? <p className="mt-3 text-[9px] text-slate-600">{pasteEvidenceState.loaded} pastes completos cargados · {result.pasteEvidence.matchedSets} sets del Team observados · Battle Data usado para {metaState.loaded} integrantes sin propuesta contextual completa.{pasteEvidenceState.failed ? ` ${pasteEvidenceState.failed} referencias no estuvieron disponibles o eran duplicadas.` : ""}</p> : null}
        {result.sets.length ? (
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {result.sets.map((suggestion) => <SetSuggestionCard key={`${suggestion.species}-${suggestion.presetId}`} suggestion={suggestion} onApply={() => onApplySet(suggestion)} />)}
          </div>
        ) : pasteEvidenceState.status === "ready" || metaState.status === "ready" ? (
          <p className="mt-5 rounded-xl border border-white/7 bg-slate-950/45 px-4 py-8 text-center text-xs leading-5 text-slate-600">
            {result.locks.some((entry) => entry.fullyLocked)
              ? `${result.locks.filter((entry) => entry.fullyLocked).length} ${result.locks.filter((entry) => entry.fullyLocked).length === 1 ? "set está completamente protegido" : "sets están completamente protegidos"}. No encontramos otro paquete legal distinto que respete los bloqueos restantes.`
              : "No encontramos un paquete legal distinto con evidencia suficiente que respete tus bloqueos."}
          </p>
        ) : <div className="mt-4 rounded-xl border border-dashed border-white/8 px-4 py-10 text-center text-xs text-slate-600">Ejecuta la búsqueda para comparar los seis sets sin tocar los campos protegidos.</div>}
      </section>

      <section className="flex flex-col gap-3 rounded-[24px] border border-cyan-300/12 bg-cyan-300/[0.035] p-5 sm:flex-row sm:items-center sm:justify-between"><h3 className="text-sm font-black text-white">Valida o continúa con el borrador</h3><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" onClick={onCompare} disabled={!canCompare || comparisonOpen} title={!canCompare ? "Aplica al menos un cambio antes de comparar" : comparisonOpen ? "La comparación ya está abierta abajo" : "Capturar el borrador y preparar el benchmark A/B"} className="gap-2 border-violet-300/20 bg-violet-300/7 font-black text-violet-100 hover:bg-violet-300/14"><Scale className="size-4" />{comparisonOpen ? "Comparación abierta" : "Comparar con original"}</Button><Button type="button" onClick={onOpenBuilder} className="gap-2 bg-cyan-300 font-black text-slate-950 hover:bg-cyan-200"><Hammer className="size-4" />Abrir en Team Builder</Button></div></section>
    </div>
  );
}

export function WarRoom({ groups, initialTeam, onOpenBuilder }: { groups: TeamGroup[]; initialTeam?: TeamVersion; onOpenBuilder: (version: TeamVersion) => void }) {
  const versions = useMemo(() => {
    const stored = groups.flatMap((group) => group.versions);
    return initialTeam && !stored.some((version) => version.id === initialTeam.id)
      ? [initialTeam, ...stored]
      : stored;
  }, [groups, initialTeam]);
  const [mode, setMode] = useState<WarRoomMode>("audit");
  const [teamId, setTeamId] = useState(initialTeam?.id ?? "");
  const [resources, setResources] = useState<{ corpus: WarRoomCorpusResponse; snapshot: ShowdownSnapshot } | null>(null);
  const [resourceError, setResourceError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [rivalQuery, setRivalQuery] = useState("");
  const [rivalId, setRivalId] = useState("");
  const [rivalState, setRivalState] = useState<RivalSetState>(EMPTY_RIVAL_STATE);
  const rivalRequest = useRef(0);
  const [optimizationLocks, setOptimizationLocks] = useState<WarRoomOptimizationLocks>({});
  const [optimizationPokemon, setOptimizationPokemon] = useState<PokemonSet[] | null>(null);
  const [optimizationHistory, setOptimizationHistory] = useState<OptimizationChangeHistory>({});
  const [optimizationComparison, setOptimizationComparison] = useState<OptimizationComparisonSnapshot | null>(null);
  const [memberApplyState, setMemberApplyState] = useState<MemberApplyState>(EMPTY_MEMBER_APPLY_STATE);
  const memberApplyRequest = useRef(0);
  const [metaState, setMetaState] = useState<MetaState>(EMPTY_META_STATE);
  const metaRequest = useRef(0);
  const [pasteEvidenceState, setPasteEvidenceState] = useState<PasteEvidenceState>(EMPTY_PASTE_EVIDENCE_STATE);
  const pasteEvidenceRequest = useRef(0);

  const selectedTeam = versions.find((version) => version.id === teamId) ?? versions[0] ?? null;
  const workingTeam = useMemo(() => {
    if (!selectedTeam) return null;
    const pokemon = optimizationPokemon ?? selectedTeam.pokemon;
    return pokemon === selectedTeam.pokemon ? selectedTeam : {
      ...selectedTeam,
      pokemon,
      paste: serializeShowdownPaste(pokemon, selectedTeam.mechanics ?? ["mega"]),
    };
  }, [optimizationPokemon, selectedTeam]);
  const workingTeamKey = useMemo(() => workingTeam ? pasteEvidenceTeamKey(workingTeam) : "", [workingTeam]);
  const activePasteEvidence = useMemo(
    () => pasteEvidenceState.teamKey === workingTeamKey ? pasteEvidenceState.teams : [],
    [pasteEvidenceState, workingTeamKey],
  );
  const excludedMemberSpecies = useMemo(() => [...new Set(
    Object.values(optimizationHistory).flatMap((steps) => steps.flatMap((step) => step.excludedMemberSpecies)),
  )], [optimizationHistory]);
  const hasOptimizationChanges = useMemo(
    () => Object.values(optimizationHistory).some((steps) => steps.length > 0),
    [optimizationHistory],
  );
  const selectedRival = resources?.corpus.teams.find((team) => team.id === rivalId) ?? null;
  const pasteCandidateCorpus = useMemo(() => resources
    ? [...resources.corpus.teams, ...resources.corpus.historicalTeams]
    : [], [resources]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setResourceError("");
      try {
        const [response, snapshot] = await Promise.all([
          fetch(`/api/war-room?format=${WAR_ROOM_FORMAT_ID}`, { cache: "no-store" }),
          loadShowdownSnapshot(),
        ]);
        const payload = await readJson(response);
        if (!response.ok) throw new Error(apiError(payload, "No pudimos cargar el corpus de War Room."));
        if (!isWarRoomCorpusResponse(payload)) throw new Error("El corpus competitivo llegó en un formato inesperado.");
        if (active) setResources({ corpus: payload, snapshot });
      } catch (caught) {
        if (active) setResourceError(caught instanceof Error ? caught.message : "No pudimos iniciar War Room.");
      }
    };
    void load();
    return () => { active = false; };
  }, [reloadKey]);

  const audit = useMemo(() => workingTeam && resources
    ? auditTeam(workingTeam.pokemon.filter((set) => set.species.trim()), resources.corpus.teams, resources.snapshot, { teamFormat: workingTeam.format })
    : null, [resources, workingTeam]);

  const matchup = useMemo(() => workingTeam && selectedRival && resources
    ? prepareMatchup(workingTeam.pokemon.filter((set) => set.species.trim()), selectedRival, resources.snapshot, rivalState.teamId === selectedRival.id ? rivalState.sets : null)
    : null, [resources, rivalState.sets, rivalState.teamId, selectedRival, workingTeam]);

  const optimization = useMemo(() => workingTeam && resources
    ? optimizeTeam(
      workingTeam.pokemon,
      optimizationLocks,
      resources.corpus.teams,
      resources.snapshot,
      metaState.teamId === workingTeam.id ? metaState.values : {},
      { excludedMemberSpecies, pasteEvidence: activePasteEvidence, historicalCorpus: resources.corpus.historicalTeams },
    )
    : null, [activePasteEvidence, excludedMemberSpecies, metaState.teamId, metaState.values, optimizationLocks, resources, workingTeam]);

  function changeTeam(value: string) {
    memberApplyRequest.current += 1;
    metaRequest.current += 1;
    pasteEvidenceRequest.current += 1;
    setTeamId(value);
    setOptimizationLocks({});
    setOptimizationPokemon(null);
    setOptimizationHistory({});
    setOptimizationComparison(null);
    setMemberApplyState(EMPTY_MEMBER_APPLY_STATE);
    setMetaState(EMPTY_META_STATE);
    setPasteEvidenceState(EMPTY_PASTE_EVIDENCE_STATE);
  }

  async function selectRival(team: WarRoomCorpusTeam) {
    rivalRequest.current += 1;
    const requestId = rivalRequest.current;
    setRivalId(team.id);
    const hasExactSource = Boolean(team.savedPasteId || team.pokepasteUrl);
    setRivalState({ teamId: team.id, status: hasExactSource ? "loading" : "preview", sets: null, error: "" });
    if (!hasExactSource || !resources) return;
    try {
      const response = team.savedPasteId
        ? await fetch(`/api/scouting-pastes/${encodeURIComponent(team.savedPasteId)}`, { cache: "no-store" })
        : await fetch("/api/pokepaste-import", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url: team.pokepasteUrl }),
        });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(apiError(payload, "No pudimos cargar el PokéPaste rival."));
      const paste = team.savedPasteId && payload && typeof payload === "object" && "item" in payload && payload.item && typeof payload.item === "object" && "paste" in payload.item && typeof payload.item.paste === "string"
        ? payload.item.paste
        : payload && typeof payload === "object" && "paste" in payload && typeof payload.paste === "string"
          ? payload.paste
          : "";
      if (!paste) throw new Error("El PokéPaste rival llegó en un formato inesperado.");
      const sets = parseShowdownPaste(paste).map((set) => hydrateSetFromSnapshot(resources.snapshot, set, "champions"));
      if (requestId === rivalRequest.current) setRivalState({ teamId: team.id, status: "exact", sets, error: "" });
    } catch (caught) {
      if (requestId === rivalRequest.current) setRivalState({ teamId: team.id, status: "preview", sets: null, error: caught instanceof Error ? caught.message : "No pudimos cargar el PokéPaste rival." });
    }
  }

  function updatePokemonLocks(
    id: string,
    update: (locks: WarRoomPokemonLocks, allLocks: WarRoomOptimizationLocks) => WarRoomPokemonLocks,
  ) {
    setOptimizationLocks((current) => {
      const source = current[id] ?? createWarRoomPokemonLocks();
      const next = update({ ...source, moves: [...source.moves] as WarRoomPokemonLocks["moves"] }, current);
      const active = next.identity || next.item || next.ability || next.nature || next.statPoints || next.moves.some(Boolean);
      if (active) return { ...current, [id]: next };
      const nextState = { ...current };
      delete nextState[id];
      return nextState;
    });
  }

  function toggleLock(id: string, field: WarRoomLockField) {
    updatePokemonLocks(id, (locks, allLocks) => {
      if (field === "identity") {
        const identityCount = Object.values(allLocks).filter((entry) => entry.identity).length;
        if (!locks.identity && identityCount >= MAX_WAR_ROOM_LOCKED_IDENTITIES) return locks;
        return { ...locks, identity: !locks.identity };
      }
      if (field === "item") return { ...locks, item: !locks.item };
      if (field === "ability") return { ...locks, ability: !locks.ability };
      if (field === "nature") return { ...locks, nature: !locks.nature };
      if (field === "statPoints") return { ...locks, statPoints: !locks.statPoints };
      const slot = Number(field.slice("move-".length));
      if (!Number.isInteger(slot) || slot < 0 || slot > 3) return locks;
      const moves = [...locks.moves] as WarRoomPokemonLocks["moves"];
      moves[slot] = !moves[slot];
      return { ...locks, moves };
    });
  }

  function toggleWholeSet(id: string, locked: boolean) {
    updatePokemonLocks(id, (current) => ({
      ...current,
      item: locked,
      ability: locked,
      nature: locked,
      statPoints: locked,
      moves: [locked, locked, locked, locked],
    }));
  }

  async function loadMeta() {
    if (!workingTeam || !resources || !optimization) return;
    metaRequest.current += 1;
    pasteEvidenceRequest.current += 1;
    const requestId = metaRequest.current;
    const pasteRequestId = pasteEvidenceRequest.current;
    const teamKey = pasteEvidenceTeamKey(workingTeam);
    setMetaState({ teamId: workingTeam.id, status: "loading", values: {}, loaded: 0, error: "" });
    setPasteEvidenceState({ teamKey, status: "loading", teams: [], loaded: 0, failed: 0, error: "" });

    const lockedSpecies = workingTeam.pokemon
      .filter((set) => optimizationLocks[set.id]?.identity)
      .map((set) => set.species);
    const candidates = selectWarRoomPasteEvidenceCandidates(
      workingTeam.pokemon.map((set) => set.species),
      lockedSpecies,
      optimization.members.map((member) => member.species),
      pasteCandidateCorpus,
      24,
    );
    let evidenceTeams: WarRoomPasteEvidenceTeam[] = [];
    let evidenceError = "";
    let failed = 0;
    if (candidates.length) {
      try {
        const response = await fetch("/api/war-room/paste-evidence", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ candidates }),
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(apiError(payload, "No pudimos cargar los pastes comparables."));
        if (!isWarRoomPasteEvidenceResponse(payload)) throw new Error("La evidencia de pastes llegó en un formato inesperado.");
        evidenceTeams = payload.teams;
        failed = payload.failed;
      } catch (caught) {
        evidenceError = caught instanceof Error ? caught.message : "No pudimos cargar los pastes comparables.";
      }
    } else {
      evidenceError = "No encontramos referencias de paste que compartan Pokémon con este Team.";
    }
    if (requestId !== metaRequest.current || pasteRequestId !== pasteEvidenceRequest.current) return;
    setPasteEvidenceState({
      teamKey,
      status: "ready",
      teams: evidenceTeams,
      loaded: evidenceTeams.length,
      failed,
      error: evidenceError,
    });

    const pasteOnly = optimizeTeam(
      workingTeam.pokemon,
      optimizationLocks,
      resources.corpus.teams,
      resources.snapshot,
      {},
      { excludedMemberSpecies, pasteEvidence: evidenceTeams, historicalCorpus: resources.corpus.historicalTeams },
    );
    const observedSetIds = new Set(pasteOnly.sets
      .filter((suggestion) => suggestion.methodology !== "battle-data-fallback")
      .map((suggestion) => suggestion.setId));
    const fullyLockedIds = new Set(pasteOnly.locks.filter((entry) => entry.fullyLocked).map((entry) => entry.setId));
    const fallbackSpecies = [...new Set(workingTeam.pokemon
      .filter((set) => set.species && !observedSetIds.has(set.id) && !fullyLockedIds.has(set.id))
      .map((set) => set.species))];
    const results = await Promise.all(fallbackSpecies.map(async (species) => {
      try {
        const response = await fetch(`/api/opponent-meta/${encodeURIComponent(species)}`, { cache: "no-store" });
        const payload = await readJson(response);
        if (!response.ok || !isOpponentMetaResponse(payload)) return null;
        return [warRoomMetaKey(species), payload] as const;
      } catch {
        return null;
      }
    }));
    if (requestId !== metaRequest.current || pasteRequestId !== pasteEvidenceRequest.current) return;
    const available = results.filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
    setMetaState({
      teamId: workingTeam.id,
      status: "ready",
      values: Object.fromEntries(available),
      loaded: available.length,
      error: fallbackSpecies.length && !available.length
        ? "Battle Data no respondió para los integrantes sin un set contextual completo; no se inventaron paquetes marginales."
        : "",
    });
  }

  function applySetSuggestion(suggestion: WarRoomSetSuggestion) {
    if (!workingTeam || !resources) return;
    const previousSet = workingTeam.pokemon.find((set) => set.id === suggestion.setId);
    if (!previousSet) return;
    const nextPokemon = applyWarRoomSetSuggestion(workingTeam.pokemon, suggestion, resources.snapshot);
    if (nextPokemon === workingTeam.pokemon) return;
    memberApplyRequest.current += 1;
    metaRequest.current += 1;
    pasteEvidenceRequest.current += 1;
    const previousLocks = optimizationLocks[suggestion.setId];
    setOptimizationHistory((current) => ({
      ...current,
      [suggestion.setId]: [
        ...(current[suggestion.setId] ?? []),
        {
          kind: "set",
          previousSet: { ...previousSet, mechanics: { ...previousSet.mechanics }, moves: previousSet.moves.map((move) => ({ ...move })), performance: { ...previousSet.performance } },
          previousLocks: previousLocks ? { ...previousLocks, moves: [...previousLocks.moves] as WarRoomPokemonLocks["moves"] } : null,
          excludedMemberSpecies: [],
          changedFields: suggestion.changes.map((change) => change.field),
        },
      ],
    }));
    setOptimizationPokemon(nextPokemon);
    setPasteEvidenceState((current) => current.status === "idle" ? current : {
      ...current,
      teamKey: pasteEvidenceTeamKey({ ...workingTeam, pokemon: nextPokemon }),
    });
    setMemberApplyState(EMPTY_MEMBER_APPLY_STATE);
    window.requestAnimationFrame(() => {
      document.getElementById("war-room-optimization-locks")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function openOptimizationComparison() {
    if (!selectedTeam || !workingTeam || !hasOptimizationChanges) return;
    setOptimizationComparison({
      token: Date.now(),
      original: cloneTeamVersion(selectedTeam),
      optimized: cloneTeamVersion(workingTeam),
    });
    window.requestAnimationFrame(() => {
      document.getElementById("war-room-optimization-comparison")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function applyMember(member: WarRoomMemberSuggestion) {
    if (!workingTeam || !resources || !optimization) return;
    const previousSet = workingTeam.pokemon.find((set) => set.id === member.replacesSetId);
    if (!previousSet) return;
    memberApplyRequest.current += 1;
    metaRequest.current += 1;
    pasteEvidenceRequest.current += 1;
    const requestId = memberApplyRequest.current;
    const pasteRequestId = pasteEvidenceRequest.current;
    setMemberApplyState({ species: member.species, setId: member.replacesSetId, status: "loading", message: "" });

    let evidenceTeams = [...activePasteEvidence];
    const targetKey = pasteEvidenceSpeciesKey(member.species);
    const hasCurrentTargetEvidence = () => evidenceTeams.some((team) => !team.historical && team.sets.some((set) => pasteEvidenceSpeciesKey(set.species) === targetKey));
    let evidenceError = "";
    let evidenceFailed = pasteEvidenceState.teamKey === workingTeamKey ? pasteEvidenceState.failed : 0;
    if (!hasCurrentTargetEvidence()) {
      const lockedSpecies = workingTeam.pokemon.filter((set) => optimizationLocks[set.id]?.identity).map((set) => set.species);
      const candidates = selectWarRoomPasteEvidenceCandidates(
        workingTeam.pokemon.map((set) => set.species),
        lockedSpecies,
        [member.species],
        pasteCandidateCorpus,
        18,
      );
      if (candidates.length) {
        try {
          const response = await fetch("/api/war-room/paste-evidence", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ candidates }),
          });
          const payload = await readJson(response);
          if (!response.ok) throw new Error(apiError(payload, "No pudimos cargar los pastes del partner."));
          if (!isWarRoomPasteEvidenceResponse(payload)) throw new Error("La evidencia del partner llegó incompleta.");
          evidenceTeams = [...new Map([...evidenceTeams, ...payload.teams].map((team) => [team.id, team])).values()];
          evidenceFailed = payload.failed;
        } catch (caught) {
          evidenceError = caught instanceof Error ? caught.message : "No pudimos cargar los pastes del partner.";
        }
      }
    }
    if (requestId !== memberApplyRequest.current || pasteRequestId !== pasteEvidenceRequest.current) return;

    let meta: OpponentMetaResponse | null = null;
    const loadMemberMeta = async (): Promise<OpponentMetaResponse | null> => {
      try {
        const response = await fetch(`/api/opponent-meta/${encodeURIComponent(member.species)}`, { cache: "no-store" });
        const payload = await readJson(response);
        return response.ok && isOpponentMetaResponse(payload) ? payload : null;
      } catch {
        // A legal deterministic baseline remains available when Battle Data is down.
        return null;
      }
    };
    let application = buildWarRoomMemberReplacement(
      workingTeam.pokemon,
      member,
      resources.snapshot,
      [],
      evidenceTeams,
    );
    const needsBattleData = application.setSource === "legal-fallback"
      || (application.setSource === "observed-paste-patched" && !application.presetId);
    if (needsBattleData) {
      meta = await loadMemberMeta();
      if (requestId !== memberApplyRequest.current || pasteRequestId !== pasteEvidenceRequest.current) return;
      application = buildWarRoomMemberReplacement(workingTeam.pokemon, member, resources.snapshot, meta?.presets ?? [], evidenceTeams);
    }
    const nextPokemon = application.pokemon;
    if (nextPokemon === workingTeam.pokemon) {
      setMemberApplyState({ species: member.species, setId: member.replacesSetId, status: "error", message: `No encontramos cuatro movimientos legales para ${member.species}; el Team no se modificó.` });
      return;
    }
    const previousLocks = optimizationLocks[member.replacesSetId];
    setOptimizationHistory((current) => ({
      ...current,
      [member.replacesSetId]: [
        ...(current[member.replacesSetId] ?? []),
        {
          kind: "member",
          previousSet: { ...previousSet, mechanics: { ...previousSet.mechanics }, moves: previousSet.moves.map((move) => ({ ...move })), performance: { ...previousSet.performance } },
          previousLocks: previousLocks ? { ...previousLocks, moves: [...previousLocks.moves] as WarRoomPokemonLocks["moves"] } : null,
          excludedMemberSpecies: optimization.members.map((suggestion) => suggestion.species),
          changedFields: [],
        },
      ],
    }));
    setOptimizationPokemon(nextPokemon);
    setPasteEvidenceState({
      teamKey: pasteEvidenceTeamKey({ ...workingTeam, pokemon: nextPokemon }),
      status: "ready",
      teams: evidenceTeams,
      loaded: evidenceTeams.length,
      failed: evidenceFailed,
      error: evidenceError,
    });
    setOptimizationLocks((current) => {
      const next = { ...current };
      delete next[member.replacesSetId];
      return next;
    });
    setMetaState((current) => {
      const values = current.teamId === workingTeam.id ? { ...current.values } : {};
      delete values[warRoomMetaKey(previousSet.species)];
      if (meta) values[warRoomMetaKey(member.species)] = meta;
      const loaded = Object.values(values).filter(Boolean).length;
      return loaded ? { teamId: workingTeam.id, status: "ready", values, loaded, error: "" } : EMPTY_META_STATE;
    });
    const appliedPreset = meta?.presets.find((preset) => preset.id === application.presetId);
    const evidenceTeam = evidenceTeams.find((team) => team.id === application.evidenceTeamId);
    const message = application.setSource === "observed-paste"
      ? `${member.species} entró con un set completo observado en ${evidenceTeam?.sourceLabel ?? "un paste comparable"}. Ya recalculamos alternativas nuevas.`
      : application.setSource === "observed-paste-patched"
        ? `${member.species} entró con un set observado en ${evidenceTeam?.sourceLabel ?? "un paste comparable"}; ${appliedPreset ? "Battle Data completó únicamente sus huecos" : "una base legal completó los campos ausentes"}. Ya recalculamos alternativas nuevas.`
        : application.setSource === "battle-data-fallback"
          ? `${member.species} no tuvo un paste contextual viable; usamos ${appliedPreset?.label ?? "Battle Data"} como fallback y recalculamos alternativas.`
          : `No hubo un paste ni paquete marginal viable para ${member.species}; aplicamos un set legal base y recalculamos alternativas.`;
    setMemberApplyState({
      species: member.species,
      setId: member.replacesSetId,
      status: application.setSource === "observed-paste" || application.setSource === "observed-paste-patched" ? "ready" : "fallback",
      message,
    });
  }

  function undoOptimizationChange(setId: string) {
    const steps = optimizationHistory[setId] ?? [];
    const previous = steps.at(-1);
    if (!previous || !workingTeam) return;
    memberApplyRequest.current += 1;
    metaRequest.current += 1;
    pasteEvidenceRequest.current += 1;
    setOptimizationPokemon(workingTeam.pokemon.map((set) => set.id === setId ? previous.previousSet : set));
    if (previous.kind === "member") {
      setOptimizationLocks((current) => {
        const next = { ...current };
        if (previous.previousLocks) next[setId] = previous.previousLocks;
        else delete next[setId];
        return next;
      });
    }
    setOptimizationHistory((current) => {
      const next = { ...current };
      const remaining = (next[setId] ?? []).slice(0, -1);
      if (remaining.length) next[setId] = remaining;
      else delete next[setId];
      return next;
    });
    setMetaState(EMPTY_META_STATE);
    setPasteEvidenceState(EMPTY_PASTE_EVIDENCE_STATE);
    setMemberApplyState(EMPTY_MEMBER_APPLY_STATE);
  }

  if (!versions.length) {
    return <section className="rounded-[28px] border border-dashed border-white/10 bg-slate-900/35 px-6 py-24 text-center"><Swords className="mx-auto size-10 text-slate-700" /><h1 className="mt-4 text-xl font-black text-white">War Room necesita un Team</h1><p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-600">Guarda una versión en Teams o envía el borrador actual desde Team Builder para auditarlo, preparar matchups y bloquear un core.</p></section>;
  }

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-rose-400 via-amber-300 to-cyan-300" />
        <div className="p-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
            <div className="max-w-3xl"><div className="flex flex-wrap items-center gap-2"><Swords className="size-5 text-rose-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-rose-200">War Room</p><Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Champions M-C</Badge></div><h1 className="mt-2 text-2xl font-black tracking-tight text-white">Del dato a una decisión de torneo</h1><p className="mt-1 text-xs leading-5 text-slate-500">Audita, prepara, optimiza y prueba el Team en combate. Cada conclusión declara si proviene del set exacto, del team preview o del corpus.</p></div>
            <div className="min-w-0 xl:w-[420px]"><label className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Team y versión de trabajo</label><Select value={selectedTeam?.id ?? ""} onValueChange={changeTeam}><SelectTrigger className="mt-2 w-full border-white/10 bg-slate-950/70"><SelectValue /></SelectTrigger><SelectContent className="border-white/10 bg-slate-950 text-slate-200">{versions.map((version) => { const storedGroup = groups.find((group) => group.versions.some((entry) => entry.id === version.id)); return <SelectItem key={version.id} value={version.id}>{storedGroup ? `${storedGroup.name} · v${formatVersion(version)}` : `${version.name} · borrador`}</SelectItem>; })}</SelectContent></Select></div>
          </div>
          <div className="mt-5 grid gap-2 border-t border-white/7 pt-5 md:grid-cols-2 xl:grid-cols-4">
            {MODES.map((entry) => {
              const Icon = entry.icon;
              const active = mode === entry.id;
              return <button key={entry.id} type="button" onClick={() => setMode(entry.id)} className={cn("rounded-2xl border p-4 text-left transition", active ? "border-rose-300/25 bg-rose-300/[0.07] shadow-[0_0_28px_rgba(251,113,133,0.05)]" : "border-white/7 bg-slate-950/45 hover:border-white/14 hover:bg-white/[0.035]")}><div className="flex items-center gap-2"><Icon className={cn("size-4", active ? "text-rose-300" : "text-slate-600")} /><strong className={cn("text-xs", active ? "text-white" : "text-slate-400")}>{entry.label}</strong></div><p className="mt-2 text-[10px] leading-4 text-slate-600">{entry.description}</p></button>;
            })}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[9px] text-slate-600">
            {(["exact-set", "team-preview", "corpus"] as const).map((scope) => <Badge key={scope} variant="outline" className="border-white/8 bg-white/[0.025] text-[8px] text-slate-500">{sourceLabel(scope)}</Badge>)}
            {resources ? <><span className="inline-flex items-center gap-1.5"><Database className="size-3 text-cyan-300" />Showdown {resources.snapshot.metadata.captured}</span><span className="inline-flex items-center gap-1.5"><Users className="size-3 text-violet-300" />{resources.corpus.tournamentTeamCount ? `${resources.corpus.tournamentTeamCount.toLocaleString("es-MX")} torneo · ` : ""}{resources.corpus.vgcPastesTeamCount.toLocaleString("es-MX")} VGCPastes · {resources.corpus.savedTeamCount.toLocaleString("es-MX")} en Mis pastes</span>{resources.corpus.historicalTeamCount ? <span className="inline-flex items-center gap-1.5 text-cyan-300">{resources.corpus.historicalTeamCount.toLocaleString("es-MX")} históricos ponderados · solo Optimizar</span> : null}<a href={resources.corpus.source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 hover:text-cyan-200">Abrir fuente VGCPastes <ExternalLink className="size-3" /></a></> : null}
          </div>
        </div>
      </section>

      {!resources ? (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/40 px-6 py-20 text-center">
          {resourceError ? <><AlertTriangle className="mx-auto size-8 text-rose-300" /><h2 className="mt-3 text-lg font-black text-white">War Room no pudo iniciar</h2><p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">{resourceError}</p><Button type="button" variant="outline" onClick={() => setReloadKey((value) => value + 1)} className="mt-5 gap-2 border-cyan-300/18 bg-cyan-300/7 text-cyan-100"><RefreshCw className="size-4" />Reintentar</Button></> : <><Loader2 className="mx-auto size-8 animate-spin text-cyan-300" /><h2 className="mt-3 text-sm font-black text-white">Cruzando regulación, Pokédex y corpus…</h2><p className="mt-1 text-xs text-slate-600">La primera carga puede tardar unos segundos.</p></>}
        </section>
      ) : null}

      {resources && workingTeam && mode === "audit" && audit ? (
        <div className="space-y-4">
          <AuditView result={audit} />
          <WarRoomAutoLab team={workingTeam} corpusTeams={resources.corpus.teams} />
        </div>
      ) : null}

      {resources && workingTeam && mode === "matchup" ? <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]"><RivalPicker teams={resources.corpus.teams} selected={selectedRival} query={rivalQuery} onQueryChange={setRivalQuery} onSelect={(team) => void selectRival(team)} /><MatchupView result={matchup} rivalState={rivalState} /></div> : null}

      {resources && workingTeam && mode === "optimize" && optimization ? (
        <div className="space-y-4">
          <OptimizationView
            team={workingTeam}
            result={optimization}
            optimizationLocks={optimizationLocks}
            metaState={metaState.teamId === workingTeam.id ? metaState : EMPTY_META_STATE}
            pasteEvidenceState={pasteEvidenceState.teamKey === workingTeamKey ? pasteEvidenceState : EMPTY_PASTE_EVIDENCE_STATE}
            memberApplyState={memberApplyState}
            optimizationHistory={optimizationHistory}
            onToggleLock={toggleLock}
            onToggleWholeSet={toggleWholeSet}
            onLoadMeta={() => void loadMeta()}
            onOpenBuilder={() => onOpenBuilder(workingTeam)}
            onCompare={openOptimizationComparison}
            canCompare={hasOptimizationChanges}
            comparisonOpen={Boolean(optimizationComparison)}
            onApplySet={applySetSuggestion}
            onApplyMember={(member) => void applyMember(member)}
            onUndoChange={undoOptimizationChange}
          />
          {optimizationComparison ? (
            <div id="war-room-optimization-comparison" className="scroll-mt-4">
              <WarRoomAutoLab
                key={optimizationComparison.token}
                team={optimizationComparison.original}
                comparisonTeam={optimizationComparison.optimized}
                corpusTeams={resources.corpus.teams}
                onClose={() => setOptimizationComparison(null)}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {resources && workingTeam && mode === "sparring" ? <WarRoomSparring team={workingTeam} corpusTeams={resources.corpus.teams} /> : null}
    </div>
  );
}
