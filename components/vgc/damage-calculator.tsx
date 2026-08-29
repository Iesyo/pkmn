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
import { NATURES } from "@/lib/team-builder";
import type { PokemonSet } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PokemonStatEditor, type BoostableStat } from "./pokemon-stat-editor";
import { TypeBadge } from "./type-badge";

type DamageCalculatorProps = {
  source: PokemonSet;
  format: string;
  dex: ShowdownSnapshot;
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

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function CalculatorPokemonPanel({
  side,
  draft,
  onChange,
  format,
  dex,
}: {
  side: "left" | "right";
  draft: DamagePokemonDraft;
  onChange: (next: DamagePokemonDraft) => void;
  format: string;
  dex: ShowdownSnapshot;
}) {
  const set = draft.set;
  const speciesOptions = useMemo(() => getSpeciesOptions(dex, format), [dex, format]);
  const selectedSpecies = getSpecies(dex, set.species);
  const legalMoves = useMemo(() => getLegalMoves(dex, set.species, format), [dex, set.species, format]);
  const legalItems = useMemo(() => getLegalItems(dex, format), [dex, format]);
  const legalAbilities = getLegalAbilities(dex, set.species);

  function updateSet(next: PokemonSet) {
    onChange({ ...draft, set: next });
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
          {set.species ? <Image src={getSpriteUrl(set.species)} alt={set.species} width={56} height={56} unoptimized className="size-12 object-contain" /> : null}
          <div className="min-w-0">
            <p className="text-[9px] font-black uppercase tracking-[0.15em] text-cyan-300/75">{side === "left" ? "Tu Pokémon" : "Rival"}</p>
            <h3 className="truncate text-base font-black text-white">{set.species || "Selecciona un Pokémon"}</h3>
            <div className="mt-1 flex gap-1">{set.types.map((type) => <TypeBadge key={type} type={type} className="text-[7px]">{type}</TypeBadge>)}</div>
          </div>
        </div>
        <Badge variant="outline" className="border-white/10 bg-white/3 text-slate-400">{format === "champions" ? "Champions" : format.toUpperCase()}</Badge>
      </div>

      <div className="mt-4 space-y-4">
        <div className="grid gap-2">
          <Label>Pokémon</Label>
          <Combobox items={speciesOptions} value={set.species || null} onValueChange={chooseSpecies}>
            <ComboboxInput placeholder="Buscar especie..." className="w-full border-white/10 bg-white/4" />
            <ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible en este formato.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent>
          </Combobox>
        </div>

        <div className="grid gap-2"><Label>Objeto</Label><Combobox items={legalItems} value={set.item || null} onValueChange={(value) => updateSet({ ...set, item: value ?? "" })}><ComboboxInput placeholder="Buscar objeto..." className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible.</ComboboxEmpty><ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox></div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-2"><Label>Habilidad</Label><Select value={set.ability || "none"} onValueChange={(value) => updateSet({ ...set, ability: value === "none" ? "" : value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{legalAbilities.map((ability) => <SelectItem key={ability} value={ability}>{ability}</SelectItem>)}</SelectContent></Select></div>
          <div className="grid gap-2"><Label>Naturaleza</Label><Select value={set.nature || "Serious"} onValueChange={(value) => updateSet({ ...set, nature: value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent>{NATURES.map((nature) => <SelectItem key={nature} value={nature}>{nature}</SelectItem>)}</SelectContent></Select></div>
        </div>

        <PokemonStatEditor
          pokemon={set}
          format={format}
          baseStats={selectedSpecies?.baseStats ?? null}
          onChange={updateSet}
          boosts={{
            Atk: draft.boosts.atk,
            Def: draft.boosts.def,
            SpA: draft.boosts.spa,
            SpD: draft.boosts.spd,
            Spe: draft.boosts.spe,
          }}
          onBoostChange={(stat, value) => {
            const damageStat = BOOST_STAT_KEYS[stat];
            onChange({ ...draft, boosts: { ...draft.boosts, [damageStat]: value } });
          }}
        />

        <div>
          <div className="flex items-center justify-between gap-3"><Label>Movimientos</Label><span className="text-[9px] text-slate-600">Resultados en vivo</span></div>
          <div className="mt-2 space-y-2">
            {set.moves.map((move, index) => {
              const selectedElsewhere = new Set(set.moves.filter((_, moveIndex) => moveIndex !== index).map((entry) => toId(entry.name)).filter(Boolean));
              const options = legalMoves.filter((name) => !selectedElsewhere.has(toId(name)));
              return <div key={index} className="grid grid-cols-[minmax(0,1fr)_68px] gap-2"><Combobox items={options} value={move.name || null} onValueChange={(value) => chooseMove(index, value)}><ComboboxInput placeholder={`Movimiento ${index + 1}`} className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible o repetido.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><div className="flex items-center justify-center rounded-lg border border-white/7 bg-white/[0.025]">{move.type ? <TypeBadge type={move.type} className="text-[7px]">{move.type.slice(0, 3)}</TypeBadge> : <span className="text-[8px] text-slate-600">Status</span>}</div></div>;
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
            {format === "gen8" ? <ToggleCard label="Dynamax" checked={draft.dynamaxActive} onChange={(checked) => onChange({ ...draft, dynamaxActive: checked })} /> : null}
            {format === "gen7" ? <ToggleCard label="Z-Move" checked={draft.zMoveActive} onChange={(checked) => onChange({ ...draft, zMoveActive: checked })} /> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function ToggleCard({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className={cn("flex min-h-10 items-center justify-between gap-2 rounded-xl border px-2.5 text-[9px] font-bold", checked ? "border-cyan-300/25 bg-cyan-300/8 text-cyan-100" : "border-white/7 bg-white/3 text-slate-500")}><span>{label}</span><Switch checked={checked} onCheckedChange={onChange} /></label>;
}

function FieldPanel({ value, onChange }: { value: DamageFieldState; onChange: (next: DamageFieldState) => void }) {
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
      </div>
      <div className="my-4 h-px bg-white/7" />
      {(["left", "right"] as const).map((side) => <div key={side} className="mt-3"><p className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-500">{side === "left" ? "Tu lado" : "Lado rival"}</p><div className="mt-2 grid gap-1.5">{([
        ["reflect", "Reflect"],
        ["lightScreen", "Light Screen"],
        ["auroraVeil", "Aurora Veil"],
        ["helpingHand", "Helping Hand"],
        ["friendGuard", "Friend Guard"],
        ["protected", "Protect"],
      ] as Array<[keyof DamageSideConditions, string]>).map(([key, label]) => <ToggleCard key={key} label={label} checked={value[side][key]} onChange={(checked) => updateSide(side, key, checked)} />)}</div></div>)}
    </section>
  );
}

function OutcomeList({ title, attacker, defender, outcomes }: { title: string; attacker: string; defender: string; outcomes: DamageOutcome[] }) {
  return (
    <section className="w-full min-w-0 rounded-[22px] border border-white/8 bg-black/20 p-3">
      <div className="flex items-center justify-between gap-3"><div><p className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-300/70">{title}</p><p className="mt-1 text-xs font-bold text-slate-300">{attacker || "Atacante"} <span className="text-slate-600">→</span> {defender || "Defensor"}</p></div><Crosshair className="size-4 text-rose-300" /></div>
      <div className="mt-3 grid min-h-64 gap-2 md:grid-cols-2 xl:grid-cols-4">
        {outcomes.length ? outcomes.map((outcome) => <article key={outcome.move} className={cn("min-w-0 rounded-2xl border bg-white/[0.025] p-3", outcome.error ? "border-rose-300/20" : "border-white/7")}><div className="flex items-start justify-between gap-2"><p className="truncate text-xs font-black text-white">{outcome.move}</p><Swords className="size-3.5 shrink-0 text-amber-300" /></div><p className="mt-3 text-xl font-black tabular-nums text-cyan-200">{outcome.minPercent}–{outcome.maxPercent}%</p><p className="mt-1 text-[10px] font-bold tabular-nums text-slate-400">{outcome.min}–{outcome.max} HP</p><p className="mt-2 min-h-8 text-[9px] leading-4 text-emerald-300/80">{outcome.koChance}</p><p className="mt-2 line-clamp-3 text-[8px] leading-3.5 text-slate-600">{outcome.description}</p>{outcome.rolls.length ? <p className="mt-2 truncate font-mono text-[8px] text-slate-700">Rolls: {outcome.rolls.join(", ")}</p> : null}{outcome.error ? <p className="mt-2 text-[8px] text-rose-300">{outcome.error}</p> : null}</article>) : <div className="col-span-full flex min-h-64 items-center justify-center rounded-2xl border border-dashed border-white/8 px-4 py-6 text-center text-xs text-slate-600">Elige al menos un movimiento para ver daño.</div>}
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

export function DamageCalculatorView({ source, format, dex, session: savedSession, onSessionChange }: DamageCalculatorProps) {
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

  return (
    <div className="w-full min-w-0 space-y-4 p-4 text-slate-100 sm:p-5">
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_210px_minmax(0,1fr)]">
        <CalculatorPokemonPanel side="left" draft={left} onChange={setLeft} format={format} dex={dex} />
        <div className="order-first xl:order-none"><FieldPanel value={field} onChange={setField} /><div className="mt-3 hidden items-center justify-center gap-2 text-[9px] font-black uppercase tracking-[0.13em] text-slate-700 xl:flex"><ShieldCheck className="size-3.5" /><ArrowLeftRight className="size-3.5" /><Swords className="size-3.5" /></div></div>
        <CalculatorPokemonPanel side="right" draft={right} onChange={setRight} format={format} dex={dex} />
      </div>
      <OutcomeList title="Daño infligido" attacker={left.set.species} defender={right.set.species} outcomes={leftOutcomes} />
      <OutcomeList title="Daño recibido" attacker={right.set.species} defender={left.set.species} outcomes={rightOutcomes} />
      <div className="rounded-2xl border border-amber-300/10 bg-amber-300/5 px-4 py-3 text-[10px] leading-5 text-amber-100/65"><Sparkles className="mr-2 inline size-3.5 text-amber-300" />Motor oficial de Pokémon Showdown · los resultados se recalculan localmente con cada cambio.</div>
    </div>
  );
}
