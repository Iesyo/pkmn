import { getMoveData, getSpeciesTypes } from "./pokemon-data";
import type { BaseStats, StatId } from "./showdown-data";
import type { BattleMechanic, PokemonSet, PokemonType, TeamVersion } from "./types";

export const DEFAULT_BATTLE_FORMAT = "champions";
export const DEFAULT_BATTLE_MECHANICS: BattleMechanic[] = ["mega"];

export const BATTLE_FORMATS = [
  { id: "champions", label: "Pokémon Champions · Mega Evolution", mechanics: ["mega"] },
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

export const NATURES = [
  "Adamant", "Bashful", "Bold", "Brave", "Calm", "Careful", "Docile", "Gentle", "Hardy",
  "Hasty", "Impish", "Jolly", "Lax", "Lonely", "Mild", "Modest", "Naive", "Naughty",
  "Quiet", "Quirky", "Rash", "Relaxed", "Sassy", "Serious", "Timid",
];
export const EV_STATS = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"] as const;

export const STAT_IDS: Record<(typeof EV_STATS)[number], StatId> = {
  HP: "hp", Atk: "atk", Def: "def", SpA: "spa", SpD: "spd", Spe: "spe",
};

const NATURE_EFFECTS: Record<string, { plus?: StatId; minus?: StatId }> = {
  Adamant: { plus: "atk", minus: "spa" }, Bold: { plus: "def", minus: "atk" },
  Brave: { plus: "atk", minus: "spe" }, Calm: { plus: "spd", minus: "atk" },
  Careful: { plus: "spd", minus: "spa" }, Gentle: { plus: "spd", minus: "def" },
  Hasty: { plus: "spe", minus: "def" }, Impish: { plus: "def", minus: "spa" },
  Jolly: { plus: "spe", minus: "spa" }, Lax: { plus: "def", minus: "spd" },
  Lonely: { plus: "atk", minus: "def" }, Mild: { plus: "spa", minus: "def" },
  Modest: { plus: "spa", minus: "atk" }, Naive: { plus: "spe", minus: "spd" },
  Naughty: { plus: "atk", minus: "spd" }, Quiet: { plus: "spa", minus: "spe" },
  Rash: { plus: "spa", minus: "spd" }, Relaxed: { plus: "def", minus: "spe" },
  Sassy: { plus: "spd", minus: "spe" }, Timid: { plus: "spe", minus: "atk" },
};

export function getStatRules(format: string) {
  return format === "champions"
    ? { label: "Stat Points", shortLabel: "SP", perStatMax: 32, totalMax: 66, step: 1 }
    : { label: "EVs", shortLabel: "EV", perStatMax: 252, totalMax: 510, step: 4 };
}

export function calculateStat(baseStats: BaseStats, stat: (typeof EV_STATS)[number], allocation: number, level: number, nature: string, format: string) {
  const id = STAT_IDS[stat];
  const contribution = format === "champions"
    ? Math.max(2 * allocation - 1, 0)
    : Math.floor(allocation / 4);
  const core = Math.floor((2 * baseStats[id] + 31 + contribution) * level / 100);
  if (id === "hp") return core + level + 10;
  const neutral = core + 5;
  const effect = NATURE_EFFECTS[nature] ?? {};
  if (effect.plus === id) return Math.floor(neutral * 1.1);
  if (effect.minus === id) return Math.floor(neutral * 0.9);
  return neutral;
}

export function getNatureEffect(nature: string, stat: (typeof EV_STATS)[number]) {
  const id = STAT_IDS[stat];
  const effect = NATURE_EFFECTS[nature] ?? {};
  return effect.plus === id ? "plus" : effect.minus === id ? "minus" : "neutral";
}

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
