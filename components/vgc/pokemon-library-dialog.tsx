"use client";

import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toId } from "@/lib/pokemon-data";
import type { PokemonSet } from "@/lib/types";

interface PokemonLibrarySource {
  teamId: string;
  teamName: string;
  teamVersionId: string;
  teamVersion: string;
  slot: number;
}

interface PokemonLibraryVersion {
  id: string;
  version: number;
  paste: string;
  createdAt: string;
  set: PokemonSet;
  sources: PokemonLibrarySource[];
}

interface PokemonLibraryEntry {
  id: string;
  species: string;
  format: string;
  versions: PokemonLibraryVersion[];
}

type PokemonLibraryDialogProps = {
  format: string;
  onLoad: (set: PokemonSet, label: string) => void;
};

type PokemonLibraryVersionSelectProps = {
  species: string;
  format: string;
  onLoad: (set: PokemonSet, label: string) => void;
};

type LibraryState = {
  format: string;
  entries: PokemonLibraryEntry[];
};

export function PokemonLibraryVersionSelect({ species, format, onLoad }: PokemonLibraryVersionSelectProps) {
  const [library, setLibrary] = useState<LibraryState>({ format: "", entries: [] });

  useEffect(() => {
    let active = true;

    fetch(`/api/pokemon-library?format=${encodeURIComponent(format)}`, { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json()) as { pokemon?: PokemonLibraryEntry[] };
        if (!active) return;
        setLibrary({ format, entries: response.ok ? payload.pokemon ?? [] : [] });
      })
      .catch(() => {
        if (active) setLibrary({ format, entries: [] });
      });

    return () => {
      active = false;
    };
  }, [format]);

  const loading = library.format !== format;
  const entry = useMemo(
    () => library.entries.find((candidate) => toId(candidate.species) === toId(species)),
    [library.entries, species],
  );
  const versions = loading ? [] : entry?.versions ?? [];

  function chooseVersion(versionId: string) {
    const version = versions.find((candidate) => candidate.id === versionId);
    if (!entry || !version) return;
    onLoad(version.set, `${entry.species} v${version.version}`);
  }

  const placeholder = !species
    ? "Elige Pokémon"
    : loading
      ? "Cargando…"
      : versions.length
        ? "Elegir versión"
        : "Sin versiones";

  return (
    <div className="grid min-w-0 gap-2">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <Label>Set</Label>
        {loading ? <Loader2 className="size-3 shrink-0 animate-spin text-slate-600" /> : versions.length ? <span className="shrink-0 text-[9px] text-violet-300/70">{versions.length}v</span> : null}
      </div>
      <Select key={`${format}:${toId(species)}`} onValueChange={chooseVersion} disabled={!species || loading || !versions.length}>
        <SelectTrigger className="w-full min-w-0 border-violet-300/15 bg-violet-300/[0.045] text-violet-100 disabled:border-white/8 disabled:bg-white/[0.025] disabled:text-slate-600">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {versions.map((version) => {
            const source = version.sources[0];
            return (
              <SelectItem key={version.id} value={version.id}>
                v{version.version}{source ? ` · ${source.teamName}` : " · Histórico"}
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>
    </div>
  );
}

// Compatibility shim: Team Builder still imports this symbol, but the standalone button is intentionally gone.
export function PokemonLibraryDialog(props: PokemonLibraryDialogProps) {
  void props;
  return null;
}
