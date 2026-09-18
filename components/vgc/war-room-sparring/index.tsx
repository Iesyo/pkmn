"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRightLeft,
  Brain,
  Check,
  CircleAlert,
  Crosshair,
  ExternalLink,
  Gamepad2,
  Gauge,
  Loader2,
  MonitorPlay,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Swords,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { parseShowdownPaste } from "@/lib/paste";
import { getMoveData, getSpriteUrl } from "@/lib/pokemon-data";
import type { PokemonSet, TeamVersion } from "@/lib/types";
import type { WarRoomCorpusTeam } from "@/lib/war-room";
import {
  inspectBattleReadyPaste,
  shuffledSparringCandidates,
  sparringCorpusCandidates,
} from "@/lib/war-room-sparring";
import { cn } from "@/lib/utils";

const LOCAL_SERVICE = "/api/battle-lab";
const SHOWDOWN_CLASSIC_BASE = "http://127.0.0.1:8767/play.pokemonshowdown.com/testclient-old.html?~~127.0.0.1:8766";

type LocalHealth = {
  ok: boolean;
  ready: boolean;
  checkpointExists: boolean;
  activeSession: string | null;
};

type SparringMon = {
  species: string;
  name: string;
  hp: number;
  fainted: boolean;
  status: string | null;
  item: string;
  ability: string;
  active: boolean;
};

type SingleAction = {
  kind: "move" | "switch" | "pass" | "other";
  value: string;
  label: string;
  target: number;
  flags: string[];
};

type LegalAction = {
  id: string;
  first: SingleAction;
  second: SingleAction;
};

type BattleState = {
  tag?: string;
  turn?: number;
  finished?: boolean;
  won?: boolean;
  lost?: boolean;
  weather?: string[];
  fields?: string[];
  ownActive?: Array<SparringMon | null>;
  opponentActive?: Array<SparringMon | null>;
  ownTeam?: SparringMon[];
  opponentTeam?: SparringMon[];
};

type NanaTelemetryTrust = {
  trust?: number | null;
  confidence?: number | null;
  teamMemoryBlend?: number | null;
  teamMemoryEffect?: string | null;
};

type NanaTelemetryCandidate = {
  action?: { first?: SingleAction; second?: SingleAction } | null;
  probability?: number | null;
  lightRegretLog?: number | null;
  expectedCounter?: number | null;
};

type NanaTeamMemoryComponent = {
  level?: string;
  key?: string;
  samples?: number;
  trust?: number;
  confidence?: number;
  ready?: boolean;
};

type NanaTelemetry = {
  turn?: number;
  generation?: number;
  reason?: string;
  intervened?: boolean;
  autonomyLevel?: "N2" | "N4" | string;
  lambdaCap?: number | null;
  effectiveLambda?: number | null;
  requiredLambdaCap?: number | null;
  lambdaGap?: number | null;
  predictionConfidence?: number | null;
  confidenceScale?: number | null;
  candidateCountEvaluated?: number;
  n4Shadow?: {
    version?: string;
    eligible?: boolean;
    reason?: string;
    legalTotal?: number;
    referenceKey?: string | null;
    selectedKey?: string | null;
    referenceAction?: { first?: SingleAction; second?: SingleAction } | null;
    selectedAction?: { first?: SingleAction; second?: SingleAction } | null;
    wouldChange?: boolean;
    commonScoreAvailable?: number;
    predictionConfidence?: number;
    safetyGate?: {
      authorized?: boolean;
      reason?: string;
      orderKey?: string;
      representable?: boolean;
      elapsedMs?: number;
    } | null;
    legalOrderMs?: number | null;
    planningMs?: number | null;
    totalDecisionMs?: number | null;
    counterCalibration?: {
      resolved?: boolean;
      reason?: string;
      samples?: number;
      slope?: number;
      r2?: number;
      confidence?: number;
      mapper_id?: string;
    } | null;
  } | null;
  legalOrders?: {
    resolved?: boolean;
    reason?: string;
    contractId?: string;
    totalLegal?: number;
    teacherJointTotal?: number;
    coveredByTeacher?: number;
    teacherCoverage?: number;
    missingFromTeacher?: number | null;
    extraTeacher?: number | null;
    individualCounts?: number[];
    joinedCount?: number;
  } | null;
  candidateFunnel?: {
    jointTotal?: number;
    jointStructured?: number;
    regretMapped?: number;
    branchRegretPassed?: number;
    branchRegretRejected?: number;
    poolAlternatives?: number;
    nurseryRegretPassed?: number;
    counterImproved?: number;
    insideCap?: number;
    counterRelevantAlternatives?: number;
    humanPredictionCandidates?: number;
  } | null;
  discarded?: {
    branchRegret?: NanaTelemetryCandidate | null;
    nurseryRegret?: NanaTelemetryCandidate | null;
    counterMiss?: NanaTelemetryCandidate | null;
  } | null;
  expectedCounterDelta?: number | null;
  margin?: number | null;
  candidate?: NanaTelemetryCandidate | null;
  lightTrust?: NanaTelemetryTrust | null;
  selfTrust?: NanaTelemetryTrust | null;
  interventionsUsed?: number | null;
  interventionBudget?: number | null;
  teamMemory?: {
    eligible?: boolean;
    reason?: string;
    selectedScope?: string | null;
    selectedKey?: string | null;
    samples?: number;
    trust?: number;
    confidence?: number;
    components?: NanaTeamMemoryComponent[];
  } | null;
  teamMemorySummary?: {
    modelVersion?: string | null;
    observations?: number;
    taggedSessions?: number;
    ignoredLegacyTeamSessions?: number;
  } | null;
};

type SparringSession = {
  id: string;
  phase: "starting" | "team-preview" | "native-team-preview" | "waiting-choice" | "native-waiting-choice" | "resolving" | "completed" | "error" | "cancelled";
  error: string;
  opponent: { id: string; label: string; source: string };
  battle: BattleState;
  actions: LegalAction[];
  result: null | {
    winner: "human" | "model" | "tie";
    turns: number;
    battleTag: string;
  };
  events: string[];
  nana?: {
    stage?: number;
    mode?: string;
    autonomyLevel?: string;
    nursery?: {
      lambdaCap?: number | null;
      maxInterventionsPerBattle?: number | null;
      interventionsUsed?: number | null;
      fallback?: string;
      teacherRole?: string;
    };
    telemetry?: NanaTelemetry;
  };
};

