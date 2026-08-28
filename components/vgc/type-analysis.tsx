"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, EyeOff, Shield, Sparkles, Swords } from "lucide-react";

import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { SHOWDOWN_SNAPSHOT } from "@/lib/pokemon-data";
import { analyzeTypes } from "@/lib/team-stats";
import type { PokemonSet } from "@/lib/types";
import { TypeBadge } from "./type-badge";

export function TypeAnalysis({ pokemon, allowTera = true }: { pokemon: PokemonSet[]; allowTera?: boolean }) {
  const [useTera, setUseTera] = useState(false);
  const analysis = useMemo(() => analyzeTypes(pokemon, allowTera && useTera), [pokemon, allowTera, useTera]);
  const weaknesses = analysis.defense.filter((entry) => entry.count > 0);

  return (
    <section className="rounded-2xl border border-white/8 bg-slate-950/65 p-4 shadow-[0_18px_50px_rgba(0,0,0,0.18)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.16em] text-slate-200">
            <Shield className="size-4 text-cyan-300" />Análisis de tipos
          </div>
          <p className="mt-1 text-[10px] text-slate-500">{SHOWDOWN_SNAPSHOT.label} · base defensiva del equipo</p>
        </div>
        {allowTera ? <div className="flex items-center gap-2 rounded-full border border-white/8 bg-white/4 px-3 py-1.5">
          <Sparkles className="size-3 text-amber-300" />
          <Label htmlFor="tera-view" className="text-[10px] font-semibold text-slate-300">Vista Tera</Label>
          <Switch id="tera-view" checked={useTera} onCheckedChange={setUseTera} aria-label="Alternar análisis con tipos Tera" />
        </div> : null}
      </div>

      <div className="mt-4">
        <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500"><Swords className="size-3" />Cobertura ofensiva</div>
        <div className="grid grid-cols-6 gap-1.5 sm:grid-cols-9">
          {analysis.coverage.map((entry) => (
            <div key={entry.type} className="rounded-lg border border-white/6 bg-white/[0.025] p-1.5 text-center">
              <TypeBadge type={entry.type} className="w-full px-1 text-[8px]">{entry.type.slice(0, 3)}</TypeBadge>
              <p className={entry.count ? "mt-1 text-[10px] font-black text-emerald-300" : "mt-1 text-[10px] font-black text-slate-600"}>{entry.count}×</p>
            </div>
          ))}
        </div>
      </div>

      <Separator className="my-4 bg-white/7" />

      <div className="grid gap-4 xl:grid-cols-2">
        <div>
          <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500"><AlertTriangle className="size-3 text-amber-300" />Debilidades defensivas</div>
          <div className="flex flex-wrap gap-1.5">
            {weaknesses.map((entry) => (
              <TypeBadge key={entry.type} type={entry.type} className="gap-1 py-1"><span>{entry.type}</span><strong className="text-rose-300">{entry.count}↓</strong>{entry.resistances ? <span className="text-emerald-300">{entry.resistances}↑</span> : null}{entry.immunities ? <span className="text-sky-300">{entry.immunities}◎</span> : null}</TypeBadge>
            ))}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Resistencias e inmunidades</div>
            <div className="flex flex-wrap gap-1.5">
              {analysis.resistances.slice(0, 8).map((entry) => <TypeBadge key={entry.type} type={entry.type}>{entry.type} {entry.count}↑</TypeBadge>)}
              {analysis.immunities.map((entry) => <TypeBadge key={entry.type} type={entry.type}>{entry.type} {entry.count}◎</TypeBadge>)}
            </div>
          </div>
          <div>
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500"><EyeOff className="size-3" />Puntos ciegos</div>
            <div className="flex flex-wrap gap-1.5">
              {analysis.blindSpots.map((type) => <TypeBadge key={type} type={type}>{type}</TypeBadge>)}
              {!analysis.blindSpots.length ? <span className="text-[10px] text-emerald-300">Sin huecos básicos</span> : null}
            </div>
          </div>
        </div>
      </div>

      {analysis.conditionals.length ? (
        <div className="mt-4 rounded-xl border border-amber-300/15 bg-amber-300/5 px-3 py-2 text-[10px] leading-4 text-amber-100/75">
          <strong className="text-amber-200">Efectos condicionales:</strong> {analysis.conditionals.join(" · ")}. No se mezclan con el cálculo base.
        </div>
      ) : null}
    </section>
  );
}
