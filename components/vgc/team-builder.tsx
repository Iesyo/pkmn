"use client";

import Image from "next/image";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Clipboard, Database, Download, Eraser, FolderOpen, Loader2, Plus, RefreshCw, Save, Shield, Upload, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { getSpriteUrl, toId } from "@/lib/pokemon-data";
import { getLegalAbilities, hydrateSetFromSnapshot, isItemLegal, isMoveLegal, isSpeciesAvailable, loadShowdownSnapshot, type ShowdownSnapshot } from "@/lib/showdown-data";
import { analyzeTypes } from "@/lib/team-stats";
import { BATTLE_FORMATS, DEFAULT_BATTLE_FORMAT, DEFAULT_BATTLE_MECHANICS, MECHANIC_LABELS, cloneForBuilder, emptyPokemon, formatVersion, getStatRules, isCompleteTeam, parseEvs, serializeShowdownPaste } from "@/lib/team-builder";
import { type BattleMechanic, type PokemonSet, type TeamGroup, type TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TypeBadge } from "./type-badge";
import type { DamageCalculatorSession } from "./damage-calculator";

const DamageCalculatorView = lazy(async () => {
  const calculator = await import("./damage-calculator");
  return { default: calculator.DamageCalculatorView };
});

type BuilderProps = {
  groups: TeamGroup[];
  initialVersion?: TeamVersion;
  onTeamCreated: (team: TeamGroup) => void;
  onVersionCreated: (version: TeamVersion) => void;
};

function mechanicsForFormat(format: string): BattleMechanic[] {
  return [...(BATTLE_FORMATS.find((entry) => entry.id === format)?.mechanics ?? [])];
}

function BuilderCoverage({ pokemon, teraEnabled }: { pokemon: PokemonSet[]; teraEnabled: boolean }) {
  const [useTera, setUseTera] = useState(false);
  const ready = pokemon.filter((set) => set.species);
  const analysis = useMemo(() => analyzeTypes(ready, teraEnabled && useTera), [ready, teraEnabled, useTera]);

  return (
    <aside className="rounded-[24px] border border-white/8 bg-[#0b1220]/92 p-4 shadow-[0_24px_80px_rgba(0,0,0,0.32)] xl:sticky xl:top-24">
      <div className="flex items-start justify-between gap-3">
        <div><p className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.15em] text-slate-200"><Shield className="size-4 text-cyan-300" />Type coverage</p><p className="mt-1 text-[10px] text-slate-600">Se actualiza con cada cambio</p></div>
        {teraEnabled ? <div className="flex items-center gap-2"><span className="text-[9px] text-slate-500">Tera</span><Switch checked={useTera} onCheckedChange={setUseTera} aria-label="Usar tipos Tera" /></div> : null}
      </div>
      <div className="mt-4 grid grid-cols-6 gap-1.5">
        {analysis.coverage.map((entry) => <div key={entry.type} className="rounded-lg border border-white/6 bg-white/[0.025] p-1 text-center"><TypeBadge type={entry.type} className="w-full px-0.5 text-[7px]">{entry.type.slice(0, 3).toUpperCase()}</TypeBadge><p className={entry.count ? "mt-1 text-[9px] font-black text-emerald-300" : "mt-1 text-[9px] font-black text-slate-700"}>{entry.count}×</p></div>)}
      </div>
      <div className="my-4 h-px bg-white/7" />
      <p className="mb-2 text-[9px] font-black uppercase tracking-[0.13em] text-slate-500">Defensive weaknesses</p>
      <div className="flex flex-wrap gap-1.5">{analysis.defense.filter((entry) => entry.count > 0).map((entry) => <TypeBadge key={entry.type} type={entry.type} className="gap-1 text-[8px]"><span>{entry.type}</span><strong className="text-rose-300">{entry.count}↓</strong>{entry.resistances ? <span className="text-emerald-300">{entry.resistances}↑</span> : null}</TypeBadge>)}</div>
      <p className="mb-2 mt-4 text-[9px] font-black uppercase tracking-[0.13em] text-slate-500">Resistances & immunities</p>
      <div className="flex flex-wrap gap-1.5">{analysis.resistances.map((entry) => <TypeBadge key={entry.type} type={entry.type} className="text-[8px]">{entry.type} {entry.count}↑</TypeBadge>)}{analysis.immunities.map((entry) => <TypeBadge key={entry.type} type={entry.type} className="text-[8px]">{entry.type} {entry.count}◎</TypeBadge>)}</div>
      <p className="mb-2 mt-4 text-[9px] font-black uppercase tracking-[0.13em] text-slate-500">Blind spots</p>
      <div className="flex flex-wrap gap-1.5">{analysis.blindSpots.map((type) => <TypeBadge key={type} type={type} className="text-[8px]">{type}</TypeBadge>)}</div>
    </aside>
  );
}