type LoadedOpponent = {
  team: WarRoomCorpusTeam;
  paste: string;
};

async function readPayload(response: Response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) as unknown : {};
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
    ? await fetch(`/api/scouting-pastes/${encodeURIComponent(team.savedPasteId)}`, { cache: "no-store" })
    : await fetch("/api/pokepaste-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: team.pokepasteUrl }),
    });
  const payload = await readPayload(response);
  if (!response.ok) throw new Error(errorText(payload, "No pudimos cargar el paste."));
  const paste = team.savedPasteId
    && payload && typeof payload === "object"
    && "item" in payload && payload.item && typeof payload.item === "object"
    && "paste" in payload.item && typeof payload.item.paste === "string"
    ? payload.item.paste
    : payload && typeof payload === "object"
      && "paste" in payload && typeof payload.paste === "string"
      ? payload.paste
      : "";
  if (!paste) throw new Error("El paste llegó vacío.");
  return paste;
}

function actionKey(action: SingleAction) {
  return `${action.kind}|${action.value}|${action.target}|${action.flags.join(",")}`;
}

function uniqueActions(actions: LegalAction[], side: "first" | "second") {
  const seen = new Map<string, SingleAction>();
  for (const candidate of actions) seen.set(actionKey(candidate[side]), candidate[side]);
  return [...seen.values()];
}

function uniqueSingleActions(actions: SingleAction[]) {
  const seen = new Map<string, SingleAction>();
  for (const action of actions) seen.set(actionKey(action), action);
  return [...seen.values()];
}

function cleanActionLabel(action: SingleAction) {
  if (action.kind === "switch") return action.value;
  const [label] = action.label.split(" · ");
  return label || action.value || "Acción";
}

function mechanicKey(action: SingleAction) {
  return action.flags.length ? action.flags.join(" + ") : "Normal";
}

function targetInfo(target: number, battle: BattleState) {
  if (target > 0) {
    const mon = battle.opponentActive?.[target - 1] ?? null;
    return {
      label: mon?.species ? mon.species : `Rival ${target}`,
      detail: "Rival",
      mon,
      opponent: true,
    };
  }
  if (target < 0) {
    const slot = Math.abs(target) - 1;
    const mon = battle.ownActive?.[slot] ?? null;
    return {
      label: mon?.species ? mon.species : `Aliado ${Math.abs(target)}`,
      detail: "Aliado",
      mon,
      opponent: false,
    };
  }
  return { label: "Sin objetivo manual", detail: "Campo", mon: null, opponent: false };
}

function describeAction(action: SingleAction | undefined, battle: BattleState) {
  if (!action) return "Sin elegir";
  if (action.kind === "switch") return `Cambiar a ${action.value}`;
  const mechanic = action.flags.length ? ` · ${action.flags.join(" + ")}` : "";
  const target = action.target ? ` → ${targetInfo(action.target, battle).label}` : "";
  return `${cleanActionLabel(action)}${target}${mechanic}`;
}

function moveTone(type: string | null) {
  switch (type) {
    case "Fire": return "border-orange-300/30 bg-orange-300/[0.09] text-orange-100";
    case "Water": return "border-sky-300/30 bg-sky-300/[0.09] text-sky-100";
    case "Electric": return "border-yellow-300/30 bg-yellow-300/[0.09] text-yellow-100";
    case "Grass": return "border-emerald-300/30 bg-emerald-300/[0.09] text-emerald-100";
    case "Ice": return "border-cyan-200/30 bg-cyan-200/[0.09] text-cyan-50";
    case "Fighting": return "border-red-300/30 bg-red-300/[0.09] text-red-100";
    case "Poison": return "border-fuchsia-300/30 bg-fuchsia-300/[0.09] text-fuchsia-100";
    case "Ground": return "border-amber-300/30 bg-amber-300/[0.09] text-amber-100";
    case "Flying": return "border-indigo-300/30 bg-indigo-300/[0.09] text-indigo-100";
    case "Psychic": return "border-pink-300/30 bg-pink-300/[0.09] text-pink-100";
    case "Bug": return "border-lime-300/30 bg-lime-300/[0.09] text-lime-100";
    case "Rock": return "border-stone-300/30 bg-stone-300/[0.09] text-stone-100";
    case "Ghost": return "border-violet-300/30 bg-violet-300/[0.09] text-violet-100";
    case "Dragon": return "border-purple-300/30 bg-purple-300/[0.09] text-purple-100";
    case "Dark": return "border-slate-400/30 bg-slate-400/[0.09] text-slate-200";
    case "Steel": return "border-zinc-300/30 bg-zinc-300/[0.09] text-zinc-100";
    case "Fairy": return "border-rose-200/30 bg-rose-200/[0.09] text-rose-100";
    default: return "border-white/10 bg-white/[0.04] text-slate-200";
  }
}

function PreviewSetCard({
  set,
  order,
  selectable = false,
  opponent = false,
  onClick,
}: {
  set: PokemonSet;
  order?: number;
  selectable?: boolean;
  opponent?: boolean;
  onClick?: () => void;
}) {
  const className = cn(
    "flex min-h-[104px] items-center gap-3 rounded-2xl border p-3 text-left transition",
    order
      ? "border-cyan-300/35 bg-cyan-300/[0.07]"
      : opponent
        ? "border-rose-300/12 bg-rose-300/[0.025]"
        : "border-white/8 bg-slate-950/45",
    selectable && !order && "hover:border-cyan-300/25 hover:bg-cyan-300/[0.035]",
  );
  const body = (
    <>
      <div className="relative shrink-0">
        <Image src={getSpriteUrl(set.species)} alt={set.species} width={64} height={64} unoptimized className="size-16 object-contain" />
        {order ? <span className="absolute -right-1 -top-1 flex size-6 items-center justify-center rounded-full bg-cyan-300 text-[10px] font-black text-slate-950">{order}</span> : null}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <strong className="truncate text-xs text-white">{set.species}</strong>
          {set.teraType ? <span className="rounded-md border border-white/8 px-1.5 py-0.5 text-[7px] font-bold text-slate-500">Tera {set.teraType}</span> : null}
        </div>
        <p className="mt-0.5 truncate text-[9px] text-slate-500">{set.item} · {set.ability}</p>
        <div className="mt-2 grid grid-cols-2 gap-1">
          {set.moves.slice(0, 4).map((move) => <span key={move.name} className="truncate rounded-md border border-white/6 bg-slate-950/35 px-1.5 py-1 text-[7px] text-slate-500">{move.name}</span>)}
        </div>
      </div>
    </>
  );
  return selectable
    ? <button type="button" className={className} onClick={onClick}>{body}</button>
    : <article className={className}>{body}</article>;
}

