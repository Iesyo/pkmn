"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { Check, Gamepad2, Loader2, Save, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { getSpeciesOptions, loadShowdownSnapshot } from "@/lib/showdown-data";
import type { MatchResult, TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";

function unique(values: string[]) {
  return [...new Set(values)];
}

export function ChampionsQuickMatchDialog({
  version,
  onCreated,
}: {
  version: TeamVersion;
  onCreated?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<MatchResult>("win");
  const [selected, setSelected] = useState<string[]>([]);
  const [lead, setLead] = useState<string[]>([]);
  const [opponentSelected, setOpponentSelected] = useState<string[]>([]);
  const [opponentQuery, setOpponentQuery] = useState("");
  const [championsSpecies, setChampionsSpecies] = useState<string[]>([]);
  const [loadingSpecies, setLoadingSpecies] = useState(false);
  const [speciesError, setSpeciesError] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || championsSpecies.length) return;
    let active = true;
    setLoadingSpecies(true);
    setSpeciesError("");
    loadShowdownSnapshot()
      .then((snapshot) => {
        if (!active) return;
        setChampionsSpecies(getSpeciesOptions(snapshot, "champions"));
      })
      .catch((caught) => {
        if (active) setSpeciesError(caught instanceof Error ? caught.message : "No pudimos cargar el catálogo Champions.");
      })
      .finally(() => {
        if (active) setLoadingSpecies(false);
      });
    return () => {
      active = false;
    };
  }, [open, championsSpecies.length]);

  const opponentResults = useMemo(() => {
    const query = opponentQuery.trim().toLocaleLowerCase();
    if (!query) return [];
    return championsSpecies
      .filter((species) => species.toLocaleLowerCase().includes(query) && !opponentSelected.includes(species))
      .slice(0, 12);
  }, [championsSpecies, opponentQuery, opponentSelected]);

  const canSave = selected.length === 4 && lead.length === 2 && opponentSelected.length === 4;

  function resetEntry() {
    setResult("win");
    setSelected([]);
    setLead([]);
    setOpponentSelected([]);
    setOpponentQuery("");
    setError("");
  }

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) resetEntry();
    setOpen(nextOpen);
  }

  function toggleSelected(species: string) {
    setError("");
    if (selected.includes(species)) {
      setSelected((current) => current.filter((entry) => entry !== species));
      setLead((current) => current.filter((entry) => entry !== species));
      return;
    }
    if (selected.length >= 4) return;
    setSelected((current) => [...current, species]);
  }

  function toggleLead(species: string) {
    if (!selected.includes(species)) return;
    setError("");
    if (lead.includes(species)) {
      setLead((current) => current.filter((entry) => entry !== species));
      return;
    }
    if (lead.length >= 2) return;
    setLead((current) => [...current, species]);
  }

  function addOpponent(species: string) {
    setError("");
    if (opponentSelected.includes(species) || opponentSelected.length >= 4) return;
    setOpponentSelected((current) => [...current, species]);
    setOpponentQuery("");
  }

  async function saveMatch() {
    setError("");
    const rival = unique(opponentSelected);
    if (selected.length !== 4) {
      setError("Elige exactamente tus 4 Pokémon usados.");
      return;
    }
    if (lead.length !== 2 || lead.some((species) => !selected.includes(species))) {
      setError("Elige exactamente tus 2 Pokémon de lead entre tus 4 picks.");
      return;
    }
    if (rival.length !== 4) {
      setError("Elige exactamente los 4 Pokémon usados por el rival.");
      return;
    }

    setSaving(true);
    try {
      const response = await fetch("/api/matches", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          teamVersionId: version.id,
          result,
          selected,
          opponentSelected: rival,
          lead,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { error?: string };
        throw new Error(payload.error ?? "No pudimos registrar la partida Champions.");
      }
      await onCreated?.();
      setOpen(false);
      resetEntry();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos registrar la partida Champions.");
    } finally {
      setSaving(false);
    }
  }

  const disabled = version.demo || !onCreated;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          type="button"
          disabled={disabled}
          className="gap-2 rounded-full bg-amber-300 font-black text-slate-950 hover:bg-amber-200"
          title={version.demo ? "Guarda un Team real para registrar partidas" : "Registrar una partida rápida de Pokémon Champions"}
        >
          <Gamepad2 className="size-4" />Partida Champions
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Partida rápida · Pokémon Champions</DialogTitle>
          <DialogDescription className="text-slate-500">
            Guarda sólo lo necesario: resultado, tus 4 picks, tu lead y los 4 picks del rival. La partida queda ligada a esta versión exacta del Team.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">Resultado</p>
              <span className="text-[9px] text-slate-600">{version.name}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button type="button" variant="outline" onClick={() => setResult("win")} className={cn("border-white/8 bg-white/3", result === "win" && "border-emerald-300/35 bg-emerald-300/10 text-emerald-200")}>WIN</Button>
              <Button type="button" variant="outline" onClick={() => setResult("loss")} className={cn("border-white/8 bg-white/3", result === "loss" && "border-rose-300/35 bg-rose-300/10 text-rose-200")}>LOSS</Button>
            </div>
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-cyan-300">Tus 4 picks</p>
              <span className="text-[10px] text-slate-600">{selected.length}/4</span>
            </div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              {version.pokemon.map((pokemon) => {
                const active = selected.includes(pokemon.species);
                return (
                  <button
                    key={pokemon.id}
                    type="button"
                    onClick={() => toggleSelected(pokemon.species)}
                    className={cn("relative flex min-h-24 flex-col items-center justify-center rounded-xl border border-white/8 bg-white/[0.025] p-2 text-center transition hover:border-cyan-300/25", active && "border-cyan-300/45 bg-cyan-300/10")}
                  >
                    <Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={54} height={54} unoptimized className="size-12 object-contain" />
                    <span className="mt-1 w-full truncate text-[9px] font-semibold text-slate-300">{pokemon.species}</span>
                    {active ? <span className="absolute right-1.5 top-1.5 flex size-4 items-center justify-center rounded-full bg-cyan-300 text-slate-950"><Check className="size-3" /></span> : null}
                  </button>
                );
              })}
            </div>
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-violet-300">Tu lead</p>
              <span className="text-[10px] text-slate-600">{lead.length}/2</span>
            </div>
            {selected.length ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {selected.map((species) => {
                  const active = lead.includes(species);
                  return (
                    <button key={species} type="button" onClick={() => toggleLead(species)} className={cn("flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.025] p-2 text-left transition", active && "border-violet-300/45 bg-violet-300/10")}>
                      <Image src={getSpriteUrl(species)} alt={species} width={34} height={34} unoptimized className="size-8 object-contain" />
                      <span className="min-w-0 flex-1 truncate text-[10px] font-semibold text-slate-300">{species}</span>
                      {active ? <Check className="size-3.5 shrink-0 text-violet-300" /> : null}
                    </button>
                  );
                })}
              </div>
            ) : <p className="text-[10px] text-slate-600">Primero elige tus 4 picks.</p>}
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[10px] font-black uppercase tracking-[0.14em] text-amber-300">4 picks del rival</p>
              <span className="text-[10px] text-slate-600">{opponentSelected.length}/4</span>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Array.from({ length: 4 }, (_, index) => opponentSelected[index] ?? "").map((species, index) => (
                <button
                  key={`${species || "empty"}-${index}`}
                  type="button"
                  onClick={() => species && setOpponentSelected((current) => current.filter((_, currentIndex) => currentIndex !== index))}
                  disabled={!species}
                  className={cn("relative flex min-h-20 flex-col items-center justify-center rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-2 text-slate-600", species && "border-solid border-amber-300/20 bg-amber-300/[0.045] text-slate-300")}
                >
                  {species ? <><Image src={getSpriteUrl(species)} alt={species} width={42} height={42} unoptimized className="size-10 object-contain" /><span className="mt-1 w-full truncate text-[9px] font-semibold">{species}</span><X className="absolute right-1.5 top-1.5 size-3 text-slate-600" /></> : <span className="text-[9px]">Pick {index + 1}</span>}
                </button>
              ))}
            </div>

            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" />
              <Input
                value={opponentQuery}
                onChange={(event) => setOpponentQuery(event.target.value)}
                disabled={loadingSpecies || opponentSelected.length >= 4}
                placeholder={loadingSpecies ? "Cargando catálogo Champions…" : opponentSelected.length >= 4 ? "Ya elegiste 4 Pokémon" : "Buscar Pokémon Champions…"}
                className="h-11 border-amber-300/20 bg-black/25 pl-10"
              />
            </div>
            {speciesError ? <p role="alert" className="text-[10px] text-rose-300">{speciesError}</p> : null}
            {loadingSpecies ? <p className="flex items-center gap-2 text-[10px] text-slate-600"><Loader2 className="size-3 animate-spin" />Cargando especies disponibles…</p> : null}
            {opponentQuery.trim() && !loadingSpecies ? (
              <div className="grid max-h-48 grid-cols-2 gap-1.5 overflow-y-auto rounded-xl border border-white/8 bg-black/20 p-2 sm:grid-cols-3">
                {opponentResults.length ? opponentResults.map((species) => (
                  <button key={species} type="button" onClick={() => addOpponent(species)} className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[10px] text-slate-300 transition hover:bg-amber-300/8 hover:text-amber-100">
                    <Image src={getSpriteUrl(species)} alt="" width={30} height={30} unoptimized className="size-7 shrink-0 object-contain" />
                    <span className="truncate">{species}</span>
                  </button>
                )) : <p className="col-span-full py-4 text-center text-[10px] text-slate-600">Sin coincidencias en Champions.</p>}
              </div>
            ) : null}
          </section>

          {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
          <Button type="button" onClick={() => void saveMatch()} disabled={!canSave || saving} className="gap-2 bg-amber-300 font-black text-slate-950 hover:bg-amber-200">
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {saving ? "Guardando" : "Guardar partida"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
