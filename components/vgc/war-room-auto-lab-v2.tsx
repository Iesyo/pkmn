"use client";

import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Minus,
  Play,
  RefreshCw,
  Scale,
  ShieldCheck,
  Swords,
  TrendingDown,
  TrendingUp,
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

const PRESETS: Record<
  RunPreset,
  {
    label: string;
    opponents: number;
    initialBattlesPerOpponent: number;
    deepDiveOpponents: number;
    additionalBattlesPerDeepDive: number;
    description: string;
  }
> = {
  quick: {
    label: "Rápido",
    opponents: 18,
    initialBattlesPerOpponent: 8,
    deepDiveOpponents: 6,
    additionalBattlesPerDeepDive: 12,
    description: "18 × 8 para mapear el meta + 6 × 12 para confirmar señales. 216 batallas.",
  },
  standard: {
    label: "Normal",
    opponents: 40,
    initialBattlesPerOpponent: 8,
    deepDiveOpponents: 10,
    additionalBattlesPerDeepDive: 16,
    description: "40 × 8 para cobertura + 10 × 16 para confirmación. 480 batallas.",
  },
  deep: {
    label: "Profundo · meta actual",
    opponents: 100,
    initialBattlesPerOpponent: 6,
    deepDiveOpponents: 20,
    additionalBattlesPerDeepDive: 20,
    description: "100 × 6 del meta reciente + 20 × 20 de confirmación. 1,000 batallas.",
  },
};

type ConfidenceInterval = {
  low: number;
  high: number;
  width: number;
};

type Evidence = {
  level: "robust" | "directional" | "side-sensitive" | "insufficient";
  label: string;
  note: string;
};

type RecordRow = {
  games: number;
  wins: number;
  losses: number;
  ties: number;
  scorePercent: number;
  confidence95?: ConfidenceInterval;
};

type MatchupRow = RecordRow & {
  id: string;
  label: string;
  roster: string[];
  archetypes: string[];
  confidence95: ConfidenceInterval;
  deepDive: boolean;
  evidenceLevel: "screening" | "confirmed";
};

type AutoLabAudit = {
  signal: {
    games: number;
    uniqueOpponents: number;
    level: "exploratory" | "directional" | "stronger";
    note: string;
  };
  dataQuality: {
    games: number;
    screeningGames: number;
    deepeningGames: number;
    uniqueOpponents: number;
    uniqueRosters: number;
    deepDiveOpponents: number;
    parsedReplays: number;
    replayCoveragePercent: number;
    uniquePreviewCombinations: number;
    candidateAlphaGames: number;
    candidateBetaGames: number;
    sideImbalanceGames: number;
  };
  policySensitivity: {
    status: "insufficient" | "review" | "stable";
    alphaScorePercent: number;
    betaScorePercent: number;
    sideGapPercentagePoints: number;
    note: string;
    limitation: string;
  };
  evidenceSummary?: {
    robust: number;
    directional: number;
    sideSensitive: number;
    insufficient: number;
    note: string;
  };
  goodMatchups: MatchupRow[];
  badMatchups: MatchupRow[];
  problematicOpponents: MatchupRow[];
  leadPerformance: Array<RecordRow & { lead: string }>;
  selectionUsage: Array<{
    pokemon: string;
    selectedGames: number;
    selectedRate: number;
    leadGames: number;
    scoreWhenSelected: number;
    smoothedScoreWhenSelected?: number;
    scoreWhenNotSelected?: number;
    smoothedScoreWhenNotSelected?: number;
    matchedDeltaPercentagePoints?: number;
    matchedStrata?: number;
    delta95?: ConfidenceInterval;
    sideDeltas?: Record<string, number>;
    confidence95: ConfidenceInterval;
    evidence?: Evidence;
    signal: "rarely-selected" | "review" | "ok";
  }>;
  moveSignals: Array<{
    pokemon: string;
    move: string;
    declared?: boolean;
    gamesUsed: number;
    appearanceGames?: number;
    availableButUnusedGames?: number;
    opportunityRate?: number;
    totalUses: number;
    wins: number;
    losses: number;
    ties: number;
    scoreWhenUsed: number;
    smoothedScoreWhenUsed?: number;
    scoreWhenAvailableButUnused?: number;
    matchedDeltaPercentagePoints?: number;
    matchedStrata?: number;
    delta95?: ConfidenceInterval;
    confidence95: ConfidenceInterval;
    evidence?: Evidence;
    signal: "review" | "low-usage" | "observed";
  }>;
  opponentPokemonPressure: Array<{
    pokemon: string;
    observedGames: number;
    lossGames: number;
    lossShare: number;
    lossRate: number;
    smoothedLossRate?: number;
    lossRateWithout?: number;
    lossRateLift: number;
    exposureRate: number;
    uniqueOpponents: number;
    confidence95: ConfidenceInterval;
    matchedStrata?: number;
    delta95?: ConfidenceInterval;
    sideDeltas?: Record<string, number>;
    evidence?: Evidence;
    comparisonBasis?: "archetype-and-side" | "broad-pool-prior";
    priorityScore: number;
  }>;
  opponentCorePressure: Array<{
    core: string;
    observedGames: number;
    lossGames: number;
    lossShare: number;
    lossRate: number;
    smoothedLossRate?: number;
    lossRateWithout?: number;
    lossRateLift: number;
    exposureRate: number;
    uniqueOpponents: number;
    confidence95: ConfidenceInterval;
    matchedStrata?: number;
    delta95?: ConfidenceInterval;
    sideDeltas?: Record<string, number>;
    evidence?: Evidence;
    comparisonBasis?: "archetype-and-side" | "broad-pool-prior";
    priorityScore: number;
  }>;
  archetypePerformance: Array<
    RecordRow & {
      archetype: string;
      smoothedScorePercent?: number;
      uniqueOpponents: number;
      confidence95: ConfidenceInterval;
      evidence?: Evidence;
    }
  >;
  recurringLossPatterns: Array<{
    pattern: string;
    count: number;
    lossShare: number;
  }>;
  replayCoverage: {
    expected: number;
    parsed: number;
    errors: Array<{ battleTag: string; error: string }>;
  };
  limitations: string[];
};

type AdaptiveSampling = {
  strategy: "adaptive-two-stage";
  heuristic: string;
  screening: {
    opponents: number;
    battlesPerOpponent: number;
    battlesPerCandidate: number;
  };
  deepDive: {
    opponents: number;
    additionalBattlesPerOpponent: number;
    battlesPerCandidate: number;
    selected: Array<{
      id: string;
      label: string;
      archetypes: string[];
      priorityScore: number;
      recurrentRiskScore: number;
      selectionRank: number;
      reasons: string[];
    }>;
  };
  battlesPerCandidate: number;
};

type AutoLabComparisonEvidence =
  | "confirmed-improvement"
  | "directional-improvement"
  | "mixed"
  | "inconclusive"
  | "directional-regression"
  | "confirmed-regression";

type AutoLabComparison = {
  deltaPercentagePoints: number;
  delta95?: ConfidenceInterval;
  opponentsImproved: number;
  opponentsRegressed: number;
  opponentsTied: number;
  criticalOpponents: number;
  criticalOpponentsImproved: number;
  criticalOpponentsRegressed: number;
  criticalOpponentsTied: number;
  evidence?: AutoLabComparisonEvidence;
  verdict: "improved" | "regressed" | "mixed";
  promotion: "candidate" | "hold";
  caveat: string;
};

type AutoLabCandidateResult = RecordRow & {
  id: string;
  label: string;
  poolEstimate: RecordRow;
  adaptiveCombined: RecordRow;
  screeningByOpponent: Record<string, RecordRow>;
  comparison?: AutoLabComparison;
};

