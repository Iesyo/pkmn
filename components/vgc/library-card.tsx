import Image from "next/image";
import { ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { winRate } from "@/lib/team-stats";
import type { TeamGroup } from "@/lib/types";
import { cn } from "@/lib/utils";

export function LibraryCard({ team, selected, onClick }: { team: TeamGroup; selected: boolean; onClick: () => void }) {
  const latest = team.versions[0];
  if (!latest) return null;
  return (
    <button type="button" onClick={onClick} className={cn("group w-full rounded-2xl border p-3 text-left transition", selected ? "border-cyan-300/30 bg-cyan-300/8 shadow-[0_16px_40px_rgba(34,211,238,0.08)]" : "border-white/7 bg-white/[0.025] hover:border-white/15 hover:bg-white/5")}>
      <div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="truncate text-sm font-bold text-white">{team.name}</p><p className="mt-0.5 text-[10px] text-slate-500">{team.versions.length} {team.versions.length === 1 ? "versión" : "versiones"}</p></div><div className="flex items-center gap-1"><Badge variant="outline" className="border-white/10 bg-white/4 text-[9px] text-slate-400">v{latest.version}</Badge><ChevronRight className="size-4 text-slate-600 transition group-hover:translate-x-0.5" /></div></div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div className="flex -space-x-2.5">
          {latest.pokemon.map((pokemon) => <Image key={pokemon.id} src={getSpriteUrl(pokemon.species)} alt="" width={30} height={30} unoptimized className="h-7 w-7 rounded-full border border-slate-800 bg-slate-900 object-contain" />)}
        </div>
        <div className="text-right"><p className="text-sm font-black tabular-nums text-white">{winRate(latest.wins, latest.games)}%</p><p className="text-[9px] text-slate-600">{latest.games} games</p></div>
      </div>
    </button>
  );
}