function TargetChoice({ action, battle, onSelect }: { action: SingleAction; battle: BattleState; onSelect: () => void }) {
  const target = targetInfo(action.target, battle);
  return (
    <button type="button" onClick={onSelect} className={cn(
      "flex min-h-16 items-center gap-3 rounded-2xl border px-3 py-2.5 text-left transition",
      target.opponent ? "border-rose-300/20 bg-rose-300/[0.04] hover:border-rose-300/35" : "border-cyan-300/20 bg-cyan-300/[0.04] hover:border-cyan-300/35",
    )}>
      {target.mon?.species ? <Image src={getSpriteUrl(target.mon.species)} alt={target.mon.species} width={48} height={48} unoptimized className="size-12 object-contain" /> : <Crosshair className="size-5 text-slate-500" />}
      <div>
        <strong className="block text-[10px] text-white">{target.label}</strong>
        <span className="text-[8px] text-slate-500">{target.detail}{target.mon ? ` · ${Math.round(target.mon.hp)}% HP` : ""}</span>
      </div>
    </button>
  );
}

function ActionPicker({
  label,
  mon,
  actions,
  battle,
  mechanic,
  onMechanicChange,
  onSelect,
}: {
  label: string;
  mon: SparringMon | null | undefined;
  actions: SingleAction[];
  battle: BattleState;
  mechanic: string;
  onMechanicChange: (value: string) => void;
  onSelect: (action: SingleAction) => void;
}) {
  const unique = useMemo(() => uniqueSingleActions(actions), [actions]);
  const moveActions = unique.filter((action) => action.kind === "move");
  const switchActions = unique.filter((action) => action.kind === "switch");
  const otherActions = unique.filter((action) => action.kind !== "move" && action.kind !== "switch");
  const mechanicOptions = [...new Set(moveActions.map(mechanicKey))];
  const effectiveMechanic = mechanicOptions.includes(mechanic)
    ? mechanic
    : mechanicOptions.includes("Normal") ? "Normal" : mechanicOptions[0] ?? "Normal";
  const filteredMoves = moveActions.filter((action) => mechanicKey(action) === effectiveMechanic);
  const moveGroups = new Map<string, SingleAction[]>();
  for (const action of filteredMoves) moveGroups.set(action.value, [...(moveGroups.get(action.value) ?? []), action]);
  const switches = [...new Map(switchActions.map((action) => [action.value, action] as const)).values()];
  const [pendingMove, setPendingMove] = useState("");
  const actionSignature = unique.map(actionKey).sort().join("||");

  useEffect(() => setPendingMove(""), [actionSignature, effectiveMechanic]);
  const pending = pendingMove ? uniqueSingleActions(moveGroups.get(pendingMove) ?? []) : [];

  return (
    <section className="rounded-[22px] border border-white/8 bg-slate-950/35 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {mon?.species ? <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={56} height={56} unoptimized className="size-14 object-contain" /> : null}
          <div>
            <p className="text-[8px] font-black uppercase tracking-[0.14em] text-cyan-300">{label}</p>
            <h3 className="text-sm font-black text-white">¿Qué hará {mon?.species || "este Pokémon"}?</h3>
          </div>
        </div>
        {mechanicOptions.length > 1 ? <div className="flex rounded-xl border border-white/8 bg-slate-950/60 p-1">
          {mechanicOptions.map((option) => <button key={option} type="button" onClick={() => onMechanicChange(option)} className={cn(
            "flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[8px] font-bold transition",
            effectiveMechanic === option ? "bg-violet-300/15 text-violet-100" : "text-slate-500 hover:text-slate-300",
          )}>{option !== "Normal" ? <Sparkles className="size-3" /> : null}{option}</button>)}
        </div> : null}
      </div>

      {moveGroups.size ? <div className="mt-4">
        <p className="text-[8px] font-black uppercase tracking-[0.12em] text-slate-600">Attack</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {[...moveGroups.entries()].map(([move, variants]) => {
            const sample = variants[0];
            const type = getMoveData(sample.value).type;
            return <button key={`${effectiveMechanic}-${move}`} type="button" onClick={() => {
              const choices = uniqueSingleActions(variants);
              if (choices.length === 1 && choices[0].target === 0) onSelect(choices[0]);
              else setPendingMove(move);
            }} className={cn("rounded-xl border px-3 py-3 text-left transition hover:brightness-125", moveTone(type), pendingMove === move && "ring-1 ring-cyan-300/40")}>
              <strong className="block text-[11px]">{cleanActionLabel(sample)}</strong>
              <span className="mt-1 block text-[7px] font-black uppercase opacity-55">{type ?? "Status"}</span>
            </button>;
          })}
        </div>
      </div> : null}

      {pending.length ? <div className="mt-3 rounded-2xl border border-cyan-300/10 bg-cyan-300/[0.025] p-3">
        <p className="flex items-center gap-2 text-[9px] font-bold text-cyan-100"><Crosshair className="size-3.5" />Elige objetivo para {cleanActionLabel(pending[0])}</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">{pending.map((action) => <TargetChoice key={actionKey(action)} action={action} battle={battle} onSelect={() => onSelect(action)} />)}</div>
      </div> : null}

      {switches.length ? <div className="mt-4">
        <p className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.12em] text-slate-600"><ArrowRightLeft className="size-3" />Switch</p>
        <div className="mt-2 flex flex-wrap gap-2">{switches.map((action) => <button key={actionKey(action)} type="button" onClick={() => onSelect(action)} className="flex min-w-32 items-center gap-2 rounded-xl border border-white/8 bg-slate-950/50 px-2.5 py-2 text-left transition hover:border-cyan-300/25">
          <Image src={getSpriteUrl(action.value)} alt={action.value} width={38} height={38} unoptimized className="size-9 object-contain" />
          <span className="truncate text-[9px] font-bold text-slate-300">{action.value}</span>
        </button>)}</div>
      </div> : null}

      {otherActions.length ? <div className="mt-3 flex flex-wrap gap-2">{otherActions.map((action) => <button key={actionKey(action)} type="button" onClick={() => onSelect(action)} className="rounded-xl border border-white/8 bg-slate-950/50 px-3 py-2 text-[9px] text-slate-400">{cleanActionLabel(action)}</button>)}</div> : null}
    </section>
  );
}

