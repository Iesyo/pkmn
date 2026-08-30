"use client";

import { Database, Minus, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { getDisplayedEffectiveStat, type DamageStat } from "@/lib/damage-calculator";
import type { BaseStats } from "@/lib/showdown-data";
import { EV_STATS, calculateStat, getNatureEffect, getStatRules, parseEvs, serializeEvs } from "@/lib/team-builder";
import type { PokemonSet } from "@/lib/types";
import { cn } from "@/lib/utils";

type EditableStat = (typeof EV_STATS)[number];
export type BoostableStat = Exclude<EditableStat, "HP">;

const BOOST_VALUES = Array.from({ length: 13 }, (_, index) => 6 - index);
const DAMAGE_STAT_KEYS: Record<BoostableStat, DamageStat> = {
  Atk: "atk",
  Def: "def",
  SpA: "spa",
  SpD: "spd",
  Spe: "spe",
};

export function PokemonStatEditor({
  pokemon,
  format,
  baseStats,
  onChange,
  boosts,
  onBoostChange,
  tailwind = false,
  stableHeight = false,
}: {
  pokemon: PokemonSet;
  format: string;
  baseStats: BaseStats | null;
  onChange: (next: PokemonSet) => void;
  boosts?: Record<BoostableStat, number>;
  onBoostChange?: (stat: BoostableStat, value: number) => void;
  tailwind?: boolean;
  stableHeight?: boolean;
}) {
  const values = parseEvs(pokemon.evs);
  const rules = getStatRules(format);
  const total = Object.values(values).reduce((sum, value) => sum + value, 0);
  const showBoosts = boosts !== undefined && onBoostChange !== undefined;
  const gridColumns = showBoosts
    ? "grid-cols-[28px_30px_minmax(56px,1fr)_42px_34px_44px_46px] gap-x-1 sm:grid-cols-[30px_34px_minmax(100px,1fr)_44px_38px_48px_48px] sm:gap-x-1.5"
    : "grid-cols-[28px_34px_minmax(70px,1fr)_50px_40px] gap-x-2 sm:grid-cols-[30px_38px_minmax(120px,1fr)_50px_42px]";

  function setAllocation(stat: EditableStat, requested: number) {
    const otherTotal = total - values[stat];
    const rounded = Math.round(requested / rules.step) * rules.step;
    const nextValue = Math.max(0, Math.min(rules.perStatMax, rules.totalMax - otherTotal, rounded));
    onChange({ ...pokemon, evs: serializeEvs({ ...values, [stat]: nextValue }) });
  }

  if (!baseStats) {
    return (
      <div className={cn("rounded-2xl border border-dashed border-white/10 bg-black/15 p-5 text-center", stableHeight && "flex min-h-[23rem] flex-col items-center justify-center")}>
        <Database className="mx-auto size-5 text-slate-600" />
        <p className="mt-2 text-xs font-bold text-slate-400">Elige un Pokémon para ver sus stats base.</p>
      </div>
    );
  }

  return (
    <div className={cn("rounded-2xl border border-white/8 bg-black/20 p-3", stableHeight && "min-h-[23rem]")}>
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-300">{rules.label}</p>
          <p className="mt-1 text-[9px] text-slate-600">Nivel {pokemon.level} · IVs perfectos</p>
        </div>
        <Badge variant="outline" className={total <= rules.totalMax ? "border-emerald-300/20 text-emerald-300" : "border-rose-300/25 text-rose-300"}>{total}/{rules.totalMax}</Badge>
      </div>
      <div className={cn("mt-4 grid items-center text-[8px] font-black uppercase tracking-[0.08em] text-slate-600", gridColumns)}>
        <span />
        <span className="text-center">Base</span>
        <span className="text-center">{rules.shortLabel}</span>
        <span />
        <span className="text-right">Total</span>
        {showBoosts ? <span className="text-center" title="Stat tras boost y Tailwind">Efect.</span> : null}
        {showBoosts ? <span className="text-center">Boost</span> : null}
      </div>
      <div className="mt-1 space-y-1.5">
        {EV_STATS.map((stat) => {
          const nature = getNatureEffect(pokemon.nature, stat);
          const finalStat = calculateStat(baseStats, stat, values[stat], pokemon.level, pokemon.nature, format);
          const boost = stat === "HP" ? 0 : boosts?.[stat] ?? 0;
          const effectiveStat = stat === "HP"
            ? finalStat
            : getDisplayedEffectiveStat(DAMAGE_STAT_KEYS[stat], finalStat, boost, stat === "Spe" && tailwind);
          const effectiveChanged = stat !== "HP" && effectiveStat !== finalStat;
          return (
            <div key={stat} className={cn("grid items-center rounded-lg px-1 py-1.5 hover:bg-white/[0.025]", gridColumns)}>
              <span className={cn("text-[10px] font-black", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-400" : "text-slate-500")}>{stat}</span>
              <span className="text-center text-[10px] font-bold text-slate-500">{baseStats[{ HP: "hp", Atk: "atk", Def: "def", SpA: "spa", SpD: "spd", Spe: "spe" }[stat] as keyof BaseStats]}</span>
              <div className="flex min-w-0 items-center gap-1.5">
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] <= 0} onClick={() => setAllocation(stat, values[stat] - rules.step)} aria-label={`Restar ${rules.shortLabel} de ${stat}`}><Minus className="size-3" /></Button>
                <Slider value={[values[stat]]} min={0} max={rules.perStatMax} step={rules.step} onValueChange={(next) => setAllocation(stat, next[0] ?? 0)} aria-label={`${rules.label} de ${stat}`} className={cn("min-w-0", nature === "plus" && "[&_[data-slot=slider-range]]:bg-rose-400 [&_[data-slot=slider-thumb]]:border-rose-400", nature === "minus" && "[&_[data-slot=slider-range]]:bg-cyan-400 [&_[data-slot=slider-thumb]]:border-cyan-400")} />
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] >= rules.perStatMax || total >= rules.totalMax} onClick={() => setAllocation(stat, values[stat] + rules.step)} aria-label={`Sumar ${rules.shortLabel} a ${stat}`}><Plus className="size-3" /></Button>
              </div>
              <Input aria-label={`${rules.shortLabel} de ${stat}`} type="number" min={0} max={rules.perStatMax} step={rules.step} value={values[stat]} onChange={(event) => setAllocation(stat, Number(event.target.value) || 0)} className="h-7 border-white/8 bg-black/25 px-1 text-center text-[10px]" />
              <strong className={cn("text-right text-xs", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-300" : "text-slate-200")}>{finalStat}</strong>
              {showBoosts ? stat === "HP" ? <span className="text-center text-[10px] font-bold text-slate-700">—</span> : <strong title={stat === "Spe" && tailwind ? "Después de boost y Tailwind" : "Después del boost"} className={cn("text-center text-xs font-black tabular-nums", effectiveChanged ? "text-amber-200" : "text-slate-500")}>{effectiveStat}</strong> : null}
              {showBoosts ? stat === "HP" ? <span aria-hidden="true" /> : (
                <Select value={String(boosts[stat])} onValueChange={(value) => onBoostChange(stat, Number(value))}>
                  <SelectTrigger aria-label={`Boost de ${stat}`} className="h-7 w-full border-white/8 bg-black/25 px-1 text-[9px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BOOST_VALUES.map((value) => <SelectItem key={value} value={String(value)}>{value === 0 ? "—" : value > 0 ? `+${value}` : value}</SelectItem>)}
                  </SelectContent>
                </Select>
              ) : null}
            </div>
          );
        })}
      </div>
      <p className="mt-3 rounded-lg border border-white/6 bg-white/[0.025] px-2.5 py-2 font-mono text-[9px] text-slate-500">{pokemon.evs || `Sin ${rules.label.toLowerCase()} asignados`}</p>
    </div>
  );
}
