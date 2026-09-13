"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Crosshair,
  Database,
  ExternalLink,
  Hammer,
  Info,
  Loader2,
  Lock,
  RefreshCw,
  Search,
  Settings2,
  Shield,
  Sparkles,
  Swords,
  Unlock,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { OpponentMetaResponse } from "@/lib/opponent-meta-presets";
import { parseShowdownPaste } from "@/lib/paste";
import { getSpriteUrl, toId } from "@/lib/pokemon-data";
import { formatVersion, serializeShowdownPaste } from "@/lib/team-builder";
import { hydrateSetFromSnapshot, loadShowdownSnapshot, type ShowdownSnapshot } from "@/lib/showdown-data";
import type { TournamentTeamBuilderImport } from "@/lib/tournament-scouting";
import type { PokemonSet, TeamGroup, TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  WAR_ROOM_FORMAT_ID,
  auditTeam,
  isWarRoomCorpusResponse,
  optimizeTeam,
  prepareMatchup,
  warRoomMetaKey,
  type WarRoomAuditResult,
  type WarRoomCorpusResponse,
  type WarRoomCorpusTeam,
  type WarRoomMatchupResult,
  type WarRoomOptimizationResult,
  type WarRoomSetSuggestion,
} from "@/lib/war-room";

type WarRoomMode = "audit" | "matchup" | "optimize";

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
];

const EMPTY_RIVAL_STATE: RivalSetState = {
  teamId: "",
  status: "idle",
  sets: null,
  error: "",
};

const EMPTY_META_STATE: MetaState = {
  teamId: "",
  status: "idle",
  values: {},
  loaded: 0,
  error: "",
};

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

