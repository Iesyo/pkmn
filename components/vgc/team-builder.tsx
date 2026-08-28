"use client";

import Image from "next/image";
import { useMemo, useState } from "react";
import { Check, Clipboard, Download, Eraser, Loader2, Plus, Save, Shield, Sparkles, Upload, WandSparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "@/components/ui/combobox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { MOVE_CATALOG, POKEMON_CATALOG, getSpriteUrl } from "@/lib/pokemon-data";
import { analyzeTypes } from "@/lib/team-stats";
import { BATTLE_FORMATS, EV_STATS, MECHANIC_LABELS, NATURES, cloneForBuilder, emptyPokemon, formatVersion, isCompleteTeam, normalizeTeraType, parseEvs, serializeEvs, serializeShowdownPaste, updateMove, updateSpecies } from "@/lib/team-builder";
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
      <div className="flex items-start justify-between"><span className="flex size-5 items-center justify-center rounded-full bg-white/5 text-[9px] font-black text-slate-500">{pokemon.slot}</span>{pokemon.species ? <Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={64} height={64} unoptimized className="size-14 object-contain" /> : <Plus className="mt-3 size-7 text-slate-700" />}</div>
      <p className={pokemon.species ? "mt-1 truncate text-xs font-black text-white" : "mt-3 text-xs font-bold text-slate-600"}>{pokemon.species || "Elegir Pokémon"}</p>
      <div className="mt-1 flex gap-1">{pokemon.types.map((type) => <TypeBadge key={type} type={type} className="px-1.5 text-[7px]">{type.slice(0, 3)}</TypeBadge>)}</div>
      <p className="mt-2 truncate text-[9px] text-amber-200/70">{pokemon.item || "Sin objeto"}</p>
    </button>
  );
}