function SlotCard({ pokemon, selected, onClick, onClear }: { pokemon: PokemonSet; selected: boolean; onClick: () => void; onClear: () => void }) {
  return (
    <div className={cn("relative h-52 min-w-0 overflow-hidden rounded-2xl border transition", selected ? "border-cyan-300/55 bg-cyan-300/8 shadow-[0_0_24px_rgba(103,232,249,0.10)]" : "border-white/8 bg-[#0c1424] hover:border-white/16")}>
      <button type="button" onClick={onClick} aria-pressed={selected} aria-label={pokemon.species ? `Editar ${pokemon.species}` : `Elegir Pokémon para el slot ${pokemon.slot}`} className="flex h-full w-full min-w-0 flex-col p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300/60">
        <span className="absolute left-2 top-2 z-10 flex size-5 items-center justify-center rounded-full border border-white/8 bg-slate-900/90 text-[9px] font-black text-slate-500">{pokemon.slot}</span>
        {pokemon.species ? (
          <>
            <div className="flex h-14 shrink-0 items-center justify-center pt-1"><Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={64} height={64} unoptimized className="size-14 object-contain" /></div>
            <div className="min-w-0">
              <p className="w-full min-w-0 truncate text-xs font-black text-white">{pokemon.species}</p>
              <div className="mt-1 flex h-4 min-w-0 flex-nowrap gap-1 overflow-hidden">{pokemon.types.map((type) => <TypeBadge key={type} type={type} className="shrink-0 px-1.5 py-0 text-[7px]">{type}</TypeBadge>)}</div>
              <p className="mt-1.5 truncate rounded-md bg-amber-300/8 px-1.5 py-0.5 text-[9px] font-semibold text-amber-200/90">{pokemon.item || "Sin objeto"}</p>
              <p className="mt-1 truncate text-[9px] font-semibold text-emerald-300/80">{pokemon.ability || "Sin habilidad"}</p>
              <div className="mt-1.5 space-y-0.5">
                {pokemon.moves.map((move, moveIndex) => <p key={`${pokemon.id}-move-${moveIndex}`} className="flex min-w-0 items-center gap-1 text-[8px] leading-3 text-slate-500"><span className="shrink-0 text-slate-700">•</span><span className={cn("truncate", move.name ? "text-slate-400" : "text-slate-700")}>{move.name || "Movimiento"}</span></p>)}
              </div>
            </div>
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center"><Plus className="size-8 text-slate-700" /><p className="mt-2 text-xs font-black text-slate-500">Elegir Pokémon</p><p className="mt-1 text-[9px] text-slate-700">Slot {pokemon.slot}</p></div>
        )}
      </button>
      {pokemon.species ? <button type="button" onClick={onClear} title={`Quitar ${pokemon.species}`} aria-label={`Quitar ${pokemon.species} del Team`} className="absolute right-2 top-2 z-20 flex size-6 items-center justify-center rounded-full border border-white/8 bg-slate-900/90 text-slate-500 transition hover:border-rose-300/25 hover:bg-rose-300/10 hover:text-rose-300"><X className="size-3.5" /></button> : null}
    </div>
  );
}

