"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import { ExternalLink, History, ListFilter, LoaderCircle, ScanSearch, Trash2, Trophy } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MatchQuickEntry } from "@/components/vgc/match-quick-entry";
import { countMatchesByOrigin, filterMatchesByOrigin, getMatchOrigin, type MatchOrigin } from "@/lib/match-history";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { MatchRecord, TeamVersion } from "@/lib/types";

const dateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit",
  month: "short",
});

const fullDateFormatter = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit",
  month: "short",
  year: "numeric",
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

function OriginBadge({ match }: { match: MatchRecord }) {
  const origin = getMatchOrigin(match);
  return origin === "showdown" ? (
    <Badge variant="outline" className="border-cyan-300/20 bg-cyan-300/8 text-[9px] text-cyan-200">Showdown</Badge>
  ) : (
    <Badge variant="outline" className="border-amber-300/20 bg-amber-300/8 text-[9px] text-amber-200">Champions</Badge>
  );
}

function MatchHistoryTable({
  matches,
  version,
  deletingMatchId,
  onDelete,
  onScoutingRequested,
  showOrigin = false,
  showFullDate = false,
}: {
  matches: MatchRecord[];
  version: TeamVersion;
  deletingMatchId: string | null;
  onDelete: (match: MatchRecord) => void;
  onScoutingRequested?: (version: TeamVersion, match: MatchRecord) => void;
  showOrigin?: boolean;
  showFullDate?: boolean;
}) {
  const isChampions = version.format === "champions";
  const formatter = showFullDate ? fullDateFormatter : dateFormatter;

  return (
    <Table>
      <TableHeader>
        <TableRow className="border-white/7 hover:bg-transparent">
          <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Partida</TableHead>
          <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Rival</TableHead>
          {showOrigin ? <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Origen</TableHead> : null}
          <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">{isChampions ? "Pokémon rival" : "Equipo rival"}</TableHead>
          <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Picks rival</TableHead>
          <TableHead className="h-9 text-[9px] uppercase tracking-wider text-slate-600">Tus picks</TableHead>
          <TableHead className="h-9 text-right text-[9px] uppercase tracking-wider text-slate-600">Rating</TableHead>
          <TableHead className="h-9 text-right text-[9px] uppercase tracking-wider text-slate-600">Replay</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {matches.map((match, index) => (
          <TableRow key={match.id} className="border-white/6 text-[10px] hover:bg-white/[0.025]">
            <TableCell className="py-2.5">
              <div className="flex min-w-max items-center gap-2">
                <span className="font-mono text-slate-600">{String(index + 1).padStart(2, "0")}</span>
                <Badge className={match.result === "win" ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-200" : "border-rose-300/20 bg-rose-300/10 text-rose-200"} variant="outline">
                  {match.result === "win" ? "WIN" : "LOSS"}
                </Badge>
                <span className="text-slate-600">{formatter.format(new Date(match.playedAt))}</span>
              </div>
            </TableCell>
            <TableCell className="max-w-36 truncate font-medium text-slate-300">{match.opponentName}</TableCell>
            {showOrigin ? <TableCell><OriginBadge match={match} /></TableCell> : null}
            <TableCell>
              <div className="flex min-w-max items-center gap-2">
                <PokemonSpriteStrip species={match.opponentSelected} label={isChampions ? "Pokémon rival" : "Equipo rival"} />
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
            <TableCell><PokemonSpriteStrip species={match.opponentPicks ?? []} label="Picks rival" limit={4} /></TableCell>
            <TableCell><PokemonSpriteStrip species={match.selected} label="Tus picks" tone="cyan" limit={4} /></TableCell>
            <TableCell className="text-right font-mono tabular-nums text-slate-400">{match.rating ?? "—"}</TableCell>
            <TableCell className="text-right">
              <div className="flex min-w-max items-center justify-end gap-1.5">
                {match.replayUrl ? (
                  <a href={match.replayUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 transition hover:text-cyan-200">
                    Ver <ExternalLink className="size-3" />
                  </a>
                ) : <span className="text-slate-700">—</span>}
                {!version.demo ? (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        disabled={deletingMatchId !== null}
                        title="Eliminar esta partida"
                        aria-label={`Eliminar partida contra ${match.opponentName}`}
                        className="rounded-full text-slate-600 hover:bg-rose-300/10 hover:text-rose-300"
                      >
                        {deletingMatchId === match.id ? <LoaderCircle className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent className="border-white/10 bg-slate-950 text-slate-100">
                      <AlertDialogHeader>
                        <AlertDialogTitle>¿Eliminar esta partida?</AlertDialogTitle>
                        <AlertDialogDescription className="text-slate-400">
                          Se quitará el registro contra {match.opponentName}, su replay y cualquier análisis de Scouting asociado. Las estadísticas del Team se recalcularán automáticamente. Esta acción no se puede deshacer.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel className="border-white/10 bg-white/4 text-slate-200">Cancelar</AlertDialogCancel>
                        <AlertDialogAction variant="destructive" onClick={() => onDelete(match)}>Eliminar partida</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                ) : null}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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
  const [deletingMatchId, setDeletingMatchId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [historyFilter, setHistoryFilter] = useState<MatchOrigin>("all");
  const isChampions = version.format === "champions";
  const originCounts = useMemo(() => countMatchesByOrigin(matches), [matches]);
  const filteredMatches = useMemo(() => filterMatchesByOrigin(matches, historyFilter), [historyFilter, matches]);

  async function removeMatch(match: MatchRecord) {
    if (deletingMatchId) return;
    setDeletingMatchId(match.id);
    setDeleteError("");

    try {
      const response = await fetch(`/api/matches/${encodeURIComponent(match.id)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { error?: string };
        throw new Error(payload.error ?? "No pudimos eliminar la partida.");
      }
      await onMatchCreated?.();
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "No pudimos eliminar la partida.");
    } finally {
      setDeletingMatchId(null);
    }
  }

  const filters: Array<{ value: MatchOrigin; label: string; count: number }> = [
    { value: "all", label: "Todos", count: matches.length },
    { value: "champions", label: "Champions", count: originCounts.champions },
    { value: "showdown", label: "Showdown", count: originCounts.showdown },
  ];

  return (
    <section className="overflow-hidden rounded-2xl border border-white/8 bg-slate-950/65">
      <div className="flex items-center justify-between gap-3 border-b border-white/7 px-4 py-3">
        <h3 className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-400"><History className="size-3.5 text-violet-300" />Historial reciente</h3>
        <div className="flex items-center gap-2">
          <span className="hidden text-[10px] text-slate-600 sm:inline">Últimas {Math.min(matches.length, 5)} de {matches.length}</span>
          {matches.length ? (
            <Dialog>
              <DialogTrigger asChild>
                <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 rounded-full px-2.5 text-[10px] font-bold text-slate-400 hover:bg-white/6 hover:text-white">
                  <ListFilter className="size-3" />Historial completo
                </Button>
              </DialogTrigger>
              <DialogContent className="max-h-[92vh] w-[96vw] max-w-[96vw] overflow-hidden border-white/10 bg-[#070b14] p-0 text-slate-100 sm:max-w-[96vw]">
                <DialogHeader className="border-b border-white/7 px-5 py-4 pr-12">
                  <DialogTitle className="flex items-center gap-2 text-base"><History className="size-4 text-violet-300" />Historial completo · {version.name}</DialogTitle>
                  <DialogDescription className="text-xs text-slate-500">Todas las partidas guardadas de esta versión, separadas por su origen de registro.</DialogDescription>
                </DialogHeader>

                <div className="flex flex-wrap items-center gap-2 border-b border-white/7 px-5 py-3">
                  {filters.map((filter) => {
                    const active = historyFilter === filter.value;
                    const tone = filter.value === "champions" ? "amber" : filter.value === "showdown" ? "cyan" : "slate";
                    return (
                      <Button
                        key={filter.value}
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-pressed={active}
                        onClick={() => setHistoryFilter(filter.value)}
                        className={
                          active
                            ? tone === "amber"
                              ? "h-8 rounded-full border-amber-300/30 bg-amber-300/12 px-3 text-[10px] font-bold text-amber-100"
                              : tone === "cyan"
                                ? "h-8 rounded-full border-cyan-300/30 bg-cyan-300/12 px-3 text-[10px] font-bold text-cyan-100"
                                : "h-8 rounded-full border-white/20 bg-white/10 px-3 text-[10px] font-bold text-white"
                            : "h-8 rounded-full border-white/8 bg-white/[0.025] px-3 text-[10px] font-bold text-slate-500 hover:bg-white/6 hover:text-slate-200"
                        }
                      >
                        {filter.label}<span className="font-mono text-[9px] opacity-70">{filter.count}</span>
                      </Button>
                    );
                  })}
                </div>

                <div className="max-h-[72vh] overflow-auto">
                  {filteredMatches.length ? (
                    <MatchHistoryTable
                      matches={filteredMatches}
                      version={version}
                      deletingMatchId={deletingMatchId}
                      onDelete={(match) => void removeMatch(match)}
                      onScoutingRequested={onScoutingRequested}
                      showOrigin
                      showFullDate
                    />
                  ) : (
                    <div className="flex min-h-52 flex-col items-center justify-center px-6 text-center">
                      <Trophy className="size-6 text-slate-700" />
                      <p className="mt-2 text-xs font-semibold text-slate-400">No hay partidas en esta categoría</p>
                      <p className="mt-1 text-[10px] text-slate-600">Cambia de filtro para ver el resto del historial.</p>
                    </div>
                  )}
                </div>
              </DialogContent>
            </Dialog>
          ) : null}
        </div>
      </div>
      <div className="border-b border-white/7 bg-white/[0.018] px-4 py-3">
        <div className="mb-2.5">
          <p className="text-xs font-bold text-slate-300">{isChampions ? "Registrar partida" : "Agregar replay"}</p>
          <p className="mt-0.5 text-[10px] text-slate-600">
            {isChampions
              ? "Sin enlace abre el registro rápido Champions; con un replay, Showdown importa automáticamente el resultado y las selecciones."
              : "Pega el enlace: Showdown completa automáticamente el resultado y las selecciones."}
          </p>
        </div>
        <MatchQuickEntry version={version} onCreated={onMatchCreated} />
        {deleteError ? <p role="status" className="mt-2 text-[10px] font-semibold text-rose-300">{deleteError}</p> : null}
      </div>
      {matches.length ? (
        <div className="overflow-x-auto">
          <MatchHistoryTable
            matches={matches.slice(0, 5)}
            version={version}
            deletingMatchId={deletingMatchId}
            onDelete={(match) => void removeMatch(match)}
            onScoutingRequested={onScoutingRequested}
          />
        </div>
      ) : (
        <div className="flex flex-col items-center px-6 py-10 text-center">
          <Trophy className="size-6 text-slate-700" />
          <p className="mt-2 text-xs font-semibold text-slate-400">Todavía no hay partidas</p>
          <p className="mt-1 max-w-xs text-[10px] leading-4 text-slate-600">Registra el primer resultado para empezar a medir este equipo.</p>
        </div>
      )}
    </section>
  );
}
