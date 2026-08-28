"use client";

import { useState } from "react";
import { Check, Loader2, Plus, Save, Swords } from "lucide-react";

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
import type { MatchResult, TeamGroup, TeamVersion } from "@/lib/types";

async function readResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new Error(payload.error || "No pudimos guardar los cambios.");
  return payload;
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
  const nextVersion = (team.versions[0]?.version ?? 0) + 1;

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
          body: JSON.stringify({ paste }),
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
          <DialogHeader><DialogTitle>Crear {team.name} v{nextVersion}</DialogTitle><DialogDescription className="text-slate-500">La versión anterior no se modifica. Las partidas ya guardadas seguirán vinculadas a su versión exacta.</DialogDescription></DialogHeader>
          <div className="my-5 grid gap-2"><Label htmlFor="version-paste">Nuevo Pokepaste</Label><Textarea id="version-paste" value={paste} onChange={(event) => setPaste(event.target.value)} className="min-h-80 border-white/10 bg-black/30 font-mono text-xs leading-5" />{error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}</div>
          <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" disabled={saving} className="gap-2 bg-violet-300 text-slate-950 hover:bg-violet-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar v{nextVersion}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function AddMatchDialog({ version, onCreated }: { version: TeamVersion; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<MatchResult>("win");
  const [opponentName, setOpponentName] = useState("");
  const [replayUrl, setReplayUrl] = useState("");
  const [rating, setRating] = useState("");
  const [notes, setNotes] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [lead, setLead] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

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
      await readResponse(
        await fetch("/api/matches", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ teamVersionId: version.id, result, opponentName, replayUrl, rating: rating ? Number(rating) : null, notes, selected, lead }),
        }),
      );
      onCreated();
      setOpen(false);
      setOpponentName(""); setReplayUrl(""); setRating(""); setNotes(""); setSelected([]); setLead([]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos registrar la partida.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button className="gap-2 rounded-full bg-white text-slate-950 hover:bg-slate-200"><Swords className="size-4" />Registrar partida</Button></DialogTrigger>
      <DialogContent className="max-h-[88vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-xl">
        <form onSubmit={submit}>
          <DialogHeader><DialogTitle>Nueva partida · {version.name} v{version.version}</DialogTitle><DialogDescription className="text-slate-500">Guarda el resultado y la selección real. Las métricas se recalculan desde el historial.</DialogDescription></DialogHeader>
          <div className="my-5 grid gap-4">
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2"><Label>Resultado</Label><Select value={result} onValueChange={(value) => setResult(value as MatchResult)}><SelectTrigger className="w-full border-white/10 bg-white/5"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="win">Victoria</SelectItem><SelectItem value="loss">Derrota</SelectItem></SelectContent></Select></div>
              <div className="grid gap-2"><Label htmlFor="match-rating">Rating final</Label><Input id="match-rating" inputMode="numeric" value={rating} onChange={(event) => setRating(event.target.value)} placeholder="1428" className="border-white/10 bg-white/5" /></div>
            </div>
            <div className="grid gap-2"><Label htmlFor="opponent-name">Rival / arquetipo</Label><Input id="opponent-name" value={opponentName} onChange={(event) => setOpponentName(event.target.value)} placeholder="Ej. Rain Balance" className="border-white/10 bg-white/5" /></div>
            <div className="grid gap-2"><Label htmlFor="replay-url">Replay de Showdown</Label><Input id="replay-url" type="url" value={replayUrl} onChange={(event) => setReplayUrl(event.target.value)} placeholder="https://replay.pokemonshowdown.com/..." className="border-white/10 bg-white/5" /></div>
            <div className="grid gap-2"><div className="flex items-center justify-between"><Label>Tus 4 picks</Label><span className="text-[10px] text-slate-600">{selected.length}/4</span></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{version.pokemon.map((pokemon) => <button key={pokemon.id} type="button" onClick={() => toggleSelected(pokemon.species)} className={selected.includes(pokemon.species) ? "flex items-center justify-between rounded-xl border border-cyan-300/35 bg-cyan-300/10 px-3 py-2 text-left text-xs text-cyan-100" : "flex items-center justify-between rounded-xl border border-white/8 bg-white/3 px-3 py-2 text-left text-xs text-slate-400"}><span className="truncate">{pokemon.species}</span>{selected.includes(pokemon.species) ? <Check className="size-3" /> : null}</button>)}</div></div>
            <div className="grid gap-2"><div className="flex items-center justify-between"><Label>Tus 2 leads</Label><span className="text-[10px] text-slate-600">{lead.length}/2</span></div><div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{version.pokemon.filter((pokemon) => selected.includes(pokemon.species)).map((pokemon) => <button key={pokemon.id} type="button" onClick={() => toggleLead(pokemon.species)} className={lead.includes(pokemon.species) ? "rounded-xl border border-violet-300/35 bg-violet-300/10 px-3 py-2 text-left text-xs text-violet-100" : "rounded-xl border border-white/8 bg-white/3 px-3 py-2 text-left text-xs text-slate-400"}>{pokemon.species}</button>)}</div></div>
            <div className="grid gap-2"><Label htmlFor="match-notes">Notas</Label><Textarea id="match-notes" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Qué funcionó, qué revisar..." className="min-h-20 border-white/10 bg-white/5" /></div>
            {error ? <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-300/8 px-3 py-2 text-xs text-rose-200">{error}</p> : null}
          </div>
          <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" disabled={saving || selected.length !== 4 || lead.length !== 2} className="gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Guardar partida</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
