"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRightLeft,
  Bot,
  Check,
  CircleAlert,
  Crosshair,
  Gamepad2,
  Loader2,
  RefreshCw,
  Sparkles,
  Swords,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { parseShowdownPaste } from "@/lib/paste";
import { getMoveData, getSpriteUrl, toId } from "@/lib/pokemon-data";
import type { PokemonSet, TeamVersion } from "@/lib/types";
import type { WarRoomCorpusTeam } from "@/lib/war-room";
import {
  inspectBattleReadyPaste,
  shuffledSparringCandidates,
  sparringCorpusCandidates,
} from "@/lib/war-room-sparring";
import { cn } from "@/lib/utils";

const LOCAL_SERVICE = "/api/battle-lab";

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

type SparringSession = {
  id: string;
  phase: "starting" | "team-preview" | "waiting-choice" | "resolving" | "completed" | "error" | "cancelled";
  error: string;
  opponent: { id: string; label: string; source: string };
  battle: {
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
  actions: LegalAction[];
  result: null | {
    winner: "human" | "model" | "tie";
    turns: number;
    battleTag: string;
  };
  events: string[];
};

type LoadedOpponent = {
  team: WarRoomCorpusTeam;
  paste: string;
};

type BattleState = SparringSession["battle"];

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
  for (const candidate of actions) {
    const action = candidate[side];
    seen.set(actionKey(action), action);
  }
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

function moveTone(type: string | null) {
  switch (type) {
    case "Fire": return "border-orange-300/25 bg-orange-300/[0.075] text-orange-100";
    case "Water": return "border-sky-300/25 bg-sky-300/[0.075] text-sky-100";
    case "Electric": return "border-yellow-300/25 bg-yellow-300/[0.075] text-yellow-100";
    case "Grass": return "border-emerald-300/25 bg-emerald-300/[0.075] text-emerald-100";
    case "Ice": return "border-cyan-200/25 bg-cyan-200/[0.075] text-cyan-50";
    case "Fighting": return "border-red-300/25 bg-red-300/[0.075] text-red-100";
    case "Poison": return "border-fuchsia-300/25 bg-fuchsia-300/[0.075] text-fuchsia-100";
    case "Ground": return "border-amber-300/25 bg-amber-300/[0.075] text-amber-100";
    case "Flying": return "border-indigo-300/25 bg-indigo-300/[0.075] text-indigo-100";
    case "Psychic": return "border-pink-300/25 bg-pink-300/[0.075] text-pink-100";
    case "Bug": return "border-lime-300/25 bg-lime-300/[0.075] text-lime-100";
    case "Rock": return "border-stone-300/25 bg-stone-300/[0.075] text-stone-100";
    case "Ghost": return "border-violet-300/25 bg-violet-300/[0.075] text-violet-100";
    case "Dragon": return "border-purple-300/25 bg-purple-300/[0.075] text-purple-100";
    case "Dark": return "border-slate-400/25 bg-slate-400/[0.075] text-slate-200";
    case "Steel": return "border-zinc-300/25 bg-zinc-300/[0.075] text-zinc-100";
    case "Fairy": return "border-rose-200/25 bg-rose-200/[0.075] text-rose-100";
    default: return "border-white/10 bg-white/[0.035] text-slate-200";
  }
}

function targetInfo(target: number, battle: BattleState) {
  if (target > 0) {
    const mon = battle.opponentActive?.[target - 1] ?? null;
    return {
      title: mon?.species ? `Rival · ${mon.species}` : `Rival · posición ${target}`,
      short: mon?.species || `Rival ${target}`,
      mon,
      opponent: true,
    };
  }
  if (target < 0) {
    const slot = Math.abs(target) - 1;
    const mon = battle.ownActive?.[slot] ?? null;
    return {
      title: mon?.species ? `Aliado · ${mon.species}` : `Aliado · posición ${Math.abs(target)}`,
      short: mon?.species || `Aliado ${Math.abs(target)}`,
      mon,
      opponent: false,
    };
  }
  return { title: "Sin objetivo manual", short: "Sin objetivo", mon: null, opponent: false };
}

function describeAction(action: SingleAction | undefined, battle: BattleState) {
  if (!action) return "Sin elegir";
  if (action.kind === "switch") return `Cambiar → ${action.value}`;
  if (action.kind === "pass") return "Pasar";
  const mechanic = action.flags.length ? ` · ${action.flags.join(" + ")}` : "";
  const target = action.target ? ` → ${targetInfo(action.target, battle).short}` : "";
  return `${cleanActionLabel(action)}${target}${mechanic}`;
}

function setFallbackMon(set: PokemonSet): SparringMon {
  return {
    species: set.species,
    name: set.nickname || set.species,
    hp: 100,
    fainted: false,
    status: null,
    item: set.item,
    ability: set.ability,
    active: false,
  };
}

function snapshotForSet(set: PokemonSet, snapshots: SparringMon[]) {
  return snapshots.find((mon) => toId(mon.species) === toId(set.species)) ?? setFallbackMon(set);
}

function PreviewSetCard({
  set,
  order,
  selectable = false,
  onClick,
  opponent = false,
}: {
  set: PokemonSet;
  order?: number;
  selectable?: boolean;
  onClick?: () => void;
  opponent?: boolean;
}) {
  const content = (
    <>
      <div className="relative shrink-0">
        <Image src={getSpriteUrl(set.species)} alt={set.species} width={64} height={64} unoptimized className="size-16 object-contain" />
        {order ? (
          <span className="absolute -right-1 -top-1 flex size-6 items-center justify-center rounded-full bg-cyan-300 text-[10px] font-black text-slate-950 shadow-lg">
            {order}
          </span>
        ) : null}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <strong className="truncate text-xs text-white">{set.species}</strong>
          {set.teraType ? <span className="rounded-md border border-white/8 px-1.5 py-0.5 text-[7px] font-bold text-slate-500">Tera {set.teraType}</span> : null}
        </div>
        <p className="mt-0.5 truncate text-[9px] text-slate-500">{set.item} · {set.ability}</p>
        <div className="mt-2 grid grid-cols-2 gap-1">
          {set.moves.slice(0, 4).map((move) => (
            <span key={move.name} className="truncate rounded-md border border-white/6 bg-slate-950/35 px-1.5 py-1 text-[7px] text-slate-500">
              {move.name}
            </span>
          ))}
        </div>
      </div>
    </>
  );

  const className = cn(
    "flex min-h-[96px] items-center gap-3 rounded-2xl border p-3 text-left transition",
    order
      ? "border-cyan-300/35 bg-cyan-300/[0.07] shadow-[0_0_24px_rgba(34,211,238,0.06)]"
      : opponent
        ? "border-rose-300/10 bg-rose-300/[0.025]"
        : "border-white/7 bg-slate-950/45",
    selectable && !order && "hover:border-cyan-300/20 hover:bg-cyan-300/[0.035]",
  );

  return selectable ? (
    <button type="button" onClick={onClick} className={className}>{content}</button>
  ) : (
    <article className={className}>{content}</article>
  );
}

function RosterStrip({
  sets,
  snapshots,
  opponent = false,
}: {
  sets: PokemonSet[];
  snapshots: SparringMon[];
  opponent?: boolean;
}) {
  if (!sets.length) return null;
  return (
    <div className={cn("flex flex-wrap gap-1.5", opponent ? "justify-end" : "justify-start")}>
      {sets.map((set) => {
        const mon = snapshotForSet(set, snapshots);
        return (
          <div
            key={`${opponent ? "opp" : "own"}-${set.slot}-${set.species}`}
            title={`${set.species} · ${Math.round(mon.hp)}%`}
            className={cn(
              "relative flex size-11 items-center justify-center rounded-xl border bg-slate-950/55",
              mon.active ? opponent ? "border-rose-300/35" : "border-cyan-300/35" : "border-white/7",
              mon.fainted && "opacity-35 grayscale",
            )}
          >
            <Image src={getSpriteUrl(set.species)} alt={set.species} width={40} height={40} unoptimized className="size-10 object-contain" />
            <span className={cn(
              "absolute inset-x-1 bottom-0.5 h-1 overflow-hidden rounded-full bg-slate-800",
            )}>
              <span
                className={cn("block h-full rounded-full", mon.hp > 50 ? "bg-emerald-400" : mon.hp > 20 ? "bg-amber-400" : "bg-rose-400")}
                style={{ width: `${Math.max(0, Math.min(100, mon.hp))}%` }}
              />
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ActivePokemonCard({ mon, opponent = false }: { mon: SparringMon | null | undefined; opponent?: boolean }) {
  if (!mon) return <div className="min-h-36 rounded-[22px] border border-dashed border-white/8 bg-slate-950/20" />;
  return (
    <article className={cn(
      "relative flex min-h-40 items-center gap-4 rounded-[22px] border p-4",
      mon.fainted ? "border-white/6 bg-slate-950/35 opacity-45" : opponent ? "border-rose-300/15 bg-rose-300/[0.035]" : "border-cyan-300/15 bg-cyan-300/[0.035]",
    )}>
      <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={128} height={128} unoptimized className="size-28 shrink-0 object-contain drop-shadow-2xl" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-[8px] font-black uppercase tracking-[0.14em] text-slate-600">{opponent ? "Rival" : "Tu campo"}</p>
            <strong className="mt-0.5 block truncate text-base text-white">{mon.species}</strong>
          </div>
          {mon.status ? <Badge variant="outline" className="border-amber-300/15 text-[8px] text-amber-200">{mon.status}</Badge> : null}
        </div>
        <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-slate-800">
          <div
            className={cn("h-full rounded-full transition-all", mon.hp > 50 ? "bg-emerald-400" : mon.hp > 20 ? "bg-amber-400" : "bg-rose-400")}
            style={{ width: `${Math.max(0, Math.min(100, mon.hp))}%` }}
          />
        </div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="font-mono text-[9px] text-slate-500">{Math.round(mon.hp)}% HP</span>
          <span className="truncate text-[8px] text-slate-600">{[mon.item, mon.ability].filter(Boolean).join(" · ")}</span>
        </div>
      </div>
    </article>
  );
}

function TargetButton({ action, battle, onSelect }: { action: SingleAction; battle: BattleState; onSelect: () => void }) {
  const target = targetInfo(action.target, battle);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex min-h-16 items-center gap-3 rounded-2xl border px-3 py-2.5 text-left transition",
        target.opponent
          ? "border-rose-300/15 bg-rose-300/[0.035] hover:border-rose-300/30"
          : "border-cyan-300/15 bg-cyan-300/[0.035] hover:border-cyan-300/30",
      )}
    >
      {target.mon?.species ? <Image src={getSpriteUrl(target.mon.species)} alt={target.mon.species} width={48} height={48} unoptimized className="size-12 object-contain" /> : <Crosshair className="size-5 text-slate-500" />}
      <div className="min-w-0">
        <strong className="block truncate text-[10px] text-white">{target.title}</strong>
        {target.mon ? <span className="mt-0.5 block text-[8px] text-slate-500">{Math.round(target.mon.hp)}% HP</span> : null}
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
    : mechanicOptions.includes("Normal")
      ? "Normal"
      : mechanicOptions[0] ?? "Normal";
  const filteredMoves = moveActions.filter((action) => mechanicKey(action) === effectiveMechanic);
  const moveGroups = new Map<string, SingleAction[]>();
  for (const action of filteredMoves) {
    const current = moveGroups.get(action.value) ?? [];
    current.push(action);
    moveGroups.set(action.value, current);
  }
  const signature = unique.map(actionKey).join("||");
  const [pendingMove, setPendingMove] = useState("");

  useEffect(() => {
    setPendingMove("");
  }, [signature, effectiveMechanic]);

  const pendingVariants = pendingMove ? uniqueSingleActions(moveGroups.get(pendingMove) ?? []) : [];

  return (
    <div className="rounded-[22px] border border-white/8 bg-slate-950/35 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {mon?.species ? <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={52} height={52} unoptimized className="size-13 object-contain" /> : null}
          <div>
            <p className="text-[8px] font-black uppercase tracking-[0.14em] text-cyan-300">{label}</p>
            <h3 className="mt-0.5 text-sm font-black text-white">¿Qué hará {mon?.species || "este Pokémon"}?</h3>
          </div>
        </div>
        {mechanicOptions.length > 1 ? (
          <div className="flex rounded-xl border border-white/8 bg-slate-950/60 p-1">
            {mechanicOptions.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => onMechanicChange(option)}
                className={cn(
                  "flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-[8px] font-bold transition",
                  effectiveMechanic === option ? "bg-violet-300/15 text-violet-100" : "text-slate-500 hover:text-slate-300",
                )}
              >
                {option !== "Normal" ? <Sparkles className="size-3" /> : null}{option}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {moveGroups.size ? (
        <div className="mt-4">
          <p className="text-[8px] font-black uppercase tracking-[0.12em] text-slate-600">Movimientos</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {[...moveGroups.entries()].map(([move, variants]) => {
              const sample = variants[0];
              const type = getMoveData(sample.value).type;
              return (
                <button
                  key={`${effectiveMechanic}-${move}`}
                  type="button"
                  onClick={() => {
                    const only = uniqueSingleActions(variants);
                    if (only.length === 1 && only[0].target === 0) onSelect(only[0]);
                    else setPendingMove(move);
                  }}
                  className={cn(
                    "rounded-2xl border px-3 py-3 text-left transition hover:brightness-125",
                    moveTone(type),
                    pendingMove === move && "ring-1 ring-cyan-300/40",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <strong className="text-[11px]">{cleanActionLabel(sample)}</strong>
                    {type ? <span className="text-[7px] font-black uppercase opacity-60">{type}</span> : null}
                  </div>
                  <p className="mt-1 text-[8px] opacity-55">{variants.some((variant) => variant.target !== 0) ? "Elige objetivo" : "Sin objetivo manual"}</p>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {pendingVariants.length ? (
        <div className="mt-3 rounded-2xl border border-cyan-300/10 bg-cyan-300/[0.025] p-3">
          <div className="flex items-center gap-2 text-[9px] font-bold text-cyan-100"><Crosshair className="size-3.5" />Elige objetivo para {cleanActionLabel(pendingVariants[0])}</div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {pendingVariants.map((action) => <TargetButton key={actionKey(action)} action={action} battle={battle} onSelect={() => onSelect(action)} />)}
          </div>
        </div>
      ) : null}

      {switchActions.length ? (
        <div className="mt-4">
          <p className="flex items-center gap-1.5 text-[8px] font-black uppercase tracking-[0.12em] text-slate-600"><ArrowRightLeft className="size-3" />Cambiar</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {[...new Map(switchActions.map((action) => [action.value, action])).values()].map((action) => (
              <button
                key={actionKey(action)}
                type="button"
                onClick={() => onSelect(action)}
                className="flex items-center gap-2 rounded-xl border border-white/8 bg-slate-950/50 px-2.5 py-2 text-left transition hover:border-cyan-300/20"
              >
                <Image src={getSpriteUrl(action.value)} alt={action.value} width={38} height={38} unoptimized className="size-9 object-contain" />
                <span className="truncate text-[9px] font-bold text-slate-300">{action.value}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {otherActions.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {otherActions.map((action) => (
            <button key={actionKey(action)} type="button" onClick={() => onSelect(action)} className="rounded-xl border border-white/8 bg-slate-950/50 px-3 py-2 text-[9px] text-slate-400">
              {cleanActionLabel(action)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SelectedAction({
  label,
  mon,
  action,
  battle,
  onChange,
}: {
  label: string;
  mon: SparringMon | null | undefined;
  action: SingleAction;
  battle: BattleState;
  onChange: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-emerald-300/12 bg-emerald-300/[0.035] px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        {mon?.species ? <Image src={getSpriteUrl(mon.species)} alt={mon.species} width={40} height={40} unoptimized className="size-10 object-contain" /> : null}
        <div className="min-w-0">
          <p className="text-[7px] font-black uppercase tracking-[0.12em] text-emerald-300">{label}</p>
          <strong className="block truncate text-[10px] text-white">{describeAction(action, battle)}</strong>
        </div>
      </div>
      <button type="button" onClick={onChange} className="shrink-0 rounded-lg border border-white/8 px-2 py-1 text-[8px] text-slate-500 hover:text-white">Cambiar</button>
    </div>
  );
}

export function WarRoomSparring({
  team,
  corpusTeams,
}: {
  team: TeamVersion;
  corpusTeams: WarRoomCorpusTeam[];
}) {
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

  const ownReport = useMemo(() => inspectBattleReadyPaste(team.paste), [team.paste]);
  const candidates = useMemo(() => sparringCorpusCandidates(corpusTeams), [corpusTeams]);
  const opponentPokemon = useMemo(() => {
    if (!opponent?.paste) return [] as PokemonSet[];
    try {
      return parseShowdownPaste(opponent.paste);
    } catch {
      return [] as PokemonSet[];
    }
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

  useEffect(() => {
    void checkHealth();
  }, []);

  useEffect(() => {
    if (!session || ["completed", "error", "cancelled"].includes(session.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${LOCAL_SERVICE}/sparring/${session.id}`, { cache: "no-store" });
        const payload = await readPayload(response);
        if (response.ok) setSession(payload as SparringSession);
      } catch {
        // Keep the last known battle state visible while loopback reconnects.
      }
    }, 400);
    return () => window.clearInterval(timer);
  }, [session?.id, session?.phase]);

  useEffect(() => {
    setSelectedFirst("");
    setSelectedSecond("");
    setFirstMechanic("Normal");
    setSecondMechanic("Normal");
  }, [session?.actions?.[0]?.id]);

  async function pickBattleReadyOpponent() {
    let skipped = 0;
    for (const candidate of shuffledSparringCandidates(corpusTeams)) {
      try {
        const paste = await loadExactPaste(candidate);
        if (!inspectBattleReadyPaste(paste).ready) {
          skipped += 1;
          continue;
        }
        setRejected(skipped);
        return { team: candidate, paste };
      } catch {
        skipped += 1;
      }
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
          opponent: {
            id: picked.team.id,
            label: picked.team.playerName || picked.team.id,
            source: picked.team.source,
          },
        }),
      });
      const payload = await readPayload(response);
      if (!response.ok) throw new Error(errorText(payload, "Battle Lab rechazó la partida."));
      setSession(payload as SparringSession);
      await checkHealth();
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "No pudimos iniciar el Sparring.");
    } finally {
      setStarting(false);
    }
  }

  function togglePreview(slot: number) {
    setPreviewOrder((current) => {
      if (current.includes(slot)) return current.filter((value) => value !== slot);
      if (current.length >= 4) return current;
      return [...current, slot];
    });
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
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "Falló Team Preview.");
    } finally {
      setSubmitting(false);
    }
  }

  const firstActions = session?.phase === "waiting-choice" ? uniqueActions(session.actions, "first") : [];
  const selectedFirstAction = firstActions.find((action) => actionKey(action) === selectedFirst);
  const compatible = selectedFirst && session
    ? session.actions.filter((entry) => actionKey(entry.first) === selectedFirst)
    : [];
  const secondActions = uniqueActions(compatible, "second");
  const selectedSecondAction = secondActions.find((action) => actionKey(action) === selectedSecond);
  const choiceCandidate = session?.actions.find((entry) =>
    actionKey(entry.first) === selectedFirst && actionKey(entry.second) === selectedSecond);

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
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "No pudimos enviar la jugada.");
    } finally {
      setSubmitting(false);
    }
  }

  const resultLabel = session?.result?.winner === "human"
    ? "Victoria"
    : session?.result?.winner === "model"
      ? "Battle Lab gana"
      : "Empate";

  return (
    <div className="space-y-4">
      <section className="rounded-[24px] border border-cyan-300/12 bg-gradient-to-br from-cyan-300/[0.055] via-slate-900/55 to-violet-300/[0.04] p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Gamepad2 className="size-5 text-cyan-300" />
              <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Sparring interactivo</p>
              <Badge variant="outline" className={cn("text-[8px]", health ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200")}>
                {health ? health.ready ? "Battle Lab listo" : "Loopback conectado" : "Servicio offline"}
              </Badge>
            </div>
            <h2 className="mt-2 text-xl font-black text-white">Tú contra LIGHT M-C</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
              Flujo de batalla inspirado en Pokémon Showdown: eliges acción, mecánica y objetivo; `battle.valid_orders` sigue siendo la autoridad detrás de la interfaz.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => void checkHealth()} className="gap-2 border-white/10 bg-slate-950/45">
              <RefreshCw className="size-4" />Revisar servicio
            </Button>
            <Button type="button" onClick={() => void startBattle()} disabled={starting || !ownReport.ready || !candidates.length} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
              {starting ? <Loader2 className="size-4 animate-spin" /> : <Swords className="size-4" />}
              {session ? "Nuevo rival" : "Buscar rival y pelear"}
            </Button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2 text-[9px]">
          <Badge variant="outline" className={ownReport.ready ? "border-emerald-300/15 text-emerald-200" : "border-rose-300/15 text-rose-200"}>
            {ownReport.ready ? "Tu Team · completo" : `Tu Team · ${ownReport.issues.length} huecos`}
          </Badge>
          <Badge variant="outline" className="border-white/8 text-slate-400">{candidates.length} candidatos con fuente exacta</Badge>
          {rejected ? <Badge variant="outline" className="border-amber-300/15 text-amber-200">{rejected} incompletos saltados</Badge> : null}
          {opponent ? <Badge variant="outline" className="border-violet-300/15 text-violet-200">Rival: {opponent.team.playerName || opponent.team.id}</Badge> : null}
        </div>
        {healthError ? <p className="mt-3 flex items-center gap-2 text-[10px] text-rose-200"><CircleAlert className="size-3.5" />{healthError}</p> : null}
        {startError ? <p className="mt-2 flex items-start gap-2 rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-3 py-2 text-[10px] leading-4 text-rose-100"><CircleAlert className="mt-0.5 size-3.5 shrink-0" />{startError}</p> : null}
      </section>

      {session?.phase === "team-preview" ? (
        <section className="rounded-[24px] border border-white/8 bg-slate-900/45 p-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-300">Team Preview · Open Team Sheet</p>
              <h2 className="mt-1 text-lg font-black text-white">Elige tus cuatro viendo ambos equipos</h2>
              <p className="mt-1 text-[10px] text-slate-600">Orden 1-2 = lead · 3-4 = backline. Battle Lab decide su Bring 4 por su cuenta.</p>
            </div>
            <Badge variant="outline" className="border-rose-300/15 text-rose-200">{session.opponent.label || session.opponent.id}</Badge>
          </div>

          <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_56px_minmax(0,1fr)]">
            <div>
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300">Tu equipo</p>
                <span className="text-[8px] text-slate-600">Haz clic para ordenar el Bring 4</span>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {team.pokemon.map((set, index) => {
                  const slot = index + 1;
                  const order = previewOrder.indexOf(slot);
                  return <PreviewSetCard key={set.id} set={set} selectable order={order >= 0 ? order + 1 : undefined} onClick={() => togglePreview(slot)} />;
                })}
              </div>
            </div>

            <div className="hidden items-center justify-center xl:flex">
              <div className="flex size-11 items-center justify-center rounded-full border border-white/8 bg-slate-950/60 text-[10px] font-black text-slate-500">VS</div>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[9px] font-black uppercase tracking-[0.14em] text-rose-300">Equipo rival</p>
                <span className="text-[8px] text-slate-600">Paste exacto · sin inferencias</span>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {opponentPokemon.map((set) => <PreviewSetCard key={`opponent-${set.id}`} set={set} opponent />)}
              </div>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/7 bg-slate-950/35 p-3">
            <div className="flex flex-wrap gap-1.5">
              {[0, 1, 2, 3].map((index) => {
                const set = team.pokemon[(previewOrder[index] ?? 0) - 1];
                return (
                  <div key={index} className="flex h-10 min-w-28 items-center gap-2 rounded-xl border border-white/7 bg-slate-950/45 px-2">
                    <span className="flex size-5 items-center justify-center rounded-full bg-cyan-300/10 text-[8px] font-black text-cyan-200">{index + 1}</span>
                    {set ? <><Image src={getSpriteUrl(set.species)} alt={set.species} width={30} height={30} unoptimized className="size-7 object-contain" /><span className="truncate text-[8px] font-bold text-slate-300">{set.species}</span></> : <span className="text-[8px] text-slate-700">Sin elegir</span>}
                  </div>
                );
              })}
            </div>
            <Button type="button" onClick={() => void submitPreview()} disabled={previewOrder.length !== 4 || submitting} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
              {submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Confirmar Bring 4
            </Button>
          </div>
        </section>
      ) : null}

      {session && session.phase !== "starting" && session.phase !== "team-preview" ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
          <section className="overflow-hidden rounded-[24px] border border-white/8 bg-slate-900/45">
            <div className="border-b border-white/7 bg-slate-950/35 px-5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <span className="flex items-center gap-2 text-[10px] font-bold text-rose-200"><Bot className="size-4" />Battle Lab · {session.opponent.label || session.opponent.id}</span>
                  <div className="mt-2"><RosterStrip sets={opponentPokemon} snapshots={session.battle.opponentTeam ?? []} opponent /></div>
                </div>
                <div className="text-right">
                  <span className="font-mono text-[10px] text-slate-500">Turno {session.battle.turn ?? 0}</span>
                  {session.battle.weather?.length || session.battle.fields?.length ? <p className="mt-1 text-[8px] text-slate-600">{[...(session.battle.weather ?? []), ...(session.battle.fields ?? [])].join(" · ")}</p> : null}
                </div>
              </div>
            </div>

            <div className="relative overflow-hidden bg-[radial-gradient(circle_at_50%_45%,rgba(34,211,238,0.07),transparent_45%)] px-5 py-6">
              <div className="pointer-events-none absolute inset-x-[8%] top-1/2 h-px bg-gradient-to-r from-transparent via-white/8 to-transparent" />
              <div className="grid gap-3 sm:grid-cols-2">
                {(session.battle.opponentActive ?? [null, null]).map((mon, index) => <ActivePokemonCard key={`opp-${index}`} mon={mon} opponent />)}
              </div>
              <div className="flex items-center justify-center py-3"><Swords className="size-5 text-slate-700" /></div>
              <div className="grid gap-3 sm:grid-cols-2">
                {(session.battle.ownActive ?? [null, null]).map((mon, index) => <ActivePokemonCard key={`own-${index}`} mon={mon} />)}
              </div>
              <div className="mt-4"><RosterStrip sets={team.pokemon} snapshots={session.battle.ownTeam ?? []} /></div>
            </div>

            <div className="border-t border-white/7 p-5">
              {session.phase === "waiting-choice" ? (
                <div className="space-y-3">
                  {selectedFirstAction ? (
                    <SelectedAction label="Pokémon izquierdo" mon={session.battle.ownActive?.[0]} action={selectedFirstAction} battle={session.battle} onChange={() => { setSelectedFirst(""); setSelectedSecond(""); }} />
                  ) : (
                    <ActionPicker
                      label="Pokémon izquierdo"
                      mon={session.battle.ownActive?.[0]}
                      actions={firstActions}
                      battle={session.battle}
                      mechanic={firstMechanic}
                      onMechanicChange={setFirstMechanic}
                      onSelect={(action) => { setSelectedFirst(actionKey(action)); setSelectedSecond(""); setSecondMechanic("Normal"); }}
                    />
                  )}

                  {selectedFirstAction ? (
                    selectedSecondAction ? (
                      <SelectedAction label="Pokémon derecho" mon={session.battle.ownActive?.[1]} action={selectedSecondAction} battle={session.battle} onChange={() => setSelectedSecond("")} />
                    ) : (
                      <ActionPicker
                        label="Pokémon derecho"
                        mon={session.battle.ownActive?.[1]}
                        actions={secondActions}
                        battle={session.battle}
                        mechanic={secondMechanic}
                        onMechanicChange={setSecondMechanic}
                        onSelect={(action) => setSelectedSecond(actionKey(action))}
                      />
                    )
                  ) : (
                    <div className="rounded-2xl border border-dashed border-white/7 px-4 py-5 text-center text-[9px] text-slate-700">Elige primero la acción del Pokémon izquierdo.</div>
                  )}

                  {choiceCandidate && selectedFirstAction && selectedSecondAction ? (
                    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-cyan-300/12 bg-cyan-300/[0.03] p-3">
                      <div>
                        <p className="text-[8px] font-black uppercase tracking-[0.12em] text-cyan-300">Turno listo</p>
                        <p className="mt-1 text-[9px] text-slate-500">Showdown validó esta combinación. Confirma para resolver el turno.</p>
                      </div>
                      <Button type="button" onClick={() => void submitTurn()} disabled={submitting} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">
                        {submitting ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}Confirmar turno
                      </Button>
                    </div>
                  ) : null}
                </div>
              ) : session.phase === "resolving" ? (
                <div className="flex items-center justify-center gap-2 py-10 text-xs text-slate-400"><Loader2 className="size-4 animate-spin text-cyan-300" />Battle Lab y Showdown están resolviendo el turno…</div>
              ) : session.phase === "completed" ? (
                <div className="py-8 text-center">
                  <p className={cn("text-2xl font-black", session.result?.winner === "human" ? "text-emerald-300" : session.result?.winner === "model" ? "text-rose-300" : "text-amber-300")}>{resultLabel}</p>
                  <p className="mt-2 text-xs text-slate-500">{session.result?.turns} turnos · {session.result?.battleTag}</p>
                </div>
              ) : session.phase === "error" ? (
                <div className="rounded-xl border border-rose-300/12 bg-rose-300/[0.04] px-4 py-3 text-xs text-rose-100">{session.error}</div>
              ) : null}
            </div>
          </section>

          <aside className="rounded-[24px] border border-white/8 bg-slate-900/45 p-4">
            <p className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Battle log</p>
            <div className="mt-3 max-h-[640px] space-y-2 overflow-y-auto pr-1">
              {session.events.map((event, index) => <div key={`${index}-${event}`} className="rounded-xl border border-white/6 bg-slate-950/40 px-3 py-2 text-[9px] leading-4 text-slate-500">{event}</div>)}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
