"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CalendarDays, ExternalLink, RefreshCw, Trophy, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { getSpriteUrl } from "@/lib/pokemon-data";
import {
  isTournamentScoutingResponse,
  type TournamentScoutingResponse,
  type TournamentScoutingTeam,
} from "@/lib/tournament-scouting";

function displayDate(value: string) {
  if (!value || Number.isNaN(Date.parse(value))) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

function upstreamError(payload: unknown) {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error;
  }
  return "No pudimos cargar los torneos ahora.";
}

async function readApiPayload(response: Response) {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const body = await response.text();
  if (!contentType.includes("json")) {
    throw new Error("El servidor respondió con una página en lugar del archivo de torneos. Recarga la aplicación e inténtalo nuevamente.");
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("El servidor devolvió un archivo de torneos incompleto. Inténtalo nuevamente.");
  }
}

function TournamentTeamCard({ team }: { team: TournamentScoutingTeam }) {
  return (
    <article className="group flex min-w-0 flex-col rounded-2xl border border-white/8 bg-slate-950/65 p-4 transition hover:border-cyan-300/20 hover:bg-slate-950/85">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {team.placement ? <Badge variant="outline" className="border-amber-300/20 bg-amber-300/8 font-mono text-amber-200">#{team.placement}</Badge> : null}
            <h3 className="truncate text-sm font-black text-white" title={team.playerName}>{team.playerName}</h3>
          </div>
          <p className="mt-1 text-[10px] font-semibold text-slate-500">{team.record ? `Récord ${team.record}` : "Récord no publicado"}</p>
        </div>
        <Button asChild variant="outline" size="sm" className="shrink-0 gap-1.5 border-white/10 bg-white/3 text-[10px] text-slate-300 hover:border-cyan-300/25 hover:bg-cyan-300/8 hover:text-cyan-200">
          <a href={team.pokepasteUrl} target="_blank" rel="noreferrer">Ver equipo <ExternalLink className="size-3" /></a>
        </Button>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {team.pokemon.map((species, index) => (
          <div key={`${team.id}-${species}-${index}`} className="min-w-0 rounded-xl border border-white/6 bg-white/[0.025] px-1.5 py-2 text-center">
            <Image src={getSpriteUrl(species)} alt={species} width={44} height={44} unoptimized className="mx-auto size-10 object-contain transition group-hover:scale-105" />
            <p className="mt-1 truncate text-[8px] font-semibold text-slate-500" title={species}>{species}</p>
          </div>
        ))}
      </div>
    </article>
  );
}

function TournamentLoading() {
  return (
    <section className="rounded-[28px] border border-white/8 bg-slate-900/45 p-5">
      <div className="h-5 w-44 animate-pulse rounded bg-white/6" />
      <div className="mt-5 h-10 animate-pulse rounded-xl bg-white/4" />
      <div className="mt-5 grid gap-3 lg:grid-cols-2">
        {[0, 1, 2, 3].map((value) => <div key={value} className="h-40 animate-pulse rounded-2xl border border-white/6 bg-white/[0.025]" />)}
      </div>
    </section>
  );
}

export function TournamentScoutingBrowser() {
  const [data, setData] = useState<TournamentScoutingResponse | null>(null);
  const [tournamentName, setTournamentName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const response = await fetch("/api/tournament-scouting", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readApiPayload(response);
        if (!response.ok) throw new Error(upstreamError(payload));
        if (!isTournamentScoutingResponse(payload)) throw new Error("Los torneos llegaron en un formato inesperado.");
        setData(payload);
        setTournamentName((current) => payload.tournaments.some((tournament) => tournament.name === current)
          ? current
          : payload.tournaments[0]?.name ?? "");
      } catch (loadError) {
        if (controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : "No pudimos cargar los torneos ahora.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [reloadKey]);

  const tournamentNames = useMemo(() => data?.tournaments.map((tournament) => tournament.name) ?? [], [data]);
  const selectedTournament = data?.tournaments.find((tournament) => tournament.name === tournamentName) ?? data?.tournaments[0];

  if (loading && !data) return <TournamentLoading />;

  if (!data) {
    return (
      <section className="rounded-[28px] border border-rose-300/12 bg-slate-900/45 px-6 py-16 text-center">
        <AlertTriangle className="mx-auto size-9 text-rose-300" />
        <h2 className="mt-4 text-lg font-black text-white">No pudimos abrir el archivo de torneos</h2>
        <p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">{error}</p>
        <Button type="button" onClick={() => setReloadKey((value) => value + 1)} className="mt-5 gap-2 bg-cyan-300 font-black text-slate-950 hover:bg-cyan-200"><RefreshCw className="size-4" />Reintentar</Button>
      </section>
    );
  }

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-amber-300 via-cyan-300 to-transparent" />
        <div className="p-5">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex items-center gap-2"><Trophy className="size-5 text-amber-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-amber-200">Archivo de torneo</p></div>
              <h1 className="mt-2 text-2xl font-black tracking-tight text-white">Equipos reales para scouting</h1>
              <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">Selecciona un torneo y revisa los equipos públicos encontrados. Cada tarjeta abre el PokéPaste original.</p>
            </div>
            <div className="grid min-w-0 gap-2 lg:w-[480px]">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Torneo</label>
              <Combobox items={tournamentNames} value={selectedTournament?.name ?? null} onValueChange={(value) => setTournamentName(value ?? "")}>
                <ComboboxInput aria-label="Torneo" placeholder="Buscar torneo..." className="w-full border-white/10 bg-slate-950/70" />
                <ComboboxContent className="border-white/10 bg-slate-950">
                  <ComboboxEmpty>No encontramos ese torneo.</ComboboxEmpty>
                  <ComboboxList>{(name: string) => {
                    const tournament = data.tournaments.find((entry) => entry.name === name);
                    return <ComboboxItem key={name} value={name}><span className="min-w-0 flex-1 truncate">{name}</span><span className="shrink-0 text-[10px] text-slate-600">{tournament?.teams.length ?? 0}</span></ComboboxItem>;
                  }}</ComboboxList>
                </ComboboxContent>
              </Combobox>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/7 pt-4 text-[10px] text-slate-500">
            <span className="inline-flex items-center gap-1.5"><Users className="size-3.5 text-cyan-300" /><strong className="text-slate-300">{selectedTournament?.teams.length ?? 0}</strong> equipos con paste disponibles</span>
            <span className="inline-flex items-center gap-1.5"><CalendarDays className="size-3.5" />Snapshot del {displayDate(data.generatedAt)}</span>
            <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-[9px] text-violet-200">Regulación {data.regulation}</Badge>
            {data.stale ? <Badge variant="outline" className="border-amber-300/20 bg-amber-300/8 text-[9px] text-amber-200">Caché de respaldo</Badge> : null}
            {loading ? <span className="inline-flex items-center gap-1.5 text-cyan-300"><RefreshCw className="size-3 animate-spin" />Actualizando</span> : null}
          </div>
        </div>
      </section>

      {error ? <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-300/15 bg-amber-300/7 px-4 py-3 text-xs text-amber-100"><span className="flex items-center gap-2"><AlertTriangle className="size-4" />{error}</span><Button type="button" variant="ghost" size="sm" onClick={() => setReloadKey((value) => value + 1)} className="text-amber-100"><RefreshCw className="size-3.5" />Reintentar</Button></div> : null}

      <section>
        <div className="mb-3 flex items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-cyan-300">Equipos publicados</p>
            <h2 className="mt-1 truncate text-lg font-black text-white" title={selectedTournament?.name}>{selectedTournament?.name}</h2>
          </div>
          <span className="shrink-0 font-mono text-[10px] text-slate-600">Ordenados por puesto</span>
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          {selectedTournament?.teams.map((team) => <TournamentTeamCard key={team.id} team={team} />)}
        </div>
      </section>

      <footer className="rounded-2xl border border-white/7 bg-slate-950/45 px-4 py-3 text-[10px] leading-5 text-slate-600">
        Datos de <a href={data.source.url} target="_blank" rel="noreferrer" className="font-semibold text-cyan-300 hover:text-cyan-200">{data.source.label}</a>, servidos desde el snapshot público de <a href={data.snapshotSource.url} target="_blank" rel="noreferrer" className="font-semibold text-cyan-300 hover:text-cyan-200">{data.snapshotSource.label}</a>. La lista incluye equipos con PokéPaste disponibles en el snapshot; no representa necesariamente todos los participantes del torneo.
      </footer>
    </div>
  );
}
