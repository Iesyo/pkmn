import Image from "next/image";
import { Crown, Route } from "lucide-react";

import { getSpriteUrl } from "@/lib/pokemon-data";
import { winRate } from "@/lib/team-stats";
import type { LeadStat } from "@/lib/types";

function LeadRow({ lead, best = false }: { lead: LeadStat; best?: boolean }) {
  return (
    <div className="grid grid-cols-[68px_minmax(0,1fr)_42px] items-center gap-2 rounded-xl border border-white/7 bg-black/15 p-2">
      <div className="flex -space-x-3">
        {lead.species.map((species) => (
          <div key={species} className="flex size-10 items-center justify-center rounded-full border border-slate-700 bg-slate-900">
            <Image src={getSpriteUrl(species)} alt={species} width={40} height={40} unoptimized className="h-9 w-9 object-contain" />
          </div>
        ))}
      </div>
      <div className="min-w-0">
        <p className="truncate text-[10px] font-semibold text-slate-300">{lead.species.join(" + ")}</p>
        <p className="mt-0.5 text-[9px] text-slate-500">{lead.wins} de {lead.games} partidas</p>
      </div>
      <div className="text-right">
        {best ? <Crown className="ml-auto mb-0.5 size-3 text-amber-300" /> : null}
        <span className="text-xs font-black tabular-nums text-white">{winRate(lead.wins, lead.games)}%</span>
      </div>
    </div>
  );
}

export function LeadsPanel({ leads }: { leads: LeadStat[] }) {
  const common = leads.slice(0, 3);
  const best = [...leads].sort((a, b) => winRate(b.wins, b.games) - winRate(a.wins, a.games) || b.games - a.games).slice(0, 3);

  return (
    <section className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-2xl border border-white/8 bg-slate-950/65 p-3">
        <h3 className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><Route className="size-3.5 text-cyan-300" />Leads más usados</h3>
        <div className="space-y-2">
          {common.map((lead) => <LeadRow key={lead.species.join("-")} lead={lead} />)}
          {!common.length ? <p className="rounded-xl border border-dashed border-white/10 p-4 text-center text-[10px] text-slate-500">Registra partidas para descubrir tus leads frecuentes.</p> : null}
        </div>
      </div>
      <div className="rounded-2xl border border-white/8 bg-slate-950/65 p-3">
        <h3 className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><Crown className="size-3.5 text-amber-300" />Mejores leads</h3>
        <div className="space-y-2">
          {best.map((lead) => <LeadRow key={lead.species.join("-")} lead={lead} best />)}
          {!best.length ? <p className="rounded-xl border border-dashed border-white/10 p-4 text-center text-[10px] text-slate-500">Aún no hay muestra suficiente.</p> : null}
        </div>
      </div>
    </section>
  );
}
