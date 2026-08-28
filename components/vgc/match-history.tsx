import { ExternalLink, History, Trophy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { MatchRecord } from "@/lib/types";

const dateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit",
  month: "short",
});

export function MatchHistory({ matches }: { matches: MatchRecord[] }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-white/8 bg-slate-950/65">
      <div className="flex items-center justify-between border-b border-white/7 px-4 py-3">
        <h3 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><History className="size-3.5 text-violet-300" />Historial reciente</h3>
        <span className="text-[10px] text-slate-600">Últimas {Math.min(matches.length, 5)}</span>
      </div>
      {matches.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="border-white/7 hover:bg-transparent">
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Partida</TableHead>
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Rival</TableHead>
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Tus picks</TableHead>
                <TableHead className="h-9 text-right text-[9px] uppercase tracking-wider text-slate-600">Rating</TableHead>
                <TableHead className="h-9 text-right text-[9px] uppercase tracking-wider text-slate-600">Replay</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {matches.slice(0, 5).map((match, index) => (
                <TableRow key={match.id} className="border-white/6 text-[10px] hover:bg-white/[0.025]">
                  <TableCell className="py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-slate-600">{String(index + 1).padStart(2, "0")}</span>
                      <Badge className={match.result === "win" ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-200" : "border-rose-300/20 bg-rose-300/10 text-rose-200"} variant="outline">
                        {match.result === "win" ? "WIN" : "LOSS"}
                      </Badge>
                      <span className="hidden text-slate-600 sm:inline">{dateFormatter.format(new Date(match.playedAt))}</span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-36 truncate font-medium text-slate-300">{match.opponentName}</TableCell>
                  <TableCell className="max-w-52 truncate text-slate-500">{match.selected.length ? match.selected.join(" · ") : "Sin selección"}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums text-slate-400">{match.rating ?? "—"}</TableCell>
                  <TableCell className="text-right">
                    {match.replayUrl ? (
                      <a href={match.replayUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 transition hover:text-cyan-200">
                        Ver <ExternalLink className="size-3" />
                      </a>
                    ) : <span className="text-slate-700">—</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="flex flex-col items-center px-6 py-10 text-center">
          <Trophy className="size-6 text-slate-700" />
          <p className="mt-2 text-xs font-semibold text-slate-400">Todavía no hay partidas</p>
          <p className="mt-1 max-w-xs text-[10px] leading-4 text-slate-600">Registra el primer resultado con su replay para empezar a medir este equipo.</p>
        </div>
      )}
    </section>
  );
}
