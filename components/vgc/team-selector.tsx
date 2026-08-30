import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { winRate } from "@/lib/team-stats";
import type { TeamGroup } from "@/lib/types";
import { formatVersion } from "@/lib/team-builder";
import { cn } from "@/lib/utils";

export function TeamSelector({
  label,
  value,
  groups,
  onChange,
  accent,
}: {
  label: string;
  value: string;
  groups: TeamGroup[];
  onChange: (value: string) => void;
  accent: "cyan" | "violet";
}) {
  const availableGroups = groups.filter((group) => group.versions.length > 0);
  const currentGroup = availableGroups.find((group) => group.versions.some((version) => version.id === value)) ?? availableGroups[0];
  const current = currentGroup?.versions.find((version) => version.id === value) ?? currentGroup?.versions[0];

  function handleTeamChange(teamId: string) {
    const team = availableGroups.find((group) => group.id === teamId);
    onChange(team?.versions[0]?.id ?? "");
  }

  return (
    <div className={cn("rounded-2xl border bg-slate-950/70 p-3", accent === "cyan" ? "border-cyan-300/20" : "border-fuchsia-300/20")}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className={cn("text-[10px] font-black uppercase tracking-[0.18em]", accent === "cyan" ? "text-cyan-300" : "text-fuchsia-300")}>{label}</span>
        {current ? <span className="text-[10px] text-slate-600">{current.games} partidas · {winRate(current.wins, current.games)}%</span> : null}
      </div>

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_180px]">
        <div className="space-y-1">
          <span className="px-1 text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Equipo</span>
          <Select value={currentGroup?.id ?? ""} onValueChange={handleTeamChange} disabled={!availableGroups.length}>
            <SelectTrigger className="w-full border-white/8 bg-white/4 text-left shadow-none">
              <SelectValue placeholder="Seleccionar equipo" />
            </SelectTrigger>
            <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
              {availableGroups.map((group) => (
                <SelectItem key={group.id} value={group.id} className="py-2.5 focus:bg-white/8 focus:text-white">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{group.name}</span>
                    <span className="text-[10px] text-slate-500">{group.versions.length} {group.versions.length === 1 ? "versión" : "versiones"}</span>
                  </div>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <span className="px-1 text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Versión</span>
          <Select value={current?.id ?? ""} onValueChange={onChange} disabled={!currentGroup}>
            <SelectTrigger className="w-full border-white/8 bg-white/4 text-left shadow-none">
              <SelectValue placeholder="Versión" />
            </SelectTrigger>
            <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
              {currentGroup?.versions.map((version) => (
                <SelectItem key={version.id} value={version.id} className="py-2.5 focus:bg-white/8 focus:text-white">
                  <span className="font-semibold">v{formatVersion(version)}</span>
                  <span className="ml-2 text-[10px] text-slate-500">{version.games} G</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {!availableGroups.length ? <p className="mt-2 text-[10px] text-slate-600">No hay equipos guardados todavía.</p> : null}
    </div>
  );
}
