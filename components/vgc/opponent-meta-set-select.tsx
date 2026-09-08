"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  isOpponentMetaResponse,
  type OpponentMetaPreset,
  type OpponentMetaResponse,
} from "@/lib/opponent-meta-presets";
import { toId } from "@/lib/pokemon-data";
import type { PokemonSet } from "@/lib/types";
import {
  loadPokemonLibraryEntries,
  type PokemonLibraryEntry,
  type PokemonLibraryVersion,
} from "./pokemon-library-dialog";

const LOCAL_CACHE_MS = 7 * 24 * 60 * 60 * 1_000;
const REQUEST_TIMEOUT_MS = 10_000;
const META_VALUE_PREFIX = "meta:";
const LIBRARY_VALUE_PREFIX = "library:";

type CachedResponse = {
  cachedAt: number;
  response: OpponentMetaResponse;
};

type OpponentMetaSetSelectProps = {
  species: string;
  format: string;
  selectedSetId: string | null;
  autoLoadOnMount: boolean;
  onLoadMeta: (preset: OpponentMetaPreset, automatic: boolean) => void;
  onLoadLibrary: (set: PokemonSet, selectionId: string) => void;
};

type MetaLoadState = {
  speciesId: string;
  response: OpponentMetaResponse | null;
  status: "idle" | "loading" | "ready" | "failed";
};

type LibraryState = {
  format: string;
  entries: PokemonLibraryEntry[];
};

function cacheKey(speciesId: string) {
  return `pkmn:opponent-meta:v3:${speciesId}`;
}

function metaValue(presetId: string) {
  return `${META_VALUE_PREFIX}${presetId}`;
}

function libraryValue(versionId: string) {
  return `${LIBRARY_VALUE_PREFIX}${versionId}`;
}

function readCachedResponse(speciesId: string) {
  try {
    const parsed = JSON.parse(localStorage.getItem(cacheKey(speciesId)) ?? "null") as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const cached = parsed as Partial<CachedResponse>;
    if (typeof cached.cachedAt !== "number" || Date.now() - cached.cachedAt > LOCAL_CACHE_MS) return null;
    return isOpponentMetaResponse(cached.response) ? cached.response : null;
  } catch {
    return null;
  }
}

function writeCachedResponse(speciesId: string, response: OpponentMetaResponse) {
  try {
    localStorage.setItem(cacheKey(speciesId), JSON.stringify({ cachedAt: Date.now(), response } satisfies CachedResponse));
  } catch {
    // Local storage is an optional last-known fallback.
  }
}

function presetHint(preset: OpponentMetaPreset, primary: OpponentMetaPreset) {
  const differentMove = preset.moves.find((move) => !primary.moves.includes(move));
  if (differentMove) return differentMove;
  if (preset.nature !== primary.nature) return preset.nature;
  if (preset.item !== primary.item) return preset.item;
  if (preset.ability !== primary.ability) return preset.ability;
  if (preset.evs !== primary.evs) return preset.evs;
  return preset.moves[3];
}

function versionLabel(version: PokemonLibraryVersion) {
  const source = version.sources[0];
  return `v${version.version}${source ? ` · ${source.teamName}` : " · Guardado"}`;
}

