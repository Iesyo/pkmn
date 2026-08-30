"use client";

import type { DragEvent } from "react";
import Image from "next/image";
import { Check, ChevronRight, FolderInput } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { formatVersion } from "@/lib/team-builder";
import type { TeamFolder, TeamGroup } from "@/lib/types";
import { getSpriteUrl } from "@/lib/pokemon-data";

export const TEAM_DRAG_MIME = "application/x-like-no-one-ever-was-team";

export function LibraryCard({
  team,
  folders,
  selected,
  onClick,
  onMove,
}: {
  team: TeamGroup;
  folders: TeamFolder[];
  selected: boolean;
  onClick: () => void;
  onMove: (folderId: string | null) => void;
}) {
  const latest = team.versions[0];
  if (!latest) return null;
  const winRate = latest.games ? Math.round((latest.wins / latest.games) * 100) : 0;
  const currentFolderId = team.folderId ?? null;

  function startDrag(event: DragEvent<HTMLDivElement>) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(TEAM_DRAG_MIME, team.id);
    event.dataTransfer.setData("text/plain", team.id);
  }

  return (
    <div
      draggable
      onDragStart={startDrag}
      className={cn(
        "group relative w-full cursor-grab overflow-hidden rounded-2xl border transition-all active:cursor-grabbing",
        selected
          ? "border-cyan-300/20 bg-cyan-300/[0.07] shadow-[0_0_25px_rgba(34,211,238,0.06)]"
          : "border-white/8 bg-white/[0.025] hover:border-white/15 hover:bg-white/[0.04]",
      )}
    >
      <button type="button" onClick={onClick} className="w-full p-3 pr-10 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300/45">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-xs font-black text-white">{team.name}</p>
            <p className="mt-1 text-[9px] font-semibold text-slate-600">{team.versions.length} {team.versions.length === 1 ? "versión" : "versiones"}</p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <span className="rounded-full border border-white/8 bg-white/[0.035] px-2 py-1 text-[9px] font-bold text-slate-500">v{formatVersion(latest)}</span>
            <ChevronRight className="mt-0.5 size-4 text-slate-700 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-400" />
          </div>
        </div>
        <div className="mt-3 flex items-end justify-between gap-3">
          <div className="flex -space-x-1.5">
            {latest.pokemon.slice(0, 6).map((pokemon, index) => (
              <span key={pokemon.id} className="relative flex size-7 items-center justify-center overflow-hidden rounded-full border border-slate-800 bg-slate-950" style={{ zIndex: 6 - index }}>
                <Image src={getSpriteUrl(pokemon.species)} alt={pokemon.species} width={28} height={28} className="size-7 object-contain" unoptimized />
              </span>
            ))}
          </div>
          <div className="text-right"><p className="text-sm font-black text-white">{winRate}%</p><p className="text-[8px] font-bold text-slate-700">{latest.games} games</p></div>
        </div>
      </button>
      <div className="absolute right-2 top-2 z-10">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="text-slate-500 hover:bg-white/8 hover:text-cyan-200"
              aria-label={`Mover ${team.name}`}
              title="Mover equipo"
            >
              <FolderInput />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-48 border-white/10 bg-slate-950 text-slate-200">
            <DropdownMenuLabel className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">Mover a</DropdownMenuLabel>
            <DropdownMenuItem onSelect={() => onMove(null)} className="gap-2">
              {currentFolderId === null ? <Check className="size-3.5 text-cyan-300" /> : <span className="size-3.5" />}
              Sin carpeta
            </DropdownMenuItem>
            {folders.length ? <DropdownMenuSeparator className="bg-white/8" /> : null}
            {folders.map((folder) => (
              <DropdownMenuItem key={folder.id} onSelect={() => onMove(folder.id)} className="gap-2">
                {currentFolderId === folder.id ? <Check className="size-3.5 text-cyan-300" /> : <span className="size-3.5" />}
                <span className="truncate">{folder.name}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
