import Image from "next/image";
import { ChevronDown } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getSpriteUrl } from "@/lib/pokemon-data";
import { winRate } from "@/lib/team-stats";
import type { TeamVersion } from "@/lib/types";
import { formatVersion } from "@/lib/team-builder";
import { cn } from "@/lib/utils";

export function TeamSelector({
  label,
  value,
  versions,
  onChange,
  accent,
}: {
  label: string;
  value: string;
  versions: TeamVersion[];
  onChange: (value: string) => void;
  accent: "cyan" | "violet";
}) {
  const current = versions.find((version) => version.id === value);

  return (
    <div className={cn("rounded-2xl border bg-slate-950/70 p-3", accent === "cyan" ? "border-cyan-300/20" : "border-fuchsia-300/20")}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className={cn("text-[10px] font-black uppercase tracking-[0.18em]", accent === "cyan" ? "text-cyan-300" : "text-fuchsia-300")}>{label}</span>
        {current ? <span className="text-[10px] text-slate-600">{current.games} partidas · {winRate(current.wins, current.games)}%</span> : null}
      </div>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-auto w-full border-white/8 bg-white/4 px-3 py-2.5 text-left shadow-none [&>svg]:hidden">
          <SelectValue>
            {current ? (
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <div className="flex -space-x-3">
                  {current.pokemon.slice(0, 3).map((pokemon) => (
                    <Image key={pokemon.id} src={getSpriteUrl(pokemon.species)} alt="" width={34} height={34} unoptimized className="h-8 w-8 rounded-full border border-slate-700 bg-slate-900 object-contain" />
                  ))}
                </div>
                <div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-white">{current.name}</p><p className="text-[10px] text-slate-500">Versión {formatVersion(current)} {current.demo ? "· muestra" : "· guardada"}</p></div>
                <ChevronDown className="size-4 text-slate-500" />
              </div>
            ) : "Seleccionar equipo"}
          </SelectValue>
        </SelectTrigger>
        <SelectContent className="border-white/10 bg-slate-950 text-slate-200">
          {versions.map((version) => (
            <SelectItem key={version.id} value={version.id} className="py-2.5 focus:bg-white/8 focus:text-white">
              <div className="flex items-center gap-2"><span className="font-semibold">{version.name}</span><span className="text-[10px] text-slate-500">v{formatVersion(version)} · {version.games} G</span></div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
