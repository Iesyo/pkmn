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
  championsOverride?: Record<string, unknown>;
}

export interface ShowdownMove {
  name: string;
  type: PokemonType;
  category: "Physical" | "Special" | "Status";
  basePower?: number;
  accuracy?: number | true | null;
  pp?: number;
  priority?: number;
  target?: string;
  flags?: string[];
  desc?: string;
  shortDesc?: string;
  effects?: Record<string, unknown>;
  details?: Record<string, unknown>;
  championsOverride?: Record<string, unknown>;
}

export interface ShowdownAbility {
  name: string;
  desc?: string;
  shortDesc?: string;
  rating?: number | null;
  num?: number | null;
  details?: Record<string, unknown>;
  championsOverride?: Record<string, unknown>;
}

export interface ShowdownItem {
  name: string;
  desc?: string;
  shortDesc?: string;
  details?: Record<string, unknown>;
  championsOverride?: Record<string, unknown>;
}

export interface ShowdownSnapshot {
  metadata: {
    source: string;
    captured: string;
    format: string;
    schema?: number;
    urls: Record<string, string>;
  };
  formats: Record<string, string[]>;
  itemFormats: Record<string, string[]>;
  species: Record<string, ShowdownSpecies>;
  moves: Record<string, ShowdownMove>;
  abilities: Record<string, ShowdownAbility>;
  items: Record<string, ShowdownItem>;
}

const MOVE_EFFECT_KEYS = [
  "boosts", "secondary", "secondaries", "self", "condition", "zMove", "maxMove", "status", "volatileStatus",
  "drain", "recoil", "multihit", "critRatio", "willCrit", "breaksProtect", "hasCrashDamage", "mindBlownRecoil",
  "struggleRecoil", "basePowerCallback", "damageCallback", "damage", "callsMove", "forceSwitch", "selfSwitch",
  "stealsBoosts", "thawsTarget",
] as const;

function validateSnapshot(value: unknown): ShowdownSnapshot {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("La Pokédex de Pokémon Showdown tiene un formato inválido.");
  }
  const snapshot = value as Partial<ShowdownSnapshot>;
  if (
    !snapshot.metadata?.captured ||
    !snapshot.species ||
    !snapshot.moves ||
    !snapshot.items ||
    !snapshot.formats ||
    !snapshot.itemFormats
  ) {
    throw new Error("La Pokédex de Pokémon Showdown está incompleta.");
  }
  return {
    ...snapshot,
    metadata: {
      ...snapshot.metadata,
      schema: snapshot.metadata.schema ?? 2,
    },
    abilities: snapshot.abilities ?? {},
  } as ShowdownSnapshot;
}

async function decodeSnapshotResponse(response: Response) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!bytes.length) throw new Error("La Pokédex de Pokémon Showdown llegó vacía.");
  const text = bytes[0] === 0x1f && bytes[1] === 0x8b
    ? await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text()
    : new TextDecoder().decode(bytes);
  return validateSnapshot(JSON.parse(text));
}

async function readRefreshError(response: Response) {
  try {
    const payload = (await response.json()) as { error?: string };
    return payload.error || "No pudimos actualizar las bases desde Pokémon Showdown.";
  } catch {
    return "No pudimos actualizar las bases desde Pokémon Showdown.";
  }
}

export async function loadShowdownSnapshot({ fresh = false }: { fresh?: boolean } = {}) {
  if (fresh) {
    let response: Response;
    try {
      response = await fetch("/api/showdown-data", {
        method: "POST",
        cache: "no-store",
      });
    } catch {
      throw new Error("No pudimos contactar el actualizador de Pokémon Showdown en el servidor.");
    }
    if (!response.ok) throw new Error(await readRefreshError(response));
    return decodeSnapshotResponse(response);
  }

  try {
    const persisted = await fetch("/api/showdown-data", { cache: "no-store" });
    if (persisted.ok) return await decodeSnapshotResponse(persisted);
  } catch {
    // The bundled snapshot remains the safe fallback if D1 is unavailable.
  }

  const response = await fetch("/data/showdown-dex.json.gz?schema=3", { cache: "force-cache" });
  if (!response.ok) throw new Error("No pudimos cargar la Pokédex de Pokémon Showdown.");
  return decodeSnapshotResponse(response);
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

function championAbilityNames(species: ShowdownSpecies) {
  const abilities = species.championsOverride?.abilities;
  if (!abilities || typeof abilities !== "object" || Array.isArray(abilities)) return [];
  return [...new Set(Object.values(abilities).filter((ability): ability is string => typeof ability === "string"))];
}

export function getLegalAbilities(snapshot: ShowdownSnapshot | null, speciesName: string, format?: string) {
  const species = getSpecies(snapshot, speciesName);
  if (!species) return [];
  const champions = championAbilityNames(species);
  if (format === "champions" && champions.length) return champions;
  if (!format && champions.length) return [...new Set([...species.abilities, ...champions])];
  return species.abilities;
}

export function getLegalItems(snapshot: ShowdownSnapshot | null, format: string) {
  if (!snapshot) return [];
  const itemFormats = snapshot.itemFormats ?? {};
  const items = snapshot.items ?? {};
  return (itemFormats[format] ?? itemFormats.custom ?? [])
    .map((id) => items[id]?.name)
    .filter((name): name is string => Boolean(name))
    .sort((left, right) => left.localeCompare(right));
}

function normalizedFlags(value: unknown, fallback: string[] | undefined) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  return Object.entries(value)
    .filter(([, enabled]) => Boolean(enabled))
    .map(([flag]) => flag)
    .sort();
}

