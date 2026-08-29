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

export function PokemonLibraryVersionSelect({ species, format, onLoad }: PokemonLibraryVersionSelectProps) {
  const [entries, setEntries] = useState<PokemonLibraryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState("");

  useEffect(() => {
    let active = true;
    setSelectedVersionId("");
    setLoading(true);

    fetch(`/api/pokemon-library?format=${encodeURIComponent(format)}`, { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json()) as { pokemon?: PokemonLibraryEntry[] };
        if (!active) return;
        setEntries(response.ok ? payload.pokemon ?? [] : []);
      })
      .catch(() => {
        if (active) setEntries([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [format]);

  useEffect(() => {
    setSelectedVersionId("");
  }, [species]);

  const entry = useMemo(
    () => entries.find((candidate) => toId(candidate.species) === toId(species)),
    [entries, species],
  );
  const versions = entry?.versions ?? [];

  function chooseVersion(versionId: string) {
    const version = versions.find((candidate) => candidate.id === versionId);
    if (!entry || !version) return;
    setSelectedVersionId(versionId);
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
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <Label>Set</Label>
        {loading ? <Loader2 className="size-3 animate-spin text-slate-600" /> : versions.length ? <span className="text-[9px] text-violet-300/70">{versions.length}v</span> : null}
      </div>
      <Select value={selectedVersionId || undefined} onValueChange={chooseVersion} disabled={!species || loading || !versions.length}>
        <SelectTrigger className="w-full border-violet-300/15 bg-violet-300/[0.045] text-violet-100 disabled:border-white/8 disabled:bg-white/[0.025] disabled:text-slate-600">
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

// Kept as a compatibility shim while Team Builder no longer renders a separate My Pokémon control.
export function PokemonLibraryDialog(props: PokemonLibraryDialogProps) {
  void props;
  return null;
}
