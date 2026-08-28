import Image from "next/image";
import { BarChart3, Eye, TrendingDown, TrendingUp } from "lucide-react";

import { getSpriteUrl } from "@/lib/pokemon-data";
import {
  calculateOpponentPokemonStats,
  type OpponentPokemonStat,
} from "@/lib/team-stats";
import type { MatchRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

const panelStyles = {
  best: {
    border: "border-emerald-300/25",
    background: "bg-emerald-300/[0.045]",
    icon: "text-emerald-300",
    metric: "text-emerald-200",
  },
  worst: {
    border: "border-rose-300/25",
    background: "bg-rose-300/[0.045]",
    icon: "text-rose-300",
    metric: "text-rose-200",
  },
  highest: {
    border: "border-violet-300/25",
    background: "bg-violet-300/[0.045]",
    icon: "text-violet-300",
    metric: "text-violet-200",
  },
  lowest: {
    border: "border-amber-300/25",
    background: "bg-amber-300/[0.045]",
    icon: "text-amber-300",
    metric: "text-amber-200",
  },
} as const;

function StatPanel({
  title,
  stats,
  kind,
  totalGames,
  icon: Icon,
}: {
  title: string;
  stats: OpponentPokemonStat[];
  kind: keyof typeof panelStyles;
  totalGames: number;
  icon: React.ComponentType<{ className?: string }>;
}) {
  const style = panelStyles[kind];
  const isAttendance = kind === "highest" || kind === "lowest";
  const rows: Array<OpponentPokemonStat | null> = [
    ...stats.slice(0, 5),
    ...Array(Math.max(0, 5 - stats.length)).fill(null),
  ];

  return (
    <article className={cn("overflow-hidden rounded-2xl border", style.border, style.background)}>
      <div className="flex items-center gap-2 border-b border-white/7 px-3 py-2.5">
        <Icon className={cn("size-3.5", style.icon)} />
        <h4 className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-300">{title}</h4>
      </div>
      <div className="divide-y divide-white/6 px-2">
        {rows.map((stat, index) => (
          <div key={stat?.species ?? `empty-${index}`} className="grid min-h-10 grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-2 px-1 py-1.5">
            {stat ? (
              <Image src={getSpriteUrl(stat.species)} alt="" width={28} height={28} unoptimized className="size-7 object-contain" />
            ) : (
              <span className="mx-auto size-1 rounded-full bg-white/10" />
            )}
            <div className="min-w-0">
              <p className="truncate text-[10px] font-semibold text-slate-300">{stat?.species ?? "Sin datos"}</p>
              <p className="text-[9px] text-slate-600">
                {stat ? (isAttendance ? `visto en ${stat.games} de ${totalGames}` : `${stat.wins} de ${stat.games} ganadas`) : "Registra partidas para calcularlo"}
              </p>
            </div>
            <span className={cn("font-mono text-[10px] font-bold tabular-nums", stat ? style.metric : "text-slate-700")}>
              {stat ? `${isAttendance ? stat.attendanceRate : stat.winRate}%` : "—"}
            </span>
          </div>
        ))}
      </div>
    </article>
  );
}

export function MatchupAttendance({ matches }: { matches: MatchRecord[] }) {
  const stats = calculateOpponentPokemonStats(matches);
  const byBest = [...stats].sort((a, b) => b.winRate - a.winRate || b.games - a.games || a.species.localeCompare(b.species));
  const byWorst = [...stats].sort((a, b) => a.winRate - b.winRate || b.games - a.games || a.species.localeCompare(b.species));
  const byHighest = [...stats].sort((a, b) => b.attendanceRate - a.attendanceRate || b.games - a.games || a.species.localeCompare(b.species));
  const byLowest = [...stats].sort((a, b) => a.attendanceRate - b.attendanceRate || b.games - a.games || a.species.localeCompare(b.species));

  return (
    <section className="rounded-2xl border border-white/8 bg-slate-950/45 p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><BarChart3 className="size-3.5 text-cyan-300" />Matchups & Attendance</h3>
          <p className="mt-1 text-[10px] text-slate-600">Rendimiento y frecuencia por cada Pokémon visto en el equipo rival.</p>
        </div>
        <span className="text-[9px] uppercase tracking-wider text-slate-700">{matches.length} partidas base</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <StatPanel title="Best Matchups" stats={byBest} kind="best" totalGames={matches.length} icon={TrendingUp} />
        <StatPanel title="Worst Matchups" stats={byWorst} kind="worst" totalGames={matches.length} icon={TrendingDown} />
        <StatPanel title="Highest Attendance" stats={byHighest} kind="highest" totalGames={matches.length} icon={Eye} />
        <StatPanel title="Lowest Attendance" stats={byLowest} kind="lowest" totalGames={matches.length} icon={Eye} />
      </div>
    </section>
  );
}
