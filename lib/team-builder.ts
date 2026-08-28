import { getMoveData, getSpeciesTypes } from "./pokemon-data";
import type { BattleMechanic, PokemonSet, PokemonType, TeamVersion } from "./types";

export const BATTLE_FORMATS = [
  { id: "gen9", label: "Gen 9 · Terastallization", mechanics: ["tera"] },
  { id: "gen8", label: "Gen 8 · Dynamax", mechanics: ["dynamax"] },
  { id: "gen7", label: "Gen 7 · Mega + Z-Moves", mechanics: ["mega", "zmove"] },
  { id: "gen6", label: "Gen 6 · Mega Evolution", mechanics: ["mega"] },
  { id: "custom", label: "Formato personalizado", mechanics: [] },
] as const satisfies ReadonlyArray<{ id: string; label: string; mechanics: BattleMechanic[] }>;

export const MECHANIC_LABELS: Record<BattleMechanic, string> = {
  tera: "Tera",
  dynamax: "Dynamax",
  mega: "Mega",
  zmove: "Z-Move",
};

export const NATURES = ["Adamant", "Bold", "Brave", "Calm", "Careful", "Impish", "Jolly", "Modest", "Quiet", "Relaxed", "Sassy", "Timid"];
export const EV_STATS = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"] as const;

export function formatVersion(version: Pick<TeamVersion, "version" | "minorVersion">) {
  return version.minorVersion ? `${version.version}.${String(version.minorVersion).padStart(2, "0")}` : String(version.version);
}

export function emptyPokemon(slot: number): PokemonSet {
  return {
    id: `builder-${slot}`,
    slot,
    nickname: "",
    species: "",
    item: "",
    ability: "",
    level: 50,
    teraType: null,
    mechanics: { dynamaxLevel: 10, gigantamax: false, megaEvolution: false, zMove: false },
    evs: "",
    nature: "",
    moves: Array.from({ length: 4 }, () => ({ name: "", type: null, damaging: false, usage: 0 })),
    types: [],
    performance: { games: 0, wins: 0, leadGames: 0, leadWins: 0, selectionRate: 0 },
  };
}

export function cloneForBuilder(pokemon: PokemonSet[]) {
  return Array.from({ length: 6 }, (_, index) => {
    const source = pokemon[index];
    if (!source) return emptyPokemon(index + 1);
    return {
      ...source,
      id: `builder-${index + 1}`,
      slot: index + 1,
      mechanics: { ...source.mechanics },
      moves: Array.from({ length: 4 }, (_, moveIndex) => source.moves[moveIndex] ? { ...source.moves[moveIndex], usage: 0 } : { name: "", type: null, damaging: false, usage: 0 }),
      performance: { games: 0, wins: 0, leadGames: 0, leadWins: 0, selectionRate: 0 },
    };
  });
}

export function updateSpecies(set: PokemonSet, species: string): PokemonSet {
  return { ...set, species, nickname: !set.nickname || set.nickname === set.species ? species : set.nickname, types: getSpeciesTypes(species) };
}

export function updateMove(set: PokemonSet, index: number, name: string): PokemonSet {
  const data = getMoveData(name);
  return { ...set, moves: set.moves.map((move, moveIndex) => moveIndex === index ? { name, type: data.type, damaging: data.damaging, usage: 0 } : move) };
}

export function parseEvs(evs: string) {
  const result = Object.fromEntries(EV_STATS.map((stat) => [stat, 0])) as Record<(typeof EV_STATS)[number], number>;
  for (const chunk of evs.split("/")) {
    const match = chunk.trim().match(/^(\d+)\s+(HP|Atk|Def|SpA|SpD|Spe)$/i);
    if (match) {
      const canonical = EV_STATS.find((stat) => stat.toLowerCase() === match[2].toLowerCase());
      if (canonical) result[canonical] = Number(match[1]);
    }
  }
  return result;
}

export function serializeEvs(evs: Record<string, number>) {
  return EV_STATS.filter((stat) => evs[stat] > 0).map((stat) => `${evs[stat]} ${stat}`).join(" / ");
}

export function serializeShowdownPaste(pokemon: PokemonSet[], mechanics: BattleMechanic[]) {
  return pokemon.map((set) => {
    const identity = set.nickname && set.nickname !== set.species ? `${set.nickname} (${set.species})` : set.species;
    const lines = [`${identity}${set.item ? ` @ ${set.item}` : ""}`];
    if (set.ability) lines.push(`Ability: ${set.ability}`);
    lines.push(`Level: ${set.level || 50}`);
    if (mechanics.includes("tera") && set.teraType) lines.push(`Tera Type: ${set.teraType}`);
    if (mechanics.includes("dynamax")) {
      lines.push(`Dynamax Level: ${set.mechanics?.dynamaxLevel ?? 10}`);
      if (set.mechanics?.gigantamax) lines.push("Gigantamax: Yes");
    }
    if (set.evs) lines.push(`EVs: ${set.evs}`);
    if (set.nature) lines.push(`${set.nature} Nature`);
    lines.push(...set.moves.filter((move) => move.name).map((move) => `- ${move.name}`));
    return lines.join("\n");
  }).join("\n\n");
}

export function isCompleteTeam(pokemon: PokemonSet[]) {
  return pokemon.length === 6 && pokemon.every((set) => set.species.trim() && set.moves.filter((move) => move.name.trim()).length === 4);
}

export function normalizeMechanics(values: string[]): BattleMechanic[] {
  const allowed: BattleMechanic[] = ["tera", "dynamax", "mega", "zmove"];
  return [...new Set(values.filter((value): value is BattleMechanic => allowed.includes(value as BattleMechanic)))];
}

export function normalizeTeraType(value: string): PokemonType | null {
  return value ? value as PokemonType : null;
}