function applyMoveFormat(move: ShowdownMove, format?: string): ShowdownMove {
  if (format !== "champions" || !move.championsOverride) return move;
  const override = move.championsOverride;
  const next: ShowdownMove = { ...move };
  if (typeof override.name === "string") next.name = override.name;
  if (typeof override.type === "string") next.type = override.type as PokemonType;
  if (override.category === "Physical" || override.category === "Special" || override.category === "Status") next.category = override.category;
  if (typeof override.basePower === "number") next.basePower = override.basePower;
  if (override.accuracy === true || typeof override.accuracy === "number") next.accuracy = override.accuracy;
  if (typeof override.pp === "number") next.pp = override.pp;
  if (typeof override.priority === "number") next.priority = override.priority;
  if (typeof override.target === "string") next.target = override.target;
  if (typeof override.desc === "string") next.desc = override.desc;
  if (typeof override.shortDesc === "string") next.shortDesc = override.shortDesc;
  if (override.flags !== undefined) next.flags = normalizedFlags(override.flags, next.flags);
  const effects = { ...(move.effects ?? {}) };
  for (const key of MOVE_EFFECT_KEYS) {
    if (override[key] !== undefined) effects[key] = override[key];
  }
  next.effects = effects;
  return next;
}

function applySimpleFormat<T extends { championsOverride?: Record<string, unknown> }>(entry: T, format?: string): T {
  if (format !== "champions" || !entry.championsOverride) return entry;
  return { ...entry, ...entry.championsOverride, championsOverride: entry.championsOverride } as T;
}

export function getMoveData(snapshot: ShowdownSnapshot | null, moveName: string, format?: string) {
  const move = snapshot?.moves[toId(moveName)] ?? null;
  return move ? applyMoveFormat(move, format) : null;
}

export function getAbilityData(snapshot: ShowdownSnapshot | null, abilityName: string, format?: string) {
  const ability = snapshot?.abilities?.[toId(abilityName)] ?? null;
  return ability ? applySimpleFormat(ability, format) : null;
}

export function getItemData(snapshot: ShowdownSnapshot | null, itemName: string, format?: string) {
  const item = snapshot?.items?.[toId(itemName)] ?? null;
  return item ? applySimpleFormat(item, format) : null;
}

export function formatMoveAccuracy(accuracy: ShowdownMove["accuracy"]) {
  if (accuracy === true) return "—";
  if (typeof accuracy === "number") return `${accuracy}%`;
  return "?";
}

export function moveFromSnapshot(snapshot: ShowdownSnapshot | null, name: string, format?: string): MoveSet {
  const move = getMoveData(snapshot, name, format);
  return {
    name: move?.name ?? name,
    type: move?.type ?? null,
    damaging: move ? move.category !== "Status" : false,
    usage: 0,
  };
}

export function hydrateSetFromSnapshot(snapshot: ShowdownSnapshot, set: PokemonSet, format?: string): PokemonSet {
  const species = getSpecies(snapshot, set.species);
  if (!species) return set;
  return {
    ...set,
    species: species.name,
    types: species.types,
    moves: set.moves.map((move) => moveFromSnapshot(snapshot, move.name, format)),
  };
}

export function isSpeciesAvailable(snapshot: ShowdownSnapshot, speciesName: string, format: string) {
  return (snapshot.formats[format] ?? snapshot.formats.custom ?? []).includes(toId(speciesName));
}

export function isMoveLegal(snapshot: ShowdownSnapshot, speciesName: string, moveName: string, format: string) {
  return getLegalMoveIds(snapshot, speciesName, format).includes(toId(moveName));
}

export function isItemLegal(snapshot: ShowdownSnapshot, itemName: string, format: string) {
  const itemFormats = snapshot.itemFormats ?? {};
  return !itemName || (itemFormats[format] ?? itemFormats.custom ?? []).includes(toId(itemName));
}
