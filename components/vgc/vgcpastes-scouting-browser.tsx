"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  ExternalLink,
  Eye,
  Filter,
  Hammer,
  Loader2,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
  useComboboxAnchor,
} from "@/components/ui/combobox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { parseShowdownPaste } from "@/lib/paste";
import { getSpriteUrl } from "@/lib/pokemon-data";
import type { TournamentTeamBuilderImport } from "@/lib/tournament-scouting";
import type { PokemonSet } from "@/lib/types";
import {
  DEFAULT_VGCPASTES_FORMAT_ID,
  isVgcPastesScoutingResponse,
  MAX_VGCPASTES_POKEMON_FILTERS,
  type VgcPastesScoutingResponse,
  type VgcPastesTeam,
} from "@/lib/vgcpastes-scouting";

type CompetitiveFilters = {
  player: string;
  event: string;
  rank: string;
  date: string;
  hasEvs: boolean;
  hasPaste: boolean;
  hasReplica: boolean;
};

const EMPTY_FILTERS: CompetitiveFilters = {
  player: "",
  event: "",
  rank: "",
  date: "",
  hasEvs: false,
  hasPaste: false,
  hasReplica: false,
};

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

function hasCompetitiveFilters(filters: CompetitiveFilters) {
  return Boolean(
    filters.player
    || filters.event
    || filters.rank
    || filters.date
    || filters.hasEvs
    || filters.hasPaste
    || filters.hasReplica,
  );
}

