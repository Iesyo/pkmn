"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  BookmarkPlus,
  Check,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  ExternalLink,
  Eye,
  Hammer,
  Loader2,
  Search,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { parseShowdownPaste } from "@/lib/paste";
import { getSpriteUrl } from "@/lib/pokemon-data";
import {
  isScoutingPasteLibraryResponse,
  MAX_SCOUTING_PASTE_POKEMON_FILTERS,
  type ScoutingPasteDetail,
  type ScoutingPasteSummary,
} from "@/lib/scouting-paste-library";
import type { TournamentTeamBuilderImport } from "@/lib/tournament-scouting";
import type { PokemonSet } from "@/lib/types";

function apiError(payload: unknown, fallback: string) {
  return payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string"
    ? payload.error
    : fallback;
}

async function jsonPayload(response: Response) {
  const body = await response.text();
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("El servidor devolvió una respuesta incompleta.");
  }
}

function SetCard({ set }: { set: PokemonSet }) {
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
          <p className="mt-1 text-[10px] font-semibold text-cyan-200">{set.item || "Sin objeto explícito"}</p>
          <p className="mt-1 text-[9px] text-slate-500">{set.ability || "Habilidad no explícita"} · {set.nature || "Naturaleza no explícita"} · Lv. {set.level}</p>
        </div>
      </div>
      <div className="mt-3 rounded-lg border border-white/6 bg-white/[0.02] px-2.5 py-2 text-[9px]">
        <span className="uppercase tracking-wide text-slate-600">EVs / Stats</span>
        <p className="mt-1 font-mono text-slate-300">{set.evs || "No incluidos"}</p>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-1.5">
        {set.moves.map((move, index) => <div key={`${set.id}-${move.name}-${index}`} className="truncate rounded-lg border border-white/6 bg-white/[0.025] px-2.5 py-2 text-[9px] font-semibold text-slate-300">{move.name}</div>)}
      </div>
    </article>
  );
}

