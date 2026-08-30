"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import { ArrowLeftRight, Crosshair, ShieldCheck, Sparkles, Swords, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  calculateMoves,
  createDamageDraft,
  defaultDamageField,
  getDisplayedEffectiveStat,
  getMegaForm,
  getSpeedOrder,
  type DamageFieldState,
  type DamageOutcome,
  type DamagePokemonDraft,
  type DamageSideConditions,
  type DamageStat,
  type DamageStatus,
  type DamageTerrain,
  type DamageWeather,
} from "@/lib/damage-calculator";
import { getSpriteUrl, toId } from "@/lib/pokemon-data";
import {
  getLegalAbilities,
  getLegalItems,
  getLegalMoves,
  getSpecies,
  getSpeciesOptions,
  moveFromSnapshot,
  type ShowdownSnapshot,
} from "@/lib/showdown-data";
import { calculateStat, NATURES, normalizeTeraType, parseEvs } from "@/lib/team-builder";
import { POKEMON_TYPES, type BattleMechanic, type PokemonSet } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PokemonLibraryVersionSelect } from "./pokemon-library-dialog";
import { PokemonStatEditor, type BoostableStat } from "./pokemon-stat-editor";
import { TypeBadge } from "./type-badge";

type DamageCalculatorProps = {
  source: PokemonSet;
  format: string;
  dex: ShowdownSnapshot;
  mechanics: BattleMechanic[];
  session?: DamageCalculatorSession;
  onSessionChange?: (session: DamageCalculatorSession) => void;
};

export type DamageCalculatorSession = {
  left: DamagePokemonDraft;
  right: DamagePokemonDraft;
  field: DamageFieldState;
};

const STATUS_OPTIONS: Array<{ value: DamageStatus; label: string }> = [
  { value: "", label: "Saludable" },
  { value: "brn", label: "Quemado" },
  { value: "par", label: "Paralizado" },
  { value: "psn", label: "Envenenado" },
  { value: "tox", label: "Tóxico" },
  { value: "slp", label: "Dormido" },
  { value: "frz", label: "Congelado" },
];

const BOOST_STAT_KEYS: Record<BoostableStat, DamageStat> = {
  Atk: "atk",
  Def: "def",
  SpA: "spa",
  SpD: "spd",
  Spe: "spe",
};

const FIELD_SIDE_TOGGLES: Array<[keyof DamageSideConditions, string]> = [
  ["reflect", "Reflect"],
  ["lightScreen", "Light Screen"],
  ["auroraVeil", "Aurora Veil"],
  ["tailwind", "Tailwind"],
  ["helpingHand", "Helping Hand"],
  ["friendGuard", "Friend Guard"],
  ["protected", "Protect"],
];

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function getCurrentEffectiveSpeed(
  draft: DamagePokemonDraft,
  format: string,
  dex: ShowdownSnapshot,
  tailwind: boolean,
) {
  const set = draft.set;
  if (!set.species) return null;
  const megaForm = getMegaForm(set);
  const speciesName = draft.megaActive && megaForm ? megaForm : set.species;
  const species = getSpecies(dex, speciesName) ?? getSpecies(dex, set.species);
  if (!species) return null;
  const allocations = parseEvs(set.evs);
  const rawSpeed = calculateStat(
    species.baseStats,
    "Spe",
    allocations.Spe,
    format === "champions" ? 50 : set.level || 50,
    set.nature || "Serious",
    format,
  );
  return getDisplayedEffectiveStat("spe", rawSpeed, draft.boosts.spe, tailwind);
}

