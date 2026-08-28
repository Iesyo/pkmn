import type { MoveSet, PokemonSet, PokemonType } from "./types";
import { toId } from "./pokemon-data";

export type StatId = "hp" | "atk" | "def" | "spa" | "spd" | "spe";
export type BaseStats = Record<StatId, number>;

export interface ShowdownSpecies {
  name: string;
  types: PokemonType[];
  baseStats: BaseStats;
  abilities: string[];
  baseSpecies?: string;
  learnset: Record<string, string>;
  championsMoves: string[];
}

export interface ShowdownMove {
  name: string;
  type: PokemonType;
  category: "Physical" | "Special" | "Status";
}

export interface ShowdownSnapshot {
  metadata: {
    source: string;
    captured: string;
    format: string;
    urls: Record<string, string>;
  };
  formats: Record<string, string[]>;
  species: Record<string, ShowdownSpecies>;
  moves: Record<string, ShowdownMove>;
}

export async function loadShowdownSnapshot() {
  const response = await fetch("/data/showdown-dex.json.gz", { cache: "force-cache" });
  if (!response.ok) throw new Error("No pudimos cargar la Pokédex de Pokémon Showdown.");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!bytes.length) throw new Error("La Pokédex de Pokémon Showdown llegó vacía.");
  const text = bytes[0] === 0x1f && bytes[1] === 0x8b
    ? await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text()
    : new TextDecoder().decode(bytes);
  return JSON.parse(text) as ShowdownSnapshot;
}

export function getSpecies(snapshot: ShowdownSnapshot | null, value: string) {
  return snapshot?.species[toId(value)] ?? null;
}

export function getSpeciesOptions(snapshot: ShowdownSnapshot | null, format: string) {
  if (!snapshot) return [];
  return (snapshot.formats[format] ?? snapshot.formats.custom ?? [])
    .map((id) => snapshot.species[id]?.name)
    .filter((name): name is string => Boolean(name))
    .sort((left, right) => left.localeCompare(right));
}

function inheritedSpeciesData<T>(
  snapshot: ShowdownSnapshot,
  species: ShowdownSpecies,
  select: (entry: ShowdownSpecies) => T,
  hasData: (value: T) => boolean,
) {
  let current: ShowdownSpecies | undefined = species;
  const visited = new Set<string>();
  while (current) {
    const value = select(current);
    if (hasData(value)) return value;
    if (!current.baseSpecies || visited.has(current.baseSpecies)) break;
    visited.add(current.baseSpecies);
    current = snapshot.species[current.baseSpecies];
  }
  return select(species);
}

export function getLegalMoveIds(snapshot: ShowdownSnapshot | null, speciesName: string, format: string) {
  const species = getSpecies(snapshot, speciesName);
  if (!snapshot || !species) return [];
  if (format === "champions") {
    return inheritedSpeciesData(snapshot, species, (entry) => entry.championsMoves, (moves) => moves.length > 0);
  }
  const generation = format.match(/^gen([6-9])$/)?.[1] ?? "9";
  const learnset = inheritedSpeciesData(
    snapshot,
    species,
    (entry) => entry.learnset,
    (moves) => Object.keys(moves).length > 0,
  );
  return Object.entries(learnset)
    .filter(([, availability]) => availability.includes(generation))
    .map(([moveId]) => moveId);
}

export function getLegalMoves(snapshot: ShowdownSnapshot | null, speciesName: string, format: string) {
  if (!snapshot) return [];
  return getLegalMoveIds(snapshot, speciesName, format)
    .map((id) => snapshot.moves[id]?.name)
    .filter((name): name is string => Boolean(name))
    .sort((left, right) => left.localeCompare(right));
}

export function getLegalAbilities(snapshot: ShowdownSnapshot | null, speciesName: string) {
  return getSpecies(snapshot, speciesName)?.abilities ?? [];
}

export function moveFromSnapshot(snapshot: ShowdownSnapshot | null, name: string): MoveSet {
  const move = snapshot?.moves[toId(name)];
  return {
    name: move?.name ?? name,
    type: move?.type ?? null,
    damaging: move ? move.category !== "Status" : false,
    usage: 0,
  };
}

export function hydrateSetFromSnapshot(snapshot: ShowdownSnapshot, set: PokemonSet): PokemonSet {
  const species = getSpecies(snapshot, set.species);
  if (!species) return set;
  return {
    ...set,
    species: species.name,
    types: species.types,
    moves: set.moves.map((move) => moveFromSnapshot(snapshot, move.name)),
  };
}

export function isSpeciesAvailable(snapshot: ShowdownSnapshot, speciesName: string, format: string) {
  return (snapshot.formats[format] ?? snapshot.formats.custom ?? []).includes(toId(speciesName));
}

export function isMoveLegal(snapshot: ShowdownSnapshot, speciesName: string, moveName: string, format: string) {
  return getLegalMoveIds(snapshot, speciesName, format).includes(toId(moveName));
}