function PasteCard({
  item,
  onInspect,
  onBuilder,
  onDelete,
}: {
  item: ScoutingPasteSummary;
  onInspect: () => void;
  onBuilder: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="group flex min-w-0 flex-col rounded-2xl border border-white/8 bg-slate-950/65 p-4 transition hover:border-cyan-300/20 hover:bg-slate-950/85">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {item.creator ? <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-[9px] text-violet-200">{item.creator}</Badge> : null}
            <h3 className="truncate text-sm font-black text-white" title={item.name}>{item.name}</h3>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[9px] text-slate-600">
            <span>{item.format || "Sin formato"}</span>
            {item.sourceLabel ? <span>· {item.sourceLabel}</span> : null}
            {item.sourceUrl ? <a href={item.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-cyan-300 hover:text-cyan-200">Fuente <ExternalLink className="size-3" /></a> : null}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
          <Button type="button" variant="outline" size="sm" onClick={onInspect} className="h-8 gap-1.5 border-cyan-300/15 bg-cyan-300/5 px-2.5 text-[9px] text-cyan-200"><Eye className="size-3" />Inspector</Button>
          <Button type="button" variant="outline" size="sm" onClick={onBuilder} className="h-8 gap-1.5 border-white/10 bg-white/3 px-2.5 text-[9px] text-slate-300"><Hammer className="size-3" />Builder</Button>
          <Button type="button" variant="ghost" size="icon" onClick={onDelete} aria-label={`Eliminar ${item.name}`} className="size-8 text-slate-600 hover:bg-rose-300/8 hover:text-rose-300"><Trash2 className="size-3.5" /></Button>
        </div>
      </div>

      <button type="button" onClick={onInspect} className="mt-4 grid grid-cols-3 gap-2 text-left sm:grid-cols-6">
        {item.pokemon.map((species, index) => (
          <span key={`${item.id}-${species}-${index}`} className="min-w-0 rounded-xl border border-white/6 bg-white/[0.025] px-1.5 py-2 text-center">
            <Image src={getSpriteUrl(species)} alt={species} width={44} height={44} unoptimized className="mx-auto size-10 object-contain transition group-hover:scale-105" />
            <span className="mt-1 block truncate text-[8px] font-semibold text-slate-500">{species}</span>
          </span>
        ))}
      </button>
      {item.notes ? <p className="mt-3 line-clamp-2 text-[9px] leading-4 text-slate-600">{item.notes}</p> : null}
    </article>
  );
}

export function ScoutingPasteLibrary({ onImportTeam }: { onImportTeam: (request: TournamentTeamBuilderImport) => void }) {
  const [data, setData] = useState<import("@/lib/scouting-paste-library").ScoutingPasteLibraryResponse | null>(null);
  const [pokemon, setPokemon] = useState<string[]>([]);
  const [format, setFormat] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveInput, setSaveInput] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveCreator, setSaveCreator] = useState("");
  const [saveFormat, setSaveFormat] = useState("");
  const [saveSource, setSaveSource] = useState("");
  const [saveNotes, setSaveNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [inspector, setInspector] = useState<ScoutingPasteDetail | null>(null);
  const [inspectorSets, setInspectorSets] = useState<PokemonSet[]>([]);
  const [inspectorLoading, setInspectorLoading] = useState(false);
  const [inspectorError, setInspectorError] = useState("");
  const [copied, setCopied] = useState(false);
  const anchor = useComboboxAnchor();

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({ page: String(page), pageSize: "24" });
        pokemon.forEach((species) => params.append("pokemon", species));
        if (format) params.set("format", format);
        if (search.trim()) params.set("search", search.trim());
        const response = await fetch(`/api/scouting-pastes?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await jsonPayload(response);
        if (!response.ok) throw new Error(apiError(payload, "No pudimos cargar tus pastes."));
        if (!isScoutingPasteLibraryResponse(payload)) throw new Error("La biblioteca llegó en un formato inesperado.");
        setData(payload);
        if (payload.query.page !== page) setPage(payload.query.page);
        if (payload.query.pokemon.join("|") !== pokemon.join("|")) setPokemon(payload.query.pokemon);
      } catch (caught) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "No pudimos cargar tus pastes.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, search ? 180 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [pokemon, format, search, page, reloadKey]);

  async function fetchDetail(id: string) {
    const response = await fetch(`/api/scouting-pastes/${encodeURIComponent(id)}`, { cache: "no-store" });
    const payload = await jsonPayload(response);
    if (!response.ok || !payload || typeof payload !== "object" || !("item" in payload)) {
      throw new Error(apiError(payload, "No pudimos abrir ese paste."));
    }
    const item = payload.item as ScoutingPasteDetail;
    if (!item || typeof item.paste !== "string") throw new Error("Ese paste guardado está incompleto.");
    return item;
  }

  async function inspect(item: ScoutingPasteSummary) {
    setInspectorLoading(true);
    setInspectorError("");
    setCopied(false);
    try {
      const detail = await fetchDetail(item.id);
      setInspector(detail);
      setInspectorSets(parseShowdownPaste(detail.paste));
    } catch (caught) {
      setInspectorError(caught instanceof Error ? caught.message : "No pudimos abrir ese paste.");
      setInspector({ ...item, versionId: "", paste: "" });
      setInspectorSets([]);
    } finally {
      setInspectorLoading(false);
    }
  }

  async function toBuilder(item: ScoutingPasteSummary) {
    try {
      const detail = await fetchDetail(item.id);
      onImportTeam({ paste: detail.paste, suggestedName: item.name, sourceLabel: `Scouting · ${item.creator || item.name}` });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos enviar ese paste al Builder.");
    }
  }

  async function remove(item: ScoutingPasteSummary) {
    if (!window.confirm(`¿Eliminar “${item.name}” de Mis pastes?`)) return;
    const response = await fetch(`/api/scouting-pastes/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    const payload = await jsonPayload(response);
    if (!response.ok) {
      setError(apiError(payload, "No pudimos eliminar ese paste."));
      return;
    }
    setReloadKey((value) => value + 1);
  }

  function resetSaveForm() {
    setSaveInput("");
    setSaveName("");
    setSaveCreator("");
    setSaveFormat("");
    setSaveSource("");
    setSaveNotes("");
    setSaveError("");
  }

  async function savePaste() {
    const input = saveInput.trim();
    if (!input || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      let paste = input;
      let originalUrl = "";
      if (/^https?:\/\//i.test(input)) {
        const importResponse = await fetch("/api/pokepaste-import", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url: input }),
        });
        const importPayload = await jsonPayload(importResponse);
        if (!importResponse.ok || !importPayload || typeof importPayload !== "object" || !("paste" in importPayload) || typeof importPayload.paste !== "string") {
          throw new Error(apiError(importPayload, "No pudimos descargar ese PokéPaste."));
        }
        paste = importPayload.paste;
        originalUrl = input;
      }
      parseShowdownPaste(paste);
      const response = await fetch("/api/scouting-pastes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          paste,
          name: saveName,
          creator: saveCreator,
          format: saveFormat,
          sourceUrl: saveSource || originalUrl,
          sourceLabel: originalUrl ? "PokéPaste" : "Manual",
          notes: saveNotes,
        }),
      });
      const payload = await jsonPayload(response);
      if (!response.ok) throw new Error(apiError(payload, "No pudimos guardar ese paste."));
      setSaveOpen(false);
      resetSaveForm();
      setPage(1);
      setReloadKey((value) => value + 1);
    } catch (caught) {
      setSaveError(caught instanceof Error ? caught.message : "No pudimos guardar ese paste.");
    } finally {
      setSaving(false);
    }
  }

  const pageLabel = data?.pagination.totalPages ? `Página ${data.pagination.page} de ${data.pagination.totalPages}` : "Sin resultados";

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.25)]">
        <div className="h-px bg-gradient-to-r from-violet-400 via-cyan-300 to-transparent" />
        <div className="p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-2"><BookmarkPlus className="size-5 text-violet-300" /><p className="text-[10px] font-black uppercase tracking-[0.18em] text-violet-200">Biblioteca personal</p></div>
              <h1 className="mt-2 text-2xl font-black tracking-tight text-white">Mis pastes</h1>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">Referencias externas guardadas como snapshots. Sin carpetas: creador, formato y fuente son metadata buscable.</p>
            </div>
            <Button type="button" onClick={() => { resetSaveForm(); setSaveOpen(true); }} className="gap-2 bg-violet-300 font-black text-slate-950 hover:bg-violet-200"><BookmarkPlus className="size-4" />Guardar paste</Button>
          </div>

          <div className="mt-5 grid gap-3 border-t border-white/7 pt-5 lg:grid-cols-[minmax(180px,0.6fr)_minmax(320px,1.2fr)_minmax(220px,0.8fr)] lg:items-end">
            <div className="grid gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Formato</label>
              <Select value={format || "__all__"} onValueChange={(value) => { setFormat(value === "__all__" ? "" : value); setPage(1); }}>
                <SelectTrigger className="border-white/10 bg-slate-950/70"><SelectValue /></SelectTrigger>
                <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
                  <SelectItem value="__all__">Todos</SelectItem>
                  {data?.formats.map((entry) => <SelectItem key={entry} value={entry}>{entry}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid min-w-0 gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Core · hasta {MAX_SCOUTING_PASTE_POKEMON_FILTERS} Pokémon</label>
              <Combobox items={data?.pokemonOptions ?? []} multiple value={pokemon} onValueChange={(value: string[]) => { setPokemon(value.slice(0, MAX_SCOUTING_PASTE_POKEMON_FILTERS)); setPage(1); }}>
                <ComboboxValue>{(selected: string[]) => <ComboboxChips ref={anchor} className="min-h-10 border-white/10 bg-slate-950/70">{selected.map((species) => <ComboboxChip key={species} className="bg-cyan-300/10 text-cyan-100">{species}</ComboboxChip>)}<ComboboxChipsInput placeholder={selected.length ? "Añadir otro Pokémon" : "Ej. Sneasler"} disabled={selected.length >= MAX_SCOUTING_PASTE_POKEMON_FILTERS} /></ComboboxChips>}</ComboboxValue>
                <ComboboxContent anchor={anchor} className="border-white/10 bg-slate-950"><ComboboxEmpty>No está en tus pastes.</ComboboxEmpty><ComboboxList>{(species: string) => <ComboboxItem key={species} value={species}>{species}</ComboboxItem>}</ComboboxList></ComboboxContent>
              </Combobox>
            </div>
            <div className="grid gap-2">
              <label className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">Buscar</label>
              <Input type="search" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Nombre, creador, fuente…" className="border-white/10 bg-slate-950/70" />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3 text-[10px] text-slate-500">
            <span><strong className="text-slate-300">{data?.pagination.totalItems ?? 0}</strong> resultados</span>
            <span>{data?.pagination.totalAvailable ?? 0} pastes guardados</span>
            {pokemon.length ? <Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-[9px] text-cyan-200">Core AND · {pokemon.join(" + ")}</Badge> : null}
            <span className="font-mono text-slate-600">{pageLabel}</span>
          </div>
        </div>
      </section>

      {error ? <div role="alert" className="flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-4 py-3 text-xs text-rose-200"><AlertTriangle className="size-4" />{error}</div> : null}

      <section>
        <div className="mb-3 flex items-center justify-between gap-3"><div><p className="text-[10px] font-black uppercase tracking-[0.16em] text-violet-300">Archivo personal</p><h2 className="mt-1 text-lg font-black text-white">Pastes guardados</h2></div><div className="flex items-center gap-2"><Button variant="outline" size="sm" disabled={loading || (data?.pagination.page ?? 1) <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="text-[10px]"><ChevronLeft className="size-3.5" />Anterior</Button><span className="font-mono text-[10px] text-slate-500">{pageLabel}</span><Button variant="outline" size="sm" disabled={loading || !data?.pagination.totalPages || data.pagination.page >= data.pagination.totalPages} onClick={() => setPage((value) => value + 1)} className="text-[10px]">Siguiente<ChevronRight className="size-3.5" /></Button></div></div>
        {loading && !data ? <div className="rounded-2xl border border-white/7 bg-slate-950/45 px-6 py-16 text-center text-xs text-slate-500"><Loader2 className="mx-auto mb-3 size-6 animate-spin text-violet-300" />Cargando biblioteca…</div> : data?.items.length ? <div className="grid gap-3 xl:grid-cols-2">{data.items.map((item) => <PasteCard key={item.id} item={item} onInspect={() => void inspect(item)} onBuilder={() => void toBuilder(item)} onDelete={() => void remove(item)} />)}</div> : <div className="rounded-2xl border border-white/7 bg-slate-950/45 px-6 py-16 text-center"><Search className="mx-auto size-8 text-slate-700" /><h3 className="mt-3 text-sm font-black text-white">No hay pastes con esos filtros</h3><p className="mt-1 text-xs text-slate-600">Guarda uno nuevo o limpia la búsqueda.</p></div>}
      </section>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-200 sm:max-w-2xl">
          <DialogHeader><DialogTitle>Guardar paste en Scouting</DialogTitle><DialogDescription>Pega un Showdown paste completo o una URL de PokéPaste. Guardaremos una copia local del contenido.</DialogDescription></DialogHeader>
          <div className="grid gap-3">
            <Textarea value={saveInput} onChange={(event) => setSaveInput(event.target.value)} placeholder="https://pokepast.es/… o pega aquí el equipo de Showdown" className="min-h-40 border-white/10 bg-slate-900/70 font-mono text-xs" />
            <div className="grid gap-2 sm:grid-cols-2"><Input value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="Nombre (opcional)" /><Input value={saveCreator} onChange={(event) => setSaveCreator(event.target.value)} placeholder="Creador / owner" /><Input value={saveFormat} onChange={(event) => setSaveFormat(event.target.value)} placeholder="Formato, ej. Champions M-C" /><Input value={saveSource} onChange={(event) => setSaveSource(event.target.value)} placeholder="Fuente https (opcional)" /></div>
            <Textarea value={saveNotes} onChange={(event) => setSaveNotes(event.target.value)} placeholder="Notas (opcional)" className="min-h-20 border-white/10 bg-slate-900/70" />
            {saveError ? <div className="flex items-center gap-2 rounded-xl border border-rose-300/15 bg-rose-300/7 px-3 py-2 text-xs text-rose-200"><AlertTriangle className="size-4" />{saveError}</div> : null}
          </div>
          <DialogFooter><Button variant="ghost" onClick={() => setSaveOpen(false)}>Cancelar</Button><Button disabled={saving || !saveInput.trim()} onClick={() => void savePaste()} className="gap-2 bg-violet-300 font-black text-slate-950 hover:bg-violet-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <BookmarkPlus className="size-4" />}Guardar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(inspector)} onOpenChange={(open) => { if (!open) { setInspector(null); setInspectorSets([]); setInspectorError(""); } }}>
        <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-200 sm:max-w-6xl">
          {inspector ? <><DialogHeader className="pr-8"><div className="flex flex-wrap gap-2">{inspector.creator ? <Badge variant="outline" className="border-violet-300/15 bg-violet-300/7 text-violet-200">{inspector.creator}</Badge> : null}<Badge variant="outline" className="border-cyan-300/15 bg-cyan-300/7 text-cyan-200">{inspector.format}</Badge></div><DialogTitle className="text-xl font-black text-white">{inspector.name}</DialogTitle><DialogDescription>{inspector.sourceLabel || "Biblioteca personal"}</DialogDescription></DialogHeader>{inspectorLoading ? <div className="py-20 text-center"><Loader2 className="mx-auto size-7 animate-spin text-violet-300" /></div> : inspectorError ? <div className="rounded-xl border border-rose-300/15 bg-rose-300/7 px-4 py-3 text-xs text-rose-200">{inspectorError}</div> : <div className="grid gap-3 lg:grid-cols-2">{inspectorSets.map((set) => <SetCard key={set.id} set={set} />)}</div>}<DialogFooter className="border-t border-white/7 pt-4 sm:items-center sm:justify-between"><div className="sm:mr-auto">{inspector.sourceUrl ? <a href={inspector.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-md border border-white/10 px-3 text-[10px] font-bold text-slate-400">Fuente <ExternalLink className="size-3.5" /></a> : null}</div><div className="flex gap-2"><Button variant="outline" size="sm" disabled={!inspector.paste} onClick={async () => { try { await navigator.clipboard.writeText(inspector.paste); setCopied(true); window.setTimeout(() => setCopied(false), 1600); } catch { setInspectorError("El navegador no permitió copiar el paste."); } }} className="gap-1.5 text-[10px]">{copied ? <Check className="size-3.5 text-emerald-300" /> : <Clipboard className="size-3.5" />}{copied ? "Copiado" : "Copiar Paste"}</Button><Button size="sm" disabled={!inspector.paste} onClick={() => { onImportTeam({ paste: inspector.paste, suggestedName: inspector.name, sourceLabel: `Scouting · ${inspector.creator || inspector.name}` }); setInspector(null); }} className="gap-1.5 bg-cyan-300 text-[10px] font-black text-slate-950"><Hammer className="size-3.5" />Importar al Builder</Button></div></DialogFooter></> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
