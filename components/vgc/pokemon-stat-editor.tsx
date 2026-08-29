"use client";

import { Database, Minus, Plus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import type { BaseStats } from "@/lib/showdown-data";
import { EV_STATS, calculateStat, getNatureEffect, getStatRules, parseEvs, serializeEvs } from "@/lib/team-builder";
import type { PokemonSet } from "@/lib/types";
import { cn } from "@/lib/utils";

export function PokemonStatEditor({
  pokemon,
  format,
  baseStats,
  onChange,
}: {
  pokemon: PokemonSet;
  format: string;
  baseStats: BaseStats | null;
  onChange: (next: PokemonSet) => void;
}) {
  const values = parseEvs(pokemon.evs);
  const rules = getStatRules(format);
  const total = Object.values(values).reduce((sum, value) => sum + value, 0);

  function setAllocation(stat: (typeof EV_STATS)[number], requested: number) {
    const otherTotal = total - values[stat];
    const rounded = Math.round(requested / rules.step) * rules.step;
    const nextValue = Math.max(0, Math.min(rules.perStatMax, rules.totalMax - otherTotal, rounded));
    onChange({ ...pokemon, evs: serializeEvs({ ...values, [stat]: nextValue }) });
  }

  if (!baseStats) {
    return (
      <div className="rounded-2xl border border-dashed border-white/10 bg-black/15 p-5 text-center">
        <Database className="mx-auto size-5 text-slate-600" />
        <p className="mt-2 text-xs font-bold text-slate-400">Elige un Pokémon para ver sus stats base.</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/8 bg-black/20 p-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-300">{rules.label}</p>
          <p className="mt-1 text-[9px] text-slate-600">Nivel {pokemon.level} · IVs perfectos</p>
        </div>
        <Badge variant="outline" className={total <= rules.totalMax ? "border-emerald-300/20 text-emerald-300" : "border-rose-300/25 text-rose-300"}>{total}/{rules.totalMax}</Badge>
      </div>
      <div className="mt-4 grid grid-cols-[28px_34px_minmax(70px,1fr)_50px_40px] items-center gap-x-2 text-[8px] font-black uppercase tracking-[0.08em] text-slate-600 sm:grid-cols-[30px_38px_minmax(120px,1fr)_50px_42px]">
        <span />
        <span className="text-center">Base</span>
        <span className="text-center">{rules.shortLabel}</span>
        <span />
        <span className="text-right">Total</span>
      </div>
      <div className="mt-1 space-y-1.5">
        {EV_STATS.map((stat) => {
          const nature = getNatureEffect(pokemon.nature, stat);
          const finalStat = calculateStat(baseStats, stat, values[stat], pokemon.level, pokemon.nature, format);
          return (
            <div key={stat} className="grid grid-cols-[28px_34px_minmax(70px,1fr)_50px_40px] items-center gap-x-2 rounded-lg px-1 py-1.5 hover:bg-white/[0.025] sm:grid-cols-[30px_38px_minmax(120px,1fr)_50px_42px]">
              <span className={cn("text-[10px] font-black", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-400" : "text-slate-500")}>{stat}</span>
              <span className="text-center text-[10px] font-bold text-slate-500">{baseStats[{ HP: "hp", Atk: "atk", Def: "def", SpA: "spa", SpD: "spd", Spe: "spe" }[stat] as keyof BaseStats]}</span>
              <div className="flex min-w-0 items-center gap-1.5">
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] <= 0} onClick={() => setAllocation(stat, values[stat] - rules.step)} aria-label={`Restar ${rules.shortLabel} de ${stat}`}><Minus className="size-3" /></Button>
                <Slider value={[values[stat]]} min={0} max={rules.perStatMax} step={rules.step} onValueChange={(next) => setAllocation(stat, next[0] ?? 0)} aria-label={`${rules.label} de ${stat}`} className={cn("min-w-0", nature === "plus" && "[&_[data-slot=slider-range]]:bg-rose-400 [&_[data-slot=slider-thumb]]:border-rose-400", nature === "minus" && "[&_[data-slot=slider-range]]:bg-cyan-400 [&_[data-slot=slider-thumb]]:border-cyan-400")} />
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] >= rules.perStatMax || total >= rules.totalMax} onClick={() => setAllocation(stat, values[stat] + rules.step)} aria-label={`Sumar ${rules.shortLabel} a ${stat}`}><Plus className="size-3" /></Button>
              </div>
              <Input aria-label={`${rules.shortLabel} de ${stat}`} type="number" min={0} max={rules.perStatMax} step={rules.step} value={values[stat]} onChange={(event) => setAllocation(stat, Number(event.target.value) || 0)} className="h-7 border-white/8 bg-black/25 px-1 text-center text-[10px]" />
              <strong className={cn("text-right text-xs", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-300" : "text-slate-200")}>{finalStat}</strong>
            </div>
          );
        })}
      </div>
      <p className="mt-3 rounded-lg border border-white/6 bg-white/[0.025] px-2.5 py-2 font-mono text-[9px] text-slate-500">{pokemon.evs || `Sin ${rules.label.toLowerCase()} asignados`}</p>
    </div>
  );
}
