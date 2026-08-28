"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Clipboard, Database, Download, Eraser, FolderOpen, Loader2, Minus, Plus, RefreshCw, Save, Shield, Sparkles, Upload, WandSparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { getSpriteUrl, toId } from "@/lib/pokemon-data";
import { getLegalAbilities, getLegalItems, getLegalMoves, getSpecies, getSpeciesOptions, hydrateSetFromSnapshot, isItemLegal, isMoveLegal, isSpeciesAvailable, loadShowdownSnapshot, moveFromSnapshot, type BaseStats, type ShowdownSnapshot } from "@/lib/showdown-data";
import { analyzeTypes } from "@/lib/team-stats";
import { BATTLE_FORMATS, EV_STATS, MECHANIC_LABELS, NATURES, calculateStat, cloneForBuilder, emptyPokemon, formatVersion, getNatureEffect, getStatRules, isCompleteTeam, normalizeTeraType, parseEvs, serializeEvs, serializeShowdownPaste } from "@/lib/team-builder";
import { POKEMON_TYPES, type BattleMechanic, type PokemonSet, type TeamGroup, type TeamVersion } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TypeBadge } from "./type-badge";

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

function SlotCard({ pokemon, selected, onClick }: { pokemon: PokemonSet; selected: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={cn("group min-h-36 rounded-2xl border p-3 text-left transition", selected ? "border-cyan-300/55 bg-cyan-300/8 shadow-[0_0_24px_rgba(103,232,249,0.10)]" : "border-white/8 bg-[#0c1424] hover:border-white/16")}>
      <div className="flex min-h-14 items-start justify-end">{pokemon.species ? <Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={64} height={64} unoptimized className="size-14 object-contain" /> : <Plus className="mt-3 size-7 text-slate-700" />}</div>
      <p className={pokemon.species ? "mt-1 truncate text-xs font-black text-white" : "mt-3 text-xs font-bold text-slate-600"}>{pokemon.species || "Elegir Pokémon"}</p>
      <div className="mt-1 flex gap-1">{pokemon.types.map((type) => <TypeBadge key={type} type={type} className="px-1.5 text-[7px]">{type.slice(0, 3)}</TypeBadge>)}</div>
      <p className="mt-2 truncate text-[9px] text-amber-200/70">{pokemon.item || "Sin objeto"}</p>
    </button>
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

function StatEditor({
  pokemon,
  format,
  baseStats,
  onChange,
}: {
  pokemon: PokemonSet;
  format: string;
  baseStats: BaseStats | null;
  onChange: (next: PokemonSet) => void;
}) {
  const values = parseEvs(pokemon.evs);
  const rules = getStatRules(format);
  const total = Object.values(values).reduce((sum, value) => sum + value, 0);

  function setAllocation(stat: (typeof EV_STATS)[number], requested: number) {
    const otherTotal = total - values[stat];
    const rounded = Math.round(requested / rules.step) * rules.step;
    const nextValue = Math.max(0, Math.min(rules.perStatMax, rules.totalMax - otherTotal, rounded));
    onChange({ ...pokemon, evs: serializeEvs({ ...values, [stat]: nextValue }) });
  }

  if (!baseStats) {
    return (
      <div className="rounded-2xl border border-dashed border-white/10 bg-black/15 p-5 text-center">
        <Database className="mx-auto size-5 text-slate-600" />
        <p className="mt-2 text-xs font-bold text-slate-400">Elige un Pokémon para ver sus stats base.</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/8 bg-black/20 p-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-300">{rules.label}</p>
          <p className="mt-1 text-[9px] text-slate-600">Nivel {pokemon.level} · IVs perfectos</p>
        </div>
        <Badge variant="outline" className={total <= rules.totalMax ? "border-emerald-300/20 text-emerald-300" : "border-rose-300/25 text-rose-300"}>{total}/{rules.totalMax}</Badge>
      </div>
      <div className="mt-4 grid grid-cols-[28px_34px_minmax(70px,1fr)_50px_40px] items-center gap-x-2 text-[8px] font-black uppercase tracking-[0.08em] text-slate-600 sm:grid-cols-[30px_38px_minmax(120px,1fr)_50px_42px]">
        <span />
        <span className="text-center">Base</span>
        <span className="text-center">{rules.shortLabel}</span>
        <span />
        <span className="text-right">Total</span>
      </div>
      <div className="mt-1 space-y-1.5">
        {EV_STATS.map((stat) => {
          const nature = getNatureEffect(pokemon.nature, stat);
          const finalStat = calculateStat(baseStats, stat, values[stat], pokemon.level, pokemon.nature, format);
          return (
            <div key={stat} className="grid grid-cols-[28px_34px_minmax(70px,1fr)_50px_40px] items-center gap-x-2 rounded-lg px-1 py-1.5 hover:bg-white/[0.025] sm:grid-cols-[30px_38px_minmax(120px,1fr)_50px_42px]">
              <span className={cn("text-[10px] font-black", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-400" : "text-slate-500")}>{stat}</span>
              <span className="text-center text-[10px] font-bold text-slate-500">{baseStats[{ HP: "hp", Atk: "atk", Def: "def", SpA: "spa", SpD: "spd", Spe: "spe" }[stat] as keyof BaseStats]}</span>
              <div className="flex min-w-0 items-center gap-1.5">
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] <= 0} onClick={() => setAllocation(stat, values[stat] - rules.step)} aria-label={`Restar ${rules.shortLabel} de ${stat}`}><Minus className="size-3" /></Button>
                <Slider value={[values[stat]]} min={0} max={rules.perStatMax} step={rules.step} onValueChange={(next) => setAllocation(stat, next[0] ?? 0)} aria-label={`${rules.label} de ${stat}`} className={cn("min-w-0", nature === "plus" && "[&_[data-slot=slider-range]]:bg-rose-400 [&_[data-slot=slider-thumb]]:border-rose-400", nature === "minus" && "[&_[data-slot=slider-range]]:bg-cyan-400 [&_[data-slot=slider-thumb]]:border-cyan-400")} />
                <Button type="button" variant="ghost" size="icon" className="size-6 shrink-0 rounded-md bg-white/5 text-slate-400 hover:bg-cyan-300/10 hover:text-cyan-200 disabled:opacity-30" disabled={values[stat] >= rules.perStatMax || total >= rules.totalMax} onClick={() => setAllocation(stat, values[stat] + rules.step)} aria-label={`Sumar ${rules.shortLabel} a ${stat}`}><Plus className="size-3" /></Button>
              </div>
              <Input aria-label={`${rules.shortLabel} de ${stat}`} type="number" min={0} max={rules.perStatMax} step={rules.step} value={values[stat]} onChange={(event) => setAllocation(stat, Number(event.target.value) || 0)} className="h-7 border-white/8 bg-black/25 px-1 text-center text-[10px]" />
              <strong className={cn("text-right text-xs", nature === "plus" ? "text-rose-400" : nature === "minus" ? "text-cyan-300" : "text-slate-200")}>{finalStat}</strong>
            </div>
          );
        })}
      </div>
      <p className="mt-3 rounded-lg border border-white/6 bg-white/[0.025] px-2.5 py-2 font-mono text-[9px] text-slate-500">{pokemon.evs || `Sin ${rules.label.toLowerCase()} asignados`}</p>
    </div>
  );
}

export function TeamBuilder({ groups, initialVersion, onTeamCreated, onVersionCreated }: BuilderProps) {
  const [teamName, setTeamName] = useState(initialVersion?.demo ? `${initialVersion.name} Copy` : initialVersion?.name ?? "");
  const [sourceTeamId, setSourceTeamId] = useState(initialVersion?.demo ? "" : initialVersion?.teamId ?? "");
  const [format, setFormat] = useState(initialVersion?.format ?? "champions");
  const [mechanics, setMechanics] = useState<BattleMechanic[]>(initialVersion?.mechanics ?? ["mega"]);
  const [pokemon, setPokemon] = useState<PokemonSet[]>(initialVersion ? cloneForBuilder(initialVersion.pokemon) : Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1)));
  const [selectedSlot, setSelectedSlot] = useState(0);
  const [dex, setDex] = useState<ShowdownSnapshot | null>(null);
  const [dexError, setDexError] = useState("");
  const [refreshingDex, setRefreshingDex] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const selected = pokemon[selectedSlot];
  const storedVersions = groups.filter((team) => !team.versions[0]?.demo).flatMap((team) => team.versions);
  const paste = useMemo(() => serializeShowdownPaste(pokemon, mechanics), [pokemon, mechanics]);
  const complete = isCompleteTeam(pokemon);
  const speciesOptions = useMemo(() => getSpeciesOptions(dex, format), [dex, format]);
  const selectedSpecies = getSpecies(dex, selected.species);
  const legalMoves = useMemo(() => getLegalMoves(dex, selected.species, format), [dex, selected.species, format]);
  const legalItems = useMemo(() => getLegalItems(dex, format), [dex, format]);
  const legalAbilities = getLegalAbilities(dex, selected.species);

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

  function updateSelected(next: PokemonSet) { setPokemon((current) => current.map((set, index) => index === selectedSlot ? next : set)); setMessage(""); }

  function loadVersion(versionId: string) {
    const version = storedVersions.find((entry) => entry.id === versionId);
    if (!version) return;
    const nextPokemon = cloneForBuilder(version.pokemon);
    setTeamName(version.name); setSourceTeamId(version.teamId); setFormat(version.format ?? "champions"); setMechanics(version.mechanics ?? mechanicsForFormat(version.format ?? "champions")); setPokemon(dex ? nextPokemon.map((set) => hydrateSetFromSnapshot(dex, set)) : nextPokemon); setSelectedSlot(0); setError(""); setMessage(`Cargado ${version.name} v${formatVersion(version)}. Los cambios crearán una versión nueva.`);
  }

  function resetBuilder() {
    setTeamName(""); setSourceTeamId(""); setFormat("champions"); setMechanics(["mega"]); setPokemon(Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1))); setSelectedSlot(0); setMessage(""); setError("");
  }

  async function importPaste(value: string) {
    setError("");
    try {
      const { parseShowdownPaste } = await import("@/lib/paste");
      const imported = cloneForBuilder(parseShowdownPaste(value));
      setPokemon(dex ? imported.map((set) => hydrateSetFromSnapshot(dex, set)) : imported);
      setSelectedSlot(0);
      setMessage("Paste importado. Revisa el formato y guarda cuando esté listo.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "No pudimos importar el paste."); }
  }

  function changeFormat(nextFormat: string) {
    const previousRules = getStatRules(format);
    const nextRules = getStatRules(nextFormat);
    setFormat(nextFormat);
    if (nextFormat !== "custom") setMechanics(mechanicsForFormat(nextFormat));
    if (previousRules.totalMax !== nextRules.totalMax) {
      setPokemon((current) => current.map((set) => ({ ...set, evs: "" })));
      setMessage(`La escala cambió a ${nextRules.label}; reiniciamos la distribución para evitar valores incompatibles.`);
    }
  }

  function chooseSpecies(value: string | null) {
    if (!value || !dex) {
      updateSelected({ ...selected, species: "", nickname: "", ability: "", types: [], moves: emptyPokemon(selected.slot).moves });
      return;
    }
    const species = getSpecies(dex, value);
    if (!species) return;
    updateSelected({
      ...selected,
      species: species.name,
      nickname: !selected.nickname || selected.nickname === selected.species ? species.name : selected.nickname,
      ability: species.abilities[0] ?? "",
      types: species.types,
      moves: emptyPokemon(selected.slot).moves,
    });
  }

  function chooseMove(index: number, value: string | null) {
    const nextMove = value ? moveFromSnapshot(dex, value) : emptyPokemon(selected.slot).moves[index];
    updateSelected({ ...selected, moves: selected.moves.map((move, moveIndex) => moveIndex === index ? nextMove : move) });
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

      <div className="grid items-start gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
        <BuilderCoverage pokemon={pokemon} teraEnabled={mechanics.includes("tera")} />
        <div className="min-w-0 space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 2xl:grid-cols-6">{pokemon.map((set, index) => <SlotCard key={set.id} pokemon={set} selected={index === selectedSlot} onClick={() => setSelectedSlot(index)} />)}</div>
          <div className="overflow-hidden rounded-[26px] border border-white/8 bg-[#0b1220]/92 shadow-[0_28px_90px_rgba(0,0,0,0.30)]">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/7 px-4 py-3 sm:px-5"><div className="flex items-center gap-3">{selected.species ? <Image src={getSpriteUrl(selected.species)} alt={selected.species} width={60} height={60} unoptimized className="size-12 object-contain" /> : <div className="flex size-12 items-center justify-center rounded-xl bg-white/4"><WandSparkles className="size-5 text-slate-600" /></div>}<div><h2 className="text-lg font-black text-white">{selected.species || "Nuevo Pokémon"}</h2><div className="mt-1.5 flex flex-wrap items-center gap-1">{selected.types.map((type) => <TypeBadge key={type} type={type} className="text-[8px]">{type}</TypeBadge>)}{selected.species ? <span className="ml-1 text-[9px] text-slate-600">Tipos oficiales · solo lectura</span> : <span className="text-[10px] text-slate-600">Elige una especie para empezar.</span>}</div></div></div></div>
            <div className="grid gap-5 p-4 sm:p-5 lg:grid-cols-2 2xl:grid-cols-[1.05fr_1fr_1fr]">
              <div className="space-y-4">
                <div className="grid gap-2"><Label>Pokémon</Label><Combobox items={speciesOptions} value={selected.species || null} onValueChange={chooseSpecies}><ComboboxInput placeholder={dex ? "Buscar especie..." : "Cargando Pokédex..."} disabled={!dex} className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible en este formato.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox></div>
                <div className="grid gap-3 sm:grid-cols-2"><div className="grid gap-2"><Label htmlFor="builder-nickname">Apodo</Label><Input id="builder-nickname" value={selected.nickname} onChange={(event) => updateSelected({ ...selected, nickname: event.target.value })} className="border-white/10 bg-white/4" /></div><div className="grid gap-2"><Label htmlFor="builder-level">Nivel</Label><Input id="builder-level" type="number" min={1} max={100} value={selected.level} onChange={(event) => updateSelected({ ...selected, level: Math.min(100, Math.max(1, Number(event.target.value) || 50)) })} className="border-white/10 bg-white/4" /></div></div>
                <div className="grid gap-2"><div className="flex items-center justify-between gap-3"><Label>Objeto</Label><span className="text-[9px] text-slate-600">{dex ? `${legalItems.length} disponibles` : "Cargando catálogo"}</span></div><Combobox items={legalItems} value={selected.item || null} onValueChange={(value) => updateSelected({ ...selected, item: value ?? "" })}><ComboboxInput placeholder={dex ? "Buscar objeto..." : "Cargando objetos..."} disabled={!dex} className={cn("w-full border-white/10 bg-white/4", selected.item && dex && !isItemLegal(dex, selected.item, format) && "border-rose-300/35 text-rose-200")} showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No disponible en este formato.</ComboboxEmpty><ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><p className="text-[9px] text-slate-600">Catálogo oficial del formato; puede dejarse sin objeto.</p></div>
                <div className="grid gap-2"><Label>Habilidad</Label><Select value={selected.ability || "none"} disabled={!selectedSpecies} onValueChange={(value) => updateSelected({ ...selected, ability: value === "none" ? "" : value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue placeholder="Elige una habilidad" /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{selected.ability && !legalAbilities.includes(selected.ability) ? <SelectItem value={selected.ability} disabled>{selected.ability} · no disponible</SelectItem> : null}{legalAbilities.map((ability) => <SelectItem key={ability} value={ability}>{ability}</SelectItem>)}</SelectContent></Select><p className="text-[9px] text-slate-600">Solo habilidades ligadas a esta especie o forma.</p></div>
                <div className="grid gap-2"><Label>Naturaleza</Label><Select value={selected.nature || "none"} onValueChange={(value) => updateSelected({ ...selected, nature: value === "none" ? "" : value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{NATURES.map((nature) => <SelectItem key={nature} value={nature}>{nature}</SelectItem>)}</SelectContent></Select></div>
              </div>

              <div className="space-y-4"><div><div className="flex items-center justify-between"><Label>Movimientos</Label><span className="text-[9px] text-slate-600">{selected.species ? `${legalMoves.length} legales · 4 requeridos` : "Elige un Pokémon"}</span></div><div className="mt-2 space-y-2">{selected.moves.map((move, index) => { const legal = !move.name || legalMoves.some((name) => toId(name) === toId(move.name)); const selectedElsewhere = new Set(selected.moves.filter((_, moveIndex) => moveIndex !== index).map((entry) => toId(entry.name)).filter(Boolean)); const moveOptions = legalMoves.filter((name) => !selectedElsewhere.has(toId(name))); return <div key={index} className="grid grid-cols-[minmax(0,1fr)_78px] gap-2"><Combobox items={moveOptions} value={move.name || null} onValueChange={(value) => chooseMove(index, value)}><ComboboxInput placeholder={`Movimiento ${index + 1}`} disabled={!selectedSpecies} className={cn("w-full border-white/10 bg-white/4", !legal && "border-rose-300/35 text-rose-200")} showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>No forma parte de su learnset o ya está elegido.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><div className="flex items-center justify-center rounded-lg border border-white/7 bg-white/[0.025]">{move.type ? <TypeBadge type={move.type} className="text-[7px]">{move.type}</TypeBadge> : <span className="text-[9px] font-bold text-slate-600">Status</span>}</div></div>; })}</div><p className="mt-2 text-[9px] text-slate-600">Tipo y categoría vienen de Showdown y no se pueden editar.</p></div>
                <div className="rounded-2xl border border-white/7 bg-black/20 p-3"><p className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.13em] text-slate-400"><Sparkles className="size-3 text-amber-300" />Mecánica especial</p>
                  {mechanics.includes("tera") ? <div className="mt-3 grid gap-2"><Label>Tipo Tera</Label><Select value={selected.teraType ?? "none"} onValueChange={(value) => updateSelected({ ...selected, teraType: normalizeTeraType(value === "none" ? "" : value) })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></div> : null}
                  {mechanics.includes("dynamax") ? <div className="mt-3 grid gap-3"><div className="grid gap-2"><Label>Dynamax Level</Label><Input type="number" min={0} max={10} value={selected.mechanics?.dynamaxLevel ?? 10} onChange={(event) => updateSelected({ ...selected, mechanics: { ...selected.mechanics, dynamaxLevel: Math.min(10, Math.max(0, Number(event.target.value) || 0)) } })} className="border-white/10 bg-white/4" /></div><label className="flex items-center justify-between rounded-xl border border-white/8 bg-white/3 px-3 py-2 text-xs"><span>Forma Gigantamax</span><Switch checked={selected.mechanics?.gigantamax ?? false} onCheckedChange={(checked) => updateSelected({ ...selected, mechanics: { ...selected.mechanics, gigantamax: checked } })} /></label></div> : null}
                  {mechanics.includes("mega") ? <div className="mt-3 rounded-xl border border-amber-300/12 bg-amber-300/5 px-3 py-2 text-[10px] leading-4 text-amber-100/70"><strong className="text-amber-200">Mega Evolution:</strong> se determina por la especie/forma y la megapiedra guardada como objeto.</div> : null}
                  {mechanics.includes("zmove") ? <div className="mt-3 rounded-xl border border-violet-300/12 bg-violet-300/5 px-3 py-2 text-[10px] leading-4 text-violet-100/70"><strong className="text-violet-200">Z-Move:</strong> se determina a partir del cristal Z y el movimiento compatible.</div> : null}
                  {!mechanics.length ? <p className="mt-3 text-[10px] text-slate-600">Este formato no usa mecánicas especiales.</p> : null}
                </div>
              </div>

              <div className="space-y-3 lg:col-span-2 2xl:col-span-1"><StatEditor pokemon={selected} format={format} baseStats={selectedSpecies?.baseStats ?? null} onChange={updateSelected} /></div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
