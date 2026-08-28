import Image from "next/image";
import { Activity, Crosshair, Gauge, ShieldCheck, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { winRate } from "@/lib/team-stats";
import type { PokemonSet } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TypeBadge } from "./type-badge";

const accents = {
  cyan: {
    border: "border-cyan-400/20 hover:border-cyan-300/45",
    glow: "from-cyan-400/15 via-transparent to-transparent",
    metric: "text-cyan-200",
    bar: "[&_[data-slot=progress-indicator]]:bg-cyan-300",
  },
  violet: {
    border: "border-fuchsia-400/20 hover:border-fuchsia-300/45",
    glow: "from-fuchsia-400/15 via-transparent to-transparent",
    metric: "text-fuchsia-200",
    bar: "[&_[data-slot=progress-indicator]]:bg-fuchsia-300",
  },
} as const;

function Metric({ label, value, detail, accent }: { label: string; value: string; detail: string; accent: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-slate-500">{label}</p>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className={cn("text-sm font-black tabular-nums", accent)}>{value}</span>
        <span className="truncate text-[9px] text-slate-500">{detail}</span>
      </div>
    </div>
  );
}

export function PokemonCard({ pokemon, accent }: { pokemon: PokemonSet; accent: keyof typeof accents }) {
  const style = accents[accent];
  const performance = pokemon.performance;

  return (
    <article className={cn("group relative overflow-hidden rounded-2xl border bg-slate-950/75 shadow-[0_18px_45px_rgba(0,0,0,0.18)] transition", style.border)}>
      <div className={cn("pointer-events-none absolute inset-0 bg-gradient-to-br opacity-80", style.glow)} />
      <div className="relative grid grid-cols-[72px_minmax(0,1fr)] gap-3 border-b border-white/7 p-3">
        <div className="flex h-[72px] items-center justify-center rounded-xl border border-white/8 bg-black/25">
          <Image
            src={getSpriteUrl(pokemon.species)}
            alt={`Sprite de ${pokemon.species}`}
            width={72}
            height={72}
            unoptimized
            className="h-16 w-16 object-contain drop-shadow-[0_8px_15px_rgba(0,0,0,0.55)] transition group-hover:-translate-y-0.5"
          />
        </div>
        <div className="min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-black tracking-tight text-white">{pokemon.nickname}</h3>
              {pokemon.nickname !== pokemon.species ? <p className="truncate text-[10px] text-slate-500">{pokemon.species}</p> : null}
            </div>
            <Badge variant="outline" className="h-5 shrink-0 border-white/10 bg-white/4 px-1.5 text-[9px] text-slate-400">#{pokemon.slot}</Badge>
          </div>
          <p className="mt-1 truncate text-[11px] font-medium text-slate-300">@ {pokemon.item || "Sin objeto"}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {pokemon.types.map((type) => <TypeBadge key={type} type={type} />)}
            {pokemon.teraType ? <TypeBadge type={pokemon.teraType} className="border-dashed"><Sparkles className="mr-1 size-2.5" />Tera {pokemon.teraType}</TypeBadge> : null}
          </div>
        </div>
      </div>

      <div className="relative grid grid-cols-2 gap-3 border-b border-white/7 bg-black/15 px-3 py-2.5">
        <Metric label="Win %" value={`${winRate(performance.wins, performance.games)}%`} detail={`${performance.wins} de ${performance.games}`} accent={style.metric} />
        <Metric label="Lead win %" value={`${winRate(performance.leadWins, performance.leadGames)}%`} detail={`${performance.leadWins} de ${performance.leadGames}`} accent={style.metric} />
      </div>

      <div className="relative space-y-2.5 p-3">
        <div className="grid gap-1 text-[10px] leading-4 text-slate-400">
          <p className="flex items-center gap-1.5"><ShieldCheck className="size-3 text-slate-500" /><span className="text-slate-500">Ability</span><strong className="font-semibold text-slate-200">{pokemon.ability || "—"}</strong></p>
          <p className="flex items-center gap-1.5"><Gauge className="size-3 text-slate-500" /><span className="text-slate-500">Nivel</span><strong className="font-mono font-medium text-slate-200">{pokemon.level}</strong></p>
          <p className="flex items-start gap-1.5"><Activity className="mt-0.5 size-3 shrink-0 text-slate-500" /><span className="shrink-0 text-slate-500">EVs</span><strong className="font-mono font-medium text-slate-300">{pokemon.evs || "No indicados"}</strong></p>
          <p className="flex items-center gap-1.5"><Crosshair className="size-3 text-slate-500" /><strong className="font-semibold text-slate-200">{pokemon.nature || "Naturaleza no indicada"}</strong></p>
        </div>

        <div className="space-y-2 border-t border-white/7 pt-2.5">
          {pokemon.moves.map((move) => (
            <div key={move.name} className="grid grid-cols-[minmax(0,1fr)_34px] items-center gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[11px] font-semibold text-slate-200">{move.name}</span>
                  {move.type ? <TypeBadge type={move.type} className="px-1.5 py-0 text-[8px]">{move.type.slice(0, 3)}</TypeBadge> : null}
                </div>
                <Progress value={move.usage} className={cn("mt-1 h-1 bg-white/6", style.bar)} />
              </div>
              <span className="text-right font-mono text-[9px] tabular-nums text-slate-500">{move.usage}%</span>
            </div>
          ))}
        </div>
      </div>
    </article>
  );
}
