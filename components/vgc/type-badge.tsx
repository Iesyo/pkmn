import { cn } from "@/lib/utils";
import type { PokemonType } from "@/lib/types";

const typeStyles: Record<PokemonType, string> = {
  Normal: "border-zinc-500/30 bg-zinc-400/15 text-zinc-200",
  Fire: "border-orange-400/30 bg-orange-500/15 text-orange-200",
  Water: "border-sky-400/30 bg-sky-500/15 text-sky-200",
  Electric: "border-yellow-300/30 bg-yellow-400/15 text-yellow-100",
  Grass: "border-emerald-400/30 bg-emerald-500/15 text-emerald-200",
  Ice: "border-cyan-300/30 bg-cyan-400/15 text-cyan-100",
  Fighting: "border-red-400/30 bg-red-500/15 text-red-200",
  Poison: "border-fuchsia-400/30 bg-fuchsia-500/15 text-fuchsia-200",
  Ground: "border-amber-400/30 bg-amber-500/15 text-amber-100",
  Flying: "border-indigo-300/30 bg-indigo-400/15 text-indigo-100",
  Psychic: "border-pink-400/30 bg-pink-500/15 text-pink-200",
  Bug: "border-lime-400/30 bg-lime-500/15 text-lime-200",
  Rock: "border-stone-400/30 bg-stone-500/15 text-stone-200",
  Ghost: "border-violet-400/30 bg-violet-500/15 text-violet-200",
  Dragon: "border-blue-400/30 bg-blue-500/15 text-blue-200",
  Dark: "border-slate-400/30 bg-slate-500/20 text-slate-200",
  Steel: "border-slate-300/30 bg-slate-300/15 text-slate-100",
  Fairy: "border-rose-300/30 bg-rose-400/15 text-rose-100",
};

export function TypeBadge({
  type,
  className,
  children,
}: {
  type: PokemonType;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.08em]",
        typeStyles[type],
        className,
      )}
    >
      {children ?? type}
    </span>
  );
}