type AutoLabResult = {
  schemaVersion: 3;
  benchmark: "light-mc-team-gauntlet";
  policy: string;
  totalBattles: number;
  sampling: AdaptiveSampling;
  opponents: Array<{
    id: string;
    label: string;
    roster: string[];
    archetypes: string[];
  }>;
  baseline: RecordRow & {
    id: string;
    label: string;
    poolEstimate: RecordRow;
    adaptiveCombined: RecordRow;
    screeningByOpponent: Record<string, RecordRow>;
  };
  variants: AutoLabCandidateResult[];
  bestVariantId: string | null;
  audit: AutoLabAudit;
  caveat: string;
};

type AutoLabJob = {
  id: string;
  phase:
    | "queued"
    | "preparing"
    | "running"
    | "finalizing"
    | "completed"
    | "error"
    | "cancelled";
  error: string;
  completedBattles: number;
  totalBattles: number;
  progress: number;
  elapsedSeconds: number;
  etaSeconds: number | null;
  currentCandidateId: string | null;
  currentCandidateLabel: string | null;
  currentOpponentId: string | null;
  samplingStage: "screening" | "deepening" | null;
  events: string[];
  result: AutoLabResult | null;
};

type AutoLabPreflightVerdict = {
  id: string;
  label: string;
  valid: boolean;
  error: string;
};

type AutoLabPreflightStatus = {
  checked: number;
  valid: number;
  rejected: number;
  lastRejected: string;
};

type LoadedOpponent = {
  team: WarRoomCorpusTeam;
  paste: string;
  label: string;
};

function seconds(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.max(0, Math.round(value))}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

