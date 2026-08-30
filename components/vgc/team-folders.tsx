"use client";

import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { ChevronDown, Folder, FolderOpen, FolderPlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { TeamFolder } from "@/lib/types";
import { TEAM_DRAG_MIME } from "./library-card";

const UNFILED_DISCLOSURE_KEY = "__unfiled__";
const folderDisclosureState = new Map<string, boolean>();

function disclosureKey(folder: TeamFolder | null) {
  return folder?.id ?? UNFILED_DISCLOSURE_KEY;
}

function defaultDisclosureOpen(folder: TeamFolder | null) {
  return folder === null;
}

async function readApiError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { error?: string };
    return payload.error ?? fallback;
  } catch {
    return fallback;
  }
}

export function CreateFolderDialog({
  onCreated,
}: {
  onCreated: (folder: TeamFolder) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/api/team-folders", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "No pudimos crear la carpeta."));
      const payload = (await response.json()) as { folder: TeamFolder };
      onCreated(payload.folder);
      setName("");
      setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos crear la carpeta.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) setError(""); }}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="icon-sm" title="Nueva carpeta" aria-label="Nueva carpeta" className="rounded-full border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.07] hover:text-cyan-200">
          <FolderPlus />
        </Button>
      </DialogTrigger>
      <DialogContent className="border-white/10 bg-slate-950 text-slate-100 sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Nueva carpeta</DialogTitle>
          <DialogDescription>Organiza equipos completos sin alterar sus versiones ni partidas.</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-3">
          <Input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Ej. Megas, Torneo, Test…" maxLength={40} className="border-white/10 bg-white/[0.04] text-white placeholder:text-slate-700" />
          {error ? <p className="text-xs font-semibold text-rose-300">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button type="submit" disabled={saving || !name.trim()} className="bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? "Creando…" : "Crear carpeta"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RenameFolderDialog({
  folder,
  open,
  onOpenChange,
  onRenamed,
}: {
  folder: TeamFolder;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRenamed: (folder: TeamFolder) => void;
}) {
  const [name, setName] = useState(folder.name);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    setError("");
    try {
      const response = await fetch(`/api/team-folders/${folder.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "No pudimos renombrar la carpeta."));
      const payload = (await response.json()) as { folder: TeamFolder };
      onRenamed(payload.folder);
      onOpenChange(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No pudimos renombrar la carpeta.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setName(folder.name);
        else setError("");
        onOpenChange(next);
      }}
    >
      <DialogContent className="border-white/10 bg-slate-950 text-slate-100 sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Renombrar carpeta</DialogTitle>
          <DialogDescription>Los equipos permanecen en la misma carpeta.</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="space-y-3">
          <Input autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={40} className="border-white/10 bg-white/[0.04] text-white" />
          {error ? <p className="text-xs font-semibold text-rose-300">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
            <Button type="submit" disabled={saving || !name.trim()} className="bg-cyan-300 text-slate-950 hover:bg-cyan-200">{saving ? "Guardando…" : "Guardar"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function TeamFolderSection({
  folder,
  teamCount,
  children,
  onDropTeam,
  onRenamed,
  onDeleted,
}: {
  folder: TeamFolder | null;
  teamCount: number;
  children: ReactNode;
  onDropTeam: (teamId: string, folderId: string | null) => void;
  onRenamed: (folder: TeamFolder) => void;
  onDeleted: (folderId: string) => Promise<void>;
}) {
  const folderKey = disclosureKey(folder);
  const [open, setOpenState] = useState(() => folderDisclosureState.get(folderKey) ?? defaultDisclosureOpen(folder));
  const [dragOver, setDragOver] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const dragDepth = useRef(0);
  const folderId = folder?.id ?? null;

  function toggleOpen() {
    const next = !open;
    folderDisclosureState.set(folderKey, next);
    setOpenState(next);
  }

  function handleDragEnter(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepth.current += 1;
    setDragOver(true);
  }

  function handleDragOver(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  function handleDragLeave(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragOver(false);
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    const teamId = event.dataTransfer.getData(TEAM_DRAG_MIME) || event.dataTransfer.getData("text/plain");
    if (teamId) onDropTeam(teamId, folderId);
  }

  return (
    <section
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "rounded-2xl border p-1.5 transition-colors",
        dragOver ? "border-cyan-300/35 bg-cyan-300/[0.07]" : "border-white/6 bg-white/[0.015]",
      )}
    >
      <div className="flex items-center gap-1">
        <button type="button" aria-expanded={open} onClick={toggleOpen} className="flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-1.5 text-left hover:bg-white/[0.035]">
          <ChevronDown className={cn("size-3.5 shrink-0 text-slate-600 transition-transform", !open && "-rotate-90")} />
          {open ? <FolderOpen className="size-3.5 shrink-0 text-cyan-300/80" /> : <Folder className="size-3.5 shrink-0 text-cyan-300/65" />}
          <span className="truncate text-[10px] font-black uppercase tracking-[0.1em] text-slate-400">{folder?.name ?? "Sin carpeta"}</span>
          <span className="ml-auto rounded-full bg-white/[0.045] px-1.5 py-0.5 text-[8px] font-bold text-slate-600">{teamCount}</span>
        </button>
        {folder ? (
          <>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="icon-xs" className="shrink-0 text-slate-600 hover:bg-white/6 hover:text-slate-300" aria-label={`Opciones de ${folder.name}`}>
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="border-white/10 bg-slate-950 text-slate-200">
                <DropdownMenuItem onSelect={() => setRenameOpen(true)}><Pencil />Renombrar</DropdownMenuItem>
                <DropdownMenuItem variant="destructive" onSelect={() => setDeleteOpen(true)}><Trash2 />Eliminar carpeta</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <RenameFolderDialog folder={folder} open={renameOpen} onOpenChange={setRenameOpen} onRenamed={onRenamed} />
            <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
              <AlertDialogContent className="border-white/10 bg-slate-950 text-slate-100">
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Eliminar “{folder.name}”?</AlertDialogTitle>
                  <AlertDialogDescription>Los {teamCount} equipos no se borrarán. Volverán automáticamente a Sin carpeta.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={() => void onDeleted(folder.id)}>Eliminar carpeta</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        ) : null}
      </div>
      <div hidden={!open} className="space-y-2 pt-1.5">
        {teamCount ? children : <div className={cn("rounded-xl border border-dashed px-3 py-3 text-center text-[9px] font-semibold", dragOver ? "border-cyan-300/25 text-cyan-200/70" : "border-white/7 text-slate-700")}>{dragOver ? "Suelta aquí para mover el equipo" : "Carpeta vacía · arrastra un equipo aquí"}</div>}
      </div>
    </section>
  );
}
