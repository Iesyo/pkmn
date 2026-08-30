"use client";

import { useState } from "react";
import { Activity, Check, Clipboard, Database, Gauge, Layers3, Trophy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { winRate } from "@/lib/team-stats";
import type { MatchRecord, TeamVersion } from "@/lib/types";
import { DEFAULT_BATTLE_FORMAT, formatVersion } from "@/lib/team-builder";
import { cn } from "@/lib/utils";
import { LeadsPanel } from "./leads-panel";
import { MatchHistory } from "./match-history";
import { MatchupAttendance } from "./matchup-attendance";
import { PokemonCard } from "./pokemon-card";
import { TypeAnalysis } from "./type-analysis";

const accentStyles = {
  cyan: {
    line: "from-cyan-300 via-sky-400 to-transparent",
    text: "text-cyan-300",
    badge: "border-cyan-300/20 bg-cyan-300/10 text-cyan-200",
    metric: "border-cyan-300/15 bg-cyan-300/5",
  },
  violet: {
    line: "from-fuchsia-300 via-violet-400 to-transparent",
    text: "text-fuchsia-300",
    badge: "border-fuchsia-300/20 bg-fuchsia-300/10 text-fuchsia-200",
    metric: "border-fuchsia-300/15 bg-fuchsia-300/5",
  },
} as const;

function SummaryMetric({ icon: Icon, label, value, detail, className }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string; detail: string; className: string }) {
  return (
    <div className={cn("rounded-xl border p-3", className)}>
      <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-[0.14em] text-slate-500"><Icon className="size-3" />{label}</div>
      <p className="mt-1 text-xl font-black tracking-tight text-white">{value}</p>
      <p className="mt-0.5 text-[9px] text-slate-600">{detail}</p>
    </div>
  );
}

function CopyPasteButton({ paste }: { paste: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <Button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(paste);
        setCopied(true);
      }}
      className="w-full shrink-0 gap-2 bg-cyan-300 text-slate-950 hover:bg-cyan-200 sm:w-auto"
    >
      {copied ? <Check className="size-4" /> : <Clipboard className="size-4" />}
      {copied ? "Copiado" : "Copiar paste"}
    </Button>
  );
}

export function TeamPanel({
  version,
  accent,
  onMatchCreated,
  onScoutingRequested,
  extraAction,
}: {
  version: TeamVersion;
  accent: keyof typeof accentStyles;
  onMatchCreated?: () => void;
  onScoutingRequested?: (version: TeamVersion, match: MatchRecord) => void;
  extraAction?: React.ReactNode;
}) {
  const style = accentStyles[accent];
  const rate = winRate(version.wins, version.games);
  const mostUsed = [...version.pokemon].sort((a, b) => b.performance.selectionRate - a.performance.selectionRate)[0];

  return (
    <section className="relative min-w-0 overflow-hidden rounded-[28px] border border-white/8 bg-slate-900/45 shadow-[0_32px_90px_rgba(0,0,0,0.28)] backdrop-blur-xl">
      <div className={cn("h-px w-full bg-gradient-to-r", style.line)} />
      <div className="border-b border-white/7 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-black tracking-[-0.03em] text-white">{version.name}</h2>
              <Badge variant="outline" className={style.badge}>v{formatVersion(version)}</Badge>
              <Badge variant="outline" className="border-white/8 bg-white/3 text-slate-500">{version.format ?? DEFAULT_BATTLE_FORMAT}</Badge>
              {version.demo ? <Badge variant="outline" className="border-white/10 bg-white/4 text-slate-500">Muestra</Badge> : <Badge variant="outline" className="border-emerald-300/15 bg-emerald-300/7 text-emerald-300"><Database className="mr-1 size-3" />SQLite</Badge>}
            </div>
            <p className="mt-1 text-xs text-slate-500">Versión inmutable · {version.pokemon.length} sets competitivos</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Dialog>
              <DialogTrigger asChild><Button variant="outline" className="gap-2 rounded-full border-white/10 bg-white/4"><Clipboard className="size-4" />Ver paste</Button></DialogTrigger>
              <DialogContent className="max-h-[86vh] overflow-y-auto border-white/10 bg-slate-950 text-slate-100 sm:max-w-2xl">
                <div className="flex flex-col gap-4 border-b border-white/8 pb-4 sm:flex-row sm:items-start sm:justify-between">
                  <DialogHeader className="min-w-0 text-left"><DialogTitle>{version.name} v{formatVersion(version)}</DialogTitle><DialogDescription className="text-slate-500">Copia exacta del paste asociado a esta versión.</DialogDescription></DialogHeader>
                  <CopyPasteButton paste={version.paste} />
                </div>
                <pre className="mt-4 overflow-x-auto rounded-2xl border border-white/8 bg-black/35 p-4 font-mono text-[11px] leading-5 text-slate-300">{version.paste}</pre>
              </DialogContent>
            </Dialog>
            {extraAction}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <SummaryMetric icon={Gauge} label="Win rate" value={`${rate}%`} detail={`${version.wins} victorias`} className={style.metric} />
          <SummaryMetric icon={Activity} label="Muestra" value={`${version.games}`} detail="partidas registradas" className="border-white/8 bg-white/[0.025]" />
          <SummaryMetric icon={Trophy} label="Mejor lead" value={version.leads[0] ? `${winRate(version.leads[0].wins, version.leads[0].games)}%` : "—"} detail={version.leads[0]?.species.join(" + ") ?? "sin datos"} className="border-white/8 bg-white/[0.025]" />
          <SummaryMetric icon={Layers3} label="Más usado" value={mostUsed ? `${mostUsed.performance.selectionRate}%` : "—"} detail={mostUsed?.species ?? "sin datos"} className="border-white/8 bg-white/[0.025]" />
        </div>
      </div>

      <div className="space-y-4 p-4 sm:p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          {version.pokemon.map((pokemon) => <PokemonCard key={pokemon.id} pokemon={pokemon} accent={accent} />)}
        </div>
        <LeadsPanel leads={version.leads} />
        <TypeAnalysis pokemon={version.pokemon} allowTera={(version.mechanics ?? ["tera"]).includes("tera")} />
        <MatchupAttendance matches={version.matches} />
        <MatchHistory key={version.id} version={version} onMatchCreated={onMatchCreated} onScoutingRequested={onScoutingRequested} />
      </div>
    </section>
  );
}