function CalculatorPokemonPanel({
  side,
  draft,
  onChange,
  format,
  dex,
  mechanics,
  outcomes,
  opponentReady,
  tailwind,
}: {
  side: "left" | "right";
  draft: DamagePokemonDraft;
  onChange: (next: DamagePokemonDraft) => void;
  format: string;
  dex: ShowdownSnapshot;
  mechanics: BattleMechanic[];
  outcomes: DamageOutcome[];
  opponentReady: boolean;
  tailwind: boolean;
}) {
  const set = draft.set;
  const speciesOptions = useMemo(() => getSpeciesOptions(dex, format), [dex, format]);
  const selectedSpecies = getSpecies(dex, set.species);
  const megaForm = mechanics.includes("mega") ? getMegaForm(set) : null;
  const megaActive = Boolean(megaForm && draft.megaActive);
  const battleSpeciesName = megaActive ? megaForm! : set.species;
  const battleSpecies = getSpecies(dex, battleSpeciesName) ?? selectedSpecies;
  const displayTypes = megaActive ? battleSpecies?.types ?? set.types : set.types;
  const legalMoves = useMemo(() => getLegalMoves(dex, set.species, format), [dex, set.species, format]);
  const legalItems = useMemo(() => getLegalItems(dex, format), [dex, format]);
  const legalAbilities = megaActive ? battleSpecies?.abilities ?? [] : getLegalAbilities(dex, set.species);
  const displayedAbility = megaActive ? battleSpecies?.abilities[0] ?? set.ability : set.ability;

  function updateSet(next: PokemonSet) {
    const nextMegaForm = mechanics.includes("mega") ? getMegaForm(next) : null;
    onChange({ ...draft, set: next, megaActive: draft.megaActive && Boolean(nextMegaForm) });
  }

  function chooseSpecies(value: string | null) {
    const nextSpecies = value ? getSpecies(dex, value) : null;
    if (!nextSpecies) return;
    updateSet({
      ...set,
      species: nextSpecies.name,
      nickname: nextSpecies.name,
      types: nextSpecies.types,
      ability: nextSpecies.abilities[0] ?? "",
      item: "",
      nature: set.nature || "Serious",
      evs: "",
      moves: Array.from({ length: 4 }, () => ({ name: "", type: null, damaging: false, usage: 0 })),
    });
  }

  function chooseLibraryVersion(librarySet: PokemonSet) {
    updateSet({
      ...librarySet,
      id: set.id,
      slot: set.slot,
      mechanics: { ...librarySet.mechanics },
      moves: Array.from({ length: 4 }, (_, moveIndex) => librarySet.moves[moveIndex]
        ? { ...librarySet.moves[moveIndex], usage: 0 }
        : { name: "", type: null, damaging: false, usage: 0 }),
      types: [...librarySet.types],
      performance: set.performance,
    });
  }

  function chooseMove(index: number, value: string | null) {
    const nextMove = value
      ? moveFromSnapshot(dex, value)
      : { name: "", type: null, damaging: false, usage: 0 } as PokemonSet["moves"][number];
    updateSet({
      ...set,
      moves: set.moves.map((move, moveIndex) => moveIndex === index ? nextMove : move),
    });
  }

  return (
    <section className="min-w-0 rounded-[24px] border border-white/8 bg-[#0b1220]/95 p-4 shadow-[0_24px_70px_rgba(0,0,0,0.26)]">
      <div className="flex items-center justify-between gap-3 border-b border-white/7 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-12 shrink-0 items-center justify-center">
            {set.species ? <Image src={getSpriteUrl(battleSpeciesName)} alt={battleSpeciesName} width={56} height={56} unoptimized className="size-12 object-contain" /> : <div aria-hidden="true" className="size-10 rounded-xl border border-dashed border-white/8 bg-white/[0.02]" />}
          </div>
          <div className="min-w-0">
            <p className="text-[9px] font-black uppercase tracking-[0.15em] text-cyan-300/75">{side === "left" ? "Tu Pokémon" : "Rival"}</p>
            <h3 className="truncate text-base font-black text-white">{battleSpeciesName || "Selecciona un Pokémon"}</h3>
            <div className="mt-1 flex min-h-4 gap-1">{displayTypes.map((type) => <TypeBadge key={type} type={type} className="text-[7px]">{type}</TypeBadge>)}</div>
          </div>
        </div>
        <Badge variant="outline" className="border-white/10 bg-white/3 text-slate-400">{format === "champions" ? "Champions" : format.toUpperCase()}</Badge>
      </div>

      <div className="mt-4 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid min-w-0 gap-2">
            <Label>Pokémon</Label>
            <Combobox items={speciesOptions} value={set.species || null} onValueChange={chooseSpecies}>
              <ComboboxInput placeholder="Buscar especie..." className="w-full min-w-0 border-white/10 bg-white/4" />
              <ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible en este formato.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent>
            </Combobox>
          </div>
          <PokemonLibraryVersionSelect species={set.species} format={format} onLoad={(librarySet) => chooseLibraryVersion(librarySet)} />
        </div>

        <div className="grid gap-2"><Label>Objeto</Label><Combobox items={legalItems} value={set.item || null} onValueChange={(value) => updateSet({ ...set, item: value ?? "" })}><ComboboxInput placeholder="Buscar objeto..." className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible.</ComboboxEmpty><ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox></div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-2"><Label>Habilidad</Label><Select value={displayedAbility || "none"} disabled={megaActive} onValueChange={(value) => updateSet({ ...set, ability: value === "none" ? "" : value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{legalAbilities.map((ability) => <SelectItem key={ability} value={ability}>{ability}</SelectItem>)}</SelectContent></Select></div>
          <div className="grid gap-2"><Label>Naturaleza</Label><Select value={set.nature || "Serious"} onValueChange={(value) => updateSet({ ...set, nature: value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent>{NATURES.map((nature) => <SelectItem key={nature} value={nature}>{nature}</SelectItem>)}</SelectContent></Select></div>
        </div>

        {mechanics.includes("tera") || mechanics.includes("dynamax") ? (
          <div className="grid gap-3 rounded-2xl border border-white/7 bg-black/15 p-3 sm:grid-cols-2">
            {mechanics.includes("tera") ? <div className="grid gap-2"><Label>Tipo Tera</Label><Select value={set.teraType ?? "none"} onValueChange={(value) => updateSet({ ...set, teraType: normalizeTeraType(value === "none" ? "" : value) })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></div> : null}
            {mechanics.includes("dynamax") ? <div className="grid gap-2"><Label>Dynamax Level</Label><Input type="number" min={0} max={10} value={set.mechanics?.dynamaxLevel ?? 10} onChange={(event) => updateSet({ ...set, mechanics: { ...set.mechanics, dynamaxLevel: clamp(Number(event.target.value) || 0, 0, 10) } })} className="border-white/10 bg-white/4" /><ToggleCard label="Forma Gigantamax" checked={set.mechanics?.gigantamax ?? false} onChange={(checked) => updateSet({ ...set, mechanics: { ...set.mechanics, gigantamax: checked } })} /></div> : null}
          </div>
        ) : null}

        <div className="relative">
          <PokemonStatEditor
            pokemon={set}
            format={format}
            baseStats={battleSpecies?.baseStats ?? null}
            onChange={updateSet}
            boosts={{
              Atk: draft.boosts.atk,
              Def: draft.boosts.def,
              SpA: draft.boosts.spa,
              SpD: draft.boosts.spd,
              Spe: draft.boosts.spe,
            }}
            tailwind={tailwind}
            stableHeight
            onBoostChange={(stat, value) => {
              const damageStat = BOOST_STAT_KEYS[stat];
              onChange({ ...draft, boosts: { ...draft.boosts, [damageStat]: value } });
            }}
          />
          {megaForm ? (
            <label className={cn("absolute bottom-3 right-3 flex h-8 items-center gap-2 rounded-lg border px-2.5 text-[9px] font-black uppercase tracking-[0.08em] shadow-sm", megaActive ? "border-fuchsia-300/30 bg-fuchsia-300/10 text-fuchsia-100" : "border-white/8 bg-slate-950/90 text-slate-500")}>
              <span>Mega</span>
              <Switch checked={megaActive} onCheckedChange={(checked) => onChange({ ...draft, megaActive: checked })} aria-label={`Mega Evolución de ${set.species}`} />
            </label>
          ) : null}
        </div>

        <div>
          <div className="flex items-center justify-between gap-3"><Label>Movimientos</Label><span className="text-[9px] text-slate-600">Resultados en vivo</span></div>
          <div className="mt-2 space-y-2">
            {set.moves.map((move, index) => {
              const selectedElsewhere = new Set(set.moves.filter((_, moveIndex) => moveIndex !== index).map((entry) => toId(entry.name)).filter(Boolean));
              const options = legalMoves.filter((name) => !selectedElsewhere.has(toId(name)));
              const outcome = outcomes.find((entry) => toId(entry.move) === toId(move.name));
              return <div key={index} className="grid grid-cols-[minmax(0,1fr)_62px_88px] gap-2"><Combobox items={options} value={move.name || null} onValueChange={(value) => chooseMove(index, value)}><ComboboxInput placeholder={`Movimiento ${index + 1}`} className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible o repetido.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><div className="flex items-center justify-center rounded-lg border border-white/7 bg-white/[0.025]">{move.type ? <TypeBadge type={move.type} className="text-[7px]">{move.type.slice(0, 3)}</TypeBadge> : <span className="text-[8px] text-slate-600">Status</span>}</div><InlineDamageRange move={move} outcome={outcome} ready={opponentReady} /></div>;
            })}
          </div>
        </div>

        <div>
          <Label>Estado</Label>
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <Select value={draft.status || "healthy"} onValueChange={(value) => onChange({ ...draft, status: value === "healthy" ? "" : value as DamageStatus })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent>{STATUS_OPTIONS.map((option) => <SelectItem key={option.value || "healthy"} value={option.value || "healthy"}>{option.label}</SelectItem>)}</SelectContent></Select>
            <ToggleCard label="Crítico" checked={draft.critical} onChange={(checked) => onChange({ ...draft, critical: checked })} />
            <div className="grid gap-2"><Label>Nivel</Label><Input type="number" min={1} max={100} disabled={format === "champions"} value={format === "champions" ? 50 : set.level} onChange={(event) => updateSet({ ...set, level: clamp(Number(event.target.value) || 50, 1, 100) })} className="border-white/10 bg-white/4" /></div>
            <div className="grid gap-2"><Label>HP actual</Label><div className="relative"><Input type="number" min={1} max={100} value={draft.hpPercent} onChange={(event) => onChange({ ...draft, hpPercent: clamp(Number(event.target.value) || 1, 1, 100) })} className="border-white/10 bg-white/4 pr-8" /><span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">%</span></div></div>
            {set.teraType ? <ToggleCard label={`Tera ${set.teraType}`} checked={draft.teraActive} onChange={(checked) => onChange({ ...draft, teraActive: checked })} /> : null}
            {mechanics.includes("dynamax") ? <ToggleCard label="Dynamax" checked={draft.dynamaxActive} onChange={(checked) => onChange({ ...draft, dynamaxActive: checked })} /> : null}
            {mechanics.includes("zmove") ? <ToggleCard label="Z-Move" checked={draft.zMoveActive} onChange={(checked) => onChange({ ...draft, zMoveActive: checked })} /> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function InlineDamageRange({ move, outcome, ready }: { move: PokemonSet["moves"][number]; outcome?: DamageOutcome; ready: boolean }) {
  const isStatus = Boolean(move.name) && !move.damaging;
  const label = !move.name
    ? "—"
    : isStatus
      ? "Estado"
      : !ready
        ? "—"
        : outcome?.error
          ? "N/D"
          : outcome
            ? `${outcome.minPercent}–${outcome.maxPercent}%`
            : "—";

  return (
    <div
      aria-label={move.name ? `Daño de ${move.name}: ${label}` : "Daño no disponible"}
      className={cn(
        "flex min-w-0 items-center justify-center rounded-lg border px-1 text-center text-[9px] font-black tabular-nums",
        isStatus ? "border-white/7 bg-white/[0.025] text-slate-500" : outcome?.error ? "border-rose-300/15 bg-rose-300/5 text-rose-300" : "border-cyan-300/12 bg-cyan-300/5 text-cyan-200",
      )}
    >
      {label}
    </div>
  );
}

function ToggleCard({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className={cn("flex min-h-10 items-center justify-between gap-2 rounded-xl border px-2.5 text-[9px] font-bold", checked ? "border-cyan-300/25 bg-cyan-300/8 text-cyan-100" : "border-white/7 bg-white/3 text-slate-500")}><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} /></label>;
}

function SpeedComparisonCard({
  leftName,
  rightName,
  leftSpeed,
  rightSpeed,
  trickRoom,
}: {
  leftName: string;
  rightName: string;
  leftSpeed: number | null;
  rightSpeed: number | null;
  trickRoom: boolean;
}) {
  const ready = leftSpeed !== null && rightSpeed !== null;
  const order = ready ? getSpeedOrder(leftSpeed, rightSpeed, trickRoom) : null;
  const leftWins = order === "left";
  const rightWins = order === "right";
  const tie = order === "tie";
  const ariaLabel = !ready
    ? "Orden por Speed: selecciona ambos Pokémon"
    : tie
      ? `Orden por Speed: ${leftName} y ${rightName}, Speed tie en ${leftSpeed}`
      : `Orden por Speed: ${leftName} ${leftSpeed}, ${rightName} ${rightSpeed}. ${leftWins ? leftName : rightName} gana el orden${trickRoom ? " con Trick Room" : ""}`;

  return (
    <div
      aria-label={ariaLabel}
      className={cn(
        "my-4 rounded-2xl border p-3 text-center",
        trickRoom ? "border-violet-300/20 bg-violet-300/[0.06]" : "border-amber-300/15 bg-amber-300/[0.045]",
      )}
    >
      <div className="flex items-center justify-center gap-1.5 text-[9px] font-black uppercase tracking-[0.12em] text-slate-400">
        <Zap className={cn("size-3.5", trickRoom ? "text-violet-300" : "text-amber-300")} />
        Orden por Speed
      </div>
      <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <p className={cn("text-lg font-black tabular-nums", tie ? "text-amber-200" : leftWins ? "text-cyan-200" : "text-slate-500")}>{leftSpeed ?? "—"}</p>
        <div className={cn("flex size-7 items-center justify-center rounded-full border text-[9px] font-black", trickRoom ? "border-violet-300/20 bg-violet-300/10 text-violet-200" : "border-white/8 bg-black/20 text-slate-600")}>{tie ? "=" : "VS"}</div>
        <p className={cn("text-lg font-black tabular-nums", tie ? "text-amber-200" : rightWins ? "text-cyan-200" : "text-slate-500")}>{rightSpeed ?? "—"}</p>
      </div>
    </div>
  );
}

function FieldSideSection({
  label,
  value,
  onChange,
}: {
  label: string;
  value: DamageSideConditions;
  onChange: (key: keyof DamageSideConditions, checked: boolean) => void;
}) {
  return (
    <div className="mt-3">
      <p className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{label}</p>
      <div className="mt-2 grid gap-1.5">
        {FIELD_SIDE_TOGGLES.map(([key, toggleLabel]) => <ToggleCard key={key} label={toggleLabel} checked={value[key]} onChange={(checked) => onChange(key, checked)} />)}
      </div>
    </div>
  );
}

function FieldPanel({
  value,
  onChange,
  leftName,
  rightName,
  leftSpeed,
  rightSpeed,
}: {
  value: DamageFieldState;
  onChange: (next: DamageFieldState) => void;
  leftName: string;
  rightName: string;
  leftSpeed: number | null;
  rightSpeed: number | null;
}) {
  function updateSide(side: "left" | "right", key: keyof DamageSideConditions, checked: boolean) {
    onChange({ ...value, [side]: { ...value[side], [key]: checked } });
  }

  return (
    <section className="rounded-[24px] border border-cyan-300/10 bg-cyan-300/[0.035] p-3 xl:sticky xl:top-4">
      <div className="text-center"><Zap className="mx-auto size-5 text-amber-300" /><p className="mt-2 text-[10px] font-black uppercase tracking-[0.16em] text-slate-300">Campo</p><p className="mt-1 text-[9px] text-slate-600">Condiciones compartidas</p></div>
      <div className="mt-4 space-y-3">
        <div className="grid gap-1.5"><Label>Combate</Label><Select value={value.gameType} onValueChange={(gameType) => onChange({ ...value, gameType: gameType as DamageFieldState["gameType"] })}><SelectTrigger className="w-full border-white/8 bg-black/20"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="Doubles">Dobles · VGC</SelectItem><SelectItem value="Singles">Individual</SelectItem></SelectContent></Select></div>
        <div className="grid gap-1.5"><Label>Clima</Label><Select value={value.weather || "none"} onValueChange={(weather) => onChange({ ...value, weather: weather === "none" ? "" : weather as DamageWeather })}><SelectTrigger className="w-full border-white/8 bg-black/20"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Ninguno</SelectItem><SelectItem value="Sun">Sol</SelectItem><SelectItem value="Rain">Lluvia</SelectItem><SelectItem value="Sand">Arena</SelectItem><SelectItem value="Snow">Nieve</SelectItem></SelectContent></Select></div>
        <div className="grid gap-1.5"><Label>Terreno</Label><Select value={value.terrain || "none"} onValueChange={(terrain) => onChange({ ...value, terrain: terrain === "none" ? "" : terrain as DamageTerrain })}><SelectTrigger className="w-full border-white/8 bg-black/20"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Ninguno</SelectItem><SelectItem value="Electric">Eléctrico</SelectItem><SelectItem value="Grassy">Hierba</SelectItem><SelectItem value="Psychic">Psíquico</SelectItem><SelectItem value="Misty">Niebla</SelectItem></SelectContent></Select></div>
        <ToggleCard label="Gravedad" checked={value.gravity} onChange={(gravity) => onChange({ ...value, gravity })} />
        <ToggleCard label="Trick Room" checked={Boolean(value.trickRoom)} onChange={(trickRoom) => onChange({ ...value, trickRoom })} />
      </div>
      <div className="my-4 h-px bg-white/7" />
      <FieldSideSection label="Tu lado" value={value.left} onChange={(key, checked) => updateSide("left", key, checked)} />
      <SpeedComparisonCard leftName={leftName} rightName={rightName} leftSpeed={leftSpeed} rightSpeed={rightSpeed} trickRoom={Boolean(value.trickRoom)} />
      <FieldSideSection label="Lado rival" value={value.right} onChange={(key, checked) => updateSide("right", key, checked)} />
    </section>
  );
}

function OutcomeList({ title, attacker, defender, outcomes }: { title: string; attacker: string; defender: string; outcomes: DamageOutcome[] }) {
  return (
    <section className="w-full min-w-0 rounded-[22px] border border-white/8 bg-black/20 p-3">
      <div className="flex items-center justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300/70">{title}</p><p className="mt-1 text-xs font-bold text-slate-300">{attacker || "Atacante"} <span className="text-slate-600">→</span> {defender || "Defensor"}</p></div><Crosshair className="size-4 text-rose-300" /></div>
      <div className="mt-3 grid min-h-64 content-start gap-2 md:grid-cols-2 xl:grid-cols-4">
        {outcomes.length ? outcomes.map((outcome) => <article key={outcome.move} className={cn("min-w-0 rounded-2xl border bg-white/[0.025] p-3", outcome.error ? "border-rose-300/20" : "border-white/7")}><div className="flex items-start justify-between gap-2"><p className="truncate text-xs font-black text-white">{outcome.move}</p><Swords className="size-3.5 shrink-0 text-amber-300" /></div><p className="mt-3 text-xl font-black tabular-nums text-cyan-200">{outcome.minPercent}–{outcome.maxPercent}%</p><p className="mt-1 text-[10px] font-bold tabular-nums text-slate-400">{outcome.min}–{outcome.max} HP</p><p className="mt-2 min-h-8 text-[9px] leading-4 text-emerald-300/80">{outcome.koChance}</p><p className="mt-2 line-clamp-3 text-[10px] leading-4 text-slate-400">{outcome.description}</p>{outcome.rolls.length ? <p className="mt-2 truncate font-mono text-[9px] leading-4 text-slate-500">Rolls: {outcome.rolls.join(", ")}</p> : null}{outcome.error ? <p className="mt-2 text-[9px] leading-4 text-rose-300">{outcome.error}</p> : null}</article>) : <div className="col-span-full flex h-64 items-center justify-center rounded-2xl border border-dashed border-white/8 px-4 py-6 text-center text-xs text-slate-600">Elige al menos un movimiento para ver daño.</div>}
      </div>
    </section>
  );
}

function createCalculatorSession(source: PokemonSet): DamageCalculatorSession {
  return {
    left: createDamageDraft(source),
    right: createDamageDraft(source),
    field: defaultDamageField(),
  };
}

export function DamageCalculatorView({ source, format, dex, mechanics, session: savedSession, onSessionChange }: DamageCalculatorProps) {
  const [localSession, setLocalSession] = useState(() => createCalculatorSession(source));
  const session = savedSession ?? localSession;
  const { left, right, field } = session;

  function updateSession(next: DamageCalculatorSession) {
    setLocalSession(next);
    onSessionChange?.(next);
  }

  function setLeft(next: DamagePokemonDraft) {
    updateSession({ ...session, left: next });
  }

  function setRight(next: DamagePokemonDraft) {
    updateSession({ ...session, right: next });
  }

  function setField(next: DamageFieldState) {
    updateSession({ ...session, field: next });
  }

  const leftOutcomes = useMemo(() => calculateMoves(format, left, right, field), [format, left, right, field]);
  const rightOutcomes = useMemo(() => calculateMoves(format, right, left, field, true), [format, left, right, field]);
  const leftSpeed = useMemo(() => getCurrentEffectiveSpeed(left, format, dex, field.left.tailwind), [dex, field.left.tailwind, format, left]);
  const rightSpeed = useMemo(() => getCurrentEffectiveSpeed(right, format, dex, field.right.tailwind), [dex, field.right.tailwind, format, right]);

  return (
    <div className="w-full min-w-0 space-y-4 p-4 text-slate-100 sm:p-5">
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_190px_minmax(0,1fr)]">
        <CalculatorPokemonPanel side="left" draft={left} onChange={setLeft} format={format} dex={dex} mechanics={mechanics} outcomes={leftOutcomes} opponentReady={Boolean(right.set.species)} tailwind={field.left.tailwind} />
        <div className="order-first xl:order-none"><FieldPanel value={field} onChange={setField} leftName={left.set.species} rightName={right.set.species} leftSpeed={leftSpeed} rightSpeed={rightSpeed} /><div className="mt-3 hidden items-center justify-center gap-2 text-[9px] font-black uppercase tracking-[0.13em] text-slate-700 xl:flex"><ShieldCheck className="size-3.5" /><ArrowLeftRight className="size-3.5" /><Swords className="size-3.5" /></div></div>
        <CalculatorPokemonPanel side="right" draft={right} onChange={setRight} format={format} dex={dex} mechanics={mechanics} outcomes={rightOutcomes} opponentReady={Boolean(left.set.species)} tailwind={field.right.tailwind} />
      </div>
      <OutcomeList title="Daño infligido" attacker={left.set.species} defender={right.set.species} outcomes={leftOutcomes} />
      <OutcomeList title="Daño recibido" attacker={right.set.species} defender={left.set.species} outcomes={rightOutcomes} />
      <div className="rounded-2xl border border-amber-300/10 bg-amber-300/5 px-4 py-3 text-[10px] leading-5 text-amber-100/65"><Sparkles className="mr-2 inline size-3.5 text-amber-300" />Motor oficial de Pokémon Showdown · los resultados se recalculan localmente con cada cambio.</div>
    </div>
  );
}
