"use client";

import { useState } from "react";
import { Bookmark, Trophy, Users } from "lucide-react";

import { ScoutingPasteLibrary } from "@/components/vgc/scouting-paste-library";
import { TournamentScoutingBrowser } from "@/components/vgc/tournament-scouting-browser";
import { VgcPastesScoutingBrowser } from "@/components/vgc/vgcpastes-scouting-browser";
import type { TournamentTeamBuilderImport } from "@/lib/tournament-scouting";

type ScoutingMode = "repository" | "private" | "tournaments";

function ScoutingModeSwitcher({
  mode,
  onChange,
}: {
  mode: ScoutingMode;
  onChange: (mode: ScoutingMode) => void;
}) {
  const idle = "inline-flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2 text-xs font-bold text-slate-500 transition hover:bg-white/4 hover:text-slate-300 sm:flex-none";
  const activeCyan = "inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-cyan-300/12 px-4 py-2 text-xs font-black text-cyan-100 ring-1 ring-cyan-300/20 sm:flex-none";
  const activeViolet = "inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-violet-300/12 px-4 py-2 text-xs font-black text-violet-100 ring-1 ring-violet-300/20 sm:flex-none";
  const activeAmber = "inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-amber-300/12 px-4 py-2 text-xs font-black text-amber-100 ring-1 ring-amber-300/20 sm:flex-none";
  return (
    <div role="tablist" aria-label="Fuente de scouting" className="inline-flex w-full flex-wrap gap-1 rounded-xl border border-white/8 bg-slate-950/60 p-1 sm:w-auto">
      <button type="button" role="tab" aria-selected={mode === "repository"} onClick={() => onChange("repository")} className={mode === "repository" ? activeCyan : idle}>
        <Users className="size-3.5" />Públicos
      </button>
      <button type="button" role="tab" aria-selected={mode === "private"} onClick={() => onChange("private")} className={mode === "private" ? activeViolet : idle}>
        <Bookmark className="size-3.5" />Mis pastes
      </button>
      <button type="button" role="tab" aria-selected={mode === "tournaments"} onClick={() => onChange("tournaments")} className={mode === "tournaments" ? activeAmber : idle}>
        <Trophy className="size-3.5" />Torneos
      </button>
    </div>
  );
}

export function ScoutingView({
  onTournamentTeamImport,
}: {
  onTournamentTeamImport: (request: TournamentTeamBuilderImport) => void;
}) {
  const [mode, setMode] = useState<ScoutingMode>("repository");
  const modeSwitcher = <ScoutingModeSwitcher mode={mode} onChange={setMode} />;

  if (mode === "private") {
    return <div className="space-y-5">{modeSwitcher}<ScoutingPasteLibrary onImportTeam={onTournamentTeamImport} /></div>;
  }

  if (mode === "tournaments") {
    return <div className="space-y-5">{modeSwitcher}<TournamentScoutingBrowser onImportTeam={onTournamentTeamImport} /></div>;
  }

  return <div className="space-y-5">{modeSwitcher}<VgcPastesScoutingBrowser onImportTeam={onTournamentTeamImport} /></div>;
}