function SelectedAction({ label, mon, action, battle, onChange }: { label: string; mon: SparringMon | null | undefined; action: SingleAction; battle: BattleState; onChange: () => void }) {
  return <div className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-300/12 bg-emerald-300/[0.035] px-3 py-2.5">
    <div className="flex min-w-0 items-center gap-2.5">
      {mon?.species ? <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={40} height={40} unoptimized className="size-10 object-contain" /> : null}
      <div className="min-w-0"><p className="text-[7px] font-black uppercase tracking-[0.12em] text-emerald-300">{label}</p><strong className="block truncate text-[10px] text-white">{describeAction(action, battle)}</strong></div>
    </div>
    <button type="button" onClick={onChange} className="shrink-0 rounded-lg border border-white/8 px-2 py-1 text-[8px] text-slate-500 hover:text-white">Cambiar</button>
  </div>;
}

function telemetryNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function telemetryPercent(value: unknown) {
  const number = telemetryNumber(value);
  return number === null ? "—" : `${Math.round(number * 100)}%`;
}

function telemetryLambda(value: unknown) {
  const number = telemetryNumber(value);
  return number === null ? "—" : number.toFixed(3);
}

function telemetryReason(reason: string | undefined) {
  switch (reason) {
    case "waiting-first-decision": return "Esperando primera decisión";
    case "no-live-candidate-inside-nursery-cap": return "Alternativas fuera del cap N2";
    case "nursery-live-near-light": return "Nana encontró una intervención válida";
    case "nursery-per-battle-budget-exhausted": return "Presupuesto de intervención agotado";
    case "human-prediction-confidence-low": return "Predicción humana con poca confianza";
    case "light-critic-high-trust-veto": return "LIGHT conserva el control por alta confianza";
    case "nana-self-low-trust-veto": return "Nana se veta por baja confianza propia";
    default: return reason || "Sin decisión registrada";
  }
}

function TelemetryTrust({ label, value }: { label: string; value: NanaTelemetryTrust | null | undefined }) {
  const trust = telemetryNumber(value?.trust);
  const confidence = telemetryNumber(value?.confidence);
  const width = trust === null ? 0 : Math.max(0, Math.min(100, trust * 100));

  return <div className="rounded-2xl border border-white/10 bg-slate-950/55 p-4">
    <div className="flex items-center justify-between gap-3">
      <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-300">{label}</span>
      <strong className="font-mono text-xl font-black text-white">{telemetryPercent(trust)}</strong>
    </div>
    <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/8">
      <div className="h-full rounded-full bg-cyan-300/80 transition-all" style={{ width: `${width}%` }} />
    </div>
    <p className="mt-2 text-[11px] text-slate-400">Confianza: <strong className="text-slate-200">{telemetryPercent(confidence)}</strong></p>
  </div>;
}

function TelemetryMiniStat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-white/8 bg-white/[0.025] px-3 py-2.5">
    <p className="text-[11px] font-semibold text-slate-400">{label}</p>
    <p className="mt-1 font-mono text-sm font-bold text-slate-100">{value}</p>
  </div>;
}

function FunnelRow({ label, value, total, active = false }: { label: string; value: number; total: number; active?: boolean }) {
  const width = total > 0 ? Math.max(3, Math.min(100, (value / total) * 100)) : 0;
  return <div>
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className={active ? "font-bold text-amber-100" : "text-slate-300"}>{label}</span>
      <strong className={cn("font-mono", active ? "text-amber-100" : "text-white")}>{value}</strong>
    </div>
    <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-white/8">
      <div className={cn("h-full rounded-full transition-all", active ? "bg-amber-300/80" : "bg-cyan-300/65")} style={{ width: `${width}%` }} />
    </div>
  </div>;
}

