"use client";

import Image from "next/image";
import { useId, useState } from "react";
import { Check, Link2, Loader2, Plus, Save, Swords, UserRound } from "lucide-react";

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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { formatVersion } from "@/lib/team-builder";
import type { MatchResult, TeamGroup, TeamVersion } from "@/lib/types";

async function readResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || "No pudimos guardar los cambios.");
  return payload;
}

export function ShowdownNamesDialog({ names, onSaved }: { names: string[]; onSaved: (names: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(names.join("\n"));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) {
      setValue(names.join("\n"));
      setError("");
    }
    setOpen(nextOpen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const showdownNames = [...new Set(value.split(/[\n,]/).map((name) => name.trim()).filter(Boolean))];
      const payload = await readResponse<{ showdownNames: string[] }>(
        await fetch("/api/settings", {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ showdownNames }),
        }),
      );
      onSaved(payload.showdownNames);
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos guardar los nombres.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" className="h-10 max-w-64 gap-2 rounded-full border-white/10 bg-white/4 px-3 text-left">
          <UserRound className="size-4 shrink-0 text-cyan-300" />
          <span className="min-w-0"><span className="block text-[8px] font-black uppercase tracking-[0.16em] text-slate-600">Trainer</span><span className="block truncate text-xs text-slate-200">{names.length ? names.join(" · ") : "Añadir Showdown name"}</span></span>
        </Button>
      </DialogTrigger>
      <DialogContent className="border-white/10 bg-slate-950 text-slate-100 sm:max-w-md">
        <form onSubmit={submit}>
          <DialogHeader><DialogTitle>Showdown Name(s)</DialogTitle><DialogDescription className="text-slate-500">Escribe los nombres con los que juegas, igual que en el Excel. Usa una línea por nombre.</DialogDescription></DialogHeader>
          <div className="my-5 grid gap-2"><Label htmlFor="showdown-names">Nombre(s) de entrenador</Label><Textarea id="showdown-names" value={value} onChange={(event) => setValue(event.target.value)} placeholder={"Roku4523\nOtroNombre"} className="min-h-28 border-white/10 bg-white/5" autoFocus />{error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}</div>
          <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" disabled={saving} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar nombres</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function AddTeamDialog({ onCreated }: { onCreated: (team: TeamGroup) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [paste, setPaste] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = await readResponse<{ team: TeamGroup }>(
        await fetch("/api/teams", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name, paste }),
        }),
      );
      onCreated(payload.team);
      setName("");
      setPaste("");
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos guardar el equipo.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2 rounded-full bg-cyan-300 text-slate-950 shadow-[0_0_28px_rgba(103,232,249,0.18)] hover:bg-cyan-200"><Plus className="size-4" />Agregar equipo</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Guardar un equipo</DialogTitle>
            <DialogDescription className="text-slate-500">Ponle nombre y pega los seis sets de Pokémon Showdown. La primera versión se guardará como v1.</DialogDescription>
          </DialogHeader>
          <div className="my-5 grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="team-name">Nombre del equipo</Label>
              <Input id="team-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Ej. Aurora Protocol" className="border-white/10 bg-white/5" autoFocus />
            </div>
            <div className="grid gap-2">
              <div className="flex items-center justify-between gap-3"><Label htmlFor="team-paste">Pokepaste / Showdown paste</Label><span className="text-[10px] text-slate-600">6 Pokémon requeridos</span></div>
              <Textarea id="team-paste" value={paste} onChange={(event) => setPaste(event.target.value)} placeholder={"Kleavor @ Choice Scarf\nAbility: Sharpness\n..."} className="min-h-72 border-white/10 bg-black/30 font-mono text-xs leading-5" />
            </div>
            {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button type="submit" disabled={saving} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar v1</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function NewVersionDialog({ team, onCreated }: { team: TeamGroup; onCreated: (version: TeamVersion) => void }) {
  const [open, setOpen] = useState(false);
  const [paste, setPaste] = useState(team.versions[0]?.paste ?? "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) setPaste(team.versions[0]?.paste ?? "");
    setOpen(nextOpen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = await readResponse<{ version: TeamVersion }>(
        await fetch(`/api/teams/${team.id}/versions`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ paste, format: team.versions[0]?.format ?? "gen9", mechanics: team.versions[0]?.mechanics ?? ["tera"] }),
        }),
      );
      onCreated(payload.version);
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos crear la versión.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild><Button variant="outline" className="gap-2 rounded-full border-white/10 bg-white/4"><Plus className="size-4" />Nueva versión</Button></DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
        <form onSubmit={submit}>
          <DialogHeader><DialogTitle>Crear una versión de {team.name}</DialogTitle><DialogDescription className="text-slate-500">Si cambia una especie subirá la versión mayor; si solo cambia el set se guardará como versión menor. El historial anterior permanece intacto.</DialogDescription></DialogHeader>
          <div className="my-5 grid gap-2"><Label htmlFor="version-paste">Nuevo Pokepaste</Label><Textarea id="version-paste" value={paste} onChange={(event) => setPaste(event.target.value)} className="min-h-80 border-white/10 bg-black/30 font-mono text-xs leading-5" />{error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}</div>
          <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" disabled={saving} className="gap-2 bg-violet-300 text-slate-950 hover:bg-violet-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar versión</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

type AddMatchDialogProps = {
  version: TeamVersion;
  onCreated: () => void;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  initialReplayUrl?: string;
  hideTrigger?: boolean;
};

function PokemonPreview({ species, tone = "cyan" }: { species: string[]; tone?: "cyan" | "violet" }) {
  if (!species.length) return null;
  return (
    <div className={tone === "cyan" ? "flex w-fit overflow-hidden rounded-lg border border-cyan-300/20 bg-cyan-300/8" : "flex w-fit overflow-hidden rounded-lg border border-violet-300/20 bg-violet-300/8"}>
      {species.slice(0, 6).map((name) => (
        <span key={name} className="flex size-9 items-center justify-center border-r border-white/8 last:border-r-0" title={name}>
          <Image src={getSpriteUrl(name)} alt={name} width={34} height={34} unoptimized className="size-8 object-contain" />
        </span>
      ))}
    </div>
  );
}

export function AddMatchDialog({ version, onCreated, open: controlledOpen, onOpenChange, initialReplayUrl = "", hideTrigger = false }: AddMatchDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const [result, setResult] = useState<MatchResult>("win");
  const [opponentName, setOpponentName] = useState("");
  const [replayUrl, setReplayUrl] = useState(initialReplayUrl);
  const [rating, setRating] = useState("");
  const [notes, setNotes] = useState("");
  const [opponentTeam, setOpponentTeam] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [lead, setLead] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function setOpen(nextOpen: boolean) {
    setInternalOpen(nextOpen);
    onOpenChange?.(nextOpen);
  }

  function toggleSelected(species: string) {
    setSelected((current) => current.includes(species) ? current.filter((entry) => entry !== species) : current.length < 4 ? [...current, species] : current);
    setLead((current) => current.filter((entry) => entry !== species));
  }

  function toggleLead(species: string) {
    if (!selected.includes(species)) return;
    setLead((current) => current.includes(species) ? current.filter((entry) => entry !== species) : current.length < 2 ? [...current, species] : current);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const opponentSelected = [...new Set(
        opponentTeam
          .split(/[\n,]/)
          .map((species) => species.trim())
          .filter(Boolean),
      )];
      if (opponentSelected.length > 6) {
        throw new Error("El equipo rival puede contener como máximo 6 Pokémon.");
      }
      await readResponse(
        await fetch("/api/matches", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ teamVersionId: version.id, result, opponentName, replayUrl, rating: rating ? Number(rating) : null, notes, selected, opponentSelected, lead }),
        }),
      );
      onCreated();
      setOpen(false);
      setOpponentName(""); setReplayUrl(""); setRating(""); setNotes(""); setOpponentTeam(""); setSelected([]); setLead([]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos registrar la partida.");
    } finally {
      setSaving(false);
    }
  }

  const opponentPreview = [...new Set(opponentTeam.split(/[\n,]/).map((species) => species.trim()).filter(Boolean))];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {!hideTrigger ? <DialogTrigger asChild><Button className="gap-2 rounded-full bg-white text-slate-950 hover:bg-slate-200"><Swords className="size-4" />Registrar partida</Button></DialogTrigger> : null}
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-xl">
        <form onSubmit={submit}>
          <DialogHeader><DialogTitle>Nueva partida · {version.name} v{formatVersion(version)}</DialogTitle><DialogDescription className="text-slate-500">Guarda el resultado y la selección real. Las métricas se recalculan desde el historial.</DialogDescription></DialogHeader>
          <div className="my-5 grid gap-4">
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2"><Label>Resultado</Label><Select value={result} onValueChange={(value) => setResult(value as MatchResult)}><SelectTrigger className="w-full border-white/10 bg-white/5"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="win">Victoria</SelectItem><SelectItem value="loss">Derrota</SelectItem></SelectContent></Select></div>
              <div className="grid gap-2"><Label htmlFor="match-rating">Rating final</Label><Input id="match-rating" inputMode="numeric" value={rating} onChange={(event) => setRating(event.target.value)} placeholder="1428" className="border-white/10 bg-white/5" /></div>
            </div>
            <div className="grid gap-2"><Label htmlFor="opponent-name">Rival / arquetipo</Label><Input id="opponent-name" value={opponentName} onChange={(event) => setOpponentName(event.target.value)} placeholder="Ej. Rain Balance" className="border-white/10 bg-white/5" /></div>
            <div className="grid gap-2">
              <div className="flex items-center justify-between gap-3"><Label htmlFor="opponent-team">Equipo rival visto</Label><span className="text-[10px] text-slate-600">Hasta 6 · separados por comas</span></div>
              <Input id="opponent-team" value={opponentTeam} onChange={(event) => setOpponentTeam(event.target.value)} placeholder="Pelipper, Archaludon, Rillaboom, ..." className="border-white/10 bg-white/5" />
              <p className="text-[10px] leading-4 text-slate-600">Este campo alimenta Best/Worst Matchups y Attendance, igual que en tus hojas.</p>
              <PokemonPreview species={opponentPreview} tone="violet" />
            </div>
            <div className="grid gap-2"><Label htmlFor="replay-url">Replay de Showdown</Label><Input id="replay-url" type="url" value={replayUrl} onChange={(event) => setReplayUrl(event.target.value)} placeholder="https://replay.pokemonshowdown.com/..." className="border-white/10 bg-white/5" /></div>
            <div className="grid gap-2"><div className="flex items-center justify-between"><Label>Tus 4 picks</Label><span className="text-[10px] text-slate-600">{selected.length}/4</span></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{version.pokemon.map((pokemon) => <button key={pokemon.id} type="button" onClick={() => toggleSelected(pokemon.species)} className={selected.includes(pokemon.species) ? "flex items-center gap-2 rounded-xl border border-cyan-300/35 bg-cyan-300/10 px-2 py-1.5 text-left text-xs text-cyan-100" : "flex items-center gap-2 rounded-xl border border-white/8 bg-white/3 px-2 py-1.5 text-left text-xs text-slate-400"}><Image src={getSpriteUrl(pokemon.species)} alt="" width={34} height={34} unoptimized className="size-8 shrink-0 object-contain" /><span className="min-w-0 flex-1 truncate">{pokemon.species}</span>{selected.includes(pokemon.species) ? <Check className="size-3 shrink-0" /> : null}</button>)}</div><PokemonPreview species={selected} /></div>
            <div className="grid gap-2"><div className="flex items-center justify-between"><Label>Tus 2 leads</Label><span className="text-[10px] text-slate-600">{lead.length}/2</span></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{version.pokemon.filter((pokemon) => selected.includes(pokemon.species)).map((pokemon) => <button key={pokemon.id} type="button" onClick={() => toggleLead(pokemon.species)} className={lead.includes(pokemon.species) ? "flex items-center gap-2 rounded-xl border border-violet-300/35 bg-violet-300/10 px-2 py-1.5 text-left text-xs text-violet-100" : "flex items-center gap-2 rounded-xl border border-white/8 bg-white/3 px-2 py-1.5 text-left text-xs text-slate-400"}><Image src={getSpriteUrl(pokemon.species)} alt="" width={30} height={30} unoptimized className="size-7 shrink-0 object-contain" /><span className="truncate">{pokemon.species}</span></button>)}</div></div>
            <div className="grid gap-2"><Label htmlFor="match-notes">Notas</Label><Textarea id="match-notes" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Qué funcionó, qué revisar..." className="min-h-20 border-white/10 bg-white/5" /></div>
            {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}
          </div>
          <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" disabled={saving || selected.length !== 4 || lead.length !== 2} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar partida</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ReplayQuickEntry({ version, onCreated }: { version: TeamVersion; onCreated?: () => void }) {
  const replayInputId = useId();
  const [replayUrl, setReplayUrl] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const disabled = version.demo || !onCreated;

  function continueEntry(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (!replayUrl.startsWith("https://replay.pokemonshowdown.com/")) {
      setError("Pega un enlace válido de replay.pokemonshowdown.com.");
      return;
    }
    setOpen(true);
  }

  return (
    <div className="w-full">
      <form onSubmit={continueEntry} className="grid gap-3 rounded-2xl border border-cyan-300/20 bg-cyan-300/[0.045] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] sm:grid-cols-[minmax(0,1fr)_auto]">
        <div className="grid gap-1.5">
          <Label htmlFor={replayInputId} className="text-[9px] font-black uppercase tracking-[0.14em] text-cyan-100">URL del replay de Showdown</Label>
          <div className="relative">
            <span className="pointer-events-none absolute inset-y-1.5 left-1.5 flex w-9 items-center justify-center rounded-lg border border-cyan-300/15 bg-cyan-300/8">
              <Link2 className="size-4 text-cyan-300" />
            </span>
            <Input
              id={replayInputId}
              type="url"
              value={replayUrl}
              onChange={(event) => setReplayUrl(event.target.value)}
              disabled={disabled}
              placeholder="https://replay.pokemonshowdown.com/gen9vgc..."
              className="replay-url-field h-12 border-cyan-300/35 bg-[#101c30] pl-12 font-mono text-xs text-white placeholder:text-slate-500"
            />
          </div>
        </div>
        <Button type="submit" disabled={disabled || !replayUrl} className="h-12 gap-2 self-end bg-cyan-300 px-5 font-black text-slate-950 shadow-[0_8px_24px_rgba(34,211,238,0.14)] hover:bg-cyan-200"><Plus className="size-4" />Agregar replay</Button>
      </form>
      <div className="mt-1.5 flex items-center justify-between gap-3 text-[9px]">
        <span className={error ? "text-rose-300" : "text-slate-600"}>{error || (disabled ? "Guarda un equipo real para habilitar el registro." : "El enlace se conserva al abrir los detalles de la partida.")}</span>
        <span className="shrink-0 text-slate-700">Showdown</span>
      </div>
      <AddMatchDialog
        key={replayUrl}
        version={version}
        onCreated={() => { setReplayUrl(""); onCreated?.(); }}
        open={open}
        onOpenChange={setOpen}
        initialReplayUrl={replayUrl}
        hideTrigger
      />
    </div>
  );
}