function PasteDialog({ mode, paste, onImport }: { mode: "import" | "export"; paste: string; onImport?: (paste: string) => void }) {
  const [value, setValue] = useState(paste);
  const [copied, setCopied] = useState(false);
  const action = mode === "import" ? (
    <Button onClick={() => onImport?.(value)} className="w-full shrink-0 gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200 sm:w-auto">
      <Download className="size-4" />Cargar equipo
    </Button>
  ) : (
    <Button onClick={async () => { await navigator.clipboard.writeText(value); setCopied(true); }} className="w-full shrink-0 gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200 sm:w-auto">
      {copied ? <Check className="size-4" /> : <Clipboard className="size-4" />}{copied ? "Copiado" : "Copiar paste"}
    </Button>
  );
  return (
    <Dialog onOpenChange={(open) => { if (open) { setValue(paste); setCopied(false); } }}>
      <DialogTrigger asChild><Button variant="outline" className="gap-2 rounded-full border-white/10 bg-white/4">{mode === "import" ? <Download className="size-4" /> : <Upload className="size-4" />}{mode === "import" ? "Importar" : "Exportar"}</Button></DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <div className="flex flex-col gap-4 border-b border-white/8 pb-4 sm:flex-row sm:items-start sm:justify-between">
          <DialogHeader className="min-w-0 text-left"><DialogTitle>{mode === "import" ? "Importar Showdown paste" : "Exportar a Showdown"}</DialogTitle><DialogDescription className="text-slate-500">{mode === "import" ? "Pega seis sets completos para cargarlos en el Builder." : "Copia el equipo con el formato estándar de Pokémon Showdown."}</DialogDescription></DialogHeader>
          {action}
        </div>
        <Textarea value={value} onChange={(event) => setValue(event.target.value)} readOnly={mode === "export"} className="mt-4 min-h-96 border-white/10 bg-black/35 font-mono text-[11px] leading-5" />
      </DialogContent>
    </Dialog>
  );
}