function NanaTelemetryPanel({ session }: { session: SparringSession }) {
  const telemetry = session.nana?.telemetry;
  const nursery = session.nana?.nursery;
  const isN4 = session.nana?.autonomyLevel === "N4"
    || telemetry?.autonomyLevel === "N4"
    || session.nana?.mode === "full-amiibo-live-v1";
  const teamMemory = telemetry?.teamMemory;
  const exactMemory = teamMemory?.components?.find((item) => item.level === "exactTeam");
  const exactSamples = exactMemory?.samples ?? 0;
  const legalOrders = telemetry?.legalOrders;
  const n4 = telemetry?.n4Shadow;
  const gateOk = n4?.safetyGate?.authorized === true;
  const decisionMs = telemetryNumber(n4?.totalDecisionMs);
  const legalCount = n4?.legalTotal ?? legalOrders?.totalLegal ?? 0;
  const scoredCount = n4?.commonScoreAvailable ?? 0;
  const reason = telemetryReason(telemetry?.reason);
  const decisionLabel = isN4
    ? n4?.eligible === false
      ? "Fallback a LIGHT"
      : n4?.wouldChange
        ? "Cambió vs LIGHT"
        : "Siguió referencia"
    : reason;
  const lambdaCap = telemetryNumber(telemetry?.lambdaCap ?? nursery?.lambdaCap);
  const required = telemetryNumber(telemetry?.requiredLambdaCap);
  const funnel = telemetry?.candidateFunnel;
  const lightAlternatives = Math.max(0, (funnel?.jointTotal ?? 0) - 1);
  const branchPassed = funnel?.poolAlternatives ?? 0;
  const regretPassed = funnel?.nurseryRegretPassed ?? 0;
  const counterImproved = funnel?.counterImproved ?? 0;
  const insideCapCount = funnel?.insideCap ?? 0;

  return <aside className="rounded-[22px] border border-violet-300/18 bg-gradient-to-b from-violet-300/[0.055] via-slate-900/80 to-slate-950/85 p-4 shadow-xl shadow-black/15 xl:sticky xl:top-4">
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <Activity className="size-4 shrink-0 text-violet-300" />
        <div className="min-w-0">
          <p className="text-[11px] font-black uppercase tracking-[0.1em] text-violet-200">Telemetría Nana</p>
          <h3 className="truncate text-base font-black text-white">{isN4 ? "Full Amiibo N4" : "Nursery N2"}</h3>
        </div>
      </div>
      <Badge variant="outline" className={cn(
        "shrink-0 border-white/10 px-2.5 py-1 text-[11px] font-bold",
        isN4 ? "bg-emerald-300/[0.06] text-emerald-100" : "text-slate-300",
      )}>{isN4 ? "LIVE" : "OBSERVANDO"}</Badge>
    </div>

    <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-bold">
      {isN4 ? <span className="rounded-lg border border-emerald-300/15 bg-emerald-300/[0.05] px-2 py-1 text-emerald-100">Rueditas OFF</span> : null}
      <span className={cn(
        "rounded-lg border px-2 py-1",
        gateOk ? "border-emerald-300/15 bg-emerald-300/[0.05] text-emerald-100" : "border-amber-300/15 bg-amber-300/[0.05] text-amber-100",
      )}>Gate {gateOk ? "OK" : "fallback"}</span>
      <span className="rounded-lg border border-white/8 bg-white/[0.025] px-2 py-1 text-slate-300">{legalCount} legales</span>
      <span className="rounded-lg border border-white/8 bg-white/[0.025] px-2 py-1 font-mono text-slate-300">{decisionMs === null ? "— ms" : decisionMs.toFixed(1) + " ms"}</span>
      <span className="rounded-lg border border-violet-300/12 bg-violet-300/[0.035] px-2 py-1 text-violet-100">Mem {exactSamples}/3</span>
    </div>

    <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-white/8 bg-slate-950/35 px-3 py-2.5">
      <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">Turno {telemetry?.turn ?? session.battle.turn ?? 0}</span>
      <Badge variant="outline" className={cn(
        "border-white/8 px-2 py-0.5 text-[11px]",
        n4?.wouldChange ? "text-fuchsia-100" : "text-slate-300",
      )}>{decisionLabel}</Badge>
    </div>

    <details className="group mt-3 rounded-xl border border-white/8 bg-white/[0.02]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-xs font-bold text-slate-300">
        <span>Diagnóstico</span>
        <span className="text-[11px] font-normal text-slate-500 group-open:hidden">ver</span>
        <span className="hidden text-[11px] font-normal text-slate-500 group-open:inline">ocultar</span>
      </summary>
      <div className="space-y-3 border-t border-white/7 px-3 pb-3 pt-3">
        <div className="grid grid-cols-2 gap-2">
          <TelemetryMiniStat label="Predicción" value={telemetryPercent(telemetry?.predictionConfidence)} />
          <TelemetryMiniStat label="Con score" value={String(scoredCount)} />
          <TelemetryMiniStat label="Cobertura teacher" value={telemetryPercent(legalOrders?.teacherCoverage)} />
          <TelemetryMiniStat label="Counter R²" value={telemetryNumber(n4?.counterCalibration?.r2)?.toFixed(2) ?? "—"} />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <TelemetryTrust label="LIGHT trust" value={telemetry?.lightTrust} />
          <TelemetryTrust label="Nana self-trust" value={telemetry?.selfTrust} />
        </div>

        <div className="rounded-xl border border-violet-300/10 bg-violet-300/[0.025] p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Brain className="size-4 text-violet-300" />
              <span className="text-[11px] font-black uppercase tracking-[0.08em] text-violet-100">TeamMemory</span>
            </div>
            <strong className="font-mono text-sm text-white">{exactSamples}/3</strong>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-slate-400">
            {telemetry?.teamMemorySummary?.observations ?? 0} observaciones · {telemetry?.teamMemorySummary?.taggedSessions ?? 0} sesiones útiles · {teamMemory?.selectedScope || "cold start"}
          </p>
        </div>

        {isN4 ? <div className="rounded-xl border border-emerald-300/10 bg-emerald-300/[0.025] p-3 text-[11px] leading-5 text-slate-400">
          <p><strong className="text-emerald-100">Fuente legal:</strong> {legalOrders?.resolved === false ? legalOrders.reason || "no resuelta" : String(legalCount) + " órdenes · " + String(legalOrders?.missingFromTeacher ?? 0) + " fuera del catálogo teacher"}</p>
          <p><strong className="text-emerald-100">SafetyGate:</strong> {gateOk ? "autorizado" : n4?.safetyGate?.reason || "fallback"} · planner {telemetryNumber(n4?.planningMs)?.toFixed(1) ?? "—"} ms</p>
          <p><strong className="text-emerald-100">Scorer:</strong> {n4?.counterCalibration?.resolved ? "counter READY · " + String(n4.counterCalibration.samples ?? 0) + " muestras" : n4?.counterCalibration?.reason || "counter sin resolver"}</p>
        </div> : <div className="rounded-xl border border-amber-300/10 bg-amber-300/[0.025] p-3">
          <div className="grid grid-cols-2 gap-2">
            <TelemetryMiniStat label="λ actual" value={telemetryLambda(lambdaCap)} />
            <TelemetryMiniStat label="λ requerido" value={telemetryLambda(required)} />
          </div>
          <div className="mt-3 space-y-2">
            <FunnelRow label="Alternativas LIGHT" value={lightAlternatives} total={Math.max(1, lightAlternatives)} />
            <FunnelRow label="Pasan rama" value={branchPassed} total={Math.max(1, lightAlternatives)} />
            <FunnelRow label="Pasan regret" value={regretPassed} total={Math.max(1, lightAlternatives)} />
            <FunnelRow label="Mejoran counter" value={counterImproved} total={Math.max(1, lightAlternatives)} />
            <FunnelRow label="Dentro del cap" value={insideCapCount} total={Math.max(1, lightAlternatives)} />
          </div>
        </div>}

        <p className="text-[11px] leading-5 text-slate-500">Raw: {telemetry?.reason || "sin decisión"} · LIGHT sigue disponible como advisor/fallback.</p>
        <p className="text-[11px] leading-5 text-slate-600">Las acciones concretas de Nana se ocultan durante la batalla para no revelar la jugada del rival.</p>
      </div>
    </details>
  </aside>;
}

type CoachAdvice = {
  adviceId: string;
  text: string;
  status: "active" | "revoked" | "superseded";
  scope?: { level?: string; species?: string };
  condition?: { kind?: string; mechanic?: string };
  effect?: { kind?: string; strength?: number; mode?: string };
  evidence?: { applied?: number; skipped?: number };
};

type CoachMemoryResponse = {
  advices?: CoachAdvice[];
};