export function OpponentMetaSetSelect({
  species,
  format,
  selectedSetId,
  autoLoadOnMount,
  onLoadMeta,
  onLoadLibrary,
}: OpponentMetaSetSelectProps) {
  const speciesId = toId(species);
  const [metaState, setMetaState] = useState<MetaLoadState>({ speciesId: "", response: null, status: "idle" });
  const [library, setLibrary] = useState<LibraryState>({ format: "", entries: [] });
  const initialAutoLoadRef = useRef(autoLoadOnMount);
  const previousSpeciesRef = useRef(speciesId);
  const speciesChangeCountRef = useRef(0);
  const lastAutoLoadTokenRef = useRef("");
  const onLoadMetaRef = useRef(onLoadMeta);

  useEffect(() => {
    onLoadMetaRef.current = onLoadMeta;
  }, [onLoadMeta]);

  useEffect(() => {
    let active = true;

    loadPokemonLibraryEntries(format).then((entries) => {
      if (active) setLibrary({ format, entries });
    });

    return () => {
      active = false;
    };
  }, [format]);

  useEffect(() => {
    if (previousSpeciesRef.current !== speciesId) {
      previousSpeciesRef.current = speciesId;
      speciesChangeCountRef.current += 1;
    }

    if (!speciesId) return;

    const autoLoadToken = `${speciesId}:${speciesChangeCountRef.current}`;
    const shouldAutoLoad = initialAutoLoadRef.current || speciesChangeCountRef.current > 0;
    const accept = (next: OpponentMetaResponse, phase: "cache" | "network") => {
      setMetaState({ speciesId, response: next, status: "ready" });
      const first = next.presets[0];
      const phaseToken = `${autoLoadToken}:${phase}`;
      if (shouldAutoLoad && first && lastAutoLoadTokenRef.current !== phaseToken) {
        lastAutoLoadTokenRef.current = phaseToken;
        onLoadMetaRef.current(first, true);
      }
    };

    const cached = readCachedResponse(speciesId);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let active = true;

    queueMicrotask(() => {
      if (!active) return;
      if (cached) accept({ ...cached, stale: true }, "cache");
      else setMetaState({ speciesId, response: null, status: "loading" });
    });

    void fetch(`/api/opponent-meta/${encodeURIComponent(speciesId)}`, {
      headers: { accept: "application/json" },
      signal: controller.signal,
    })
      .then(async (result) => {
        if (!result.ok) throw new Error(`Opponent meta responded ${result.status}`);
        return result.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (!active || !isOpponentMetaResponse(payload)) throw new Error("Invalid opponent meta response");
        writeCachedResponse(speciesId, payload);
        accept(payload, "network");
      })
      .catch(() => {
        if (active && !cached) setMetaState({ speciesId, response: null, status: "failed" });
      })
      .finally(() => {
        window.clearTimeout(timeout);
      });

    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [speciesId]);

  const response = metaState.speciesId === speciesId ? metaState.response : null;
  const metaLoading = Boolean(speciesId) && (metaState.speciesId !== speciesId || metaState.status === "loading");
  const presets = response?.presets ?? [];
  const primary = presets[0];
  const libraryLoading = library.format !== format;
  const libraryEntry = useMemo(
    () => library.entries.find((candidate) => toId(candidate.species) === speciesId),
    [library.entries, speciesId],
  );
  const versions = useMemo(
    () => libraryLoading ? [] : [...(libraryEntry?.versions ?? [])]
      .sort((left, right) => right.version - left.version || right.createdAt.localeCompare(left.createdAt)),
    [libraryEntry?.versions, libraryLoading],
  );
  const selectedMeta = presets.find((preset) => metaValue(preset.id) === selectedSetId) ?? null;
  const selectedLibrary = versions.find((version) => libraryValue(version.id) === selectedSetId) ?? null;
  const selectedValue = selectedMeta
    ? metaValue(selectedMeta.id)
    : selectedLibrary
      ? libraryValue(selectedLibrary.id)
      : "";
  const loading = metaLoading || libraryLoading;
  const hasOptions = presets.length > 0 || versions.length > 0;
  const placeholder = !speciesId
    ? "Elige un Pokémon"
    : loading && !hasOptions
      ? "Cargando sets…"
      : hasOptions
        ? "Personalizado"
        : "Sin sets disponibles";
  const selectedDetails = selectedMeta
    ? `${selectedMeta.item} · ${selectedMeta.ability} · ${selectedMeta.nature} · ${selectedMeta.evs || "sin SP"}`
    : selectedLibrary
      ? `${versionLabel(selectedLibrary)} · ${selectedLibrary.set.item || "sin objeto"} · ${selectedLibrary.set.ability} · ${selectedLibrary.set.nature} · ${selectedLibrary.set.evs || "sin SP"}`
      : "";

  function chooseSet(value: string) {
    if (value.startsWith(META_VALUE_PREFIX)) {
      const preset = presets.find((entry) => metaValue(entry.id) === value);
      if (preset) onLoadMeta(preset, false);
      return;
    }

    const version = versions.find((entry) => libraryValue(entry.id) === value);
    if (version) onLoadLibrary(version.set, value);
  }

  return (
    <div className="grid min-w-0 content-start gap-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <Label>Set rival</Label>
        {loading ? <Loader2 className="size-3 shrink-0 animate-spin text-slate-600" /> : hasOptions ? <span className="shrink-0 text-[9px] text-slate-600">{presets.length} meta · {versions.length} propios</span> : null}
      </div>
      <Select value={selectedValue} disabled={!speciesId || !hasOptions} onValueChange={chooseSet}>
        <SelectTrigger className="w-full border-white/10 bg-white/4">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {presets.length ? (
            <SelectGroup>
              <SelectLabel className="text-[9px] font-black uppercase tracking-[0.12em] text-cyan-300/60">Meta estimado</SelectLabel>
              {presets.map((preset) => (
                <SelectItem key={metaValue(preset.id)} value={metaValue(preset.id)}>
                  {preset.rank === 1 ? "★ " : ""}#{preset.rank} Meta · {presetHint(preset, primary)}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : null}
          {presets.length && versions.length ? <SelectSeparator /> : null}
          {versions.length ? (
            <SelectGroup>
              <SelectLabel className="text-[9px] font-black uppercase tracking-[0.12em] text-violet-300/60">Mis sets guardados</SelectLabel>
              {versions.map((version) => (
                <SelectItem key={libraryValue(version.id)} value={libraryValue(version.id)}>
                  {versionLabel(version)}
                </SelectItem>
              ))}
            </SelectGroup>
          ) : null}
        </SelectContent>
      </Select>
      {selectedDetails ? <p className="line-clamp-2 text-[9px] leading-4 text-slate-500">{selectedDetails}</p> : null}
    </div>
  );
}