function MyTeamsDialog({ versions, onLoad }: { versions: TeamVersion[]; onLoad: (versionId: string) => void }) {
  const [open, setOpen] = useState(false);

  function selectVersion(versionId: string) {
    onLoad(versionId);
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-2 rounded-full border-cyan-300/15 bg-cyan-300/5 text-cyan-100">
          <FolderOpen className="size-4" />My Teams
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-hidden border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>My Teams</DialogTitle>
          <DialogDescription className="text-slate-500">Abre una versión guardada para editarla. Al guardar, el original permanecerá intacto.</DialogDescription>
        </DialogHeader>
        {versions.length ? (
          <div className="max-h-[62vh] space-y-2 overflow-y-auto pr-1">
            {versions.map((version) => (
              <button key={version.id} type="button" onClick={() => selectVersion(version.id)} className="flex w-full items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.025] p-3 text-left transition hover:border-cyan-300/30 hover:bg-cyan-300/5">
                <div className="grid w-36 shrink-0 grid-cols-6 gap-0.5">
                  {version.pokemon.map((set) => <Image key={set.id} src={getSpriteUrl(set.species)} alt={set.species} width={34} height={34} unoptimized className="size-8 object-contain" />)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-black text-white">{version.name}</p>
                  <p className="mt-1 text-[10px] text-slate-500">{BATTLE_FORMATS.find((entry) => entry.id === version.format)?.label ?? version.format}</p>
                </div>
                <Badge variant="outline" className="shrink-0 border-cyan-300/20 bg-cyan-300/5 text-cyan-200">v{formatVersion(version)}</Badge>
              </button>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-5 py-10 text-center">
            <FolderOpen className="mx-auto size-6 text-slate-600" />
            <p className="mt-3 text-sm font-bold text-slate-300">Todavía no hay Teams guardados.</p>
            <p className="mt-1 text-xs text-slate-600">Completa el equipo actual y usa Guardar en Teams.</p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function TeamBuilder({ groups, initialVersion, onTeamCreated, onVersionCreated }: BuilderProps) {
  const [teamName, setTeamName] = useState(initialVersion?.demo ? `${initialVersion.name} Copy` : initialVersion?.name ?? "");
  const [sourceTeamId, setSourceTeamId] = useState(initialVersion?.demo ? "" : initialVersion?.teamId ?? "");
  const [format, setFormat] = useState(initialVersion?.format ?? DEFAULT_BATTLE_FORMAT);
  const [mechanics, setMechanics] = useState<BattleMechanic[]>(initialVersion?.mechanics ?? [...DEFAULT_BATTLE_MECHANICS]);
  const [pokemon, setPokemon] = useState<PokemonSet[]>(initialVersion ? cloneForBuilder(initialVersion.pokemon) : Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1)));
  const [selectedSlot, setSelectedSlot] = useState(0);
  const [slotRevisions, setSlotRevisions] = useState(() => Array.from({ length: 6 }, () => 0));
  const [dex, setDex] = useState<ShowdownSnapshot | null>(null);
  const [dexError, setDexError] = useState("");
  const [refreshingDex, setRefreshingDex] = useState(false);
  const [calculatorSessions, setCalculatorSessions] = useState<Record<string, DamageCalculatorSession>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const selected = pokemon[selectedSlot];
  const calculatorSessionKey = `${selected.id}:${format}:${slotRevisions[selectedSlot]}`;
  const storedVersions = groups.filter((team) => !team.versions[0]?.demo).flatMap((team) => team.versions);
  const paste = useMemo(() => serializeShowdownPaste(pokemon, mechanics), [pokemon, mechanics]);
  const complete = isCompleteTeam(pokemon);

  useEffect(() => {
    let active = true;
    loadShowdownSnapshot()
      .then((snapshot) => {
        if (!active) return;
        setDex(snapshot);
        setPokemon((current) => current.map((set) => hydrateSetFromSnapshot(snapshot, set)));
      })
      .catch((caught) => {
        if (active) setDexError(caught instanceof Error ? caught.message : "No pudimos cargar la Pokédex.");
      });
    return () => { active = false; };
  }, []);

  function loadVersion(versionId: string) {
    const version = storedVersions.find((entry) => entry.id === versionId);
    if (!version) return;
    const nextPokemon = cloneForBuilder(version.pokemon);
    setTeamName(version.name); setSourceTeamId(version.teamId); setFormat(version.format ?? DEFAULT_BATTLE_FORMAT); setMechanics(version.mechanics ?? mechanicsForFormat(version.format ?? DEFAULT_BATTLE_FORMAT)); setPokemon(dex ? nextPokemon.map((set) => hydrateSetFromSnapshot(dex, set)) : nextPokemon); setSelectedSlot(0); setCalculatorSessions({}); setSlotRevisions((current) => current.map((revision) => revision + 1)); setError(""); setMessage(`Cargado ${version.name} v${formatVersion(version)}. Los cambios crearán una versión nueva.`);
  }

  function resetBuilder() {
    setTeamName(""); setSourceTeamId(""); setFormat(DEFAULT_BATTLE_FORMAT); setMechanics([...DEFAULT_BATTLE_MECHANICS]); setPokemon(Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1))); setSelectedSlot(0); setCalculatorSessions({}); setSlotRevisions((current) => current.map((revision) => revision + 1)); setMessage(""); setError("");
  }

  function clearSlot(index: number) {
    const slotId = pokemon[index]?.id;
    if (!slotId) return;
    setPokemon((current) => current.map((set, currentIndex) => currentIndex === index ? emptyPokemon(set.slot) : set));
    setCalculatorSessions((current) => Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${slotId}:`))));
    setSlotRevisions((current) => current.map((revision, currentIndex) => currentIndex === index ? revision + 1 : revision));
    setSelectedSlot(index);
    setMessage("Pokémon eliminado del Team. El slot quedó libre.");
    setError("");
  }

  async function importPaste(value: string) {
    setError("");
    try {
      const { parseShowdownPaste } = await import("@/lib/paste");
      const imported = cloneForBuilder(parseShowdownPaste(value));
      setPokemon(dex ? imported.map((set) => hydrateSetFromSnapshot(dex, set)) : imported);
      setSelectedSlot(0);
      setCalculatorSessions({});
      setSlotRevisions((current) => current.map((revision) => revision + 1));
      setMessage("Paste importado. Revisa el formato y guarda cuando esté listo.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "No pudimos importar el paste."); }
  }

  function changeFormat(nextFormat: string) {
    const previousRules = getStatRules(format);
    const nextRules = getStatRules(nextFormat);
    setFormat(nextFormat);
    setCalculatorSessions({});
    if (nextFormat !== "custom") setMechanics(mechanicsForFormat(nextFormat));
    if (previousRules.totalMax !== nextRules.totalMax) {
      setPokemon((current) => current.map((set) => ({ ...set, evs: "" })));
      setMessage(`La escala cambió a ${nextRules.label}; reiniciamos la distribución para evitar valores incompatibles.`);
    }
  }

  async function refreshDatabases() {
    setRefreshingDex(true);
    setDexError("");
    setMessage("");
    try {
      const snapshot = await loadShowdownSnapshot({ fresh: true });
      setDex(snapshot);
      setPokemon((current) => current.map((set) => hydrateSetFromSnapshot(snapshot, set)));
      setMessage(`Bases actualizadas: ${Object.keys(snapshot.species).length.toLocaleString("es-MX")} Pokémon, ${Object.keys(snapshot.moves).length.toLocaleString("es-MX")} movimientos y ${Object.keys(snapshot.items ?? {}).length.toLocaleString("es-MX")} objetos · ${snapshot.metadata.captured}.`);
    } catch (caught) {
      setDexError(caught instanceof Error ? caught.message : "No pudimos actualizar las bases de datos.");
    } finally {
      setRefreshingDex(false);
    }
  }

  async function saveTeam() {
    setError(""); setMessage("");
    if (teamName.trim().length < 2) { setError("Ponle un nombre al Team."); return; }
    if (!complete) { setError("Completa los seis Pokémon y sus cuatro movimientos."); return; }
    if (!dex) { setError("Espera a que termine de cargar la Pokédex de Showdown."); return; }
    const statRules = getStatRules(format);
    if (pokemon.some((set) => Object.values(parseEvs(set.evs)).reduce((sum, value) => sum + value, 0) > statRules.totalMax)) { setError(`Revisa ${statRules.label}: algún Pokémon supera el máximo de ${statRules.totalMax}.`); return; }
    if (pokemon.some((set) => Object.values(parseEvs(set.evs)).some((value) => value > statRules.perStatMax))) { setError(`Revisa ${statRules.label}: ningún stat puede superar ${statRules.perStatMax}.`); return; }
    const unavailable = pokemon.find((set) => !isSpeciesAvailable(dex, set.species, format));
    if (unavailable) { setError(`${unavailable.species} no está disponible en ${BATTLE_FORMATS.find((entry) => entry.id === format)?.label ?? format}.`); return; }
    const invalidAbility = pokemon.find((set) => set.ability && !getLegalAbilities(dex, set.species).includes(set.ability));
    if (invalidAbility) { setError(`${invalidAbility.ability} no es una habilidad válida de ${invalidAbility.species}.`); return; }
    const invalidItem = pokemon.find((set) => !isItemLegal(dex, set.item, format));
    if (invalidItem) { setError(`${invalidItem.item} no está disponible en ${BATTLE_FORMATS.find((entry) => entry.id === format)?.label ?? format}.`); return; }
    const invalidMove = pokemon.flatMap((set) => set.moves.map((move) => ({ set, move }))).find(({ set, move }) => move.name && !isMoveLegal(dex, set.species, move.name, format));
    if (invalidMove) { setError(`${invalidMove.move.name} no está disponible para ${invalidMove.set.species} en este formato.`); return; }
    const duplicatedMoves = pokemon.find((set) => {
      const moveIds = set.moves.map((move) => toId(move.name)).filter(Boolean);
      return new Set(moveIds).size !== moveIds.length;
    });
    if (duplicatedMoves) { setError(`${duplicatedMoves.species} tiene movimientos repetidos.`); return; }
    setSaving(true);
    try {
      const response = await fetch(sourceTeamId ? `/api/teams/${sourceTeamId}/versions` : "/api/teams", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: teamName, paste, format, mechanics, builderSets: pokemon.map((set) => ({ types: set.types, moves: set.moves, mechanics: set.mechanics })) }) });
      const payload = (await response.json()) as { team?: TeamGroup; version?: TeamVersion; error?: string };
      if (!response.ok) throw new Error(payload.error || "No pudimos guardar el Team.");
      if (payload.team) { onTeamCreated(payload.team); const created = payload.team.versions[0]; setSourceTeamId(payload.team.id); setMessage(`Guardado como ${payload.team.name} v${formatVersion(created)}.`); }
      if (payload.version) { onVersionCreated(payload.version); setMessage(`Nueva versión guardada: v${formatVersion(payload.version)}.`); }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "No pudimos guardar el Team."); }
    finally { setSaving(false); }
  }

  return (
    <section className="space-y-4">
      <div className="rounded-[24px] border border-white/8 bg-slate-900/45 p-4 shadow-[0_24px_80px_rgba(0,0,0,0.24)]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="grid flex-1 gap-3 sm:grid-cols-[minmax(220px,1fr)_300px]">
            <div className="grid gap-1.5"><Label htmlFor="builder-name" className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Nombre del Team</Label><Input id="builder-name" value={teamName} onChange={(event) => setTeamName(event.target.value)} disabled={Boolean(sourceTeamId)} placeholder="Ej. Aurora Protocol" className="border-white/10 bg-black/20" /></div>
            <div className="grid gap-1.5"><Label className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Formato</Label><Select value={format} onValueChange={changeFormat}><SelectTrigger className="w-full border-white/10 bg-black/20"><SelectValue /></SelectTrigger><SelectContent>{BATTLE_FORMATS.map((entry) => <SelectItem key={entry.id} value={entry.id}>{entry.label}</SelectItem>)}</SelectContent></Select></div>
          </div>
          <div className="flex flex-wrap gap-2"><MyTeamsDialog versions={storedVersions} onLoad={loadVersion} /><PasteDialog mode="import" paste="" onImport={importPaste} /><PasteDialog mode="export" paste={paste} /><Button variant="outline" onClick={resetBuilder} className="gap-2 rounded-full border-rose-300/15 bg-rose-300/5 text-rose-200"><Eraser className="size-4" />Nuevo</Button><Button onClick={saveTeam} disabled={saving} className="gap-2 rounded-full bg-cyan-300 px-5 font-black text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}{sourceTeamId ? "Guardar versión" : "Guardar en Teams"}</Button></div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><span className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Mecánicas</span>{(["tera", "dynamax", "mega", "zmove"] as BattleMechanic[]).map((mechanic) => <label key={mechanic} className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px]", mechanics.includes(mechanic) ? "border-cyan-300/20 bg-cyan-300/8 text-cyan-100" : "border-white/7 bg-white/3 text-slate-600", format !== "custom" && "pointer-events-none opacity-75")}><Checkbox checked={mechanics.includes(mechanic)} disabled={format !== "custom"} onCheckedChange={(checked) => setMechanics((current) => checked ? [...new Set([...current, mechanic])] : current.filter((entry) => entry !== mechanic))} />{MECHANIC_LABELS[mechanic]}</label>)}<div className="ml-auto flex items-center gap-2"><span className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[9px]", dex ? "bg-emerald-300/7 text-emerald-300" : "bg-white/4 text-slate-500")}>{dex ? <Database className="size-3" /> : <Loader2 className="size-3 animate-spin" />}{dex ? `Showdown · ${dex.metadata.captured}` : "Cargando Pokédex"}</span><Button type="button" variant="ghost" size="sm" onClick={refreshDatabases} disabled={refreshingDex} className="h-7 gap-1.5 rounded-full border border-white/8 bg-white/3 px-2.5 text-[9px] text-slate-400 hover:bg-cyan-300/8 hover:text-cyan-100">{refreshingDex ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}Actualizar bases</Button></div></div>
        {message ? <p className="mt-3 rounded-xl border border-emerald-300/15 bg-emerald-300/5 px-3 py-2 text-xs text-emerald-200">{message}</p> : null}{error ? <p role="alert" className="mt-3 rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}{dexError ? <p role="alert" className="mt-3 flex items-center gap-2 rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200"><AlertTriangle className="size-4" />{dexError}</p> : null}
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[270px_minmax(0,1fr)]">
        <BuilderCoverage pokemon={pokemon} teraEnabled={mechanics.includes("tera")} />
        <div className="min-w-0 space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 2xl:grid-cols-6">{pokemon.map((set, index) => <SlotCard key={set.id} pokemon={set} selected={index === selectedSlot} onClick={() => setSelectedSlot(index)} onClear={() => clearSlot(index)} />)}</div>
          <div className="overflow-hidden rounded-[26px] border border-white/8 bg-[#0b1220]/92 shadow-[0_28px_90px_rgba(0,0,0,0.30)]">
            <div data-team-calculator className="min-h-[44rem]">
              {dex ? (
                <Suspense fallback={<div role="status" className="flex h-full items-center justify-center gap-2 p-6 text-sm text-cyan-200"><Loader2 className="size-4 animate-spin" />Cargando calculadora…</div>}>
                  <DamageCalculatorView
                    key={calculatorSessionKey}
                    source={selected}
                    format={format}
                    dex={dex}
                    mechanics={mechanics}
                    session={calculatorSessions[calculatorSessionKey]}
                    onSessionChange={(nextSession) => {
                      setCalculatorSessions((current) => ({ ...current, [calculatorSessionKey]: nextSession }));
                      setPokemon((current) => current.map((set, index) => index === selectedSlot ? {
                        ...nextSession.left.set,
                        id: set.id,
                        slot: set.slot,
                      } : set));
                      setMessage("");
                    }}
                  />
                </Suspense>
              ) : (
                <div role="status" className="flex h-full items-center justify-center gap-2 p-6 text-sm text-cyan-200"><Loader2 className="size-4 animate-spin" />Cargando calculadora…</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}