"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import { BookOpen, Loader2, Search } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { BATTLE_FORMATS } from "@/lib/team-builder";
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

export function PokemonLibraryDialog({ format, onLoad }: PokemonLibraryDialogProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [entries, setEntries] = useState<PokemonLibraryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("es-MX");
    if (!normalized) return entries;
    return entries.filter((entry) =>
      entry.species.toLocaleLowerCase("es-MX").includes(normalized),
    );
  }, [entries, query]);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `/api/pokemon-library?format=${encodeURIComponent(format)}`,
        { cache: "no-store" },
      );
      const payload = (await response.json()) as {
        pokemon?: PokemonLibraryEntry[];
        error?: string;
      };
      if (!response.ok) {
        throw new Error(payload.error || "No pudimos cargar My Pokémon.");
      }
      setEntries(payload.pokemon ?? []);
    } catch (caught) {
      setEntries([]);
      setError(
        caught instanceof Error ? caught.message : "No pudimos cargar My Pokémon.",
      );
    } finally {
      setLoading(false);
    }
  }

  function changeOpen(nextOpen: boolean) {
    setOpen(nextOpen);
    if (nextOpen) {
      setQuery("");
      void refresh();
    }
  }

  function choose(entry: PokemonLibraryEntry, version: PokemonLibraryVersion) {
    onLoad(version.set, `${entry.species} v${version.version}`);
    setOpen(false);
  }

  const formatLabel =
    BATTLE_FORMATS.find((entry) => entry.id === format)?.label ?? format;

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          className="gap-2 rounded-full border-violet-300/15 bg-violet-300/5 text-violet-100"
        >
          <BookOpen className="size-4" />My Pokémon
        </Button>
      </DialogTrigger>
      <DialogContent className="grid max-h-[88vh] grid-rows-[auto_auto_minmax(0,1fr)] overflow-hidden border-white/10 bg-slate-950 text-slate-100 sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>My Pokémon</DialogTitle>
          <DialogDescription className="text-slate-500">
            Sets reutilizables de {formatLabel}. Las configuraciones idénticas comparten versión aunque aparezcan en varios Teams.
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Buscar Pokémon…"
            className="border-white/10 bg-black/25 pl-9"
          />
        </div>

        <div className="min-h-0 overflow-y-auto pr-1">
          {loading ? (
            <div role="status" className="flex items-center justify-center gap-2 py-14 text-sm text-violet-200">
              <Loader2 className="size-4 animate-spin" />Actualizando biblioteca…
            </div>
          ) : error ? (
            <div role="alert" className="rounded-2xl border border-rose-300/20 bg-rose-300/8 px-4 py-5 text-sm text-rose-200">
              {error}
            </div>
          ) : filtered.length ? (
            <div className="space-y-3 pb-1">
              {filtered.map((entry) => (
                <section key={entry.id} className="overflow-hidden rounded-2xl border border-white/8 bg-white/[0.025]">
                  <div className="flex items-center gap-3 border-b border-white/7 px-4 py-3">
                    <Image
                      src={getSpriteUrl(entry.species)}
                      alt={entry.species}
                      width={48}
                      height={48}
                      unoptimized
                      className="size-12 object-contain"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-black text-white">{entry.species}</p>
                      <p className="mt-0.5 text-[10px] text-slate-500">
                        {entry.versions.length} {entry.versions.length === 1 ? "versión" : "versiones"}
                      </p>
                    </div>
                  </div>

                  <div className="divide-y divide-white/6">
                    {entry.versions.map((version) => {
                      const source = version.sources[0];
                      const extraSources = Math.max(0, version.sources.length - 1);
                      return (
                        <button
                          key={version.id}
                          type="button"
                          onClick={() => choose(entry, version)}
                          className="grid w-full gap-3 px-4 py-3 text-left transition hover:bg-violet-300/6 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center"
                        >
                          <Badge variant="outline" className="w-fit border-violet-300/20 bg-violet-300/7 text-violet-200">
                            v{version.version}
                          </Badge>
                          <div className="min-w-0">
                            <p className="truncate text-xs font-bold text-slate-200">
                              {version.set.item || "Sin objeto"} · {version.set.nature || "Sin naturaleza"} · {version.set.ability || "Sin habilidad"}
                            </p>
                            <p className="mt-1 truncate text-[10px] text-slate-500">
                              {version.set.moves.map((move) => move.name).filter(Boolean).join(" · ") || "Sin movimientos"}
                            </p>
                            <p className="mt-1 truncate text-[9px] text-slate-600">
                              {source ? `${source.teamName} · Team v${source.teamVersion}` : "Set histórico"}
                              {extraSources ? ` · +${extraSources} uso${extraSources === 1 ? "" : "s"}` : ""}
                            </p>
                          </div>
                          <span className="text-[10px] font-black uppercase tracking-[0.12em] text-violet-300">
                            Cargar
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-5 py-12 text-center">
              <BookOpen className="mx-auto size-7 text-slate-700" />
              <p className="mt-3 text-sm font-bold text-slate-300">
                {query ? "No encontramos ese Pokémon." : "Todavía no hay sets guardados para este formato."}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                La biblioteca se alimenta automáticamente de las versiones de tus Teams.
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
