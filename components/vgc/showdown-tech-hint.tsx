"use client";

import { useEffect, useState, type ReactNode } from "react";

import {
  formatMoveAccuracy,
  getAbilityData,
  getMoveData,
  loadShowdownSnapshot,
  type ShowdownSnapshot,
} from "@/lib/showdown-data";

let sharedSnapshotPromise: Promise<ShowdownSnapshot> | null = null;

function getSharedSnapshot() {
  sharedSnapshotPromise ??= loadShowdownSnapshot();
  return sharedSnapshotPromise;
}

function moveHint(snapshot: ShowdownSnapshot, name: string) {
  const move = getMoveData(snapshot, name);
  if (!move) return "";
  const stats = [
    move.category,
    `${move.basePower ?? 0} BP`,
    `${formatMoveAccuracy(move.accuracy)} Acc`,
    `${move.pp ?? 0} PP`,
    move.priority ? `${move.priority > 0 ? "+" : ""}${move.priority} prioridad` : "",
  ].filter(Boolean).join(" · ");
  const description = move.shortDesc || move.desc || "";
  return description ? `${stats}\n${description}` : stats;
}

function abilityHint(snapshot: ShowdownSnapshot, name: string) {
  const ability = getAbilityData(snapshot, name);
  if (!ability) return "";
  return ability.shortDesc || ability.desc || "";
}

export function ShowdownTechHint({
  kind,
  name,
  children,
}: {
  kind: "move" | "ability";
  name: string;
  children: ReactNode;
}) {
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (!name) return;
    let active = true;
    getSharedSnapshot()
      .then((snapshot) => {
        if (!active) return;
        setHint(kind === "move" ? moveHint(snapshot, name) : abilityHint(snapshot, name));
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [kind, name]);

  return (
    <span
      title={hint || undefined}
      className={hint ? "cursor-help decoration-dotted underline-offset-2 hover:underline" : undefined}
    >
      {children}
    </span>
  );
}
