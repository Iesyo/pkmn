import Image from "next/image";
import { ExternalLink, History, ScanSearch, Trophy } from "lucide-react";

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
import { ReplayQuickEntry } from "@/components/vgc/team-dialogs";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { MatchRecord, TeamVersion } from "@/lib/types";

const dateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit",
  month: "short",
});

function PokemonSpriteStrip({ species, label, tone = "violet", limit = 6 }: { species: string[]; label: string; tone?: "cyan" | "violet"; limit?: number }) {
  if (!species.length) return <span className="text-slate-700">Sin registrar</span>;

  return (
    <span className={tone === "cyan" ? "inline-flex min-w-max overflow-hidden rounded-md border border-cyan-300/15 bg-cyan-300/8" : "inline-flex min-w-max overflow-hidden rounded-md border border-violet-300/15 bg-violet-300/8"} aria-label={`${label}: ${species.join(", ")}`}>
      {species.slice(0, limit).map((name) => (
        <span key={name} className="flex size-8 items-center justify-center border-r border-white/8 last:border-r-0">
          <Image src={getSpriteUrl(name)} alt={name} title={name} width={30} height={30} unoptimized className="size-7 object-contain" />
        </span>
      ))}
    </span>
  );
}

export function MatchHistory({
  version,
  onMatchCreated,
  onScoutingRequested,
}: {
  version: TeamVersion;
  onMatchCreated?: () => void;
  onScoutingRequested?: (version: TeamVersion, match: MatchRecord) => void;
}) {
  const matches = version.matches;
  return (
    <section className="overflow-hidden rounded-2xl border border-white/8 bg-slate-950/65">
      <div className="flex items-center justify-between border-b border-white/7 px-4 py-3">
        <h3 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><History className="size-3.5 text-violet-300" />Historial reciente</h3>
        <span className="text-[10px] text-slate-600">Últimas {Math.min(matches.length, 5)}</span>
      </div>
      <div className="border-b border-white/7 bg-white/[0.018] px-4 py-3">
        <div className="mb-2.5"><p className="text-xs font-bold text-slate-300">Agregar replay</p><p className="mt-0.5 text-[10px] text-slate-600">Pega el enlace: Showdown completa automáticamente el resultado y las selecciones.</p></div>
        <ReplayQuickEntry version={version} onCreated={onMatchCreated} />
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
                  <TableCell>
                    <div className="flex min-w-max items-center gap-2">
                      <PokemonSpriteStrip species={match.opponentSelected} label="Equipo rival" />
                      <Button
                        type="button"
                        variant="outline"
                        size="icon-sm"
                        disabled={version.demo || !match.replayUrl || !onScoutingRequested}
                        onClick={() => onScoutingRequested?.(version, match)}
                        title={version.demo ? "Guarda una partida real para analizarla" : match.replayUrl ? `Analizar el equipo de ${match.opponentName}` : "Esta partida no tiene replay"}
                        aria-label={`Analizar el equipo rival de ${match.opponentName}`}
                        className="shrink-0 rounded-full border-cyan-300/15 bg-cyan-300/5 text-cyan-300 hover:bg-cyan-300/10 hover:text-cyan-200"
                      >
                        <ScanSearch className="size-3.5" />
                      </Button>
                    </div>
                  </TableCell>
                  <TableCell><PokemonSpriteStrip species={match.selected} label="Tus picks" tone="cyan" limit={4} /></TableCell>
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
