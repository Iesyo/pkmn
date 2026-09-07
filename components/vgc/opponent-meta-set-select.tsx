"use client";

import { useEffect, useRef, useState } from "react";

import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  isOpponentMetaResponse,
  type OpponentMetaPreset,
  type OpponentMetaResponse,
} from "@/lib/opponent-meta-presets";
import { toId } from "@/lib/pokemon-data";

const LOCAL_CACHE_MS = 7 * 24 * 60 * 60 * 1_000;
const REQUEST_TIMEOUT_MS = 10_000;

type CachedResponse = {
  cachedAt: number;
  response: OpponentMetaResponse;
};

type OpponentMetaSetSelectProps = {
  species: string;
  selectedPresetId: string | null;
  autoLoadOnMount: boolean;
  onLoad: (preset: OpponentMetaPreset, automatic: boolean) => void;
};

type LoadState = {
  speciesId: string;
  response: OpponentMetaResponse | null;
  status: "idle" | "loading" | "ready" | "failed";
};

function cacheKey(speciesId: string) {
  return `pkmn:opponent-meta:${speciesId}`;
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

export function OpponentMetaSetSelect({
  species,
  selectedPresetId,
  autoLoadOnMount,
  onLoad,
}: OpponentMetaSetSelectProps) {
  const speciesId = toId(species);
  const [loadState, setLoadState] = useState<LoadState>({ speciesId: "", response: null, status: "idle" });
  const initialAutoLoadRef = useRef(autoLoadOnMount);
  const previousSpeciesRef = useRef(speciesId);
  const speciesChangeCountRef = useRef(0);
  const lastAutoLoadTokenRef = useRef("");
  const onLoadRef = useRef(onLoad);

  useEffect(() => {
    onLoadRef.current = onLoad;
  }, [onLoad]);

  useEffect(() => {
    if (previousSpeciesRef.current !== speciesId) {
      previousSpeciesRef.current = speciesId;
      speciesChangeCountRef.current += 1;
    }

    if (!speciesId) return;

    const autoLoadToken = `${speciesId}:${speciesChangeCountRef.current}`;
    const shouldAutoLoad = initialAutoLoadRef.current || speciesChangeCountRef.current > 0;
    const accept = (next: OpponentMetaResponse, phase: "cache" | "network") => {
      setLoadState({ speciesId, response: next, status: "ready" });
      const first = next.presets[0];
      const phaseToken = `${autoLoadToken}:${phase}`;
      if (shouldAutoLoad && first && lastAutoLoadTokenRef.current !== phaseToken) {
        lastAutoLoadTokenRef.current = phaseToken;
        onLoadRef.current(first, true);
      }
    };

    const cached = readCachedResponse(speciesId);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let active = true;

    queueMicrotask(() => {
      if (!active) return;
      if (cached) accept({ ...cached, stale: true }, "cache");
      else setLoadState({ speciesId, response: null, status: "loading" });
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
        if (active && !cached) setLoadState({ speciesId, response: null, status: "failed" });
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

  const response = loadState.speciesId === speciesId ? loadState.response : null;
  const loading = Boolean(speciesId) && (loadState.speciesId !== speciesId || loadState.status === "loading");
  const failed = loadState.speciesId === speciesId && loadState.status === "failed";
  const presets = response?.presets ?? [];
  const selected = presets.find((preset) => preset.id === selectedPresetId) ?? null;
  const primary = presets[0];
  const disabledLabel = loading ? "Buscando meta…" : failed || response ? "Meta no disponible" : "Elige un Pokémon";

  return (
    <div className="grid min-w-0 content-start gap-2">
      <Label>Set rival</Label>
      <Select
        value={selected?.id ?? "custom"}
        disabled={!speciesId || !presets.length}
        onValueChange={(value) => {
          const preset = presets.find((entry) => entry.id === value);
          if (preset) onLoad(preset, false);
        }}
      >
        <SelectTrigger className="w-full border-white/10 bg-white/4">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="custom" disabled>{presets.length ? "Personalizado" : disabledLabel}</SelectItem>
          {presets.map((preset) => (
            <SelectItem key={preset.id} value={preset.id}>
              {preset.rank === 1 ? "★ " : ""}#{preset.rank} {preset.label} · {presetHint(preset, primary)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selected ? (
        <p className="line-clamp-2 text-[9px] leading-4 text-slate-500">
          {selected.item} · {selected.ability} · {selected.nature} · {selected.evs || "sin SP"}
        </p>
      ) : null}
      {response ? (
        <p className="text-[8px] leading-3 text-slate-600">
          Estimación estadística · {response.season === "Current" ? "temporada actual" : response.season}
          {response.stale ? " · caché reciente" : ""} ·{" "}
          <a href={response.source.url} target="_blank" rel="noreferrer" className="text-cyan-300/60 underline underline-offset-2">
            {response.source.label}
          </a>
        </p>
      ) : null}
    </div>
  );
}
