import Image from "next/image";
import { ExternalLink, History, LockKeyhole, Trophy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AddMatchDialog } from "@/components/vgc/team-dialogs";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { TeamVersion } from "@/lib/types";

const dateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit",
  month: "short",
});

function OpponentTeam({ species }: { species: string[] }) {
  if (!species.length) return <span className="text-slate-700">Sin registrar</span>;

  return (
    <span className="flex min-w-32 items-center gap-0.5" aria-label={`Equipo rival: ${species.join(", ")}`}>
      {species.slice(0, 6).map((name) => (
        <Image key={name} src={getSpriteUrl(name)} alt={name} title={name} width={24} height={24} unoptimized className="size-6 object-contain" />
      ))}
    </span>
  );
}

export function MatchHistory({ version, onMatchCreated }: { version: TeamVersion; onMatchCreated?: () => void }) {
  const matches = version.matches;
  return (
    <section className="overflow-hidden rounded-2xl border border-white/8 bg-slate-950/65">
      <div className="flex items-center justify-between border-b border-white/7 px-4 py-3">
        <h3 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><History className="size-3.5 text-violet-300" />Historial reciente</h3>
        <span className="text-[10px] text-slate-600">Últimas {Math.min(matches.length, 5)}</span>
      </div>
      <div className="flex flex-col gap-3 border-b border-white/7 bg-white/[0.018] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-bold text-slate-300">Agregar partida</p>
          <p className="mt-0.5 text-[10px] text-slate-600">Resultado, replay, tus picks y los seis Pokémon vistos del rival.</p>
        </div>
        {!version.demo && onMatchCreated ? (
          <AddMatchDialog version={version} onCreated={onMatchCreated} />
        ) : (
          <Button disabled variant="outline" className="gap-2 rounded-full border-white/8 bg-white/3 text-slate-600"><LockKeyhole className="size-3.5" />Guarda un equipo para registrar</Button>
        )}
      </div>
      {matches.length ? (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="border-white/7 hover:bg-transparent">
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Partida</TableHead>
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Rival</TableHead>
                <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Equipo rival</TableHead>
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
                  <TableCell><OpponentTeam species={match.opponentSelected} /></TableCell>
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