function TeamCard({
  team,
  formatLabel,
  importing,
  importDisabled,
  onInspect,
  onImport,
}: {
  team: VgcPastesTeam;
  formatLabel: string;
  importing: boolean;
  importDisabled: boolean;
  onInspect: () => void;
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
          <Button type="button" variant="outline" size="sm" onClick={onInspect} className="h-8 gap-1.5 border-cyan-300/15 bg-cyan-300/5 px-2.5 text-[9px] text-cyan-200 hover:bg-cyan-300/10">
            <Eye className="size-3" />Inspector
          </Button>
          {team.pokepasteUrl ? <a href={team.pokepasteUrl} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/3 px-2.5 text-[9px] font-bold text-slate-400 transition hover:border-cyan-300/20 hover:text-cyan-200">Paste <ExternalLink className="size-3" /></a> : null}
          <Button type="button" variant="outline" size="sm" disabled={importDisabled || !team.pokepasteUrl} onClick={onImport} className="h-8 gap-1.5 border-white/10 bg-white/3 px-2.5 text-[9px] text-slate-300 hover:border-cyan-300/25 hover:bg-cyan-300/8 hover:text-cyan-200">
            {importing ? <Loader2 className="size-3 animate-spin" /> : <Hammer className="size-3" />}
            {importing ? "Importando" : "Builder"}
          </Button>
        </div>
      </div>

      <button type="button" onClick={onInspect} className="mt-4 grid grid-cols-3 gap-2 text-left sm:grid-cols-6" aria-label={`Inspeccionar equipo de ${team.playerName}`}>
        {team.pokemon.map((species, index) => (
          <span key={`${team.id}-${species}-${index}`} className="min-w-0 rounded-xl border border-white/6 bg-white/[0.025] px-1.5 py-2 text-center transition hover:border-cyan-300/15 hover:bg-cyan-300/[0.035]">
            <Image src={getSpriteUrl(species)} alt={species} width={44} height={44} unoptimized className="mx-auto size-10 object-contain transition group-hover:scale-105" />
            <span className="mt-1 block truncate text-[8px] font-semibold text-slate-500" title={species}>{species}</span>
          </span>
        ))}
      </button>
      {team.description ? <p className="mt-3 line-clamp-2 text-[9px] leading-4 text-slate-600">{team.description}</p> : null}
    </article>
  );
}

function InspectorSetCard({ set }: { set: PokemonSet }) {
  return (
    <article className="rounded-2xl border border-white/8 bg-slate-950/70 p-4">
      <div className="flex items-start gap-3">
        <div className="flex size-16 shrink-0 items-center justify-center rounded-xl border border-white/7 bg-white/[0.025]">
          <Image src={getSpriteUrl(set.species)} alt={set.species} width={60} height={60} unoptimized className="size-14 object-contain" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-black text-white">{set.species}</h3>
            {set.teraType ? <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-[9px] text-violet-200">Tera {set.teraType}</Badge> : null}
          </div>
          {set.nickname && set.nickname !== set.species ? <p className="mt-0.5 truncate text-[9px] text-slate-600">{set.nickname}</p> : null}
          <p className="mt-1 text-[10px] font-semibold text-cyan-200">{set.item || "Sin objeto explícito"}</p>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[9px] sm:grid-cols-3">
        <div className="rounded-lg border border-white/6 bg-white/[0.02] px-2.5 py-2"><span className="block uppercase tracking-wide text-slate-600">Habilidad</span><strong className="mt-0.5 block text-slate-300">{set.ability || "—"}</strong></div>
        <div className="rounded-lg border border-white/6 bg-white/[0.02] px-2.5 py-2"><span className="block uppercase tracking-wide text-slate-600">Naturaleza</span><strong className="mt-0.5 block text-slate-300">{set.nature || "—"}</strong></div>
        <div className="rounded-lg border border-white/6 bg-white/[0.02] px-2.5 py-2"><span className="block uppercase tracking-wide text-slate-600">Nivel</span><strong className="mt-0.5 block text-slate-300">{set.level}</strong></div>
      </div>

      <div className="mt-2 rounded-lg border border-white/6 bg-white/[0.02] px-2.5 py-2 text-[9px]">
        <span className="uppercase tracking-wide text-slate-600">EVs / Stats explícitos</span>
        <p className="mt-1 font-mono text-slate-300">{set.evs || "No incluidos en el paste"}</p>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5">
        {set.moves.map((move, index) => (
          <div key={`${set.id}-${move.name}-${index}`} className="truncate rounded-lg border border-white/6 bg-white/[0.025] px-2.5 py-2 text-[9px] font-semibold text-slate-300" title={move.name}>{move.name}</div>
        ))}
      </div>
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
  const [pokemon, setPokemon] = useState<string[]>([]);
  const [filterDraft, setFilterDraft] = useState<CompetitiveFilters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<CompetitiveFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [importingTeamId, setImportingTeamId] = useState("");
  const [importError, setImportError] = useState("");
  const [inspectorTeam, setInspectorTeam] = useState<VgcPastesTeam | null>(null);
  const [inspectorPaste, setInspectorPaste] = useState("");
  const [inspectorSets, setInspectorSets] = useState<PokemonSet[]>([]);
  const [inspectorLoading, setInspectorLoading] = useState(false);
  const [inspectorError, setInspectorError] = useState("");
  const [copyDone, setCopyDone] = useState(false);
  const refreshNextRequest = useRef(false);
  const pokemonAnchor = useComboboxAnchor();
  const pasteCache = useRef(new Map<string, string>());
  const inspectorAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({ format: formatId, page: String(page), pageSize: "24" });
        pokemon.forEach((species) => params.append("pokemon", species));
        if (filters.player) params.set("player", filters.player);
        if (filters.event) params.set("event", filters.event);
        if (filters.rank) params.set("rank", filters.rank);
        if (filters.date) params.set("date", filters.date);
        if (filters.hasEvs) params.set("hasEvs", "1");
        if (filters.hasPaste) params.set("hasPaste", "1");
        if (filters.hasReplica) params.set("hasReplica", "1");
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
        const normalizedFilterChanged = payload.query.pokemon.length !== pokemon.length
          || payload.query.pokemon.some((species, index) => species !== pokemon[index]);
        if (normalizedFilterChanged) setPokemon(payload.query.pokemon);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setError(caught instanceof Error ? caught.message : "No pudimos cargar VGCPastes ahora.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, [formatId, pokemon, filters, page, reloadKey]);

  async function downloadTeamPaste(team: VgcPastesTeam, signal?: AbortSignal) {
    const cacheKey = team.pokepasteUrl;
    if (!cacheKey) throw new Error("Este equipo no tiene PokéPaste público.");
    const cached = pasteCache.current.get(cacheKey);
    if (cached) return cached;

    const response = await fetch("/api/pokepaste-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: team.pokepasteUrl }),
      signal,
    });
    const payload = await readApiPayload(response);
    if (!response.ok) throw new Error(upstreamError(payload));
    if (!payload || typeof payload !== "object" || !("paste" in payload) || typeof payload.paste !== "string") {
      throw new Error("PokéPaste devolvió un equipo en un formato inesperado.");
    }
    pasteCache.current.set(cacheKey, payload.paste);
    return payload.paste;
  }

  function sendTeamToBuilder(team: VgcPastesTeam, paste: string) {
    const context = team.tournament && team.tournament !== "-" ? team.tournament : data?.format.label ?? "VGCPastes";
    onImportTeam({
      paste,
      suggestedName: `${team.playerName} · ${context}`.slice(0, 80),
      sourceLabel: `VGCPastes · ${team.playerName} · ${team.id}`,
    });
  }

  async function importTeam(team: VgcPastesTeam) {
    if (!team.pokepasteUrl || importingTeamId) return;
    setImportingTeamId(team.id);
    setImportError("");
    try {
      const paste = await downloadTeamPaste(team);
      sendTeamToBuilder(team, paste);
    } catch (caught) {
      setImportError(caught instanceof Error ? caught.message : "No pudimos importar ese equipo.");
    } finally {
      setImportingTeamId("");
    }
  }

  async function inspectTeam(team: VgcPastesTeam) {
    inspectorAbort.current?.abort();
    const controller = new AbortController();
    inspectorAbort.current = controller;
    setInspectorTeam(team);
    setInspectorPaste("");
    setInspectorSets([]);
    setInspectorError("");
    setCopyDone(false);
    setInspectorLoading(Boolean(team.pokepasteUrl));
    if (!team.pokepasteUrl) return;

    try {
      const paste = await downloadTeamPaste(team, controller.signal);
      if (controller.signal.aborted) return;
      setInspectorPaste(paste);
      setInspectorSets(parseShowdownPaste(paste));
    } catch (caught) {
      if (controller.signal.aborted) return;
      setInspectorError(caught instanceof Error ? caught.message : "No pudimos inspeccionar ese equipo.");
    } finally {
      if (inspectorAbort.current === controller) inspectorAbort.current = null;
      if (!controller.signal.aborted) setInspectorLoading(false);
    }
  }

  function closeInspector() {
    inspectorAbort.current?.abort();
    inspectorAbort.current = null;
    setInspectorTeam(null);
    setInspectorPaste("");
    setInspectorSets([]);
    setInspectorError("");
    setInspectorLoading(false);
    setCopyDone(false);
  }

  async function copyInspectorPaste() {
    if (!inspectorPaste) return;
    try {
      await navigator.clipboard.writeText(inspectorPaste);
      setCopyDone(true);
      window.setTimeout(() => setCopyDone(false), 1600);
    } catch {
      setInspectorError("El navegador no permitió copiar el paste al portapapeles.");
    }
  }

  function importInspectedTeam() {
    if (!inspectorTeam || !inspectorPaste) return;
    const team = inspectorTeam;
    const paste = inspectorPaste;
    closeInspector();
    sendTeamToBuilder(team, paste);
  }

  function applyCompetitiveFilters() {
    setFilters({
      ...filterDraft,
      player: filterDraft.player.trim(),
      event: filterDraft.event.trim(),
      rank: filterDraft.rank.trim(),
      date: filterDraft.date.trim(),
    });
    setPage(1);
  }

  function clearCompetitiveFilters() {
    setFilterDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
  }

  function changeFormat(value: string) {
    setFormatId(value);
    setPokemon([]);
    setFilterDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
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
  const filterLabel = pokemon.length ? pokemon.join(" + ") : "";
  const competitiveActive = hasCompetitiveFilters(filters);

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-cyan-300 via-violet-400 to-transparent" />
        <div className="p-5">
          <div>
            <div className="flex items-center gap-2"><Search className="size-5 text-cyan-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-200">VGCPastes Repository</p></div>
            <h1 className="mt-2 text-2xl font-black tracking-tight text-white">Archivo de equipos por formato</h1>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">Explora equipos públicos, filtra cores y abre el Inspector para leer el PokéPaste real sin salir de Scouting.</p>
          </div>

          <div className="mt-5 grid gap-3 border-t border-white/7 pt-5 lg:grid-cols-[minmax(220px,0.8fr)_minmax(320px,1.4fr)_auto] lg:items-end">
            <div className="grid gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Formato</label>
              <Select value={formatId} onValueChange={changeFormat}>
                <SelectTrigger className="w-full border-white/10 bg-slate-950/70"><SelectValue /></SelectTrigger>
                <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
                  {data.formats.map((format) => <SelectItem key={format.id} value={format.id}>{format.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="grid min-w-0 gap-2">
              <div className="flex items-center justify-between gap-3">
                <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Buscar core · hasta {MAX_VGCPASTES_POKEMON_FILTERS} Pokémon</label>
                <span className="text-[9px] font-semibold text-slate-600">AND · deben aparecer todos</span>
              </div>
              <Combobox
                items={data.pokemonOptions}
                multiple
                value={pokemon}
                onValueChange={(value: string[]) => {
                  setPokemon(value.slice(0, MAX_VGCPASTES_POKEMON_FILTERS));
                  setPage(1);
                }}
              >
                <ComboboxValue>
                  {(selectedValue: string[]) => (
                    <ComboboxChips ref={pokemonAnchor} aria-label={selectedValue.length ? "Pokémon seleccionados para el core" : undefined} className="min-h-10 border-white/10 bg-slate-950/70">
                      {selectedValue.map((species) => <ComboboxChip key={species} aria-label={species} aria-description="Presiona Backspace o Delete para quitarlo" className="bg-cyan-300/10 text-cyan-100">{species}</ComboboxChip>)}
                      <ComboboxChipsInput
                        aria-label="Añadir Pokémon al core"
                        aria-description={selectedValue.length ? `${selectedValue.length} Pokémon seleccionados de un máximo de ${MAX_VGCPASTES_POKEMON_FILTERS}` : undefined}
                        placeholder={selectedValue.length ? (selectedValue.length >= MAX_VGCPASTES_POKEMON_FILTERS ? "Máximo 3 Pokémon" : "Añadir otro Pokémon") : "Ej. Sneasler"}
                        disabled={selectedValue.length >= MAX_VGCPASTES_POKEMON_FILTERS}
                      />
                    </ComboboxChips>
                  )}
                </ComboboxValue>
                <ComboboxContent anchor={pokemonAnchor} className="border-white/10 bg-slate-950">
                  <ComboboxEmpty>No encontramos ese Pokémon en este formato.</ComboboxEmpty>
                  <ComboboxList>{(species: string) => <ComboboxItem key={species} value={species} disabled={pokemon.length >= MAX_VGCPASTES_POKEMON_FILTERS && !pokemon.includes(species)}>{species}</ComboboxItem>}</ComboboxList>
                </ComboboxContent>
              </Combobox>
            </div>

            <Button type="button" variant="outline" onClick={() => { refreshNextRequest.current = true; setReloadKey((value) => value + 1); }} disabled={loading} className="h-10 gap-2 border-cyan-300/20 bg-cyan-300/7 text-xs font-black text-cyan-200 hover:bg-cyan-300/12">
              <RefreshCw className={loading ? "size-4 animate-spin" : "size-4"} />Actualizar
            </Button>
          </div>

          <div className="mt-4 rounded-2xl border border-white/7 bg-slate-950/35 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2"><Filter className="size-4 text-violet-300" /><span className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">Filtros competitivos</span></div>
              {competitiveActive ? <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-[9px] text-violet-200">Activos</Badge> : null}
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              <Input type="search" value={filterDraft.player} onChange={(event) => setFilterDraft((value) => ({ ...value, player: event.target.value }))} placeholder="Jugador / owner" aria-label="Filtrar por jugador" className="border-white/10 bg-slate-950/70" />
              <Input type="search" value={filterDraft.event} onChange={(event) => setFilterDraft((value) => ({ ...value, event: event.target.value }))} placeholder="Torneo / evento" aria-label="Filtrar por evento" className="border-white/10 bg-slate-950/70" />
              <Input type="search" value={filterDraft.rank} onChange={(event) => setFilterDraft((value) => ({ ...value, rank: event.target.value }))} placeholder="Rank / placement" aria-label="Filtrar por rank" className="border-white/10 bg-slate-950/70" />
              <Input type="search" value={filterDraft.date} onChange={(event) => setFilterDraft((value) => ({ ...value, date: event.target.value }))} placeholder="Fecha, ej. 10 Sep 2026" aria-label="Filtrar por fecha" className="border-white/10 bg-slate-950/70" />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3">
              <label className="inline-flex cursor-pointer items-center gap-2 text-[10px] font-semibold text-slate-400"><Checkbox checked={filterDraft.hasEvs} onCheckedChange={(checked) => setFilterDraft((value) => ({ ...value, hasEvs: checked === true }))} />Con EVs</label>
              <label className="inline-flex cursor-pointer items-center gap-2 text-[10px] font-semibold text-slate-400"><Checkbox checked={filterDraft.hasPaste} onCheckedChange={(checked) => setFilterDraft((value) => ({ ...value, hasPaste: checked === true }))} />Con PokéPaste</label>
              <label className="inline-flex cursor-pointer items-center gap-2 text-[10px] font-semibold text-slate-400"><Checkbox checked={filterDraft.hasReplica} onCheckedChange={(checked) => setFilterDraft((value) => ({ ...value, hasReplica: checked === true }))} />Con Replica Code</label>
              <div className="ml-auto flex gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={clearCompetitiveFilters} disabled={!hasCompetitiveFilters(filterDraft) && !competitiveActive} className="text-[10px] text-slate-500">Limpiar</Button>
                <Button type="button" variant="outline" size="sm" onClick={applyCompetitiveFilters} className="gap-1.5 border-violet-300/20 bg-violet-300/7 text-[10px] font-black text-violet-200"><Filter className="size-3.5" />Aplicar filtros</Button>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[10px] text-slate-500">
            <span className="inline-flex items-center gap-1.5"><Users className="size-3.5 text-cyan-300" /><strong className="text-slate-300">{data.pagination.totalItems}</strong> resultados</span>
            <span>{data.pagination.totalAvailable} equipos en {data.format.label}</span>
            {pokemon.length ? <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Core AND · {filterLabel}</Badge> : null}
            {competitiveActive ? <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-[9px] text-violet-200">Filtros competitivos</Badge> : null}
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
            <h2 className="mt-1 text-lg font-black text-white">{data.format.label}{filterLabel ? ` · ${filterLabel}` : ""}</h2>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" disabled={loading || data.pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="gap-1 border-white/10 bg-white/3 text-[10px] text-slate-300"><ChevronLeft className="size-3.5" />Anterior</Button>
            <span className="min-w-24 text-center font-mono text-[10px] text-slate-500">{pageLabel}</span>
            <Button type="button" variant="outline" size="sm" disabled={loading || data.pagination.totalPages === 0 || data.pagination.page >= data.pagination.totalPages} onClick={() => setPage((value) => value + 1)} className="gap-1 border-white/10 bg-white/3 text-[10px] text-slate-300">Siguiente<ChevronRight className="size-3.5" /></Button>
          </div>
        </div>

        {data.teams.length ? <div className="grid gap-3 xl:grid-cols-2">{data.teams.map((team) => <TeamCard key={team.id} team={team} formatLabel={data.format.label} importing={importingTeamId === team.id} importDisabled={Boolean(importingTeamId)} onInspect={() => void inspectTeam(team)} onImport={() => void importTeam(team)} />)}</div> : (
          <div className="rounded-2xl border border-white/7 bg-slate-950/45 px-6 py-16 text-center">
            <Search className="mx-auto size-8 text-slate-700" />
            <h3 className="mt-3 text-sm font-black text-white">No hay equipos con esos filtros</h3>
            <p className="mt-1 text-xs text-slate-600">Quita un Pokémon o limpia algún filtro competitivo.</p>
          </div>
        )}

        {data.pagination.totalPages > 1 ? <div className="mt-4 flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" disabled={loading || data.pagination.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="text-[10px] text-slate-400"><ChevronLeft className="size-3.5" />Anterior</Button>
          <span className="font-mono text-[10px] text-slate-600">{pageLabel}</span>
          <Button type="button" variant="ghost" size="sm" disabled={loading || data.pagination.page >= data.pagination.totalPages} onClick={() => setPage((value) => value + 1)} className="text-[10px] text-slate-400">Siguiente<ChevronRight className="size-3.5" /></Button>
        </div> : null}
      </section>

      <footer className="rounded-2xl border border-white/7 bg-slate-950/45 px-4 py-3 text-[10px] leading-5 text-slate-600">
        Datos de <a href={data.source.url} target="_blank" rel="noreferrer" className="font-semibold text-cyan-300 hover:text-cyan-200">{data.source.label}</a>. La app pagina y filtra en el servidor; el PokéPaste sólo se descarga al abrir el Inspector o importar al Team Builder.
      </footer>

      <Dialog open={Boolean(inspectorTeam)} onOpenChange={(open) => { if (!open) closeInspector(); }}>
        <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-200 sm:max-w-6xl">
          {inspectorTeam ? <>
            <DialogHeader className="pr-8">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Team Inspector</Badge>
                {inspectorTeam.rank && inspectorTeam.rank !== "-" ? <Badge variant="outline" className="border-amber-300/15 bg-amber-300/7 text-[9px] text-amber-200">{inspectorTeam.rank}</Badge> : null}
                {inspectorTeam.hasEvs ? <Badge variant="outline" className="border-emerald-300/15 bg-emerald-300/7 text-[9px] text-emerald-200">EVs publicados</Badge> : null}
              </div>
              <DialogTitle className="text-xl font-black text-white">{inspectorTeam.playerName}</DialogTitle>
              <DialogDescription className="text-xs text-slate-500">
                {(inspectorTeam.tournament && inspectorTeam.tournament !== "-" ? inspectorTeam.tournament : data.format.label)}{inspectorTeam.dateShared ? ` · ${inspectorTeam.dateShared}` : ""}{inspectorTeam.replicaCode ? ` · Replica ${inspectorTeam.replicaCode}` : ""}
              </DialogDescription>
            </DialogHeader>

            {inspectorLoading ? <div className="flex min-h-56 flex-col items-center justify-center gap-3 rounded-2xl border border-white/7 bg-white/[0.02]"><Loader2 className="size-7 animate-spin text-cyan-300" /><p className="text-xs text-slate-500">Descargando y leyendo el PokéPaste…</p></div> : null}

            {!inspectorLoading && inspectorError ? <div role="alert" className="flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-4 py-3 text-xs text-rose-200"><AlertTriangle className="size-4" />{inspectorError}</div> : null}

            {!inspectorLoading && inspectorSets.length ? <div className="grid gap-3 lg:grid-cols-2">{inspectorSets.map((set) => <InspectorSetCard key={set.id} set={set} />)}</div> : null}

            {!inspectorLoading && !inspectorSets.length && !inspectorError ? <div className="rounded-2xl border border-white/7 bg-white/[0.02] p-4">
              <p className="text-xs font-black text-white">Roster público</p>
              <p className="mt-1 text-[10px] text-slate-600">Este equipo no tiene un PokéPaste público utilizable; mostramos únicamente los seis Pokémon publicados por VGCPastes.</p>
              <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-6">{inspectorTeam.pokemon.map((species, index) => <div key={`${species}-${index}`} className="rounded-xl border border-white/6 bg-slate-950/50 p-2 text-center"><Image src={getSpriteUrl(species)} alt={species} width={52} height={52} unoptimized className="mx-auto size-12 object-contain" /><p className="mt-1 truncate text-[8px] font-semibold text-slate-500">{species}</p></div>)}</div>
            </div> : null}

            <DialogFooter className="border-t border-white/7 pt-4 sm:items-center sm:justify-between">
              <div className="flex flex-wrap gap-2 sm:mr-auto">
                {inspectorTeam.sourceUrl ? <a href={inspectorTeam.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-md border border-white/10 px-3 text-[10px] font-bold text-slate-400 hover:text-cyan-200">Fuente <ExternalLink className="size-3.5" /></a> : null}
                {inspectorTeam.pokepasteUrl ? <a href={inspectorTeam.pokepasteUrl} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-md border border-white/10 px-3 text-[10px] font-bold text-slate-400 hover:text-cyan-200">Abrir Paste <ExternalLink className="size-3.5" /></a> : null}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm" disabled={!inspectorPaste} onClick={() => void copyInspectorPaste()} className="gap-1.5 border-white/10 text-[10px] text-slate-300">{copyDone ? <Check className="size-3.5 text-emerald-300" /> : <Clipboard className="size-3.5" />}{copyDone ? "Copiado" : "Copiar Paste"}</Button>
                <Button type="button" size="sm" disabled={!inspectorPaste} onClick={importInspectedTeam} className="gap-1.5 bg-cyan-300 text-[10px] font-black text-slate-950 hover:bg-cyan-200"><Hammer className="size-3.5" />Importar al Builder</Button>
              </div>
            </DialogFooter>
          </> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
