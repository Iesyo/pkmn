"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CalendarDays, ChevronLeft, ChevronRight, ExternalLink, Hammer, Loader2, RefreshCw, Search, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { TournamentTeamBuilderImport } from "@/lib/tournament-scouting";
import {
  DEFAULT_VGCPASTES_FORMAT_ID,
  isVgcPastesScoutingResponse,
  type VgcPastesScoutingResponse,
  type VgcPastesTeam,
} from "@/lib/vgcpastes-scouting";

function upstreamError(payload: unknown) {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error;
  }
  return "No pudimos cargar VGCPastes ahora.";
}

async function readApiPayload(response: Response) {
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  const body = await response.text();
  if (!contentType.includes("json")) {
    throw new Error("El servidor respondió con una página en lugar del archivo de equipos. Recarga e inténtalo nuevamente.");
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("El servidor devolvió una respuesta incompleta. Inténtalo nuevamente.");
  }
}

function TeamCard({
  team,
  formatLabel,
  importing,
  importDisabled,
  onImport,
}: {
  team: VgcPastesTeam;
  formatLabel: string;
  importing: boolean;
  importDisabled: boolean;
  onImport: () => void;
}) {
  const context = team.tournament && team.tournament !== "-" ? team.tournament : formatLabel;
  return (
    <article className="group flex min-w-0 flex-col rounded-2xl border border-white/8 bg-slate-950/65 p-4 transition hover:border-cyan-300/20 hover:bg-slate-950/85">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {team.rank && team.rank !== "-" ? <Badge variant="outline" className="border-amber-300/20 bg-amber-300/8 text-[9px] text-amber-200">{team.rank}</Badge> : null}
            {team.hasEvs ? <Badge variant="outline" className="border-emerald-300/15 bg-emerald-300/7 text-[9px] text-emerald-200">EVs</Badge> : null}
            <h3 className="truncate text-sm font-black text-white" title={team.playerName}>{team.playerName}</h3>
          </div>
          <p className="mt-1 truncate text-[10px] font-semibold text-slate-500" title={context}>{context}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-slate-600">
            {team.dateShared ? <span className="inline-flex items-center gap-1"><CalendarDays className="size-3" />{team.dateShared}</span> : null}
            {team.replicaCode ? <span>Replica <strong className="font-mono text-slate-400">{team.replicaCode}</strong></span> : null}
            {team.sourceUrl ? <a href={team.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 hover:text-cyan-200">Fuente <ExternalLink className="size-3" /></a> : null}
          </div>
        </div>
        <div className="flex shrink-0 flex-col gap-1.5 sm:flex-row">
          {team.pokepasteUrl ? <a href={team.pokepasteUrl} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/3 px-2.5 text-[9px] font-bold text-slate-400 transition hover:border-cyan-300/20 hover:text-cyan-200">Paste <ExternalLink className="size-3" /></a> : null}
          <Button type="button" variant="outline" size="sm" disabled={importDisabled || !team.pokepasteUrl} onClick={onImport} className="h-8 gap-1.5 border-white/10 bg-white/3 px-2.5 text-[9px] text-slate-300 hover:border-cyan-300/25 hover:bg-cyan-300/8 hover:text-cyan-200">
            {importing ? <Loader2 className="size-3 animate-spin" /> : <Hammer className="size-3" />}
            {importing ? "Importando" : "Builder"}
          </Button>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {team.pokemon.map((species, index) => (
          <div key={`${team.id}-${species}-${index}`} className="min-w-0 rounded-xl border border-white/6 bg-white/[0.025] px-1.5 py-2 text-center">
            <Image src={getSpriteUrl(species)} alt={species} width={44} height={44} unoptimized className="mx-auto size-10 object-contain transition group-hover:scale-105" />
            <p className="mt-1 truncate text-[8px] font-semibold text-slate-500" title={species}>{species}</p>
          </div>
        ))}
      </div>
      {team.description ? <p className="mt-3 line-clamp-2 text-[9px] leading-4 text-slate-600">{team.description}</p> : null}
    </article>
  );
}

function LoadingState() {
  return (
    <section className="rounded-[28px] border border-white/8 bg-slate-900/45 p-5">
      <div className="h-5 w-44 animate-pulse rounded bg-white/6" />
      <div className="mt-5 grid gap-2 lg:grid-cols-2"><div className="h-10 animate-pulse rounded-xl bg-white/4" /><div className="h-10 animate-pulse rounded-xl bg-white/4" /></div>
      <div className="mt-5 grid gap-3 xl:grid-cols-2">{[0, 1, 2, 3].map((value) => <div key={value} className="h-40 animate-pulse rounded-2xl border border-white/6 bg-white/[0.025]" />)}</div>
    </section>
  );
}

export function VgcPastesScoutingBrowser({ onImportTeam }: { onImportTeam: (request: TournamentTeamBuilderImport) => void }) {
  const [data, setData] = useState<VgcPastesScoutingResponse | null>(null);
  const [formatId, setFormatId] = useState(DEFAULT_VGCPASTES_FORMAT_ID);
  const [pokemon, setPokemon] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [importingTeamId, setImportingTeamId] = useState("");
  const [importError, setImportError] = useState("");
  const refreshNextRequest = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({ format: formatId, page: String(page), pageSize: "24" });
        if (pokemon) params.set("pokemon", pokemon);
        if (refreshNextRequest.current) {
          params.set("refresh", "1");
          refreshNextRequest.current = false;
        }
        const response = await fetch(`/api/vgcpastes-scouting?${params.toString()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readApiPayload(response);
        if (!response.ok) throw new Error(upstreamError(payload));
        if (!isVgcPastesScoutingResponse(payload)) throw new Error("VGCPastes llegó en un formato inesperado.");
        setData(payload);
        if (payload.query.page !== page) setPage(payload.query.page);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError(caught instanceof Error ? caught.message : "No pudimos cargar VGCPastes ahora.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [formatId, pokemon, page, reloadKey]);

  async function importTeam(team: VgcPastesTeam) {
    if (!team.pokepasteUrl || importingTeamId) return;
    setImportingTeamId(team.id);
    setImportError("");
    try {
      const response = await fetch("/api/pokepaste-import", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: team.pokepasteUrl }),
      });
      const payload = await readApiPayload(response);
      if (!response.ok) throw new Error(upstreamError(payload));
      if (!payload || typeof payload !== "object" || !("paste" in payload) || typeof payload.paste !== "string") {
        throw new Error("PokéPaste devolvió un equipo en un formato inesperado.");
      }
      const context = team.tournament && team.tournament !== "-" ? team.tournament : data?.format.label ?? "VGCPastes";
      onImportTeam({
        paste: payload.paste,
        suggestedName: `${team.playerName} · ${context}`.slice(0, 80),
        sourceLabel: `VGCPastes · ${team.playerName} · ${team.id}`,
      });
    } catch (caught) {
      setImportError(caught instanceof Error ? caught.message : "No pudimos importar ese equipo.");
    } finally {
      setImportingTeamId("");
    }
  }

  if (loading && !data) return <LoadingState />;

  if (!data) {
    return (
      <section className="rounded-[28px] border border-rose-300/12 bg-slate-900/45 px-6 py-16 text-center">
        <AlertTriangle className="mx-auto size-9 text-rose-300" />
        <h2 className="mt-4 text-lg font-black text-white">No pudimos abrir VGCPastes</h2>
        <p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">{error}</p>
        <Button type="button" onClick={() => setReloadKey((value) => value + 1)} className="mt-5 gap-2 bg-cyan-300 font-black text-slate-950 hover:bg-cyan-200"><RefreshCw className="size-4" />Reintentar</Button>
      </section>
    );
  }

  const pageLabel = data.pagination.totalPages > 0
    ? `Página ${data.pagination.page} de ${data.pagination.totalPages}`
    : "Sin resultados";

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-cyan-300 via-violet-400 to-transparent" />
        <div className="p-5">
          <div>
            <div className="flex items-center gap-2"><Search className="size-5 text-cyan-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-200">VGCPastes Repository</p></div>
            <h1 className="mt-2 text-2xl font-black tracking-tight text-white">Archivo de equipos por formato</h1>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">Explora equipos públicos sin cargar miles de tarjetas de golpe. Filtra por formato o por un Pokémon y abre sólo una página de resultados.</p>
          </div>

          <div className="mt-5 grid gap-3 border-t border-white/7 pt-5 lg:grid-cols-[minmax(220px,0.8fr)_minmax(260px,1.2fr)_auto] lg:items-end">
            <div className="grid gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Formato</label>
              <Select value={formatId} onValueChange={(value) => { setFormatId(value); setPokemon(""); setPage(1); }}>
                <SelectTrigger className="w-full border-white/10 bg-slate-950/70"><SelectValue /></SelectTrigger>
                <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
                  {data.formats.map((format) => <SelectItem key={format.id} value={format.id}>{format.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="grid min-w-0 gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Buscar por 1 Pokémon</label>
              <Combobox items={data.pokemonOptions} value={pokemon || null} onValueChange={(value) => { setPokemon(value ?? ""); setPage(1); }}>
                <ComboboxInput aria-label="Buscar por Pokémon" placeholder="Ej. Sneasler" showClear className="w-full border-white/10 bg-slate-950/70" />
                <ComboboxContent className="border-white/10 bg-slate-950">
                  <ComboboxEmpty>No encontramos ese Pokémon en este formato.</ComboboxEmpty>
                  <ComboboxList>{(species: string) => <ComboboxItem key={species} value={species}>{species}</ComboboxItem>}</ComboboxList>
                </ComboboxContent>
              </Combobox>
            </div>

            <Button type="button" variant="outline" onClick={() => { refreshNextRequest.current = true; setReloadKey((value) => value + 1); }} disabled={loading} className="h-10 gap-2 border-cyan-300/20 bg-cyan-300/7 text-xs font-black text-cyan-200 hover:bg-cyan-300/12">
              <RefreshCw className={loading ? "size-4 animate-spin" : "size-4"} />Actualizar
            </Button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[10px] text-slate-500">
            <span className="inline-flex items-center gap-1.5"><Users className="size-3.5 text-cyan-300" /><strong className="text-slate-300">{data.pagination.totalItems}</strong> resultados</span>
            <span>{data.pagination.totalAvailable} equipos en {data.format.label}</span>
            {pokemon ? <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Contiene {pokemon}</Badge> : null}
            <span className="font-mono text-slate-600">{pageLabel}</span>
          </div>
        </div>
      </section>

      {error ? <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-amber-300/15 bg-amber-300/7 px-4 py-3 text-xs text-amber-100"><span className="flex items-center gap-2"><AlertTriangle className="size-4" />{error}</span><Button type="button" variant="ghost" size="sm" onClick={() => setReloadKey((value) => value + 1)} className="text-amber-100"><RefreshCw className="size-3.5" />Reintentar</Button></div> : null}
      {importError ? <div role="alert" className="flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-4 py-3 text-xs text-rose-200"><AlertTriangle className="size-4" />{importError}</div> : null}

      <section>
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[10px] font-black uppercase tracking-[0.16em] text-cyan-300">Equipos publicados</p>
            <h2 className="mt-1 text-lg font-black text-white">{data.format.label}{pokemon ? ` · ${pokemon}` : ""}</h2>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" disabled={loading || data.pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="gap-1 border-white/10 bg-white/3 text-[10px] text-slate-300"><ChevronLeft className="size-3.5" />Anterior</Button>
            <span className="min-w-24 text-center font-mono text-[10px] text-slate-500">{pageLabel}</span>
            <Button type="button" variant="outline" size="sm" disabled={loading || data.pagination.totalPages === 0 || data.pagination.page >= data.pagination.totalPages} onClick={() => setPage((value) => value + 1)} className="gap-1 border-white/10 bg-white/3 text-[10px] text-slate-300">Siguiente<ChevronRight className="size-3.5" /></Button>
          </div>
        </div>

        {data.teams.length ? <div className="grid gap-3 xl:grid-cols-2">{data.teams.map((team) => <TeamCard key={team.id} team={team} formatLabel={data.format.label} importing={importingTeamId === team.id} importDisabled={Boolean(importingTeamId)} onImport={() => void importTeam(team)} />)}</div> : (
          <div className="rounded-2xl border border-white/7 bg-slate-950/45 px-6 py-16 text-center">
            <Search className="mx-auto size-8 text-slate-700" />
            <h3 className="mt-3 text-sm font-black text-white">No hay equipos con ese Pokémon</h3>
            <p className="mt-1 text-xs text-slate-600">Limpia el filtro o selecciona otro Pokémon del formato.</p>
          </div>
        )}

        {data.pagination.totalPages > 1 ? <div className="mt-4 flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" disabled={loading || data.pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="text-[10px] text-slate-400"><ChevronLeft className="size-3.5" />Anterior</Button>
          <span className="font-mono text-[10px] text-slate-600">{pageLabel}</span>
          <Button type="button" variant="ghost" size="sm" disabled={loading || data.pagination.page >= data.pagination.totalPages} onClick={() => setPage((value) => value + 1)} className="text-[10px] text-slate-400">Siguiente<ChevronRight className="size-3.5" /></Button>
        </div> : null}
      </section>

      <footer className="rounded-2xl border border-white/7 bg-slate-950/45 px-4 py-3 text-[10px] leading-5 text-slate-600">
        Datos de <a href={data.source.url} target="_blank" rel="noreferrer" className="font-semibold text-cyan-300 hover:text-cyan-200">{data.source.label}</a>. La app pagina y filtra los resultados en el servidor; un PokéPaste sólo se descarga cuando eliges importarlo al Team Builder.
      </footer>
    </div>
  );
}