function OptimizationView({
  team,
  result,
  lockedIds,
  metaState,
  onToggleLock,
  onLoadMeta,
  onOpenBuilder,
  onBuildSuggestion,
}: {
  team: TeamVersion;
  result: WarRoomOptimizationResult;
  lockedIds: Set<string>;
  metaState: MetaState;
  onToggleLock: (id: string) => void;
  onLoadMeta: () => void;
  onOpenBuilder: () => void;
  onBuildSuggestion: (suggestion: WarRoomSetSuggestion) => void;
}) {
  return (
    <div className="space-y-4">
      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Core lock</p><h2 className="mt-1 text-lg font-black text-white">Bloquea de 1 a 4 identidades</h2><p className="mt-1 text-[10px] leading-4 text-slate-500">War Room nunca propondrá reemplazar las identidades bloqueadas. Sus sets sí pueden entrar en revisión.</p></div><Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">{lockedIds.size}/4 bloqueados</Badge></div>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {team.pokemon.map((set) => {
            const locked = lockedIds.has(set.id);
            const disabled = !locked && lockedIds.size >= 4;
            return (
              <label key={set.id} className={cn("relative cursor-pointer rounded-2xl border p-3 text-center transition", locked ? "border-cyan-300/35 bg-cyan-300/8" : "border-white/7 bg-slate-950/50 hover:border-white/15", disabled && "cursor-not-allowed opacity-45")}>
                <Checkbox checked={locked} disabled={disabled} onCheckedChange={() => onToggleLock(set.id)} className="absolute top-2 left-2" />
                {locked ? <Lock className="absolute top-2 right-2 size-3 text-cyan-300" /> : <Unlock className="absolute top-2 right-2 size-3 text-slate-700" />}
                <Image src={getSpriteUrl(set.species)} alt={set.species} width={58} height={58} unoptimized className="mx-auto size-14 object-contain" /><span className="mt-1 block truncate text-[9px] font-black text-slate-300">{set.species}</span>
              </label>
            );
          })}
        </div>
      </section>

      {!lockedIds.size ? <section className="rounded-[24px] border border-dashed border-cyan-300/15 bg-cyan-300/[0.025] px-6 py-14 text-center"><Lock className="mx-auto size-8 text-cyan-300/50" /><h3 className="mt-3 text-sm font-black text-white">Elige primero el core que no quieres tocar</h3><p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-slate-600">Al bloquearlo, el motor buscará compañeros observados con ese núcleo y probará qué slot libre corrige mejor el balance defensivo.</p></section> : (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-violet-300">Partner search</p><h2 className="mt-1 text-lg font-black text-white">Integrantes que encajan con el core</h2></div><Badge variant="outline" className={cn("text-[9px]", result.coreSample.mode === "exact" ? "border-emerald-300/15 text-emerald-200" : "border-amber-300/15 text-amber-200")}>{result.coreSample.size} teams · {result.coreSample.mode === "exact" ? "core exacto" : "coincidencia parcial"}</Badge></div>
          {result.members.length ? <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">{result.members.map((member) => <article key={member.species} className="rounded-2xl border border-white/7 bg-slate-950/55 p-4"><div className="flex items-center gap-3"><Image src={getSpriteUrl(member.observedAs)} alt={member.species} width={54} height={54} unoptimized className="size-13 object-contain" /><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-black text-white">{member.species}</h3><p className="mt-0.5 text-[9px] text-violet-200">por {member.replaces}</p><div className="mt-2 flex items-center gap-2"><Progress value={member.score} className="h-1.5 bg-white/7 [&_[data-slot=progress-indicator]]:bg-violet-300" /><span className="font-mono text-[9px] text-violet-200">{member.score}</span></div></div></div><p className="mt-3 text-[10px] leading-4 text-slate-500">{member.reasons.join(" ")}</p>{member.patchedTypes.length ? <div className="mt-3 flex flex-wrap gap-1">{member.patchedTypes.map((type) => <Badge key={type} variant="outline" className="border-emerald-300/12 bg-emerald-300/5 text-[8px] text-emerald-200">+ {type}</Badge>)}</div> : null}</article>)}</div> : <p className="mt-5 rounded-xl border border-white/7 bg-slate-950/45 px-4 py-8 text-center text-xs text-slate-600">No hay una muestra suficiente para proponer integrantes con ese core.</p>}
        </section>
      )}

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div><p className="text-[9px] font-black uppercase tracking-[0.16em] text-amber-300">Set search</p><h2 className="mt-1 text-lg font-black text-white">Objeto, moves, naturaleza y Stat Points</h2><p className="mt-1 max-w-2xl text-[10px] leading-4 text-slate-500">Contrasta cada set con Battle Data y vuelve a puntuar su cobertura sobre las amenazas frecuentes. Los porcentajes son marginales, no un set observado.</p></div><Button type="button" variant="outline" onClick={onLoadMeta} disabled={metaState.status === "loading"} className="gap-2 border-amber-300/18 bg-amber-300/7 text-xs font-black text-amber-100 hover:bg-amber-300/12">{metaState.status === "loading" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}{metaState.status === "loading" ? "Buscando…" : metaState.status === "ready" ? "Recalcular sets" : "Buscar cambios de set"}</Button></div>
        {metaState.error ? <p className="mt-3 rounded-xl border border-amber-300/15 bg-amber-300/7 px-3 py-2 text-[10px] text-amber-100">{metaState.error}</p> : null}
        {metaState.status === "ready" ? <p className="mt-3 text-[9px] text-slate-600">Battle Data disponible para {metaState.loaded}/{team.pokemon.length} integrantes.</p> : null}
        {result.sets.length ? <div className="mt-4 grid gap-3 lg:grid-cols-2">{result.sets.map((suggestion) => <article key={`${suggestion.species}-${suggestion.presetId}`} className="rounded-2xl border border-white/7 bg-slate-950/55 p-4"><div className="flex items-center gap-3"><Image src={getSpriteUrl(suggestion.species)} alt={suggestion.species} width={50} height={50} unoptimized className="size-12 object-contain" /><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-black text-white">{suggestion.species}</h3><p className={cn("mt-0.5 text-[9px] font-bold", suggestion.structuralDelta > 0 ? "text-emerald-300" : suggestion.structuralDelta < 0 ? "text-amber-300" : "text-slate-500")}>{suggestion.structuralDelta > 0 ? "+" : ""}{suggestion.structuralDelta} encaje estructural</p></div><Badge variant="outline" className="border-amber-300/15 bg-amber-300/7 text-[8px] text-amber-200">Hipótesis</Badge></div><div className="mt-3 space-y-2">{suggestion.changes.map((change) => <div key={change.field} className="rounded-xl border border-white/6 bg-white/[0.02] px-3 py-2"><div className="flex items-center justify-between gap-2"><span className="text-[8px] font-black uppercase tracking-wide text-slate-600">{change.field}</span>{change.evidence !== null ? <span className="font-mono text-[8px] text-amber-200">{change.evidence}% marginal</span> : null}</div><p className="mt-1 line-clamp-2 text-[9px] leading-4 text-slate-500"><span className="text-slate-700">Actual:</span> {change.current}</p><p className="line-clamp-2 text-[9px] leading-4 text-slate-300"><span className="text-amber-300">Probar:</span> {change.suggested}</p></div>)}</div><p className="mt-3 text-[9px] leading-4 text-slate-600">{suggestion.reasons.join(" ")}</p><Button type="button" variant="outline" size="sm" onClick={() => onBuildSuggestion(suggestion)} className="mt-3 w-full gap-2 border-amber-300/15 bg-amber-300/5 text-[9px] font-black text-amber-100 hover:bg-amber-300/10"><Hammer className="size-3.5" />Probar este set en Builder</Button></article>)}</div> : metaState.status === "ready" ? <p className="mt-5 rounded-xl border border-white/7 bg-slate-950/45 px-4 py-8 text-center text-xs text-slate-600">No encontramos un paquete legal distinto con evidencia suficiente.</p> : <div className="mt-4 rounded-xl border border-dashed border-white/8 px-4 py-10 text-center text-xs text-slate-600">Ejecuta la búsqueda para comparar los seis sets.</div>}
      </section>

      <section className="flex flex-col gap-3 rounded-[24px] border border-cyan-300/12 bg-cyan-300/[0.035] p-5 sm:flex-row sm:items-center sm:justify-between"><div><h3 className="text-sm font-black text-white">¿Quieres convertir una hipótesis en versión?</h3><p className="mt-1 text-[10px] text-slate-500">Abre una copia editable del Team; el original permanece inmutable.</p></div><Button type="button" onClick={onOpenBuilder} className="gap-2 bg-cyan-300 font-black text-slate-950 hover:bg-cyan-200"><Hammer className="size-4" />Abrir en Team Builder</Button></section>

      <div className="grid gap-2 lg:grid-cols-2">{result.notes.map((note) => <EvidenceNote key={note}>{note}</EvidenceNote>)}</div>
    </div>
  );
}

export function WarRoom({ groups, onOpenBuilder, onBuildDraft }: { groups: TeamGroup[]; onOpenBuilder: (version: TeamVersion) => void; onBuildDraft: (request: TournamentTeamBuilderImport) => void }) {
  const versions = useMemo(() => groups.flatMap((group) => group.versions), [groups]);
  const [mode, setMode] = useState<WarRoomMode>("audit");
  const [teamId, setTeamId] = useState("");
  const [resources, setResources] = useState<{ corpus: WarRoomCorpusResponse; snapshot: ShowdownSnapshot } | null>(null);
  const [resourceError, setResourceError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [rivalQuery, setRivalQuery] = useState("");
  const [rivalId, setRivalId] = useState("");
  const [rivalState, setRivalState] = useState<RivalSetState>(EMPTY_RIVAL_STATE);
  const rivalRequest = useRef(0);
  const [lockedIds, setLockedIds] = useState<Set<string>>(() => new Set());
  const [metaState, setMetaState] = useState<MetaState>(EMPTY_META_STATE);
  const metaRequest = useRef(0);

  const selectedTeam = versions.find((version) => version.id === teamId) ?? versions[0] ?? null;
  const selectedRival = resources?.corpus.teams.find((team) => team.id === rivalId) ?? null;

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

  const audit = useMemo(() => selectedTeam && resources
    ? auditTeam(selectedTeam.pokemon, resources.corpus.teams, resources.snapshot, { teamFormat: selectedTeam.format })
    : null, [resources, selectedTeam]);

  const matchup = useMemo(() => selectedTeam && selectedRival && resources
    ? prepareMatchup(selectedTeam.pokemon, selectedRival, resources.snapshot, rivalState.teamId === selectedRival.id ? rivalState.sets : null)
    : null, [resources, rivalState.sets, rivalState.teamId, selectedRival, selectedTeam]);

  const optimization = useMemo(() => selectedTeam && resources
    ? optimizeTeam(
      selectedTeam.pokemon,
      lockedIds,
      resources.corpus.teams,
      resources.snapshot,
      metaState.teamId === selectedTeam.id ? metaState.values : {},
    )
    : null, [lockedIds, metaState.teamId, metaState.values, resources, selectedTeam]);

  function changeTeam(value: string) {
    setTeamId(value);
    setLockedIds(new Set());
    setMetaState(EMPTY_META_STATE);
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

  function toggleLock(id: string) {
    setLockedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < 4) next.add(id);
      return next;
    });
  }

  async function loadMeta() {
    if (!selectedTeam) return;
    metaRequest.current += 1;
    const requestId = metaRequest.current;
    setMetaState({ teamId: selectedTeam.id, status: "loading", values: {}, loaded: 0, error: "" });
    const results = await Promise.all(selectedTeam.pokemon.map(async (set) => {
      try {
        const response = await fetch(`/api/opponent-meta/${encodeURIComponent(set.species)}`, { cache: "no-store" });
        const payload = await readJson(response);
        if (!response.ok || !isOpponentMetaResponse(payload)) return null;
        return [warRoomMetaKey(set.species), payload] as const;
      } catch {
        return null;
      }
    }));
    if (requestId !== metaRequest.current) return;
    const available = results.filter((entry): entry is NonNullable<typeof entry> => Boolean(entry));
    setMetaState({
      teamId: selectedTeam.id,
      status: "ready",
      values: Object.fromEntries(available),
      loaded: available.length,
      error: available.length ? "" : "Battle Data no respondió para este equipo; las propuestas de integrantes siguen disponibles.",
    });
  }

  function buildSuggestion(suggestion: WarRoomSetSuggestion) {
    if (!selectedTeam) return;
    const pokemon = selectedTeam.pokemon.map((set) => set.id === suggestion.setId ? {
      ...set,
      item: suggestion.proposal.item,
      ability: suggestion.proposal.ability,
      nature: suggestion.proposal.nature,
      evs: suggestion.proposal.evs,
      moves: suggestion.proposal.moves.map((name) => ({ name, type: null, damaging: false, usage: 0 })),
    } : set);
    onBuildDraft({
      paste: serializeShowdownPaste(pokemon, selectedTeam.mechanics ?? ["mega"]),
      suggestedName: `${selectedTeam.name} · War Room ${suggestion.species}`.slice(0, 80),
      sourceLabel: `War Room ${suggestion.methodology} · ${suggestion.presetId}`,
    });
  }

  if (!versions.length) {
    return <section className="rounded-[28px] border border-dashed border-white/10 bg-slate-900/35 px-6 py-24 text-center"><Swords className="mx-auto size-10 text-slate-700" /><h1 className="mt-4 text-xl font-black text-white">War Room necesita un Team</h1><p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-600">Guarda al menos una versión completa en Teams o Team Builder para poder auditarla, preparar matchups y bloquear un core.</p></section>;
  }

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-rose-400 via-amber-300 to-cyan-300" />
        <div className="p-5">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
            <div className="max-w-3xl"><div className="flex flex-wrap items-center gap-2"><Swords className="size-5 text-rose-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-rose-200">War Room</p><Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Champions M-C</Badge></div><h1 className="mt-2 text-2xl font-black tracking-tight text-white">Del dato a una decisión de torneo</h1><p className="mt-1 text-xs leading-5 text-slate-500">Audita, prepara y optimiza con un motor determinista. Cada conclusión declara si proviene del set exacto, del team preview o del corpus.</p></div>
            <div className="min-w-0 xl:w-[420px]"><label className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Team y versión de trabajo</label><Select value={selectedTeam?.id ?? ""} onValueChange={changeTeam}><SelectTrigger className="mt-2 w-full border-white/10 bg-slate-950/70"><SelectValue /></SelectTrigger><SelectContent className="border-white/10 bg-slate-950 text-slate-200">{groups.flatMap((group) => group.versions.map((version) => <SelectItem key={version.id} value={version.id}>{group.name} · v{formatVersion(version)}</SelectItem>))}</SelectContent></Select></div>
          </div>
          <div className="mt-5 grid gap-2 border-t border-white/7 pt-5 lg:grid-cols-3">
            {MODES.map((entry) => {
              const Icon = entry.icon;
              const active = mode === entry.id;
              return <button key={entry.id} type="button" onClick={() => setMode(entry.id)} className={cn("rounded-2xl border p-4 text-left transition", active ? "border-rose-300/25 bg-rose-300/[0.07] shadow-[0_0_28px_rgba(251,113,133,0.05)]" : "border-white/7 bg-slate-950/45 hover:border-white/14 hover:bg-white/[0.035]")}><div className="flex items-center gap-2"><Icon className={cn("size-4", active ? "text-rose-300" : "text-slate-600")} /><strong className={cn("text-xs", active ? "text-white" : "text-slate-400")}>{entry.label}</strong></div><p className="mt-2 text-[10px] leading-4 text-slate-600">{entry.description}</p></button>;
            })}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[9px] text-slate-600">
            {(["exact-set", "team-preview", "corpus"] as const).map((scope) => <Badge key={scope} variant="outline" className="border-white/8 bg-white/[0.025] text-[8px] text-slate-500">{sourceLabel(scope)}</Badge>)}
            {resources ? <><span className="inline-flex items-center gap-1.5"><Database className="size-3 text-cyan-300" />Showdown {resources.snapshot.metadata.captured}</span><span className="inline-flex items-center gap-1.5"><Users className="size-3 text-violet-300" />{resources.corpus.publicTeamCount.toLocaleString("es-MX")} públicos · {resources.corpus.savedTeamCount.toLocaleString("es-MX")} en Mis pastes</span><a href={resources.corpus.source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 hover:text-cyan-200">Abrir fuente VGCPastes <ExternalLink className="size-3" /></a></> : null}
          </div>
        </div>
      </section>

      {!resources ? (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/40 px-6 py-20 text-center">
          {resourceError ? <><AlertTriangle className="mx-auto size-8 text-rose-300" /><h2 className="mt-3 text-lg font-black text-white">War Room no pudo iniciar</h2><p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">{resourceError}</p><Button type="button" variant="outline" onClick={() => setReloadKey((value) => value + 1)} className="mt-5 gap-2 border-cyan-300/18 bg-cyan-300/7 text-cyan-100"><RefreshCw className="size-4" />Reintentar</Button></> : <><Loader2 className="mx-auto size-8 animate-spin text-cyan-300" /><h2 className="mt-3 text-sm font-black text-white">Cruzando regulación, Pokédex y corpus…</h2><p className="mt-1 text-xs text-slate-600">La primera carga puede tardar unos segundos.</p></>}
        </section>
      ) : null}

      {resources && selectedTeam && mode === "audit" && audit ? <AuditView result={audit} /> : null}

      {resources && selectedTeam && mode === "matchup" ? <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]"><RivalPicker teams={resources.corpus.teams} selected={selectedRival} query={rivalQuery} onQueryChange={setRivalQuery} onSelect={(team) => void selectRival(team)} /><MatchupView result={matchup} rivalState={rivalState} /></div> : null}

      {resources && selectedTeam && mode === "optimize" && optimization ? <OptimizationView team={selectedTeam} result={optimization} lockedIds={lockedIds} metaState={metaState.teamId === selectedTeam.id ? metaState : EMPTY_META_STATE} onToggleLock={toggleLock} onLoadMeta={() => void loadMeta()} onOpenBuilder={() => onOpenBuilder(selectedTeam)} onBuildSuggestion={buildSuggestion} /> : null}
    </div>
  );
}