function PasteDialog({ mode, paste, onImport }: { mode: "import" | "export"; paste: string; onImport?: (paste: string) => void }) {
  const [value, setValue] = useState(paste);
  const [copied, setCopied] = useState(false);
  return (
    <Dialog onOpenChange={(open) => { if (open) { setValue(paste); setCopied(false); } }}>
      <DialogTrigger asChild><Button variant="outline" className="gap-2 rounded-full border-white/10 bg-white/4">{mode === "import" ? <Upload className="size-4" /> : <Download className="size-4" />}{mode === "import" ? "Importar" : "Exportar"}</Button></DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <DialogHeader><DialogTitle>{mode === "import" ? "Importar Showdown paste" : "Exportar a Showdown"}</DialogTitle><DialogDescription className="text-slate-500">{mode === "import" ? "Pega seis sets completos para cargarlos en el Builder." : "Copia el equipo con el formato estándar de Pokémon Showdown."}</DialogDescription></DialogHeader>
        <Textarea value={value} onChange={(event) => setValue(event.target.value)} readOnly={mode === "export"} className="my-4 min-h-96 border-white/10 bg-black/35 font-mono text-[11px] leading-5" />
        <DialogFooter>{mode === "import" ? <Button onClick={() => onImport?.(value)} className="bg-cyan-300 text-slate-950 hover:bg-cyan-200">Cargar equipo</Button> : <Button onClick={async () => { await navigator.clipboard.writeText(value); setCopied(true); }} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{copied ? <Check className="size-4" /> : <Clipboard className="size-4" />}{copied ? "Copiado" : "Copiar paste"}</Button>}</DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function TeamBuilder({ groups, initialVersion, onTeamCreated, onVersionCreated }: BuilderProps) {
  const [teamName, setTeamName] = useState(initialVersion?.demo ? `${initialVersion.name} Copy` : initialVersion?.name ?? "");
  const [sourceTeamId, setSourceTeamId] = useState(initialVersion?.demo ? "" : initialVersion?.teamId ?? "");
  const [format, setFormat] = useState(initialVersion?.format ?? "gen9");
  const [mechanics, setMechanics] = useState<BattleMechanic[]>(initialVersion?.mechanics ?? ["tera"]);
  const [pokemon, setPokemon] = useState<PokemonSet[]>(initialVersion ? cloneForBuilder(initialVersion.pokemon) : Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1)));
  const [selectedSlot, setSelectedSlot] = useState(0);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const selected = pokemon[selectedSlot];
  const storedVersions = groups.filter((team) => !team.versions[0]?.demo).flatMap((team) => team.versions);
  const paste = useMemo(() => serializeShowdownPaste(pokemon, mechanics), [pokemon, mechanics]);
  const complete = isCompleteTeam(pokemon);

  function updateSelected(next: PokemonSet) { setPokemon((current) => current.map((set, index) => index === selectedSlot ? next : set)); setMessage(""); }

  function loadVersion(versionId: string) {
    const version = storedVersions.find((entry) => entry.id === versionId);
    if (!version) return;
    setTeamName(version.name); setSourceTeamId(version.teamId); setFormat(version.format ?? "gen9"); setMechanics(version.mechanics ?? ["tera"]); setPokemon(cloneForBuilder(version.pokemon)); setSelectedSlot(0); setError(""); setMessage(`Cargado ${version.name} v${formatVersion(version)}. Los cambios crearán una versión nueva.`);
  }

  function resetBuilder() {
    setTeamName(""); setSourceTeamId(""); setFormat("gen9"); setMechanics(["tera"]); setPokemon(Array.from({ length: 6 }, (_, index) => emptyPokemon(index + 1))); setSelectedSlot(0); setMessage(""); setError("");
  }

  async function importPaste(value: string) {
    setError("");
    try {
      const { parseShowdownPaste } = await import("@/lib/paste");
      setPokemon(cloneForBuilder(parseShowdownPaste(value)));
      setSelectedSlot(0);
      setMessage("Paste importado. Revisa el formato y guarda cuando esté listo.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "No pudimos importar el paste."); }
  }

  function changeFormat(nextFormat: string) {
    setFormat(nextFormat);
    if (nextFormat !== "custom") setMechanics(mechanicsForFormat(nextFormat));
  }

  async function saveTeam() {
    setError(""); setMessage("");
    if (teamName.trim().length < 2) { setError("Ponle un nombre al Team."); return; }
    if (!complete) { setError("Completa los seis Pokémon y sus cuatro movimientos."); return; }
    if (pokemon.some((set) => Object.values(parseEvs(set.evs)).reduce((sum, value) => sum + value, 0) > 510)) { setError("Revisa los EVs: algún Pokémon supera el máximo de 510."); return; }
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

  const evValues = parseEvs(selected.evs);

  return (
    <section className="space-y-4">
      <div className="rounded-[24px] border border-white/8 bg-slate-900/45 p-4 shadow-[0_24px_80px_rgba(0,0,0,0.24)]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="grid flex-1 gap-3 sm:grid-cols-[minmax(220px,1fr)_260px]">
            <div className="grid gap-1.5"><Label htmlFor="builder-name" className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Nombre del Team</Label><Input id="builder-name" value={teamName} onChange={(event) => setTeamName(event.target.value)} disabled={Boolean(sourceTeamId)} placeholder="Ej. Aurora Protocol" className="border-white/10 bg-black/20" /></div>
            <div className="grid gap-1.5"><Label className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Formato</Label><Select value={format} onValueChange={changeFormat}><SelectTrigger className="w-full border-white/10 bg-black/20"><SelectValue /></SelectTrigger><SelectContent>{BATTLE_FORMATS.map((entry) => <SelectItem key={entry.id} value={entry.id}>{entry.label}</SelectItem>)}</SelectContent></Select></div>
          </div>
          <div className="flex flex-wrap gap-2"><PasteDialog mode="import" paste="" onImport={importPaste} /><PasteDialog mode="export" paste={paste} />{storedVersions.length ? <Select onValueChange={loadVersion}><SelectTrigger className="w-48 rounded-full border-white/10 bg-white/4"><SelectValue placeholder="Cargar desde Teams" /></SelectTrigger><SelectContent>{storedVersions.map((version) => <SelectItem key={version.id} value={version.id}>{version.name} · v{formatVersion(version)}</SelectItem>)}</SelectContent></Select> : null}<Button variant="outline" onClick={resetBuilder} className="gap-2 rounded-full border-rose-300/15 bg-rose-300/5 text-rose-200"><Eraser className="size-4" />Nuevo</Button><Button onClick={saveTeam} disabled={saving} className="gap-2 rounded-full bg-cyan-300 px-5 font-black text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}{sourceTeamId ? "Guardar versión" : "Guardar en Teams"}</Button></div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><span className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Mecánicas</span>{(["tera", "dynamax", "mega", "zmove"] as BattleMechanic[]).map((mechanic) => <label key={mechanic} className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px]", mechanics.includes(mechanic) ? "border-cyan-300/20 bg-cyan-300/8 text-cyan-100" : "border-white/7 bg-white/3 text-slate-600", format !== "custom" && "pointer-events-none opacity-75")}><Checkbox checked={mechanics.includes(mechanic)} disabled={format !== "custom"} onCheckedChange={(checked) => setMechanics((current) => checked ? [...new Set([...current, mechanic])] : current.filter((entry) => entry !== mechanic))} />{MECHANIC_LABELS[mechanic]}</label>)}</div>
        {message ? <p className="mt-3 rounded-xl border border-emerald-300/15 bg-emerald-300/5 px-3 py-2 text-xs text-emerald-200">{message}</p> : null}{error ? <p role="alert" className="mt-3 rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
        <BuilderCoverage pokemon={pokemon} teraEnabled={mechanics.includes("tera")} />
        <div className="min-w-0 space-y-4">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-3 2xl:grid-cols-6">{pokemon.map((set, index) => <SlotCard key={set.id} pokemon={set} selected={index === selectedSlot} onClick={() => setSelectedSlot(index)} />)}</div>
          <div className="overflow-hidden rounded-[26px] border border-white/8 bg-[#0b1220]/92 shadow-[0_28px_90px_rgba(0,0,0,0.30)]">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/7 px-4 py-3 sm:px-5"><div className="flex items-center gap-3">{selected.species ? <Image src={getSpriteUrl(selected.species)} alt={selected.species} width={60} height={60} unoptimized className="size-12 object-contain" /> : <div className="flex size-12 items-center justify-center rounded-xl bg-white/4"><WandSparkles className="size-5 text-slate-600" /></div>}<div><div className="flex items-center gap-2"><Badge className="bg-cyan-300 text-slate-950">Slot {selected.slot}</Badge><h2 className="text-lg font-black text-white">{selected.species || "Nuevo Pokémon"}</h2></div><p className="mt-1 text-[10px] text-slate-600">Configura el set competitivo; la cobertura responde al instante.</p></div></div><div className="flex gap-1">{selected.types.map((type) => <TypeBadge key={type} type={type}>{type}</TypeBadge>)}</div></div>
            <div className="grid gap-5 p-4 sm:p-5 lg:grid-cols-2 2xl:grid-cols-[1.05fr_1fr_1fr]">
              <div className="space-y-4">
                <div className="grid gap-2"><Label>Pokémon</Label><Combobox items={POKEMON_CATALOG.map((entry) => entry.name)} value={selected.species || null} onValueChange={(value) => value && updateSelected(updateSpecies(selected, value))} onInputValueChange={(value) => { if (value && value !== selected.species) updateSelected(updateSpecies(selected, value)); }}><ComboboxInput placeholder="Buscar especie..." className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>Sin coincidencias; puedes escribir el nombre.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><div className="grid grid-cols-2 gap-2"><Select value={selected.types[0] ?? "none"} onValueChange={(value) => updateSelected({ ...selected, types: value === "none" ? selected.types.slice(1) : [value as (typeof POKEMON_TYPES)[number], ...(selected.types[1] ? [selected.types[1]] : [])] })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue placeholder="Tipo 1" /></SelectTrigger><SelectContent><SelectItem value="none">Tipo 1</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select><Select value={selected.types[1] ?? "none"} onValueChange={(value) => updateSelected({ ...selected, types: value === "none" ? selected.types.slice(0, 1) : [selected.types[0] ?? value as (typeof POKEMON_TYPES)[number], value as (typeof POKEMON_TYPES)[number]] })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue placeholder="Tipo 2" /></SelectTrigger><SelectContent><SelectItem value="none">Sin segundo tipo</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></div></div>
                <div className="grid gap-3 sm:grid-cols-2"><div className="grid gap-2"><Label htmlFor="builder-nickname">Apodo</Label><Input id="builder-nickname" value={selected.nickname} onChange={(event) => updateSelected({ ...selected, nickname: event.target.value })} className="border-white/10 bg-white/4" /></div><div className="grid gap-2"><Label htmlFor="builder-level">Nivel</Label><Input id="builder-level" type="number" min={1} max={100} value={selected.level} onChange={(event) => updateSelected({ ...selected, level: Math.min(100, Math.max(1, Number(event.target.value) || 50)) })} className="border-white/10 bg-white/4" /></div></div>
                <div className="grid gap-2"><Label htmlFor="builder-item">Objeto</Label><Input id="builder-item" value={selected.item} onChange={(event) => updateSelected({ ...selected, item: event.target.value })} placeholder="Ej. Choice Scarf" className="border-white/10 bg-white/4" /></div>
                <div className="grid gap-2"><Label htmlFor="builder-ability">Habilidad</Label><Input id="builder-ability" value={selected.ability} onChange={(event) => updateSelected({ ...selected, ability: event.target.value })} placeholder="Ej. Intimidate" className="border-white/10 bg-white/4" /></div>
                <div className="grid gap-2"><Label>Naturaleza</Label><Select value={selected.nature || "none"} onValueChange={(value) => updateSelected({ ...selected, nature: value === "none" ? "" : value })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{NATURES.map((nature) => <SelectItem key={nature} value={nature}>{nature}</SelectItem>)}</SelectContent></Select></div>
              </div>

              <div className="space-y-4"><div><div className="flex items-center justify-between"><Label>Movimientos</Label><span className="text-[9px] text-slate-600">4 requeridos</span></div><div className="mt-2 space-y-2">{selected.moves.map((move, index) => <div key={index} className="grid grid-cols-[minmax(0,1fr)_92px] gap-2"><Combobox items={MOVE_CATALOG} value={move.name || null} onValueChange={(value) => value && updateSelected(updateMove(selected, index, value))} onInputValueChange={(value) => { if (value !== move.name) updateSelected(updateMove(selected, index, value)); }}><ComboboxInput placeholder={`Movimiento ${index + 1}`} className="w-full border-white/10 bg-white/4" showClear /><ComboboxContent className="border-white/10 bg-slate-950"><ComboboxEmpty>Escribe el movimiento manualmente.</ComboboxEmpty><ComboboxList>{(name: string) => <ComboboxItem key={name} value={name}>{name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><Select value={move.type ?? "none"} onValueChange={(value) => updateSelected({ ...selected, moves: selected.moves.map((entry, moveIndex) => moveIndex === index ? { ...entry, type: value === "none" ? null : value as (typeof POKEMON_TYPES)[number], damaging: value !== "none" } : entry) })}><SelectTrigger className="w-full border-white/10 bg-white/4 px-2 text-[10px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Status</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></div>)}</div></div>
                <div className="rounded-2xl border border-white/7 bg-black/20 p-3"><p className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.13em] text-slate-400"><Sparkles className="size-3 text-amber-300" />Mecánica especial</p>
                  {mechanics.includes("tera") ? <div className="mt-3 grid gap-2"><Label>Tipo Tera</Label><Select value={selected.teraType ?? "none"} onValueChange={(value) => updateSelected({ ...selected, teraType: normalizeTeraType(value === "none" ? "" : value) })}><SelectTrigger className="w-full border-white/10 bg-white/4"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">Sin definir</SelectItem>{POKEMON_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></div> : null}
                  {mechanics.includes("dynamax") ? <div className="mt-3 grid gap-3"><div className="grid gap-2"><Label>Dynamax Level</Label><Input type="number" min={0} max={10} value={selected.mechanics?.dynamaxLevel ?? 10} onChange={(event) => updateSelected({ ...selected, mechanics: { ...selected.mechanics, dynamaxLevel: Math.min(10, Math.max(0, Number(event.target.value) || 0)) } })} className="border-white/10 bg-white/4" /></div><label className="flex items-center justify-between rounded-xl border border-white/8 bg-white/3 px-3 py-2 text-xs"><span>Forma Gigantamax</span><Switch checked={selected.mechanics?.gigantamax ?? false} onCheckedChange={(checked) => updateSelected({ ...selected, mechanics: { ...selected.mechanics, gigantamax: checked } })} /></label></div> : null}
                  {mechanics.includes("mega") ? <div className="mt-3 rounded-xl border border-amber-300/12 bg-amber-300/5 px-3 py-2 text-[10px] leading-4 text-amber-100/70"><strong className="text-amber-200">Mega Evolution:</strong> se determina por la especie/forma y la megapiedra guardada como objeto.</div> : null}
                  {mechanics.includes("zmove") ? <div className="mt-3 rounded-xl border border-violet-300/12 bg-violet-300/5 px-3 py-2 text-[10px] leading-4 text-violet-100/70"><strong className="text-violet-200">Z-Move:</strong> se determina a partir del cristal Z y el movimiento compatible.</div> : null}
                  {!mechanics.length ? <p className="mt-3 text-[10px] text-slate-600">Este formato no usa mecánicas especiales.</p> : null}
                </div>
              </div>

              <div className="space-y-3 lg:col-span-2 2xl:col-span-1"><div className="flex items-end justify-between"><div><Label>EVs</Label><p className="mt-1 text-[9px] text-slate-600">Máximo 252 por stat · 510 total</p></div><Badge variant="outline" className={(Object.values(evValues).reduce((sum, value) => sum + value, 0) <= 510) ? "border-emerald-300/15 text-emerald-300" : "border-rose-300/20 text-rose-300"}>{Object.values(evValues).reduce((sum, value) => sum + value, 0)}/510</Badge></div><div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-1">{EV_STATS.map((stat) => <label key={stat} className="grid grid-cols-[38px_minmax(0,1fr)] items-center gap-2 rounded-xl border border-white/7 bg-white/3 px-3 py-2"><span className="text-[10px] font-black text-slate-500">{stat}</span><Input aria-label={`EVs de ${stat}`} type="number" min={0} max={252} step={4} value={evValues[stat]} onChange={(event) => { const next = { ...evValues, [stat]: Math.min(252, Math.max(0, Number(event.target.value) || 0)) }; updateSelected({ ...selected, evs: serializeEvs(next) }); }} className="h-8 border-white/8 bg-black/20 text-right" /></label>)}</div><div className="rounded-xl border border-white/7 bg-black/20 px-3 py-2 font-mono text-[9px] leading-4 text-slate-500">{selected.evs || "Sin EVs asignados"}</div></div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