function signedPercentagePoints(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)} pp`;
}

function OptimizationComparisonResult({ result }: { result: AutoLabResult }) {
  const optimized = result.variants?.[0];
  if (!optimized) return null;
  const comparison = optimized.comparison;
  if (!comparison?.evidence || !comparison.delta95) {
    return (
      <div className="mt-5 flex items-start gap-2 rounded-2xl border border-amber-300/15 bg-amber-300/[0.04] p-4 text-xs leading-5 text-amber-100">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        Esta respuesta viene de un runtime anterior y no incluye el intervalo comparativo. Reinicia Battle Lab desde la rama actual y repite la corrida.
      </div>
    );
  }
  const presentation = {
    "confirmed-improvement": {
      label: "Mejora confirmada",
      detail: "El optimizado supera al original y el IC95% del delta queda completamente sobre cero.",
      tone: "border-emerald-300/25 bg-emerald-300/[0.055] text-emerald-100",
      icon: TrendingUp,
    },
    "directional-improvement": {
      label: "Mejora direccional",
      detail: "La señal favorece al optimizado, pero todavía admite ruido de combate.",
      tone: "border-cyan-300/25 bg-cyan-300/[0.055] text-cyan-100",
      icon: TrendingUp,
    },
    mixed: {
      label: "Resultado mixto",
      detail: "El promedio y la distribución por rival no apuntan en la misma dirección.",
      tone: "border-amber-300/25 bg-amber-300/[0.055] text-amber-100",
      icon: Scale,
    },
    inconclusive: {
      label: "Sin evidencia suficiente",
      detail: "La corrida no distingue de forma útil al optimizado del original.",
      tone: "border-white/12 bg-white/[0.035] text-slate-200",
      icon: Minus,
    },
    "directional-regression": {
      label: "Regresión direccional",
      detail: "La señal favorece al original, aunque el intervalo todavía cruza cero.",
      tone: "border-rose-300/25 bg-rose-300/[0.055] text-rose-100",
      icon: TrendingDown,
    },
    "confirmed-regression": {
      label: "Regresión confirmada",
      detail: "El optimizado rinde peor y el IC95% del delta queda completamente bajo cero.",
      tone: "border-rose-300/30 bg-rose-300/[0.07] text-rose-100",
      icon: TrendingDown,
    },
  } satisfies Record<AutoLabComparisonEvidence, { label: string; detail: string; tone: string; icon: typeof Scale }>;
  const verdict = presentation[comparison.evidence];
  const VerdictIcon = verdict.icon;
  const opponentMeta = new Map(result.opponents.map((opponent) => [opponent.id, opponent]));
  const matchupDeltas = Object.entries(optimized.screeningByOpponent)
    .flatMap(([id, row]) => {
      const baseline = result.baseline.screeningByOpponent[id];
      if (!baseline) return [];
      return [{
        id,
        label: opponentMeta.get(id)?.label ?? id,
        archetypes: opponentMeta.get(id)?.archetypes ?? [],
        delta: row.scorePercent - baseline.scorePercent,
      }];
    });
  const gains = [...matchupDeltas].filter((row) => row.delta > 0).sort((left, right) => right.delta - left.delta).slice(0, 4);
  const regressions = [...matchupDeltas].filter((row) => row.delta < 0).sort((left, right) => left.delta - right.delta).slice(0, 4);
  const archetypes = new Map<string, number[]>();
  for (const row of matchupDeltas) {
    for (const archetype of row.archetypes.length ? row.archetypes : ["Balance / Other"]) {
      archetypes.set(archetype, [...(archetypes.get(archetype) ?? []), row.delta]);
    }
  }
  const archetypeDeltas = [...archetypes].map(([archetype, values]) => ({
    archetype,
    delta: values.reduce((sum, value) => sum + value, 0) / values.length,
    opponents: values.length,
  }));
  const archetypeGains = archetypeDeltas.filter((row) => row.delta > 0).sort((left, right) => right.delta - left.delta).slice(0, 3);
  const archetypeRegressions = archetypeDeltas.filter((row) => row.delta < 0).sort((left, right) => left.delta - right.delta).slice(0, 3);

  return (
    <div className="mt-5 space-y-4">
      <section className={cn("rounded-[24px] border p-5", verdict.tone)}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-2xl border border-current/20 bg-slate-950/25"><VerdictIcon className="size-5" /></div>
            <div>
              <p className="text-[9px] font-black uppercase tracking-[0.16em] opacity-70">Veredicto comparativo</p>
              <h3 className="mt-1 text-xl font-black">{verdict.label}</h3>
              <p className="mt-1 max-w-2xl text-[11px] leading-5 opacity-75">{verdict.detail}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="font-mono text-3xl font-black">{signedPercentagePoints(comparison.deltaPercentagePoints)}</p>
            <p className="mt-1 text-[10px] opacity-70">IC95% del barrido · {signedPercentagePoints(comparison.delta95.low)} a {signedPercentagePoints(comparison.delta95.high)}</p>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {[
          ["Original · barrido", `${result.baseline.poolEstimate.scorePercent.toFixed(1)}%`, `${result.baseline.poolEstimate.wins}-${result.baseline.poolEstimate.losses}-${result.baseline.poolEstimate.ties} · ${result.baseline.poolEstimate.games} batallas`],
          ["Optimizado · barrido", `${optimized.poolEstimate.scorePercent.toFixed(1)}%`, `${optimized.poolEstimate.wins}-${optimized.poolEstimate.losses}-${optimized.poolEstimate.ties} · ${optimized.poolEstimate.games} batallas`],
          ["Rivales mejorados", `${comparison.opponentsImproved}`, `de ${comparison.opponentsImproved + comparison.opponentsRegressed + comparison.opponentsTied}`],
          ["Rivales empeorados", `${comparison.opponentsRegressed}`, `${comparison.opponentsTied} sin cambio`],
          ["Críticos mejorados", `${comparison.criticalOpponentsImproved}/${comparison.criticalOpponents}`, `${comparison.criticalOpponentsRegressed} regresiones`],
        ].map(([label, value, detail]) => (
          <div key={label} className="rounded-2xl border border-white/8 bg-slate-950/55 p-4">
            <p className="text-[9px] font-black uppercase tracking-[0.13em] text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-black text-white">{value}</p>
            <p className="mt-1 text-[10px] text-slate-600">{detail}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-[22px] border border-emerald-300/12 bg-emerald-300/[0.025] p-4">
          <p className="text-[9px] font-black uppercase tracking-[0.14em] text-emerald-300">Mayores ganancias en barrido</p>
          <div className="mt-3 space-y-2">
            {gains.length ? gains.map((row) => <div key={row.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/7 bg-slate-950/45 px-3 py-2"><span className="truncate text-[11px] text-slate-300">{row.label}</span><strong className="shrink-0 font-mono text-[11px] text-emerald-200">{signedPercentagePoints(row.delta)}</strong></div>) : <p className="text-[11px] text-slate-600">No hubo rivales con mejora medible.</p>}
          </div>
        </div>
        <div className="rounded-[22px] border border-rose-300/12 bg-rose-300/[0.025] p-4">
          <p className="text-[9px] font-black uppercase tracking-[0.14em] text-rose-300">Mayores costos en barrido</p>
          <div className="mt-3 space-y-2">
            {regressions.length ? regressions.map((row) => <div key={row.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/7 bg-slate-950/45 px-3 py-2"><span className="truncate text-[11px] text-slate-300">{row.label}</span><strong className="shrink-0 font-mono text-[11px] text-rose-200">{signedPercentagePoints(row.delta)}</strong></div>) : <p className="text-[11px] text-slate-600">No hubo rivales con regresión medible.</p>}
          </div>
        </div>
      </section>

      <section className="rounded-[22px] border border-white/8 bg-slate-950/45 p-4">
        <p className="text-[9px] font-black uppercase tracking-[0.14em] text-violet-300">Cambio por arquetipo</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {archetypeGains.map((row) => <Badge key={`gain-${row.archetype}`} variant="outline" className="border-emerald-300/15 bg-emerald-300/5 text-[9px] text-emerald-200">{row.archetype} {signedPercentagePoints(row.delta)} · {row.opponents}</Badge>)}
          {archetypeRegressions.map((row) => <Badge key={`loss-${row.archetype}`} variant="outline" className="border-rose-300/15 bg-rose-300/5 text-[9px] text-rose-200">{row.archetype} {signedPercentagePoints(row.delta)} · {row.opponents}</Badge>)}
          {!archetypeGains.length && !archetypeRegressions.length ? <span className="text-[11px] text-slate-600">Sin diferencias por arquetipo.</span> : null}
        </div>
      </section>

      <div className="flex items-start gap-2 rounded-2xl border border-white/8 bg-slate-950/45 p-4 text-[11px] leading-5 text-slate-400">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-cyan-300" />
        <div><strong className="text-slate-200">Lectura correcta:</strong> {comparison.caveat} {result.caveat}</div>
      </div>
    </div>
  );
}

function signalLabel(level: AutoLabAudit["signal"]["level"]) {
  if (level === "stronger") return "Señal más fuerte";
  if (level === "directional") return "Señal direccional";
  return "Exploratoria";
}

const LEGACY_EVIDENCE: Evidence = {
  level: "insufficient",
  label: "Resultado previo",
  note: "Esta auditoría fue generada por un runtime anterior. Reinicia Battle Lab y ejecuta una auditoría nueva para obtener la evidencia comparativa.",
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function EvidenceBadge({ evidence }: { evidence?: Evidence }) {
  const resolved = evidence ?? LEGACY_EVIDENCE;
  const tone = {
    robust: "border-emerald-300/20 bg-emerald-300/[0.05] text-emerald-200",
    directional: "border-cyan-300/20 bg-cyan-300/[0.05] text-cyan-200",
    "side-sensitive": "border-amber-300/20 bg-amber-300/[0.05] text-amber-200",
    insufficient: "border-white/10 bg-white/[0.02] text-slate-400",
  }[resolved.level];
  return (
    <Badge variant="outline" title={resolved.note} className={cn("text-[9px]", tone)}>
      {resolved.label}
    </Badge>
  );
}

async function readPayload(response: Response) {
  const text = await response.text();
  try {
    return text ? (JSON.parse(text) as unknown) : {};
  } catch {
    return {};
  }
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
    ? await fetch(`/api/scouting-pastes/${encodeURIComponent(team.savedPasteId)}`, {
        cache: "no-store",
      })
    : await fetch("/api/pokepaste-import", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: team.pokepasteUrl }),
      });
  const payload = await readPayload(response);
  if (!response.ok) throw new Error(errorText(payload, "No se pudo cargar el paste rival."));
  const paste =
    team.savedPasteId &&
    payload &&
    typeof payload === "object" &&
    "item" in payload &&
    payload.item &&
    typeof payload.item === "object" &&
    "paste" in payload.item &&
    typeof payload.item.paste === "string"
      ? payload.item.paste
      : payload &&
          typeof payload === "object" &&
          "paste" in payload &&
          typeof payload.paste === "string"
        ? payload.paste
        : "";
  if (!paste) throw new Error("El paste rival llegó vacío.");
  return paste;
}

function opponentLabel(team: WarRoomCorpusTeam, deep: boolean, fallback: string) {
  return [deep ? team.dateShared : "", team.playerName, team.tournament]
    .filter(Boolean)
    .join(" · ") || fallback;
}

async function validateBattleReadyBatch(
  teams: Array<{ id: string; label: string; teamPaste: string }>,
) {
  const response = await fetch("/api/battle-lab/auto-lab/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ teams }),
  });
  const payload = await readPayload(response);
  if (!response.ok) {
    throw new Error(errorText(payload, "No se pudo prevalidar el corpus con Pokémon Showdown."));
  }
  const results =
    payload &&
    typeof payload === "object" &&
    "results" in payload &&
    Array.isArray((payload as { results?: unknown }).results)
      ? (payload as { results: AutoLabPreflightVerdict[] }).results
      : [];
  return new Map(results.map((result) => [result.id, result]));
}

function speciesFromPair(value: string) {
  if (!value || value === "No recuperado") return [];
  return value
    .split(" + ")
    .map((item) => item.trim())
    .filter(Boolean);
}

function PokemonPill({
  species,
  tone = "slate",
}: {
  species: string;
  tone?: "cyan" | "violet" | "amber" | "slate";
}) {
  const tones = {
    cyan: "border-cyan-300/18 bg-cyan-300/7 text-cyan-100",
    violet: "border-violet-300/18 bg-violet-300/7 text-violet-100",
    amber: "border-amber-300/18 bg-amber-300/7 text-amber-100",
    slate: "border-white/8 bg-white/[0.025] text-slate-200",
  };
  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center gap-1.5 rounded-full border py-1.5 pr-3 pl-1.5 text-[11px] font-bold",
        tones[tone],
      )}
    >
      <Image
        src={getSpriteUrl(species)}
        alt=""
        width={28}
        height={28}
        unoptimized
        className="size-7 shrink-0 object-contain"
      />
      <span className="truncate">{species}</span>
    </span>
  );
}

function SpriteStrip({ species, size = 38 }: { species: string[]; size?: number }) {
  if (!species.length) {
    return <span className="text-[11px] text-slate-500">Sin sprites recuperados</span>;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {species.map((name, index) => (
        <div
          key={`${name}-${index}`}
          title={name}
          className="flex items-center justify-center rounded-lg border border-white/8 bg-slate-950/60 p-0.5"
        >
          <Image
            src={getSpriteUrl(name)}
            alt={name}
            width={size}
            height={size}
            unoptimized
            className="object-contain"
            style={{ width: size, height: size }}
          />
        </div>
      ))}
    </div>
  );
}

function MatchupCard({ row, tone }: { row: MatchupRow; tone: "good" | "bad" }) {
  return (
    <article
      className={cn(
        "rounded-2xl border bg-slate-950/55 p-4",
        tone === "good" ? "border-emerald-300/14" : "border-rose-300/14",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-black text-white">{row.label}</h4>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <p className="font-mono text-[11px] text-slate-400">
              {row.wins}-{row.losses}-{row.ties} · {row.games} partidas
            </p>
            <Badge
              variant="outline"
              className={cn(
                "text-[9px]",
                row.deepDive
                  ? "border-cyan-300/20 bg-cyan-300/[0.05] text-cyan-200"
                  : "border-white/10 text-slate-400",
              )}
            >
              {row.deepDive ? "Confirmado" : "Barrido"}
            </Badge>
          </div>
        </div>
        <strong
          className={cn(
            "text-lg font-black",
            tone === "good" ? "text-emerald-200" : "text-rose-200",
          )}
        >
          {row.scorePercent.toFixed(1)}%
        </strong>
      </div>
      <p className="mt-2 text-[10px] text-slate-500">
        IC95% {row.confidence95.low.toFixed(1)}–{row.confidence95.high.toFixed(1)}%
      </p>
      <div className="mt-3">
        <SpriteStrip species={row.roster} size={34} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {row.archetypes.map((tag) => (
          <Badge
            key={tag}
            variant="outline"
            className="border-white/10 bg-white/[0.02] text-[10px] text-slate-300"
          >
            {tag}
          </Badge>
        ))}
      </div>
    </article>
  );
}

function SelectionCard({ row }: { row: AutoLabAudit["selectionUsage"][number] }) {
  const hasComparison =
    isFiniteNumber(row.scoreWhenNotSelected) &&
    isFiniteNumber(row.matchedDeltaPercentagePoints) &&
    isFiniteNumber(row.matchedStrata) &&
    isFiniteNumber(row.smoothedScoreWhenSelected);

  return (
    <article className="rounded-2xl border border-white/8 bg-slate-950/50 p-4">
      <div className="flex items-center gap-3">
        <Image
          src={getSpriteUrl(row.pokemon)}
          alt={row.pokemon}
          width={56}
          height={56}
          unoptimized
          className="size-14 shrink-0 object-contain"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <strong className="truncate text-sm font-black text-white">{row.pokemon}</strong>
            <div className="flex flex-wrap justify-end gap-1.5">
              <EvidenceBadge evidence={row.evidence} />
              {row.signal !== "ok" ? (
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px]",
                    row.signal === "rarely-selected"
                      ? "border-amber-300/18 bg-amber-300/[0.04] text-amber-200"
                      : "border-rose-300/18 bg-rose-300/[0.04] text-rose-200",
                  )}
                >
                  {row.signal === "rarely-selected" ? "Poco elegido" : "Revisar"}
                </Badge>
              ) : null}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <Progress
              value={row.selectedRate}
              className="h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300"
            />
            <span className="w-12 text-right font-mono text-[11px] text-cyan-200">
              {row.selectedRate.toFixed(1)}%
            </span>
          </div>
          {hasComparison ? (
            <>
              <p className="mt-1.5 text-[11px] leading-4 text-slate-400">
                {row.selectedGames} selecciones del barrido · {row.leadGames} leads. Score al elegirlo{" "}
                {row.scoreWhenSelected.toFixed(1)}% vs{" "}
                {row.scoreWhenNotSelected.toFixed(1)}% sin elegirlo.
              </p>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                Δ ajustado por rival+lado{" "}
                {row.matchedDeltaPercentagePoints >= 0 ? "+" : ""}
                {row.matchedDeltaPercentagePoints.toFixed(1)} pp · {row.matchedStrata} estratos
                comparables · suavizado {row.smoothedScoreWhenSelected.toFixed(1)}%.
              </p>
            </>
          ) : (
            <p className="mt-1.5 text-[11px] text-slate-400">
              {row.selectedGames} selecciones · {row.leadGames} leads · score{" "}
              {row.scoreWhenSelected.toFixed(1)}%
            </p>
          )}
        </div>
      </div>
    </article>
  );
}

function ArchetypeCard({ row }: { row: AutoLabAudit["archetypePerformance"][number] }) {
  const displayScore = isFiniteNumber(row.smoothedScorePercent)
    ? row.smoothedScorePercent
    : row.scorePercent;

  return (
    <article className="rounded-2xl border border-fuchsia-300/12 bg-slate-950/55 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <strong className="text-sm font-black text-white">{row.archetype}</strong>
          <div className="mt-1"><EvidenceBadge evidence={row.evidence} /></div>
        </div>
        <div className="text-right">
          <span className="block font-mono text-lg font-black text-fuchsia-200">
            {displayScore.toFixed(1)}%
          </span>
          <span className="text-[9px] text-slate-500">
            {isFiniteNumber(row.smoothedScorePercent) ? "suavizado" : "resultado previo"}
          </span>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <Progress
          value={displayScore}
          className="h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-fuchsia-300"
        />
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        {row.games} partidas · {row.uniqueOpponents} rivales · crudo {row.scorePercent.toFixed(1)}% · IC95% {row.confidence95.low.toFixed(1)}–{row.confidence95.high.toFixed(1)}%
      </p>
    </article>
  );
}

function OpponentThreatCard({
  row,
}: {
  row: AutoLabAudit["opponentPokemonPressure"][number];
}) {
  const displayLossRate = isFiniteNumber(row.smoothedLossRate)
    ? row.smoothedLossRate
    : row.lossRate;
  const hasComparison =
    isFiniteNumber(row.lossRateWithout) && isFiniteNumber(row.matchedStrata);

  return (
    <article className="rounded-2xl border border-white/8 bg-slate-950/55 p-4">
      <div className="flex items-start gap-3">
        <Image
          src={getSpriteUrl(row.pokemon)}
          alt={row.pokemon}
          width={56}
          height={56}
          unoptimized
          className="size-14 shrink-0 object-contain"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <h3 className="truncate text-sm font-black text-white">{row.pokemon}</h3>
            <span className="font-mono text-sm font-black text-amber-200">
              {displayLossRate.toFixed(1)}%
            </span>
          </div>
          <div className="mt-1"><EvidenceBadge evidence={row.evidence} /></div>
          <div className="mt-2 flex items-center gap-2">
            <Progress
              value={displayLossRate}
              className="h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-amber-300"
            />
          </div>
          {hasComparison ? (
            <>
              <p className="mt-2 text-[11px] leading-4 text-slate-400">
                {row.lossGames}/{row.observedGames} derrotas cuando apareció · crudo{" "}
                {row.lossRate.toFixed(1)}% vs {row.lossRateWithout.toFixed(1)}% sin verlo ·{" "}
                {row.uniqueOpponents} rivales.
              </p>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                Δ{" "}
                {row.comparisonBasis === "archetype-and-side"
                  ? "ajustado por arquetipo+lado"
                  : "vs referencia suavizada"}{" "}
                {row.lossRateLift >= 0 ? "+" : ""}
                {row.lossRateLift.toFixed(1)} pp · {row.matchedStrata} estratos.
              </p>
            </>
          ) : (
            <p className="mt-2 text-[11px] leading-4 text-slate-400">
              {row.lossGames}/{row.observedGames} derrotas cuando apareció ·{" "}
              {row.lossRateLift >= 0 ? "+" : ""}
              {row.lossRateLift.toFixed(1)} pp vs referencia · {row.uniqueOpponents} rivales.
            </p>
          )}
        </div>
      </div>
    </article>
  );
}

function OpponentCoreCard({
  row,
}: {
  row: AutoLabAudit["opponentCorePressure"][number];
}) {
  const species = speciesFromPair(row.core);
  const displayLossRate = isFiniteNumber(row.smoothedLossRate)
    ? row.smoothedLossRate
    : row.lossRate;
  const hasComparison =
    isFiniteNumber(row.lossRateWithout) && isFiniteNumber(row.matchedStrata);

  return (
    <article className="rounded-2xl border border-white/8 bg-slate-950/55 p-4">
      <div className="flex flex-wrap gap-2">
        {species.map((name) => (
          <PokemonPill key={name} species={name} tone="violet" />
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Progress
          value={displayLossRate}
          className="h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-violet-300"
        />
        <span className="w-12 text-right font-mono text-[11px] font-black text-violet-200">
          {displayLossRate.toFixed(1)}%
        </span>
      </div>
      <div className="mt-2"><EvidenceBadge evidence={row.evidence} /></div>
      {hasComparison ? (
        <>
          <p className="mt-2 text-[11px] leading-4 text-slate-400">
            {row.lossGames}/{row.observedGames} derrotas al aparecer · crudo{" "}
            {row.lossRate.toFixed(1)}% vs {row.lossRateWithout.toFixed(1)}% sin ese lead ·{" "}
            {row.uniqueOpponents} rivales.
          </p>
          <p className="mt-1 text-[10px] leading-4 text-slate-500">
            Δ{" "}
            {row.comparisonBasis === "archetype-and-side"
              ? "ajustado por arquetipo+lado"
              : "vs referencia suavizada"}{" "}
            {row.lossRateLift >= 0 ? "+" : ""}
            {row.lossRateLift.toFixed(1)} pp · {row.matchedStrata} estratos.
          </p>
        </>
      ) : (
        <p className="mt-2 text-[11px] leading-4 text-slate-400">
          {row.lossGames}/{row.observedGames} derrotas al aparecer ·{" "}
          {row.lossRateLift >= 0 ? "+" : ""}
          {row.lossRateLift.toFixed(1)} pp vs referencia · {row.uniqueOpponents} rivales.
        </p>
      )}
    </article>
  );
}

export function WarRoomAutoLab({
  team,
  comparisonTeam,
  corpusTeams,
  onClose,
}: {
  team: TeamVersion;
  comparisonTeam?: TeamVersion;
  corpusTeams: WarRoomCorpusTeam[];
  onClose?: () => void;
}) {
  const comparisonMode = Boolean(comparisonTeam);
  const [preset, setPreset] = useState<RunPreset>(comparisonMode ? "standard" : "quick");
  const activePreset: RunPreset = comparisonMode ? "standard" : preset;
  const useRecentVgcPastesPool = comparisonMode || activePreset === "deep";
  const [starting, setStarting] = useState(false);
  const [job, setJob] = useState<AutoLabJob | null>(null);
  const [runError, setRunError] = useState("");
  const [preflight, setPreflight] = useState<AutoLabPreflightStatus | null>(null);
  const pollRef = useRef<number | null>(null);
  const pollFailuresRef = useRef(0);
  const teamKey = useMemo(() => `${team.id}|${team.paste}|${comparisonTeam?.paste ?? ""}`, [comparisonTeam?.paste, team.id, team.paste]);
  const baselinePaste = useMemo(
    () => serializeShowdownPaste(team.pokemon, team.mechanics ?? ["mega"]),
    [team],
  );
  const comparisonPaste = useMemo(
    () => comparisonTeam
      ? serializeShowdownPaste(comparisonTeam.pokemon, comparisonTeam.mechanics ?? ["mega"])
      : "",
    [comparisonTeam],
  );
  const baselineReady = inspectBattleReadyPaste(baselinePaste);
  const comparisonReady = comparisonPaste ? inspectBattleReadyPaste(comparisonPaste) : null;

  useEffect(() => () => {
    if (pollRef.current != null) window.clearTimeout(pollRef.current);
  }, []);

  async function poll(jobId: string) {
    try {
      const response = await fetch(
        `/api/battle-lab/auto-lab/${encodeURIComponent(jobId)}`,
        { cache: "no-store" },
      );
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
      const message =
        error instanceof Error ? error.message : "Se perdió la conexión con Auto Lab.";
      setRunError(`${message} · reintentando consulta de estado…`);
      const delay = Math.min(
        MAX_POLL_BACKOFF_MS,
        POLL_MS * (pollFailuresRef.current + 1),
      );
      pollRef.current = window.setTimeout(() => void poll(jobId), delay);
    }
  }

  async function startAudit() {
    if (!baselineReady.ready || (comparisonMode && !comparisonReady?.ready)) return;
    if (pollRef.current != null) window.clearTimeout(pollRef.current);
    pollFailuresRef.current = 0;
    setStarting(true);
    setRunError("");
    setJob(null);
    setPreflight({ checked: 0, valid: 0, rejected: 0, lastRejected: "" });
    try {
      const spec = PRESETS[activePreset];
      const participantVerdicts = await validateBattleReadyBatch([
        {
          id: "baseline-current",
          label: `${team.name} · ${comparisonMode ? "Original" : "Team actual"}`,
          teamPaste: baselinePaste,
        },
        ...(comparisonMode && comparisonTeam ? [{
          id: "variant-optimized",
          label: `${comparisonTeam.name} · Optimizado`,
          teamPaste: comparisonPaste,
        }] : []),
      ]);
      const baselineVerdict = participantVerdicts.get("baseline-current");
      if (!baselineVerdict?.valid) {
        throw new Error(
          `El Team ${comparisonMode ? "original" : "actual"} no supera validate-team M-C: ${baselineVerdict?.error || "validación sin respuesta"}`,
        );
      }
      const comparisonVerdict = participantVerdicts.get("variant-optimized");
      if (comparisonMode && !comparisonVerdict?.valid) {
        throw new Error(
          `El Team optimizado no supera validate-team M-C: ${comparisonVerdict?.error || "validación sin respuesta"}`,
        );
      }

      const pool =
        useRecentVgcPastesPool
          ? selectAutoLabRecentVgcPastesCandidates(corpusTeams, spec.opponents * 3)
          : selectAutoLabOpponentCandidates(corpusTeams, spec.opponents * 3, teamKey);
      const loaded: LoadedOpponent[] = [];
      let checked = 0;
      let rejected = 0;
      let lastRejected = "";

      for (
        let offset = 0;
        offset < pool.length && loaded.length < spec.opponents;
        offset += LOAD_BATCH
      ) {
        const candidates = pool.slice(offset, offset + LOAD_BATCH);
        const fetched = await Promise.all(
          candidates.map(async (candidate, index) => {
            const id = `preflight-${offset + index + 1}`;
            const label = opponentLabel(
              candidate,
              useRecentVgcPastesPool,
              `Rival ${offset + index + 1}`,
            );
            try {
              const paste = await loadExactPaste(candidate);
              const structural = inspectBattleReadyPaste(paste);
              if (!structural.ready) {
                return {
                  id,
                  team: candidate,
                  label,
                  error: structural.issues[0] || "Paste incompleto.",
                };
              }
              return { id, team: candidate, label, paste, error: "" };
            } catch (error) {
              return {
                id,
                team: candidate,
                label,
                error:
                  error instanceof Error
                    ? error.message
                    : "No se pudo cargar el paste rival.",
              };
            }
          }),
        );

        const readyToValidate = fetched.filter(
          (item): item is LoadedOpponent & { id: string; error: string } =>
            "paste" in item && Boolean(item.paste),
        );
        for (const item of fetched) {
          if ("paste" in item && item.paste) continue;
          checked += 1;
          rejected += 1;
          lastRejected = `${item.label}: ${item.error}`;
        }

        if (readyToValidate.length) {
          const verdicts = await validateBattleReadyBatch(
            readyToValidate.map((item) => ({
              id: item.id,
              label: item.label,
              teamPaste: item.paste,
            })),
          );
          for (const item of readyToValidate) {
            checked += 1;
            const verdict = verdicts.get(item.id);
            if (verdict?.valid) {
              if (loaded.length < spec.opponents) {
                loaded.push({ team: item.team, paste: item.paste, label: item.label });
              }
            } else {
              rejected += 1;
              lastRejected = `${item.label}: ${verdict?.error || "Showdown rechazó el paste."}`;
            }
          }
        }
        setPreflight({ checked, valid: loaded.length, rejected, lastRejected });
      }

      if (useRecentVgcPastesPool && loaded.length < spec.opponents) {
        throw new Error(
          `${comparisonMode ? "La comparación" : "Profundo"} necesita ${spec.opponents} VGCPastes M-C actuales, recientes y validados por Showdown; solo encontramos ${loaded.length} después de revisar ${checked} y descartar ${rejected}.`,
        );
      }
      if (loaded.length < Math.min(6, spec.opponents)) {
        throw new Error(
          "No hay suficientes rivales válidos en Showdown entre VGCPastes, torneos y Mis pastes.",
        );
      }

      const response = await fetch("/api/battle-lab/auto-lab", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          baseline: {
            id: "baseline-current",
            label: `${team.name} · ${comparisonMode ? "Original" : "Team actual"}`,
            teamPaste: baselinePaste,
          },
          variants: comparisonMode && comparisonTeam ? [{
            id: "variant-optimized",
            label: `${comparisonTeam.name} · Optimizado`,
            teamPaste: comparisonPaste,
          }] : [],
          opponents: loaded.map(({ paste, label }, index) => ({
            id: `opponent-${index + 1}`,
            label,
            teamPaste: paste,
          })),
          initialBattlesPerOpponent: spec.initialBattlesPerOpponent,
          deepDiveOpponents: Math.min(spec.deepDiveOpponents, loaded.length),
          additionalBattlesPerDeepDive: spec.additionalBattlesPerDeepDive,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(errorText(payload, comparisonMode ? "No se pudo iniciar la comparación profunda." : "No se pudo iniciar la auditoría empírica."));
      }
      const next = payload as AutoLabJob;
      setJob(next);
      void poll(next.id);
    } catch (error) {
      setRunError(
        error instanceof Error ? error.message : comparisonMode ? "No se pudo iniciar la comparación profunda." : "No se pudo iniciar la auditoría empírica.",
      );
    } finally {
      setStarting(false);
    }
  }

  const audit = job?.result?.audit;
  const jobActive = Boolean(job && !["completed", "error", "cancelled"].includes(job.phase));

  return (
    <section className={cn("rounded-[26px] border p-5", comparisonMode ? "border-violet-300/15 bg-violet-300/[0.025]" : "border-cyan-300/12 bg-cyan-300/[0.02]")}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className={cn("flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em]", comparisonMode ? "text-violet-300" : "text-cyan-300")}>
            {comparisonMode ? <Scale className="size-3.5" /> : <ShieldCheck className="size-3.5" />}
            {comparisonMode ? "Auto Lab · benchmark A/B" : "Auto Lab · auditoría empírica"}
          </div>
          <h2 className="mt-1 text-xl font-black text-white">
            {comparisonMode ? "Original contra optimizado, bajo la misma prueba" : "Tortura el Team actual contra un meta mucho más ancho"}
          </h2>
          <p className="mt-2 text-[12px] leading-5 text-slate-400">
            {comparisonMode
              ? "Mismo pool, lados y semillas de Team Preview para ambos Teams."
              : <>Audit usa todo el presupuesto en este Team: LIGHT explora Team Preview, mantiene los turnos deterministas y convierte los replays en diagnóstico. Los paquetes de set se quedaron en <strong className="text-violet-200">Optimizar o construir</strong>.</>}
          </p>
        </div>
        {onClose ? (
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={jobActive} className="border-white/10 text-[11px]">Cerrar comparación</Button>
        ) : job ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setJob(null);
              setRunError("");
              setPreflight(null);
            }}
            disabled={jobActive}
            className="gap-2 border-white/10 text-[11px]"
          >
            <RefreshCw className="size-3.5" />
            Limpiar informe
          </Button>
        ) : null}
      </div>

      <div className="mt-5 rounded-2xl border border-white/8 bg-slate-950/50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">
              {comparisonMode ? "Comparación fija" : "Cobertura del Gauntlet"}
            </p>
            {comparisonMode ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge variant="outline" className="border-white/10 bg-white/[0.03] text-[10px] text-slate-300">Original · 480 batallas</Badge>
                <Badge variant="outline" className="border-violet-300/18 bg-violet-300/6 text-[10px] text-violet-200">Optimizado · 480 batallas</Badge>
                <Badge variant="outline" className="border-cyan-300/18 bg-cyan-300/6 text-[10px] text-cyan-200">40 rivales · 960 total</Badge>
              </div>
            ) : <div className="mt-2 flex flex-wrap gap-2">
              {(Object.keys(PRESETS) as RunPreset[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setPreset(value);
                    setPreflight(null);
                  }}
                  disabled={Boolean(
                    job && !["completed", "error", "cancelled"].includes(job.phase),
                  )}
                  className={cn(
                    "rounded-xl border px-3 py-2 text-left transition",
                    preset === value
                      ? "border-cyan-300/30 bg-cyan-300/[0.08]"
                      : "border-white/8 bg-slate-950/30 hover:border-white/15",
                  )}
                >
                  <span
                    className={cn(
                      "block text-[11px] font-black",
                      preset === value ? "text-cyan-100" : "text-slate-200",
                    )}
                  >
                    {PRESETS[value].label}
                  </span>
                  <span className="mt-0.5 block text-[10px] text-slate-400">
                    {PRESETS[value].opponents} × {PRESETS[value].initialBattlesPerOpponent}
                    {" + "}
                    {PRESETS[value].deepDiveOpponents} × {PRESETS[value].additionalBattlesPerDeepDive}
                  </span>
                </button>
              ))}
            </div>}
            {!comparisonMode ? <p className="mt-2 text-[11px] text-slate-400">{PRESETS[activePreset].description}</p> : null}
          </div>
          <Button
            type="button"
            onClick={() => void startAudit()}
            disabled={
              starting ||
              !baselineReady.ready ||
              (comparisonMode && !comparisonReady?.ready) ||
              jobActive
            }
            className={cn("gap-2 text-slate-950", comparisonMode ? "bg-violet-300 hover:bg-violet-200" : "bg-cyan-300 hover:bg-cyan-200")}
          >
            {starting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            {starting ? "Validando meta…" : comparisonMode ? "Iniciar 960 batallas" : "Auditar Team con LIGHT"}
          </Button>
        </div>
        {!baselineReady.ready ? (
          <p className="mt-3 text-[11px] text-rose-300">
            El Team {comparisonMode ? "original" : "actual"} no es battle-ready: {baselineReady.issues[0]}
          </p>
        ) : null}
        {comparisonMode && comparisonReady && !comparisonReady.ready ? <p className="mt-3 text-[11px] text-rose-300">El Team optimizado no es battle-ready: {comparisonReady.issues[0]}</p> : null}
        {preflight ? (
          <div className="mt-3 rounded-xl border border-cyan-300/10 bg-cyan-300/[0.025] px-3 py-2 text-[11px] text-slate-400">
            <strong className="text-cyan-200">Preflight Showdown M-C:</strong>{" "}
            {preflight.valid}/{PRESETS[activePreset].opponents} válidos · {preflight.rejected}{" "}
            descartados · {preflight.checked} revisados
            {preflight.lastRejected ? (
              <div
                className="mt-1 truncate text-amber-200/85"
                title={preflight.lastRejected}
              >
                Último descarte: {preflight.lastRejected}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      {runError ? (
        <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-300/15 bg-rose-300/[0.04] p-4 text-xs text-rose-200">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {runError}
        </div>
      ) : null}

      {job && !job.result ? (
        <div className="mt-4 rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.025] p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Swords className="size-4 text-cyan-300" />
              <strong className="text-sm text-white">
                {job.phase === "finalizing"
                  ? "Generando informe de combate…"
                  : job.currentOpponentId
                    ? `${job.currentCandidateLabel ? `${job.currentCandidateLabel} · ` : ""}${job.samplingStage === "deepening" ? "confirmando" : "mapeando"} ${job.currentOpponentId}`
                    : job.phase === "preparing"
                      ? "Preparando arena…"
                      : "Gauntlet en curso"}
              </strong>
            </div>
            <span className="font-mono text-[11px] text-slate-300">
              {job.completedBattles}/{job.totalBattles}
            </span>
          </div>
          <Progress
            value={Math.max(0, Math.min(100, job.progress * 100))}
            className="mt-3 h-2 bg-white/7 [&_[data-slot=progress-indicator]]:bg-cyan-300"
          />
          <div className="mt-2 flex justify-between text-[11px] text-slate-400">
            <span>Transcurrido {seconds(job.elapsedSeconds)}</span>
            <span>
              ETA {job.phase === "finalizing" ? "armando informe" : seconds(job.etaSeconds)}
            </span>
          </div>
          {job.error ? <p className="mt-3 text-[11px] text-rose-300">{job.error}</p> : null}
        </div>
      ) : null}

      {job?.result && comparisonMode ? <OptimizationComparisonResult result={job.result} /> : null}

      {job?.result && audit && !comparisonMode ? (
        <div className="mt-5 space-y-5">
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            {[
              ["Score cobertura", `${job.result.baseline.poolEstimate.scorePercent.toFixed(1)}%`, "text-cyan-100"],
              [
                "Registro cobertura",
                `${job.result.baseline.poolEstimate.wins}-${job.result.baseline.poolEstimate.losses}-${job.result.baseline.poolEstimate.ties}`,
                "text-white",
              ],
              ["Rivales", `${job.result.opponents.length}`, "text-white"],
              ["Batallas", `${audit.signal.games}`, "text-white"],
              [
                "Replays",
                `${audit.replayCoverage.parsed}/${audit.replayCoverage.expected}`,
                "text-white",
              ],
            ].map(([label, value, tone]) => (
              <div
                key={label}
                className="rounded-2xl border border-white/8 bg-slate-950/50 p-4"
              >
                <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">
                  {label}
                </p>
                <p className={cn("mt-1 text-2xl font-black", tone)}>{value}</p>
              </div>
            ))}
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="rounded-[24px] border border-cyan-300/12 bg-cyan-300/[0.025] p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] font-black uppercase tracking-[0.14em] text-cyan-300">
                    Muestreo adaptativo
                  </p>
                  <h3 className="mt-1 text-lg font-black text-white">
                    Cobertura primero, confirmación después
                  </h3>
                </div>
                <Badge
                  variant="outline"
                  className="border-cyan-300/15 bg-cyan-300/[0.03] text-[10px] text-cyan-200"
                >
                  {job.result.sampling.screening.opponents} × {job.result.sampling.screening.battlesPerOpponent}
                  {" + "}
                  {job.result.sampling.deepDive.opponents} × {job.result.sampling.deepDive.additionalBattlesPerOpponent}
                </Badge>
              </div>
              <p className="mt-2 text-[11px] leading-5 text-slate-400">
                {job.result.sampling.heuristic}. Los rivales confirmados conservan el mismo
                piloto LIGHT y reciben batallas nuevas, con semillas de Preview no repetidas.
              </p>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {job.result.sampling.deepDive.selected.slice(0, 4).map((row) => (
                  <article
                    key={row.id}
                    className="rounded-xl border border-white/8 bg-slate-950/45 px-3 py-2.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <strong className="truncate text-[11px] text-white">
                        #{row.selectionRank} · {row.label}
                      </strong>
                      <span className="font-mono text-[10px] text-cyan-200">
                        {row.priorityScore.toFixed(1)}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-400">
                      {row.reasons.join(" ")}
                    </p>
                  </article>
                ))}
              </div>
            </div>

            <div className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-violet-300">
                Calidad de datos
              </p>
              <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                {[
                  ["Rosters únicos", audit.dataQuality.uniqueRosters],
                  ["Rivales", audit.dataQuality.uniqueOpponents],
                  ["Preview distintos", audit.dataQuality.uniquePreviewCombinations],
                  ["Barrido / confirm.", `${audit.dataQuality.screeningGames} / ${audit.dataQuality.deepeningGames}`],
                  ["Replay coverage", `${audit.dataQuality.replayCoveragePercent.toFixed(1)}%`],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl border border-white/8 bg-slate-950/45 p-3">
                    <p className="text-[10px] text-slate-500">{label}</p>
                    <strong className="mt-0.5 block text-sm text-white">{value}</strong>
                  </div>
                ))}
              </div>
              <div className="mt-3 rounded-xl border border-white/8 bg-slate-950/45 p-3">
                <div className="flex items-center justify-between gap-2">
                  <strong className="text-[11px] text-white">Sensibilidad de ejecución</strong>
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-[9px]",
                      audit.policySensitivity.status === "review"
                        ? "border-amber-300/20 text-amber-200"
                        : "border-white/10 text-slate-300",
                    )}
                  >
                    Δ lados {audit.policySensitivity.sideGapPercentagePoints.toFixed(1)} pp
                  </Badge>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-400">
                  {audit.policySensitivity.note} {audit.policySensitivity.limitation}
                </p>
              </div>
              <div className="mt-3 rounded-xl border border-white/8 bg-slate-950/45 p-3">
                <strong className="text-[11px] text-white">Semáforo de evidencia</strong>
                {audit.evidenceSummary ? (
                  <>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Badge
                        variant="outline"
                        className="border-emerald-300/20 text-[9px] text-emerald-200"
                      >
                        Robustas {audit.evidenceSummary.robust}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="border-cyan-300/20 text-[9px] text-cyan-200"
                      >
                        Direccionales {audit.evidenceSummary.directional}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="border-amber-300/20 text-[9px] text-amber-200"
                      >
                        Sensibles {audit.evidenceSummary.sideSensitive}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="border-white/10 text-[9px] text-slate-400"
                      >
                        Insuficientes {audit.evidenceSummary.insufficient}
                      </Badge>
                    </div>
                    <p className="mt-2 text-[10px] leading-4 text-slate-400">
                      {audit.evidenceSummary.note}
                    </p>
                  </>
                ) : (
                  <p className="mt-2 text-[10px] leading-4 text-amber-200">
                    Esta ejecución viene de un runtime anterior. El resultado sigue visible, pero
                    necesita reiniciar Battle Lab y repetir la auditoría para calcular el nuevo
                    semáforo.
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="rounded-[24px] border border-cyan-300/12 bg-cyan-300/[0.025] p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.14em] text-cyan-300">
                  Informe de combate
                </p>
                <h3 className="mt-1 text-lg font-black text-white">
                  Qué hace bien y dónde se rompe este Team
                </h3>
              </div>
              <Badge
                variant="outline"
                className="border-cyan-300/15 bg-cyan-300/[0.03] text-[10px] text-cyan-200"
              >
                {signalLabel(audit.signal.level)} · {audit.signal.games} partidas
              </Badge>
            </div>
            <p className="mt-2 text-[12px] leading-5 text-slate-400">{audit.signal.note}</p>
          </section>

          <section className="rounded-[24px] border border-fuchsia-300/12 bg-fuchsia-300/[0.025] p-5">
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-fuchsia-300">
              Meta por estilo
            </p>
            <h2 className="mt-1 text-xl font-black text-white">Rendimiento por arquetipo</h2>
            <p className="mt-2 max-w-3xl text-[12px] leading-5 text-slate-400">
              Cómo rindió LIGHT con este Team frente a las estructuras detectadas en el Gauntlet.
              Un rival puede pertenecer a más de un arquetipo.
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {audit.archetypePerformance.map((row) => (
                <ArchetypeCard key={row.archetype} row={row} />
              ))}
            </div>
          </section>

          <div className="grid gap-4 xl:grid-cols-2">
            <section className="rounded-[24px] border border-emerald-300/10 bg-emerald-300/[0.02] p-5">
              <h2 className="text-lg font-black text-emerald-200">Matchups favorables</h2>
              <div className="mt-4 grid gap-3">
                {audit.goodMatchups.map((row) => (
                  <MatchupCard key={row.id} row={row} tone="good" />
                ))}
              </div>
            </section>
            <section className="rounded-[24px] border border-rose-300/10 bg-rose-300/[0.02] p-5">
              <h2 className="text-lg font-black text-rose-200">Matchups duros</h2>
              <div className="mt-4 grid gap-3">
                {audit.badMatchups.map((row) => (
                  <MatchupCard key={row.id} row={row} tone="bad" />
                ))}
              </div>
            </section>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
              <p className="text-[10px] font-black uppercase tracking-[0.16em] text-cyan-300">
                Team Preview
              </p>
              <h2 className="mt-1 text-lg font-black text-white">Leads propios</h2>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {[...audit.leadPerformance]
                  .sort((a, b) => b.scorePercent - a.scorePercent || b.games - a.games)
                  .map((row) => (
                    <article
                      key={row.lead}
                      className="rounded-2xl border border-white/8 bg-slate-950/55 p-4"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <SpriteStrip species={speciesFromPair(row.lead)} size={38} />
                        <strong className="font-mono text-sm font-black text-cyan-200">
                          {row.scorePercent.toFixed(1)}%
                        </strong>
                      </div>
                      <p className="mt-2 text-[11px] leading-4 text-slate-400">
                        {row.lead} · {row.games} partidas · {row.wins}-{row.losses}-{row.ties}
                      </p>
                    </article>
                  ))}
              </div>
            </section>

            <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
              <p className="text-[10px] font-black uppercase tracking-[0.16em] text-violet-300">
                Team usage
              </p>
              <h2 className="mt-1 text-lg font-black text-white">Uso del roster</h2>
              <div className="mt-4 grid gap-3">
                {audit.selectionUsage.map((row) => (
                  <SelectionCard key={row.pokemon} row={row} />
                ))}
              </div>
            </section>
          </div>

          <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.16em] text-amber-300">
                  Battle pressure
                </p>
                <h2 className="mt-1 text-lg font-black text-white">
                  Pokémon rivales ligados a derrotas
                </h2>
              </div>
              <Badge
                variant="outline"
                className="border-amber-300/15 bg-amber-300/[0.04] text-[10px] text-amber-200"
              >
                Asociación observada · no causalidad
              </Badge>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {audit.opponentPokemonPressure.map((row) => (
                <OpponentThreatCard key={row.pokemon} row={row} />
              ))}
            </div>
          </section>

          <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-violet-300">
              Core pressure
            </p>
            <h2 className="mt-1 text-lg font-black text-white">
              Cores / leads rivales ligados a derrotas
            </h2>
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {audit.opponentCorePressure.map((row) => (
                <OpponentCoreCard key={row.core} row={row} />
              ))}
            </div>
          </section>

          <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-slate-300">
              Move usage
            </p>
            <h2 className="mt-1 text-lg font-black text-white">Moves que merecen revisión</h2>
            <p className="mt-2 text-[12px] leading-5 text-slate-400">
              Señal correlacional de uso; no significa que el move sea malo por sí mismo. Una
              aparición cuenta cuando su Pokémon entró al campo, no como garantía de que el move
              fuera correcto en todos esos turnos.
            </p>
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {audit.moveSignals.map((row) => (
                <article
                  key={`${row.pokemon}-${row.move}`}
                  className="flex items-center gap-3 rounded-2xl border border-white/8 bg-slate-950/55 p-4"
                >
                  <Image
                    src={getSpriteUrl(row.pokemon)}
                    alt={row.pokemon}
                    width={48}
                    height={48}
                    unoptimized
                    className="size-12 object-contain"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <strong className="truncate text-[12px] font-bold text-white">
                        {row.pokemon} · {row.move}
                      </strong>
                      {row.signal !== "observed" ? (
                        <Badge
                          variant="outline"
                          className="border-amber-300/15 bg-amber-300/[0.04] text-[10px] text-amber-200"
                        >
                          {row.signal === "review" ? "Revisar" : "Poco usado"}
                        </Badge>
                      ) : null}
                    </div>
                    <div className="mt-1"><EvidenceBadge evidence={row.evidence} /></div>
                    {isFiniteNumber(row.appearanceGames) &&
                    isFiniteNumber(row.scoreWhenAvailableButUnused) ? (
                      <p className="mt-1 text-[11px] text-slate-400">
                        {row.totalUses} usos en {row.gamesUsed}/{row.appearanceGames} apariciones del
                        Pokémon · score {row.scoreWhenUsed.toFixed(1)}% vs{" "}
                        {row.scoreWhenAvailableButUnused.toFixed(1)}% sin usarlo.
                      </p>
                    ) : (
                      <p className="mt-1 text-[11px] text-slate-400">
                        {row.totalUses} usos · {row.gamesUsed} partidas · score{" "}
                        {row.scoreWhenUsed.toFixed(1)}%
                      </p>
                    )}
                    {isFiniteNumber(row.matchedDeltaPercentagePoints) &&
                    isFiniteNumber(row.opportunityRate) ? (
                      <p className="mt-1 text-[10px] leading-4 text-slate-500">
                        Δ rival+lado {row.matchedDeltaPercentagePoints >= 0 ? "+" : ""}
                        {row.matchedDeltaPercentagePoints.toFixed(1)} pp · tasa de uso{" "}
                        {row.opportunityRate.toFixed(1)}%.
                      </p>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="rounded-[24px] border border-rose-300/10 bg-rose-300/[0.02] p-5">
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-rose-300">
              Loss patterns
            </p>
            <h2 className="mt-1 text-lg font-black text-white">
              Patrones recurrentes en derrotas
            </h2>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {audit.recurringLossPatterns.map((row) => (
                <div
                  key={row.pattern}
                  className="flex items-center gap-3 rounded-2xl border border-white/8 bg-slate-950/55 px-4 py-3"
                >
                  <AlertTriangle className="size-5 shrink-0 text-rose-300" />
                  <div className="min-w-0 flex-1">
                    <strong className="text-[12px] font-bold text-white">{row.pattern}</strong>
                    <p className="mt-1 text-[11px] text-slate-400">
                      {row.count} veces · {row.lossShare.toFixed(1)}% de derrotas
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <div className="flex items-start gap-2 rounded-2xl border border-white/8 bg-slate-950/45 p-4 text-[11px] leading-5 text-slate-400">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-cyan-300" />
            <div>
              <strong className="text-slate-200">Lectura correcta:</strong> {job.result.caveat}
              <div className="mt-1 text-[10px] text-slate-500">
                {audit.limitations.join(" · ")}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
