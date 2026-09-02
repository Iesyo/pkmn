"use client";

import { useRef, useState, type DragEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { TEAM_DRAG_MIME } from "./library-card";

export type TeamDropPosition = "before" | "after";

function isTeamDrag(event: DragEvent<HTMLElement>) {
  return Array.from(event.dataTransfer.types).includes(TEAM_DRAG_MIME);
}

function getDropPosition(event: DragEvent<HTMLElement>): TeamDropPosition {
  const rect = event.currentTarget.getBoundingClientRect();
  return event.clientY < rect.top + rect.height / 2 ? "before" : "after";
}

export function TeamOrderItem({
  teamId,
  children,
  onDropTeam,
  disabled = false,
}: {
  teamId: string;
  children: ReactNode;
  onDropTeam: (teamId: string, targetTeamId: string, position: TeamDropPosition) => void;
  disabled?: boolean;
}) {
  const [dropIndicator, setDropIndicator] = useState<TeamDropPosition | null>(null);
  const dragDepth = useRef(0);

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!isTeamDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current += 1;
    event.dataTransfer.dropEffect = disabled ? "none" : "move";
    setDropIndicator(disabled ? null : getDropPosition(event));
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!isTeamDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = disabled ? "none" : "move";
    setDropIndicator(disabled ? null : getDropPosition(event));
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!isTeamDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDropIndicator(null);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!isTeamDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    const position = dropIndicator ?? getDropPosition(event);
    setDropIndicator(null);
    if (disabled) return;
    const draggedTeamId = event.dataTransfer.getData(TEAM_DRAG_MIME) || event.dataTransfer.getData("text/plain");
    if (draggedTeamId && draggedTeamId !== teamId) {
      onDropTeam(draggedTeamId, teamId, position);
    }
  }

  return (
    <div
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className="relative"
    >
      {dropIndicator ? (
        <div
          aria-hidden="true"
          className={cn(
            "pointer-events-none absolute inset-x-2 z-20 h-0.5 rounded-full bg-cyan-300 shadow-[0_0_10px_rgba(103,232,249,0.75)]",
            dropIndicator === "before" ? "-top-1" : "-bottom-1",
          )}
        />
      ) : null}
      {children}
    </div>
  );
}