function CoachMemoryPanel() {
  const [memory, setMemory] = useState<CoachMemoryResponse>({});
  const [text, setText] = useState("");
  const [species, setSpecies] = useState("");
  const [mechanic, setMechanic] = useState("Mega");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const active = (memory.advices ?? []).filter((item) => item.status === "active");

  async function refresh() {
    try {
      const response = await fetch(`${LOCAL_SERVICE}/nana/advice`, { cache: "no-store" });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No pudimos leer CoachMemory."));
      setMemory(payload as CoachMemoryResponse);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "CoachMemory no disponible.");
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function createAdvice() {
    if (!text.trim() || !species.trim()) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${LOCAL_SERVICE}/nana/advice`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text: text.trim(),
          species: species.trim(),
          mechanic,
          strength: 0.20,
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No pudimos guardar el tip."));
      if (payload && typeof payload === "object" && "memory" in payload) {
        setMemory(payload.memory as CoachMemoryResponse);
      } else {
        await refresh();
      }
      setText("");
      setSpecies("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No pudimos guardar el tip.");
    } finally {
      setBusy(false);
    }
  }

  async function revokeAdvice(adviceId: string) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${LOCAL_SERVICE}/nana/advice/${encodeURIComponent(adviceId)}/revoke`, {
        method: "POST",
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No pudimos retirar el tip."));
      if (payload && typeof payload === "object" && "memory" in payload) {
        setMemory(payload.memory as CoachMemoryResponse);
      } else {
        await refresh();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No pudimos retirar el tip.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="rounded-[22px] border border-amber-300/14 bg-amber-300/[0.025] p-4">
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-[11px] font-black uppercase tracking-[0.1em] text-amber-100">CoachMemory</p>
        <h3 className="mt-0.5 text-sm font-black text-white">Tips para Nana</h3>
      </div>
      <Badge variant="outline" className="border-white/8 px-2 py-1 text-[11px] text-slate-300">{active.length} activos</Badge>
    </div>

    {active.length ? <div className="mt-3 space-y-2">
      {active.slice(0, 4).map((advice) => <div key={advice.adviceId} className="rounded-xl border border-white/8 bg-slate-950/35 px-3 py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold leading-5 text-slate-100">{advice.text}</p>
            <p className="mt-1 text-[11px] text-slate-500">
              {advice.scope?.species || "species"} · {advice.condition?.mechanic || "mechanic"} · soft
              {advice.evidence?.applied ? ` · usado ${advice.evidence.applied}×` : ""}
            </p>
          </div>
          <button type="button" disabled={busy} onClick={() => void revokeAdvice(advice.adviceId)} className="shrink-0 rounded-lg border border-white/8 px-2 py-1 text-[11px] text-slate-500 hover:text-rose-200">Retirar</button>
        </div>
      </div>)}
    </div> : <p className="mt-3 text-xs leading-5 text-slate-500">Aún no hay tips activos.</p>}

    <details className="group mt-3 rounded-xl border border-white/8 bg-slate-950/30">
      <summary className="cursor-pointer list-none px-3 py-2.5 text-xs font-bold text-amber-100">+ Nuevo tip</summary>
      <div className="space-y-2.5 border-t border-white/7 px-3 pb-3 pt-3">
        <textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={500} placeholder="Ej. Con Garchomp, si Mega es legal, priorizar Mega salvo razón táctica fuerte." className="min-h-20 w-full resize-y rounded-xl border border-white/8 bg-slate-950/55 px-3 py-2.5 text-xs leading-5 text-white outline-none placeholder:text-slate-600 focus:border-amber-300/25" />
        <div className="grid grid-cols-[1fr_120px] gap-2">
          <input value={species} onChange={(event) => setSpecies(event.target.value)} placeholder="Especie: Garchomp" className="rounded-xl border border-white/8 bg-slate-950/55 px-3 py-2 text-xs text-white outline-none placeholder:text-slate-600 focus:border-amber-300/25" />
          <select value={mechanic} onChange={(event) => setMechanic(event.target.value)} className="rounded-xl border border-white/8 bg-slate-950/55 px-3 py-2 text-xs text-white outline-none">
            <option>Mega</option>
            <option>Tera</option>
            <option>Z-Move</option>
            <option>Dynamax</option>
          </select>
        </div>
        <p className="rounded-lg border border-amber-300/10 bg-amber-300/[0.025] px-2.5 py-2 text-[11px] leading-4 text-slate-400">
          Regla ejecutable: si <strong className="text-slate-200">{species.trim() || "la especie"}</strong> está activa y <strong className="text-slate-200">{mechanic}</strong> es legal, Nana le da una preferencia soft. SafetyGate y el resto del scorer pueden elegir otra jugada.
        </p>
        <Button type="button" onClick={() => void createAdvice()} disabled={busy || !text.trim() || !species.trim()} className="w-full bg-amber-200 text-slate-950 hover:bg-amber-100">{busy ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}Confirmar y guardar tip</Button>
      </div>
    </details>
    {error ? <p className="mt-2 text-[11px] leading-4 text-rose-200">{error}</p> : null}
  </section>;
}

export function WarRoomSparring({ team, corpusTeams }: { team: TeamVersion; corpusTeams: WarRoomCorpusTeam[] }) {
  const [health, setHealth] = useState<LocalHealth | null>(null);
  const [healthError, setHealthError] = useState("");
  const [opponent, setOpponent] = useState<LoadedOpponent | null>(null);
  const [session, setSession] = useState<SparringSession | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [rejected, setRejected] = useState(0);
  const [previewOrder, setPreviewOrder] = useState<number[]>([]);
  const [selectedFirst, setSelectedFirst] = useState("");
  const [selectedSecond, setSelectedSecond] = useState("");
  const [firstMechanic, setFirstMechanic] = useState("Normal");
  const [secondMechanic, setSecondMechanic] = useState("Normal");
  const [submitting, setSubmitting] = useState(false);
  const [viewerLoaded, setViewerLoaded] = useState(false);

  const ownReport = useMemo(() => inspectBattleReadyPaste(team.paste), [team.paste]);
  const candidates = useMemo(() => sparringCorpusCandidates(corpusTeams), [corpusTeams]);
  const opponentPokemon = useMemo(() => {
    if (!opponent?.paste) return [] as PokemonSet[];
    try { return parseShowdownPaste(opponent.paste); } catch { return [] as PokemonSet[]; }
  }, [opponent?.paste]);

  async function checkHealth() {
    try {
      const response = await fetch(`${LOCAL_SERVICE}/health`, { cache: "no-store" });
      const payload = await readPayload(response);
      if (!response.ok || !payload || typeof payload !== "object" || !("ok" in payload)) throw new Error("Respuesta inválida.");
      setHealth(payload as LocalHealth);
      setHealthError("");
    } catch {
      setHealth(null);
      setHealthError("Battle Lab local no responde en 127.0.0.1:8765.");
    }
  }

  useEffect(() => { void checkHealth(); }, []);
  useEffect(() => {
    if (!session || ["completed", "error", "cancelled"].includes(session.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}`, { cache: "no-store" });
        const payload = await readPayload(response);
        if (response.ok) setSession(payload as SparringSession);
      } catch { /* keep last state */ }
    }, 400);
    return () => window.clearInterval(timer);
  }, [session?.id, session?.phase]);
  useEffect(() => {
    setSelectedFirst("");
    setSelectedSecond("");
    setFirstMechanic("Normal");
    setSecondMechanic("Normal");
  }, [session?.actions?.[0]?.id]);
  useEffect(() => setViewerLoaded(false), [session?.battle.tag]);

  async function pickBattleReadyOpponent() {
    let skipped = 0;
    for (const candidate of shuffledSparringCandidates(corpusTeams)) {
      try {
        const paste = await loadExactPaste(candidate);
        if (!inspectBattleReadyPaste(paste).ready) { skipped += 1; continue; }
        setRejected(skipped);
        return { team: candidate, paste };
      } catch { skipped += 1; }
    }
    setRejected(skipped);
    throw new Error("No encontramos un rival con paste completo en VGCPastes o Mis pastes.");
  }

  async function startBattle() {
    if (!ownReport.ready) {
      setStartError(`Tu Team todavía no es battle-ready: ${ownReport.issues.slice(0, 3).join(" ")}`);
      return;
    }
    setStarting(true);
    setStartError("");
    setSession(null);
    setPreviewOrder([]);
    setOpponent(null);
    try {
      const picked = await pickBattleReadyOpponent();
      setOpponent(picked);
      const response = await fetch(`${LOCAL_SERVICE}/sparring`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          teamPaste: team.paste,
          opponentPaste: picked.paste,
          opponent: { id: picked.team.id, label: picked.team.playerName || picked.team.id, source: picked.team.source },
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "Battle Lab rechazó la partida."));
      setSession(payload as SparringSession);
      await checkHealth();
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "No pudimos iniciar el Sparring.");
    } finally { setStarting(false); }
  }

  function togglePreview(slot: number) {
    setPreviewOrder((current) => current.includes(slot)
      ? current.filter((value) => value !== slot)
      : current.length >= 4 ? current : [...current, slot]);
  }

  async function submitPreview() {
    if (!session || previewOrder.length !== 4) return;
    setSubmitting(true);
    try {
      const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}/team-preview`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ order: previewOrder }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "No pudimos enviar el Team Preview."));
      setSession(payload as SparringSession);
    } catch (error) { setStartError(error instanceof Error ? error.message : "Falló Team Preview."); }
    finally { setSubmitting(false); }
  }

  const firstActions = session?.phase === "waiting-choice" ? uniqueActions(session.actions, "first") : [];
  const selectedFirstAction = firstActions.find((action) => actionKey(action) === selectedFirst);
  const compatible = selectedFirst && session ? session.actions.filter((entry) => actionKey(entry.first) === selectedFirst) : [];
  const secondActions = uniqueActions(compatible, "second");
  const selectedSecondAction = secondActions.find((action) => actionKey(action) === selectedSecond);
  const choiceCandidate = session?.actions.find((entry) => actionKey(entry.first) === selectedFirst && actionKey(entry.second) === selectedSecond);

  async function submitTurn() {
    if (!session || !choiceCandidate) return;
    setSubmitting(true);
    setStartError("");
    try {
      const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}/choice`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ choiceId: choiceCandidate.id }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "Showdown rechazó la jugada."));
      setSession(payload as SparringSession);
      setSelectedFirst("");
      setSelectedSecond("");
    } catch (error) { setStartError(error instanceof Error ? error.message : "No pudimos enviar la jugada."); }
    finally { setSubmitting(false); }
  }

  const viewerUrl = session?.battle.tag ? `${SHOWDOWN_CLASSIC_BASE}#${session.battle.tag}` : "";
  const resultLabel = session?.result?.winner === "human" ? "Victoria" : session?.result?.winner === "model" ? "Battle Lab gana" : "Empate";

  return <div className="space-y-4">
    <section className="rounded-[24px] border border-cyan-300/12 bg-gradient-to-br from-cyan-300/[0.055] via-slate-900/55 to-violet-300/[0.04] p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2"><Gamepad2 className="size-5 text-cyan-300" /><p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Sparring interactivo</p><Badge variant="outline" className={cn("text-[8px]", health ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200")}>{health ? health.ready ? "Battle Lab listo" : "Loopback conectado" : "Servicio offline"}</Badge></div>
          <h2 className="mt-2 text-xl font-black text-white">Tú contra LIGHT M-C</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">La batalla visual y el log los renderiza el cliente clásico oficial de Pokémon Showdown; War Room conserva la selección de rival y tus decisiones legales.</p>
        </div>
        <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" onClick={() => void checkHealth()} className="gap-2 border-white/10 bg-slate-950/45"><RefreshCw className="size-4" />Revisar servicio</Button><Button type="button" onClick={() => void startBattle()} disabled={starting || !ownReport.ready || !candidates.length} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{starting ? <Loader2 className="size-4 animate-spin" /> : <Swords className="size-4" />}{session ? "Nuevo rival" : "Buscar rival y pelear"}</Button></div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-[9px]"><Badge variant="outline" className={ownReport.ready ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200"}>{ownReport.ready ? "Tu Team · completo" : `Tu Team · ${ownReport.issues.length} huecos`}</Badge><Badge variant="outline" className="border-white/8 text-slate-400">{candidates.length} candidatos con fuente exacta</Badge>{rejected ? <Badge variant="outline" className="border-amber-300/15 text-amber-200">{rejected} incompletos saltados</Badge> : null}{opponent ? <Badge variant="outline" className="border-violet-300/15 text-violet-200">Rival: {opponent.team.playerName || opponent.team.id}</Badge> : null}</div>
      {healthError ? <p className="mt-3 flex items-center gap-2 text-[10px] text-rose-200"><CircleAlert className="size-3.5" />{healthError}</p> : null}
      {startError ? <p className="mt-2 flex items-start gap-2 rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-3 py-2 text-[10px] leading-4 text-rose-100"><CircleAlert className="mt-0.5 size-3.5 shrink-0" />{startError}</p> : null}
    </section>

    {session?.phase === "team-preview" ? <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
      <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Team Preview · Open Team Sheet</p>
      <h2 className="mt-1 text-lg font-black text-white">Elige tus cuatro viendo ambos equipos</h2>
      <p className="mt-1 text-[10px] text-slate-600">1-2 serán lead · 3-4 backline. El rival usa el paste exacto completo.</p>
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <div><p className="mb-2 text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300">Tu equipo</p><div className="grid gap-2 md:grid-cols-2">{team.pokemon.map((set, index) => { const slot = index + 1; const order = previewOrder.indexOf(slot); return <PreviewSetCard key={set.id} set={set} selectable order={order >= 0 ? order + 1 : undefined} onClick={() => togglePreview(slot)} />; })}</div></div>
        <div><p className="mb-2 text-[9px] font-black uppercase tracking-[0.14em] text-rose-300">Equipo rival · {session.opponent.label || session.opponent.id}</p><div className="grid gap-2 md:grid-cols-2">{opponentPokemon.map((set) => <PreviewSetCard key={`opp-${set.id}`} set={set} opponent />)}</div></div>
      </div>
      <Button type="button" onClick={() => void submitPreview()} disabled={previewOrder.length !== 4 || submitting} className="mt-5 gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Confirmar Bring 4</Button>
    </section> : null}

    {session && session.phase !== "starting" && session.phase !== "team-preview" ? <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px] 2xl:grid-cols-[minmax(0,1fr)_400px] xl:items-start">
      <section className="overflow-hidden rounded-[24px] border border-white/8 bg-slate-900/45">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/7 bg-slate-950/45 px-5 py-3">
          <div className="flex items-center gap-2"><MonitorPlay className="size-4 text-cyan-300" /><div><p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300">Pokémon Showdown · batalla real</p><p className="text-[9px] text-slate-500">Animaciones y log vienen directamente del room local.</p></div></div>
          <div className="flex items-center gap-2"><Badge variant="outline" className={viewerLoaded ? "border-emerald-300/15 text-emerald-200" : "border-amber-300/15 text-amber-200"}>{viewerLoaded ? "Renderer cargado" : "Cargando renderer"}</Badge>{viewerUrl ? <a href={viewerUrl} target="_blank" rel="noreferrer" className="flex items-center gap-1 rounded-lg border border-white/8 px-2.5 py-1.5 text-[8px] text-slate-400 hover:text-white"><ExternalLink className="size-3" />Abrir aparte</a> : null}</div>
        </div>
        {viewerUrl ? <iframe key={viewerUrl} src={viewerUrl} title="Pokémon Showdown battle renderer" onLoad={() => setViewerLoaded(true)} className="h-[720px] w-full bg-[#444]" allow="autoplay" /> : <div className="flex h-72 items-center justify-center gap-2 text-xs text-slate-500"><Loader2 className="size-4 animate-spin text-cyan-300" />Esperando que Showdown publique el room…</div>}
        <div className="border-t border-white/7 px-5 py-2 text-[8px] text-slate-600">Renderer externo local: Pokémon Showdown Client AGPLv3, checkout sin modificar y separado del código de War Room.</div>
      </section>
      <div className="space-y-3"><NanaTelemetryPanel session={session} /><CoachMemoryPanel /></div>
      </div>

      <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
        {session.phase === "waiting-choice" ? <div className="space-y-3">
          <div className="mb-2"><p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300">Tus decisiones · turno {session.battle.turn ?? 0}</p><p className="text-[9px] text-slate-600">El campo y el log están arriba; aquí eliges exactamente como en Showdown, pero la legalidad sale de `battle.valid_orders`.</p></div>
          {selectedFirstAction ? <SelectedAction label="Pokémon izquierdo" mon={session.battle.ownActive?.[0]} action={selectedFirstAction} battle={session.battle} onChange={() => { setSelectedFirst(""); setSelectedSecond(""); }} /> : <ActionPicker label="Pokémon izquierdo" mon={session.battle.ownActive?.[0]} actions={firstActions} battle={session.battle} mechanic={firstMechanic} onMechanicChange={setFirstMechanic} onSelect={(action) => { setSelectedFirst(actionKey(action)); setSelectedSecond(""); setSecondMechanic("Normal"); }} />}
          {selectedFirstAction ? selectedSecondAction ? <SelectedAction label="Pokémon derecho" mon={session.battle.ownActive?.[1]} action={selectedSecondAction} battle={session.battle} onChange={() => setSelectedSecond("")} /> : <ActionPicker label="Pokémon derecho" mon={session.battle.ownActive?.[1]} actions={secondActions} battle={session.battle} mechanic={secondMechanic} onMechanicChange={setSecondMechanic} onSelect={(action) => setSelectedSecond(actionKey(action))} /> : <div className="rounded-2xl border border-dashed border-white/7 px-4 py-5 text-center text-[9px] text-slate-700">Elige primero la acción del Pokémon izquierdo.</div>}
          {choiceCandidate && selectedFirstAction && selectedSecondAction ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.03] p-3"><div><p className="text-[8px] font-black uppercase tracking-[0.12em] text-cyan-300">Turno listo</p><p className="mt-1 text-[9px] text-slate-500">Showdown validó la combinación.</p></div><Button type="button" onClick={() => void submitTurn()} disabled={submitting} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Confirmar turno</Button></div> : null}
        </div> : session.phase === "resolving" ? <div className="flex items-center justify-center gap-2 py-10 text-xs text-slate-400"><Loader2 className="size-4 animate-spin text-cyan-300" />Showdown está resolviendo el turno; mira la animación arriba…</div> : session.phase === "completed" ? <div className="py-8 text-center"><p className={cn("text-2xl font-black", session.result?.winner === "human" ? "text-emerald-300" : session.result?.winner === "model" ? "text-rose-300" : "text-amber-300")}>{resultLabel}</p><p className="mt-2 text-xs text-slate-500">{session.result?.turns} turnos · {session.result?.battleTag}</p></div> : session.phase === "error" ? <div className="rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-4 py-3 text-xs text-rose-100">{session.error}</div> : null}
      </section>
    </div> : null}
  </div>;
}