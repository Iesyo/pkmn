import { CHAMPIONS_REGULATION } from "./champions-regulation.mjs";
import { calculateDamage, createDamageDraft, defaultDamageField } from "./damage-calculator";
import type { OpponentMetaResponse, OpponentMetaPreset } from "./opponent-meta-presets";
import { toId } from "./pokemon-data";
import {
  getItemData,
  getLegalAbilities,
  getLegalItems,
  getLegalMoves,
  getMoveData,
  getSpecies,
  hydrateSetFromSnapshot,
  isItemLegal,
  isMoveLegal,
  isSpeciesAvailable,
  type ShowdownSnapshot,
} from "./showdown-data";
import { EV_STATS, NATURES, getStatRules, parseEvs } from "./team-builder";
import { effectiveness, POKEMON_TYPES } from "./type-chart";
import type { PokemonSet, PokemonType } from "./types";
import type { ScoutingPasteSummary } from "./scouting-paste-library";
import type { TournamentScoutingResponse } from "./tournament-scouting";
import {
  buildVgcPastesSheetUrl,
  type VgcPastesFormat,
  type VgcPastesTeam,
} from "./vgcpastes-scouting";
import { getVgcPastesScoutingSpeciesIdentity } from "./vgcpastes-scouting-search";
import type {
  WarRoomPasteEvidenceTeam,
  WarRoomPasteSource,
} from "./war-room-paste-evidence";
import {
  WAR_ROOM_CURRENT_FORMAT_ID,
  getWarRoomRegulationEvidence,
  inferWarRoomRegulationEvidence,
} from "./war-room-regulations";

export const WAR_ROOM_SCHEMA_VERSION = 3;
export const WAR_ROOM_FORMAT_ID = WAR_ROOM_CURRENT_FORMAT_ID;
export const WAR_ROOM_BATTLE_FORMAT = "champions";
export const MAX_WAR_ROOM_CORPUS_TEAMS = 5_000;
export const MAX_WAR_ROOM_HISTORICAL_TEAMS = 5_000;
export const MAX_WAR_ROOM_TEAM_MEGAS = 2;
export const MAX_WAR_ROOM_LOCKED_IDENTITIES = 6;
export const MAX_WAR_ROOM_MEMBER_SUGGESTIONS_PER_SLOT = 4;
export const MAX_WAR_ROOM_MEMBER_SUGGESTIONS = 20;

export type WarRoomEvidenceScope = "exact-set" | "team-preview" | "corpus";
export type WarRoomSeverity = "blocker" | "high" | "medium" | "low";

export interface WarRoomCorpusTeam {
  id: string;
  source: WarRoomPasteSource;
  savedPasteId: string;
  playerName: string;
  tournament: string;
  rank: string;
  dateShared: string;
  pokepasteUrl: string;
  pokemon: string[];
  formatId: string;
  formatLabel: string;
  regulationWeight: number;
  historical: boolean;
  setEvidenceEligible: boolean;
}

export interface WarRoomHistoricalCorpusSource {
  format: VgcPastesFormat;
  teams: VgcPastesTeam[];
  fetchedAt: string;
}

export interface WarRoomCorpusResponse {
  schemaVersion: typeof WAR_ROOM_SCHEMA_VERSION;
  regulation: typeof CHAMPIONS_REGULATION;
  format: VgcPastesFormat;
  fetchedAt: string;
  totalTeams: number;
  publicTeamCount: number;
  vgcPastesTeamCount: number;
  tournamentTeamCount: number;
  savedTeamCount: number;
  historicalTeamCount: number;
  historicalFormats: Array<{
    formatId: string;
    formatLabel: string;
    regulationWeight: number;
    teamCount: number;
  }>;
  source: {
    label: "VGCPastes Repository";
    url: string;
  };
  teams: WarRoomCorpusTeam[];
  historicalTeams: WarRoomCorpusTeam[];
}

export interface WarRoomLegalityIssue {
  id: string;
  severity: "blocker" | "warning";
  subject: string;
  detail: string;
}

export interface WarRoomThreat {
  species: string;
  score: number;
  tier: "crítica" | "alta" | "media";
  appearances: number;
  usageRate: number;
  weakTargets: string[];
  answers: string[];
  switchIns: string[];
  reasons: string[];
}

export interface WarRoomCoreThreat {
  id: string;
  species: [string, string];
  score: number;
  appearances: number;
  usageRate: number;
  pressuredTargets: string[];
  sharedAnswers: string[];
  reasons: string[];
}

export interface WarRoomGap {
  id: string;
  severity: Exclude<WarRoomSeverity, "blocker">;
  title: string;
  detail: string;
  evidence: string;
}

export interface WarRoomAuditResult {
  regulation: typeof CHAMPIONS_REGULATION;
  evidenceScope: WarRoomEvidenceScope[];
  legality: {
    status: "legal" | "review" | "blocked";
    issues: WarRoomLegalityIssue[];
    blockers: number;
    warnings: number;
  };
  threats: WarRoomThreat[];
  cores: WarRoomCoreThreat[];
  gaps: WarRoomGap[];
  summary: {
    corpusTeams: number;
    criticalThreats: number;
    highThreats: number;
    unresolvedThreats: number;
    unknownCorpusSpecies: number;
  };
  notes: string[];
}

export interface WarRoomMatchupPick {
  id: string;
  slot: number;
  species: string;
  fit: number;
  offense: number;
  safety: number;
  speed: number;
  utility: number;
  covers: string[];
  pressuredBy: string[];
  roles: string[];
  damage: Array<{
    target: string;
    move: string;
    maxPercent: number;
  }>;
  incomingDamage: Array<{
    source: string;
    move: string;
    maxPercent: number;
  }>;
}

export interface WarRoomMatchupPlan {
  id: string;
  label: string;
  intent: string;
  fit: number;
  lead: string[];
  backline: string[];
  bench: string[];
  reasons: string[];
}

export interface WarRoomMatchupResult {
  regulation: typeof CHAMPIONS_REGULATION;
  evidenceScope: "exact-set" | "team-preview";
  rival: WarRoomCorpusTeam;
  recommended: WarRoomMatchupPlan | null;
  alternatives: WarRoomMatchupPlan[];
  picks: WarRoomMatchupPick[];
  notes: string[];
}

export interface WarRoomMemberSuggestion {
  species: string;
  observedAs: string;
  isMega: boolean;
  score: number;
  appearancesWithCore: number;
  sampleSize: number;
  usageRate: number;
  replaces: string;
  replacesSetId: string;
  patchedTypes: PokemonType[];
  evidenceMode: "core" | "historical" | "expanded";
  evidenceRegulations: string[];
  currentAppearances: number;
  historicalAppearances: number;
  reasons: string[];
}

export function groupWarRoomMemberSuggestions(members: readonly WarRoomMemberSuggestion[]): WarRoomMemberSuggestion[][] {
  const groups = new Map<string, WarRoomMemberSuggestion[]>();
  for (const member of members) {
    const options = groups.get(member.species) ?? [];
    options.push(member);
    groups.set(member.species, options);
  }
  return [...groups.values()];
}

export interface WarRoomMemberApplicationResult {
  pokemon: PokemonSet[];
  setSource: "observed-paste" | "observed-paste-patched" | "battle-data-fallback" | "legal-fallback";
  presetId: string | null;
  evidenceTeamId: string | null;
}

export interface WarRoomOptimizationOptions {
  excludedMemberSpecies?: readonly string[];
  pasteEvidence?: readonly WarRoomPasteEvidenceTeam[];
  historicalCorpus?: readonly WarRoomCorpusTeam[];
}

export type WarRoomMoveSlot = 0 | 1 | 2 | 3;

export type WarRoomLockField =
  | "identity"
  | "item"
  | "ability"
  | "nature"
  | "statPoints"
  | `move-${WarRoomMoveSlot}`;

export interface WarRoomPokemonLocks {
  identity: boolean;
  item: boolean;
  ability: boolean;
  nature: boolean;
  statPoints: boolean;
  moves: [boolean, boolean, boolean, boolean];
}

export type WarRoomPokemonLockInput = Partial<Omit<WarRoomPokemonLocks, "moves">> & {
  moves?: readonly boolean[];
};

export type WarRoomOptimizationLocks = Record<string, WarRoomPokemonLocks>;
export type WarRoomOptimizationLockInput = Iterable<string> | Record<string, WarRoomPokemonLockInput>;

export interface WarRoomLockedField {
  key: WarRoomLockField;
  label: string;
  value: string;
}

export interface WarRoomPokemonLockSummary {
  setId: string;
  species: string;
  fields: WarRoomLockedField[];
  fullyLocked: boolean;
}

export interface WarRoomSetChange {
  key: Exclude<WarRoomLockField, "identity">;
  field: string;
  current: string;
  suggested: string;
  evidence: number | null;
  source: "paste" | "battle-data";
}

export interface WarRoomSetSuggestion {
  setId: string;
  species: string;
  presetId: string;
  structuralDelta: number;
  proposal: {
    item: string;
    ability: string;
    nature: string;
    evs: string;
    moves: string[];
  };
  changes: WarRoomSetChange[];
  preservedFields: WarRoomLockedField[];
  reasons: string[];
  methodology: "observed-paste" | "observed-paste-patched" | "battle-data-fallback";
  source: {
    teamId: string;
    label: string;
    url: string;
    tournament: string;
    rank: string;
    observations: number;
    contextFit: number;
    formatLabel: string;
    historical: boolean;
  } | null;
  patchedFields: Array<Exclude<WarRoomLockField, "identity">>;
}

export interface WarRoomOptimizationResult {
  regulation: typeof CHAMPIONS_REGULATION;
  lockedSpecies: string[];
  megaPolicy: {
    configured: number;
    maximum: typeof MAX_WAR_ROOM_TEAM_MEGAS;
    recommendationSlots: number;
  };
  coreSample: {
    size: number;
    mode: "exact" | "partial" | "none";
  };
  pasteEvidence: {
    loadedTeams: number;
    matchedSets: number;
    mode: "observed" | "patched" | "fallback" | "unavailable";
  };
  members: WarRoomMemberSuggestion[];
  sets: WarRoomSetSuggestion[];
  locks: WarRoomPokemonLockSummary[];
  notes: string[];
}

type CombatProfile = {
  id: string;
  slot: number;
  species: string;
  types: PokemonType[];
  attackTypes: PokemonType[];
  attacks: Array<{ name: string; type: PokemonType }>;
  speed: number | null;
  roles: string[];
  set: PokemonSet | null;
  megaActive: boolean;
};

type SpeciesUsage = {
  key: string;
  species: string;
  appearances: number;
};

const ABILITY_IMMUNITIES: Record<string, PokemonType> = {
  levitate: "Ground",
  flashfire: "Fire",
  waterabsorb: "Water",
  stormdrain: "Water",
  dryskin: "Water",
  lightningrod: "Electric",
  motordrive: "Electric",
  voltabsorb: "Electric",
  sapsipper: "Grass",
};

const ROLE_MOVES: Record<string, Set<string>> = {
  "Control de velocidad": new Set([
    "tailwind", "trickroom", "icywind", "electroweb", "thunderwave", "nuzzle",
    "bulldoze", "scaryface", "quash", "afteryou",
  ]),
  "Control de posición": new Set([
    "fakeout", "followme", "ragepowder", "wideguard", "quickguard", "allyswitch",
  ]),
  "Disrupción": new Set([
    "taunt", "encore", "spore", "hypnosis", "willowisp", "snarl", "partingshot",
    "disable", "haze", "clearsmog",
  ]),
  "Pivot": new Set(["uturn", "voltswitch", "partingshot", "flipturn", "chillyreception"]),
  "Protección": new Set([
    "protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap",
    "burningbulwark", "obstruct",
  ]),
};

const SEVERITY_ORDER: Record<WarRoomGap["severity"], number> = {
  high: 0,
  medium: 1,
  low: 2,
};

const MOVE_SLOTS = [0, 1, 2, 3] as const;

export function createWarRoomPokemonLocks(identity = false): WarRoomPokemonLocks {
  return {
    identity,
    item: false,
    ability: false,
    nature: false,
    statPoints: false,
    moves: [false, false, false, false],
  };
}

function isLegacyLockInput(value: WarRoomOptimizationLockInput): value is Iterable<string> {
  return typeof (value as Iterable<string>)[Symbol.iterator] === "function";
}

function normalizeOptimizationLocks(
  team: PokemonSet[],
  input: WarRoomOptimizationLockInput,
): WarRoomOptimizationLocks {
  const normalized: WarRoomOptimizationLocks = {};
  if (isLegacyLockInput(input)) {
    const identities = new Set(input);
    let identityCount = 0;
    for (const set of team) {
      if (identities.has(set.id) && identityCount < MAX_WAR_ROOM_LOCKED_IDENTITIES) {
        normalized[set.id] = createWarRoomPokemonLocks(true);
        identityCount += 1;
      }
    }
    return normalized;
  }

  let identityCount = 0;
  for (const set of team) {
    const source = input[set.id];
    if (!source) continue;
    const locks: WarRoomPokemonLocks = {
      identity: source.identity === true && identityCount < MAX_WAR_ROOM_LOCKED_IDENTITIES,
      item: source.item === true,
      ability: source.ability === true,
      nature: source.nature === true,
      statPoints: source.statPoints === true,
      moves: [
        source.moves?.[0] === true,
        source.moves?.[1] === true,
        source.moves?.[2] === true,
        source.moves?.[3] === true,
      ],
    };
    if (locks.identity) identityCount += 1;
    if (locks.identity || locks.item || locks.ability || locks.nature || locks.statPoints || locks.moves.some(Boolean)) {
      normalized[set.id] = locks;
    }
  }
  return normalized;
}

function locksForSet(locks: WarRoomOptimizationLocks, setId: string) {
  return locks[setId] ?? createWarRoomPokemonLocks();
}

function lockFieldsForSet(set: PokemonSet, locks: WarRoomPokemonLocks): WarRoomLockedField[] {
  const fields: WarRoomLockedField[] = [];
  if (locks.identity) fields.push({ key: "identity", label: "Identidad", value: set.species });
  if (locks.item) fields.push({ key: "item", label: "Objeto", value: set.item || "Sin objeto" });
  if (locks.ability) fields.push({ key: "ability", label: "Habilidad", value: set.ability || "Sin declarar" });
  if (locks.nature) fields.push({ key: "nature", label: "Naturaleza", value: set.nature || "Sin declarar" });
  if (locks.statPoints) fields.push({ key: "statPoints", label: "Stat Points", value: set.evs || "0" });
  for (const slot of MOVE_SLOTS) {
    if (!locks.moves[slot]) continue;
    fields.push({
      key: `move-${slot}`,
      label: `Movimiento ${slot + 1}`,
      value: set.moves[slot]?.name || "Vacío",
    });
  }
  return fields;
}

function isSetFullyLocked(locks: WarRoomPokemonLocks) {
  return locks.item && locks.ability && locks.nature && locks.statPoints && locks.moves.every(Boolean);
}

function clamp(value: number, minimum = 0, maximum = 100) {
  return Math.min(maximum, Math.max(minimum, value));
}

function round(value: number, digits = 0) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function unique<T>(values: T[]) {
  return [...new Set(values)];
}

function combinations<T>(values: T[], size: number) {
  const result: T[][] = [];
  function visit(start: number, current: T[]) {
    if (current.length === size) {
      result.push([...current]);
      return;
    }
    for (let index = start; index <= values.length - (size - current.length); index += 1) {
      current.push(values[index]);
      visit(index + 1, current);
      current.pop();
    }
  }
  visit(0, []);
  return result;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function baseSpeciesLabel(species: string) {
  return getVgcPastesScoutingSpeciesIdentity(species) || species;
}

function baseSpeciesKey(species: string) {
  return toId(baseSpeciesLabel(species));
}

function isMegaSpeciesLabel(species: string) {
  return /-Mega(?:-[A-Za-z0-9]+)?$/i.test(species.trim());
}

function normalizedTeamSpecies(team: WarRoomCorpusTeam) {
  const seen = new Set<string>();
  return team.pokemon.filter((species) => {
    const key = baseSpeciesKey(species);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function statisticalCorpus(teams: WarRoomCorpusTeam[]) {
  const publicTeams = teams.filter((team) => team.source === "vgcpastes" || team.source === "tournament");
  return publicTeams.length ? publicTeams : teams;
}

function resolveSetSpecies(snapshot: ShowdownSnapshot, set: PokemonSet) {
  if (toId(set.species) === "rayquaza" && set.moves.some((move) => toId(move.name) === "dragonascent")) {
    return "Rayquaza-Mega";
  }
  const item = getItemData(snapshot, set.item, WAR_ROOM_BATTLE_FORMAT);
  const megaStone = recordValue(item?.details?.megaStone);
  if (!megaStone) return set.species;
  const match = Object.entries(megaStone).find(([species]) => toId(species) === toId(set.species));
  return typeof match?.[1] === "string" ? match[1] : set.species;
}

function itemForObservedMega(
  snapshot: ShowdownSnapshot,
  species: string,
  observedAs: string,
) {
  if (!isMegaSpeciesLabel(observedAs)) return "";
  const speciesKey = toId(species);
  const observedKey = toId(observedAs);
  for (const itemId of snapshot.itemFormats[WAR_ROOM_BATTLE_FORMAT] ?? []) {
    const item = snapshot.items[itemId];
    const megaStone = recordValue(item?.details?.megaStone);
    if (!item || !megaStone) continue;
    const matches = Object.entries(megaStone).some(([baseSpecies, megaSpecies]) => (
      toId(baseSpecies) === speciesKey
      && typeof megaSpecies === "string"
      && toId(megaSpecies) === observedKey
    ));
    if (matches) return item.name;
  }
  return "";
}

const MEMBER_FALLBACK_ITEMS = [
  "Focus Sash",
  "Sitrus Berry",
  "Life Orb",
  "Safety Goggles",
  "Clear Amulet",
  "Covert Cloak",
  "Assault Vest",
  "Leftovers",
  "Choice Scarf",
  "Choice Band",
  "Choice Specs",
] as const;

const MEMBER_FALLBACK_SUPPORT_MOVES = [
  "Protect",
  "Fake Out",
  "Tailwind",
  "Trick Room",
  "Follow Me",
  "Rage Powder",
  "Spore",
  "Wide Guard",
  "Helping Hand",
  "Encore",
  "Taunt",
  "Will-O-Wisp",
  "Icy Wind",
] as const;

function availableMemberItem(
  team: PokemonSet[],
  targetId: string,
  snapshot: ShowdownSnapshot,
  preferred = "",
) {
  const used = new Set(team.filter((set) => set.id !== targetId).map((set) => toId(set.item)).filter(Boolean));
  const legalItems = getLegalItems(snapshot, WAR_ROOM_BATTLE_FORMAT);
  const legalByKey = new Map(legalItems.map((item) => [toId(item), item]));
  const candidates = [preferred, ...MEMBER_FALLBACK_ITEMS, ...legalItems];
  for (const candidate of candidates) {
    const item = legalByKey.get(toId(candidate));
    if (!item || used.has(toId(item))) continue;
    const details = recordValue(getItemData(snapshot, item, WAR_ROOM_BATTLE_FORMAT)?.details);
    if (recordValue(details?.megaStone)) continue;
    return item;
  }
  return "";
}

function fallbackMemberProposal(
  team: PokemonSet[],
  targetId: string,
  snapshot: ShowdownSnapshot,
  species: string,
  observedAs: string,
) {
  const battleSpecies = getSpecies(snapshot, observedAs, WAR_ROOM_BATTLE_FORMAT)
    ?? getSpecies(snapshot, species, WAR_ROOM_BATTLE_FORMAT);
  const preferredCategory = (battleSpecies?.baseStats.atk ?? 0) >= (battleSpecies?.baseStats.spa ?? 0)
    ? "Physical"
    : "Special";
  const legalMoves = getLegalMoves(snapshot, species, WAR_ROOM_BATTLE_FORMAT);
  const supportRanks = new Map(MEMBER_FALLBACK_SUPPORT_MOVES.map((move, index) => [toId(move), index]));
  const requiredMove = toId(species) === "rayquaza" && toId(observedAs) === "rayquazamega"
    ? "Dragon Ascent"
    : "";
  const rankedMoves = legalMoves.map((name) => {
    const data = getMoveData(snapshot, name, WAR_ROOM_BATTLE_FORMAT);
    const supportRank = supportRanks.get(toId(name));
    const damaging = data?.category === "Physical" || data?.category === "Special";
    const stab = Boolean(data?.type && battleSpecies?.types.includes(data.type));
    const basePower = typeof data?.basePower === "number" ? data.basePower : 0;
    const accuracy = data?.accuracy === true ? 100 : typeof data?.accuracy === "number" ? data.accuracy : 0;
    const slowPenalty = Math.min(0, data?.priority ?? 0) * 120;
    const twoTurnPenalty = data?.flags?.some((flag) => flag === "charge" || flag === "recharge") ? 180 : 0;
    const damageScore = damaging
      ? 400 + (data?.category === preferredCategory ? 180 : 0) + (stab ? 250 : 0)
        + basePower * Math.max(0.5, accuracy / 100) + slowPenalty - twoTurnPenalty
        + Math.max(0, data?.priority ?? 0) * 20
      : 0;
    const score = toId(name) === toId(requiredMove)
      ? 2_000
      : supportRank !== undefined
        ? 1_200 - supportRank * 10
        : damaging ? damageScore : 100;
    return { name, data, score, damageScore, supportRank };
  }).sort((left, right) => right.score - left.score || left.name.localeCompare(right.name));
  const selectedMoves: typeof rankedMoves = [];
  const selectedKeys = new Set<string>();
  const addMove = (entry: (typeof rankedMoves)[number] | undefined) => {
    if (!entry || selectedMoves.length >= 4 || selectedKeys.has(toId(entry.name))) return;
    selectedMoves.push(entry);
    selectedKeys.add(toId(entry.name));
  };
  addMove(rankedMoves.find((entry) => toId(entry.name) === toId(requiredMove)));
  const damagingMoves = rankedMoves
    .filter((entry) => entry.damageScore > 0)
    .sort((left, right) => (
      Number(right.data?.category === preferredCategory) - Number(left.data?.category === preferredCategory)
      || right.damageScore - left.damageScore
      || left.name.localeCompare(right.name)
    ));
  while (selectedMoves.filter((entry) => entry.damageScore > 0).length < Math.min(2, damagingMoves.length)) {
    const selectedTypes = new Set(selectedMoves.map((entry) => entry.data?.type).filter(Boolean));
    addMove(damagingMoves.find((entry) => !selectedKeys.has(toId(entry.name)) && !selectedTypes.has(entry.data?.type))
      ?? damagingMoves.find((entry) => !selectedKeys.has(toId(entry.name))));
  }
  for (const entry of rankedMoves.filter((move) => move.supportRank !== undefined)) addMove(entry);
  for (const entry of rankedMoves) addMove(entry);
  const moves = selectedMoves.map((entry) => entry.name);

  const selectedData = moves.map((move) => getMoveData(snapshot, move, WAR_ROOM_BATTLE_FORMAT));
  const physicalPower = selectedData.reduce((sum, move) => sum + (move?.category === "Physical" ? move.basePower ?? 0 : 0), 0);
  const specialPower = selectedData.reduce((sum, move) => sum + (move?.category === "Special" ? move.basePower ?? 0 : 0), 0);
  const physical = physicalPower >= specialPower;
  const trickRoom = moves.some((move) => toId(move) === "trickroom");
  const fast = (battleSpecies?.baseStats.spe ?? 0) >= 80 && !trickRoom;
  const offense = physical ? "Atk" : "SpA";
  const nature = trickRoom
    ? physical ? "Brave" : "Quiet"
    : fast
      ? physical ? "Jolly" : "Timid"
      : physical ? "Adamant" : "Modest";
  const evs = fast ? `2 HP / 32 ${offense} / 32 Spe` : `32 HP / 32 ${offense} / 2 SpD`;
  const megaItem = itemForObservedMega(snapshot, species, observedAs);
  return {
    item: megaItem || availableMemberItem(team, targetId, snapshot),
    ability: getLegalAbilities(snapshot, species, WAR_ROOM_BATTLE_FORMAT)[0] ?? "",
    nature,
    evs,
    moves,
  } satisfies WarRoomSetProposal;
}

/**
 * Builds a complete replacement for a disposable War Room draft. Observed
 * paste bundles are preferred, Battle Data only fills missing fields, and a
 * deterministic legal baseline prevents a selected partner from becoming an
 * empty card when upstream evidence is unavailable.
 */
export function buildWarRoomMemberReplacement(
  team: PokemonSet[],
  suggestion: WarRoomMemberSuggestion,
  snapshot: ShowdownSnapshot,
  presets: readonly OpponentMetaPreset[] = [],
  pasteEvidence: readonly WarRoomPasteEvidenceTeam[] = [],
): WarRoomMemberApplicationResult {
  const targetIndex = team.findIndex((set) => set.id === suggestion.replacesSetId);
  if (targetIndex < 0) return { pokemon: team, setSource: "legal-fallback", presetId: null, evidenceTeamId: null };
  const species = baseSpeciesLabel(suggestion.species);
  const speciesKey = baseSpeciesKey(species);
  if (team.some((set, index) => index !== targetIndex && baseSpeciesKey(set.species) === speciesKey)) {
    return { pokemon: team, setSource: "legal-fallback", presetId: null, evidenceTeamId: null };
  }

  const source = team[targetIndex];
  const rayquazaMega = toId(species) === "rayquaza" && toId(suggestion.observedAs) === "rayquazamega";
  const megaItem = itemForObservedMega(snapshot, species, suggestion.observedAs);
  const shell = hydrateSetFromSnapshot(snapshot, {
    ...source,
    nickname: species,
    species,
    item: "",
    ability: "",
    level: 50,
    teraType: null,
    mechanics: { dynamaxLevel: 10, gigantamax: false, megaEvolution: Boolean(megaItem || rayquazaMega), zMove: false },
    evs: "",
    nature: "",
    moves: Array.from({ length: 4 }, () => ({ name: "", type: null, damaging: false, usage: 0 })),
    types: [],
    performance: { games: 0, wins: 0, leadGames: 0, leadWins: 0, selectionRate: 0 },
  }, WAR_ROOM_BATTLE_FORMAT);

  const fallback = fallbackMemberProposal(team, source.id, snapshot, species, suggestion.observedAs);
  const meta = presets[0];
  const otherSpecies = new Set(team.filter((_, index) => index !== targetIndex).map((set) => baseSpeciesKey(set.species)));
  const observedCandidates = pasteEvidence.flatMap((evidenceTeam) => evidenceTeam.sets.flatMap((rawObserved) => {
    if (baseSpeciesKey(rawObserved.species) !== speciesKey) return [];
    const observed = hydrateSetFromSnapshot(snapshot, rawObserved, WAR_ROOM_BATTLE_FORMAT);
    const moves = unique([
      ...observed.moves.map((move) => move.name),
      ...(meta?.moves ?? []),
      ...fallback.moves,
    ].map((move) => move.trim()).filter(Boolean)).slice(0, 4);
    const observedItem = observed.item.trim();
    const proposal = {
      item: megaItem || observedItem || availableMemberItem(team, source.id, snapshot, meta?.item || fallback.item),
      ability: observed.ability || meta?.ability || fallback.ability,
      nature: observed.nature || meta?.nature || fallback.nature,
      evs: observed.evs || meta?.evs || fallback.evs,
      moves,
    } satisfies WarRoomSetProposal;
    if (!proposalIsLegal(team, shell, proposal, snapshot)) return [];
    const context = proposalContextEvaluation(team, shell, proposal, snapshot);
    if (!context.compatible) return [];
    const overlap = evidenceTeam.pokemon.filter((pokemon) => otherSpecies.has(baseSpeciesKey(pokemon))).length;
    const contextFit = strategyContextSimilarity(teamStrategyContext(team), teamStrategyContext(evidenceTeam.sets));
    const patched = !observedItem || !observed.ability || !observed.nature || !observed.evs || observed.moves.length < 4;
    return [{
      evidenceTeam,
      proposal,
      patched,
      score: evidenceTeam.quality + overlap * 12 + contextFit * 0.55 + context.score * 0.35,
    }];
  })).sort((left, right) => (
    Number(left.evidenceTeam.historical) - Number(right.evidenceTeam.historical)
    || right.score - left.score
    || right.evidenceTeam.quality - left.evidenceTeam.quality
    || left.evidenceTeam.id.localeCompare(right.evidenceTeam.id)
  ));

  const observedBest = observedCandidates[0];
  if (observedBest) {
    const replacement = proposalToSet(snapshot, shell, observedBest.proposal);
    return {
      pokemon: team.map((set, index) => index === targetIndex ? replacement : set),
      setSource: observedBest.patched ? "observed-paste-patched" : "observed-paste",
      presetId: observedBest.patched && meta ? meta.id : null,
      evidenceTeamId: observedBest.evidenceTeam.id,
    };
  }

  for (const preset of presets) {
    const proposal = {
      item: megaItem || availableMemberItem(team, source.id, snapshot, preset.item),
      ability: preset.ability,
      nature: preset.nature,
      evs: preset.evs,
      moves: [...preset.moves],
    } satisfies WarRoomSetProposal;
    if (!proposalIsLegal(team, shell, proposal, snapshot)) continue;
    if (!proposalContextEvaluation(team, shell, proposal, snapshot).compatible) continue;
    const replacement = proposalToSet(snapshot, shell, proposal);
    return {
      pokemon: team.map((set, index) => index === targetIndex ? replacement : set),
      setSource: "battle-data-fallback",
      presetId: preset.id,
      evidenceTeamId: null,
    };
  }

  if (!proposalIsLegal(team, shell, fallback, snapshot) || !proposalContextEvaluation(team, shell, fallback, snapshot).compatible) {
    return { pokemon: team, setSource: "legal-fallback", presetId: null, evidenceTeamId: null };
  }
  const replacement = proposalToSet(snapshot, shell, fallback);
  return {
    pokemon: team.map((set, index) => index === targetIndex ? replacement : set),
    setSource: "legal-fallback",
    presetId: null,
    evidenceTeamId: null,
  };
}

export function applyWarRoomMemberSuggestion(
  team: PokemonSet[],
  suggestion: WarRoomMemberSuggestion,
  snapshot: ShowdownSnapshot,
  presets: readonly OpponentMetaPreset[] = [],
  pasteEvidence: readonly WarRoomPasteEvidenceTeam[] = [],
) {
  return buildWarRoomMemberReplacement(team, suggestion, snapshot, presets, pasteEvidence).pokemon;
}

/** Applies only the fields explicitly proposed by Set Search to the disposable draft. */
export function applyWarRoomSetSuggestion(
  team: PokemonSet[],
  suggestion: WarRoomSetSuggestion,
  snapshot: ShowdownSnapshot,
) {
  const targetIndex = team.findIndex((set) => set.id === suggestion.setId);
  if (targetIndex < 0 || !suggestion.changes.length) return team;
  const current = team[targetIndex];
  const next: PokemonSet = {
    ...current,
    mechanics: { ...current.mechanics },
    moves: current.moves.map((move) => ({ ...move })),
    performance: { ...current.performance },
  };
  for (const change of suggestion.changes) {
    if (change.key === "item") next.item = suggestion.proposal.item;
    else if (change.key === "ability") next.ability = suggestion.proposal.ability;
    else if (change.key === "nature") next.nature = suggestion.proposal.nature;
    else if (change.key === "statPoints") next.evs = suggestion.proposal.evs;
    else {
      if (!change.key.startsWith("move-")) continue;
      const slot = Number(change.key.slice("move-".length));
      if (!Number.isInteger(slot) || slot < 0 || slot > 3) continue;
      while (next.moves.length <= slot) next.moves.push({ name: "", type: null, damaging: false, usage: 0 });
      next.moves[slot] = {
        name: suggestion.proposal.moves[slot] ?? "",
        type: null,
        damaging: false,
        usage: 0,
      };
    }
  }
  const applied = hydrateSetFromSnapshot(snapshot, next, WAR_ROOM_BATTLE_FORMAT);
  return team.map((set, index) => index === targetIndex ? applied : set);
}

function rolesForSet(snapshot: ShowdownSnapshot, set: PokemonSet) {
  const moveIds = new Set(set.moves.map((move) => toId(move.name)).filter(Boolean));
  const roles = Object.entries(ROLE_MOVES)
    .filter(([, moves]) => [...moveIds].some((move) => moves.has(move)))
    .map(([role]) => role);
  const hasPriority = set.moves.some((move) => {
    const data = getMoveData(snapshot, move.name, WAR_ROOM_BATTLE_FORMAT);
    return data && data.category !== "Status" && (data.priority ?? 0) > 0;
  });
  if (hasPriority) roles.push("Prioridad");
  return roles;
}

function profileFromSet(snapshot: ShowdownSnapshot, set: PokemonSet): CombatProfile {
  const battleSpecies = resolveSetSpecies(snapshot, set);
  const species = getSpecies(snapshot, battleSpecies, WAR_ROOM_BATTLE_FORMAT)
    ?? getSpecies(snapshot, set.species, WAR_ROOM_BATTLE_FORMAT);
  const attacks = set.moves.flatMap((move): Array<{ name: string; type: PokemonType }> => {
    const data = getMoveData(snapshot, move.name, WAR_ROOM_BATTLE_FORMAT);
    const type = data?.type ?? move.type;
    const damaging = data ? data.category !== "Status" : move.damaging;
    return damaging && type ? [{ name: move.name, type }] : [];
  });
  return {
    id: set.id,
    slot: set.slot,
    species: set.species,
    types: species?.types ?? set.types,
    attackTypes: unique(attacks.map((move) => move.type)),
    attacks,
    speed: species?.baseStats.spe ?? null,
    roles: rolesForSet(snapshot, set),
    set,
    megaActive: isMegaSpeciesLabel(battleSpecies),
  };
}

function profileFromPreview(snapshot: ShowdownSnapshot, speciesName: string): CombatProfile {
  const species = getSpecies(snapshot, speciesName, WAR_ROOM_BATTLE_FORMAT);
  return {
    id: toId(speciesName),
    slot: 0,
    species: speciesName,
    types: species?.types ?? [],
    attackTypes: species?.types ?? [],
    attacks: [],
    speed: species?.baseStats.spe ?? null,
    roles: [],
    set: null,
    megaActive: isMegaSpeciesLabel(speciesName),
  };
}

function immunityType(profile: CombatProfile) {
  if (!profile.set) return null;
  if (toId(profile.set.item) === "airballoon") return "Ground" as PokemonType;
  return ABILITY_IMMUNITIES[toId(profile.set.ability)] ?? null;
}

function defensiveMultiplier(attackType: PokemonType, defender: CombatProfile) {
  if (immunityType(defender) === attackType) return 0;
  return defender.types.length ? effectiveness(attackType, defender.types) : 1;
}

function incomingMultiplier(attacker: CombatProfile, defender: CombatProfile) {
  if (!attacker.attackTypes.length) return 1;
  return Math.max(...attacker.attackTypes.map((type) => defensiveMultiplier(type, defender)));
}

function profileThreatens(attacker: CombatProfile, defender: CombatProfile) {
  return attacker.attackTypes.some((type) => defender.types.length && defensiveMultiplier(type, defender) > 1);
}

function usageBySpecies(teams: WarRoomCorpusTeam[]) {
  const usage = new Map<string, SpeciesUsage>();
  for (const team of teams) {
    const seen = new Set<string>();
    for (const species of team.pokemon) {
      const key = toId(species);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      const current = usage.get(key) ?? { key, species, appearances: 0 };
      current.appearances += 1;
      usage.set(key, current);
    }
  }
  return [...usage.values()].sort((left, right) =>
    right.appearances - left.appearances || left.species.localeCompare(right.species),
  );
}

function isValidCorpusTeam(value: unknown): value is WarRoomCorpusTeam {
  const team = recordValue(value);
  const regulation = getWarRoomRegulationEvidence(team?.formatId);
  return Boolean(
    team
    && typeof team.id === "string"
    && team.id.length > 0
    && (team.source === "tournament" || team.source === "vgcpastes" || team.source === "scouting-library")
    && typeof team.savedPasteId === "string"
    && typeof team.playerName === "string"
    && typeof team.tournament === "string"
    && typeof team.rank === "string"
    && typeof team.dateShared === "string"
    && typeof team.pokepasteUrl === "string"
    && Array.isArray(team.pokemon)
    && team.pokemon.length === 6
    && team.pokemon.every((species) => typeof species === "string" && species.length > 0 && species.length <= 64)
    && regulation
    && team.formatLabel === regulation.formatLabel
    && team.regulationWeight === regulation.regulationWeight
    && team.historical === regulation.historical
    && team.setEvidenceEligible === regulation.setEvidenceEligible
  );
}

function safePokepasteUrl(value: string) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "pokepast.es" || url.port || url.username || url.password || !/^\/[a-z0-9]+\/?$/i.test(url.pathname)) return "";
    return `${url.origin}${url.pathname.replace(/\/$/, "")}`;
  } catch {
    return "";
  }
}

export function buildWarRoomCorpusResponse(
  format: VgcPastesFormat,
  teams: VgcPastesTeam[],
  fetchedAt = new Date().toISOString(),
  savedPastes: ScoutingPasteSummary[] = [],
  tournamentSnapshot?: TournamentScoutingResponse | null,
  historicalSources: readonly WarRoomHistoricalCorpusSource[] = [],
): WarRoomCorpusResponse {
  if (format.id !== WAR_ROOM_FORMAT_ID) throw new Error("War Room solo admite la regulación vigente M-C");
  if (teams.length > MAX_WAR_ROOM_CORPUS_TEAMS) throw new Error("El corpus público de War Room supera el límite seguro");
  const currentRegulation = getWarRoomRegulationEvidence(WAR_ROOM_FORMAT_ID);
  if (!currentRegulation) throw new Error("No existe configuración para la regulación vigente");
  const regulationFields = (regulation: NonNullable<ReturnType<typeof getWarRoomRegulationEvidence>>) => ({
    formatId: regulation.formatId,
    formatLabel: regulation.formatLabel,
    regulationWeight: regulation.regulationWeight,
    historical: regulation.historical,
    setEvidenceEligible: regulation.setEvidenceEligible,
  });
  const tournamentTeams: WarRoomCorpusTeam[] = tournamentSnapshot
    && toId(tournamentSnapshot.regulation) === toId(CHAMPIONS_REGULATION)
    ? tournamentSnapshot.tournaments.flatMap((event) => event.teams.map((team): WarRoomCorpusTeam => ({
      id: `tournament-${team.id}`,
      source: "tournament",
      savedPasteId: "",
      playerName: team.playerName,
      tournament: event.name,
      rank: [team.placement ? `#${team.placement}` : "", team.record].filter(Boolean).join(" · "),
      dateShared: tournamentSnapshot.generatedAt.slice(0, 10),
      pokepasteUrl: team.pokepasteUrl,
      pokemon: [...team.pokemon],
      ...regulationFields(currentRegulation),
    })))
    : [];
  const tournamentPasteUrls = new Set(tournamentTeams.map((team) => safePokepasteUrl(team.pokepasteUrl)).filter(Boolean));
  const vgcPastesTeams: WarRoomCorpusTeam[] = teams.filter((team) => {
    const url = safePokepasteUrl(team.pokepasteUrl);
    return !url || !tournamentPasteUrls.has(url);
  }).map((team): WarRoomCorpusTeam => ({
    id: team.id,
    source: "vgcpastes",
    savedPasteId: "",
    playerName: team.playerName,
    tournament: team.tournament,
    rank: team.rank,
    dateShared: team.dateShared,
    pokepasteUrl: team.pokepasteUrl,
    pokemon: [...team.pokemon],
    ...regulationFields(currentRegulation),
  })).slice(0, Math.max(0, MAX_WAR_ROOM_CORPUS_TEAMS - tournamentTeams.length));
  const boundedTournamentTeams = tournamentTeams.slice(0, MAX_WAR_ROOM_CORPUS_TEAMS);
  const publicTeams = [...boundedTournamentTeams, ...vgcPastesTeams];
  const publicPasteUrls = new Set(publicTeams.map((team) => safePokepasteUrl(team.pokepasteUrl)).filter(Boolean));
  const privateTeams: WarRoomCorpusTeam[] = savedPastes.flatMap((paste): WarRoomCorpusTeam[] => {
    const pasteRegulation = inferWarRoomRegulationEvidence(paste.format);
    const pokepasteUrl = safePokepasteUrl(paste.sourceUrl);
    if (pasteRegulation?.formatId !== WAR_ROOM_FORMAT_ID || paste.pokemon.length !== 6 || (pokepasteUrl && publicPasteUrls.has(pokepasteUrl))) return [];
    return [{
      id: `saved-${paste.id}`,
      source: "scouting-library",
      savedPasteId: paste.id,
      playerName: paste.creator || paste.name,
      tournament: paste.name,
      rank: "",
      dateShared: paste.updatedAt.slice(0, 10),
      pokepasteUrl,
      pokemon: [...paste.pokemon],
      ...regulationFields(currentRegulation),
    }];
  }).slice(0, MAX_WAR_ROOM_CORPUS_TEAMS - publicTeams.length);
  const merged = [...publicTeams, ...privateTeams];
  const seenPasteUrls = new Set(merged.map((team) => safePokepasteUrl(team.pokepasteUrl)).filter(Boolean));
  const historicalTeams: WarRoomCorpusTeam[] = [];
  const appendHistorical = (team: WarRoomCorpusTeam) => {
    if (historicalTeams.length >= MAX_WAR_ROOM_HISTORICAL_TEAMS) return;
    const url = safePokepasteUrl(team.pokepasteUrl);
    if (url && seenPasteUrls.has(url)) return;
    if (url) seenPasteUrls.add(url);
    historicalTeams.push(team);
  };
  for (const source of historicalSources) {
    const sourceRegulation = getWarRoomRegulationEvidence(source.format.id);
    if (!sourceRegulation?.historical) continue;
    for (const team of source.teams) {
      appendHistorical({
        id: `${sourceRegulation.formatId}-${team.id}`,
        source: "vgcpastes",
        savedPasteId: "",
        playerName: team.playerName,
        tournament: team.tournament,
        rank: team.rank,
        dateShared: team.dateShared,
        pokepasteUrl: team.pokepasteUrl,
        pokemon: [...team.pokemon],
        ...regulationFields(sourceRegulation),
      });
    }
  }
  for (const paste of savedPastes) {
    const pasteRegulation = inferWarRoomRegulationEvidence(paste.format);
    if (!pasteRegulation?.historical || paste.pokemon.length !== 6) continue;
    appendHistorical({
      id: `${pasteRegulation.formatId}-saved-${paste.id}`,
      source: "scouting-library",
      savedPasteId: paste.id,
      playerName: paste.creator || paste.name,
      tournament: paste.name,
      rank: "",
      dateShared: paste.updatedAt.slice(0, 10),
      pokepasteUrl: safePokepasteUrl(paste.sourceUrl),
      pokemon: [...paste.pokemon],
      ...regulationFields(pasteRegulation),
    });
  }
  const historicalFormats = historicalSources.flatMap((source) => {
    const sourceRegulation = getWarRoomRegulationEvidence(source.format.id);
    if (!sourceRegulation?.historical) return [];
    return [{
      formatId: sourceRegulation.formatId,
      formatLabel: sourceRegulation.formatLabel,
      regulationWeight: sourceRegulation.regulationWeight,
      teamCount: historicalTeams.filter((team) => team.formatId === sourceRegulation.formatId).length,
    }];
  });
  return {
    schemaVersion: WAR_ROOM_SCHEMA_VERSION,
    regulation: CHAMPIONS_REGULATION,
    format,
    fetchedAt,
    totalTeams: merged.length,
    publicTeamCount: publicTeams.length,
    vgcPastesTeamCount: vgcPastesTeams.length,
    tournamentTeamCount: boundedTournamentTeams.length,
    savedTeamCount: privateTeams.length,
    historicalTeamCount: historicalTeams.length,
    historicalFormats,
    source: {
      label: "VGCPastes Repository",
      url: buildVgcPastesSheetUrl(format),
    },
    teams: merged,
    historicalTeams,
  };
}

export function isWarRoomCorpusResponse(value: unknown): value is WarRoomCorpusResponse {
  const root = recordValue(value);
  const format = recordValue(root?.format);
  const source = recordValue(root?.source);
  return Boolean(
    root
    && root.schemaVersion === WAR_ROOM_SCHEMA_VERSION
    && root.regulation === CHAMPIONS_REGULATION
    && format?.id === WAR_ROOM_FORMAT_ID
    && typeof format.label === "string"
    && typeof format.gid === "string"
    && typeof root.fetchedAt === "string"
    && !Number.isNaN(Date.parse(root.fetchedAt))
    && typeof root.totalTeams === "number"
    && Number.isInteger(root.totalTeams)
    && root.totalTeams >= 0
    && root.totalTeams <= MAX_WAR_ROOM_CORPUS_TEAMS
    && typeof root.publicTeamCount === "number"
    && Number.isInteger(root.publicTeamCount)
    && root.publicTeamCount >= 0
    && typeof root.vgcPastesTeamCount === "number"
    && Number.isInteger(root.vgcPastesTeamCount)
    && root.vgcPastesTeamCount >= 0
    && typeof root.tournamentTeamCount === "number"
    && Number.isInteger(root.tournamentTeamCount)
    && root.tournamentTeamCount >= 0
    && root.vgcPastesTeamCount + root.tournamentTeamCount === root.publicTeamCount
    && typeof root.savedTeamCount === "number"
    && Number.isInteger(root.savedTeamCount)
    && root.savedTeamCount >= 0
    && root.publicTeamCount + root.savedTeamCount === root.totalTeams
    && typeof root.historicalTeamCount === "number"
    && Number.isInteger(root.historicalTeamCount)
    && root.historicalTeamCount >= 0
    && root.historicalTeamCount <= MAX_WAR_ROOM_HISTORICAL_TEAMS
    && Array.isArray(root.historicalFormats)
    && root.historicalFormats.every((value) => {
      const entry = recordValue(value);
      const regulation = getWarRoomRegulationEvidence(entry?.formatId);
      return Boolean(
        entry
        && regulation?.historical
        && entry.formatLabel === regulation.formatLabel
        && entry.regulationWeight === regulation.regulationWeight
        && typeof entry.teamCount === "number"
        && Number.isInteger(entry.teamCount)
        && entry.teamCount >= 0,
      );
    })
    && source?.label === "VGCPastes Repository"
    && typeof source.url === "string"
    && Array.isArray(root.teams)
    && root.teams.length === root.totalTeams
    && root.teams.every(isValidCorpusTeam)
    && root.teams.every((team) => recordValue(team)?.historical === false)
    && Array.isArray(root.historicalTeams)
    && root.historicalTeams.length === root.historicalTeamCount
    && root.historicalTeams.every((team) => isValidCorpusTeam(team) && team.historical)
  );
}

function legalityAudit(
  team: PokemonSet[],
  snapshot: ShowdownSnapshot,
  teamFormat: string,
): WarRoomAuditResult["legality"] {
  const issues: WarRoomLegalityIssue[] = [];
  const add = (id: string, severity: WarRoomLegalityIssue["severity"], subject: string, detail: string) => {
    issues.push({ id, severity, subject, detail });
  };

  if (teamFormat !== WAR_ROOM_BATTLE_FORMAT) {
    add("format", "warning", "Formato guardado", `La versión está marcada como ${teamFormat}; War Room la contrasta deliberadamente contra Champions ${CHAMPIONS_REGULATION}.`);
  }
  if (team.length !== 6) add("team-size", "blocker", "Equipo", `La regulación exige 6 Pokémon y esta versión contiene ${team.length}.`);

  const speciesGroups = new Map<string, string[]>();
  const itemGroups = new Map<string, string[]>();
  for (const set of team) {
    const speciesKey = baseSpeciesKey(set.species);
    speciesGroups.set(speciesKey, [...(speciesGroups.get(speciesKey) ?? []), set.species]);
    if (set.item) {
      const itemKey = toId(set.item);
      itemGroups.set(itemKey, [...(itemGroups.get(itemKey) ?? []), set.species]);
    }

    if (!isSpeciesAvailable(snapshot, set.species, WAR_ROOM_BATTLE_FORMAT)) {
      add(`species-${set.id}`, "blocker", set.species, `No aparece como especie legal en el snapshot ${CHAMPIONS_REGULATION}.`);
    }
    if (set.item && !isItemLegal(snapshot, set.item, WAR_ROOM_BATTLE_FORMAT)) {
      add(`item-${set.id}`, "blocker", set.species, `${set.item} no está disponible en ${CHAMPIONS_REGULATION}.`);
    }

    const legalAbilities = getLegalAbilities(snapshot, set.species, WAR_ROOM_BATTLE_FORMAT);
    if (!set.ability) {
      add(`ability-empty-${set.id}`, "warning", set.species, "El paste no declara habilidad; conviene fijarla antes de preparar matchups exactos.");
    } else if (!legalAbilities.includes(set.ability)) {
      add(`ability-${set.id}`, "blocker", set.species, `${set.ability} no es una habilidad legal para esta especie.`);
    }

    const moves = set.moves.map((move) => move.name.trim()).filter(Boolean);
    if (moves.length !== 4) add(`moves-${set.id}`, "blocker", set.species, `Tiene ${moves.length} movimientos declarados; se requieren 4.`);
    const moveIds = moves.map(toId);
    if (new Set(moveIds).size !== moveIds.length) add(`moves-duplicate-${set.id}`, "blocker", set.species, "Contiene movimientos repetidos.");
    for (const move of moves) {
      if (!isMoveLegal(snapshot, set.species, move, WAR_ROOM_BATTLE_FORMAT)) {
        add(`move-${set.id}-${toId(move)}`, "blocker", set.species, `${move} no está disponible para ${set.species} en ${CHAMPIONS_REGULATION}.`);
      }
    }

    if (!set.nature) add(`nature-${set.id}`, "warning", set.species, "No declara naturaleza.");
    else if (!NATURES.includes(set.nature)) add(`nature-invalid-${set.id}`, "blocker", set.species, `${set.nature} no es una naturaleza reconocida.`);

    const rules = getStatRules(WAR_ROOM_BATTLE_FORMAT);
    const allocations = parseEvs(set.evs);
    const total = Object.values(allocations).reduce((sum, value) => sum + value, 0);
    if (total > rules.totalMax || Object.values(allocations).some((value) => value > rules.perStatMax)) {
      add(`stats-${set.id}`, "blocker", set.species, `Supera el límite de ${rules.perStatMax} por stat o ${rules.totalMax} Stat Points totales.`);
    }
    const chunks = set.evs ? set.evs.split("/").map((chunk) => chunk.trim()).filter(Boolean) : [];
    const validChunks = chunks.filter((chunk) => /^\d+\s+(?:HP|Atk|Def|SpA|SpD|Spe)$/i.test(chunk));
    if (chunks.length !== validChunks.length) add(`stats-format-${set.id}`, "warning", set.species, "Hay Stat Points que no se pudieron interpretar por completo.");
  }

  for (const [key, species] of speciesGroups) {
    if (key && species.length > 1) add(`species-clause-${key}`, "blocker", "Species Clause", `La misma identidad aparece más de una vez: ${species.join(" + ")}.`);
  }
  for (const [key, species] of itemGroups) {
    if (key && species.length > 1) add(`item-clause-${key}`, "blocker", "Item Clause", `El mismo objeto está repetido entre ${species.join(" y ")}.`);
  }

  const blockers = issues.filter((issue) => issue.severity === "blocker").length;
  const warnings = issues.length - blockers;
  return {
    status: blockers ? "blocked" : warnings ? "review" : "legal",
    issues,
    blockers,
    warnings,
  };
}

function threatAssessments(
  teamProfiles: CombatProfile[],
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
) {
  const usage = usageBySpecies(corpus);
  const maxAppearances = Math.max(1, usage[0]?.appearances ?? 1);
  let unknown = 0;
  const threats = usage.flatMap((entry): WarRoomThreat[] => {
    const opponent = profileFromPreview(snapshot, entry.species);
    if (!opponent.types.length) {
      unknown += 1;
      return [];
    }
    const weakTargets = teamProfiles.filter((profile) => incomingMultiplier(opponent, profile) > 1).map((profile) => profile.species);
    const answers = teamProfiles.filter((profile) => profileThreatens(profile, opponent)).map((profile) => profile.species);
    const switchIns = teamProfiles.filter((profile) => opponent.attackTypes.length && opponent.attackTypes.every((type) => defensiveMultiplier(type, profile) < 1)).map((profile) => profile.species);
    const frequencyFactor = entry.appearances / maxAppearances;
    const weakFactor = weakTargets.length / Math.max(1, teamProfiles.length);
    const answerGap = 1 - Math.min(answers.length, 3) / 3;
    const switchGap = 1 - Math.min(switchIns.length, 2) / 2;
    const score = clamp(round(100 * (0.32 * frequencyFactor + 0.30 * weakFactor + 0.23 * answerGap + 0.15 * switchGap)));
    const reasons = [
      `${entry.appearances}/${corpus.length} equipos del corpus (${round(entry.appearances / Math.max(1, corpus.length) * 100, 1)}%).`,
      weakTargets.length ? `Presiona con STAB supereficaz a ${weakTargets.length} de tus 6.` : "No abre una debilidad STAB directa en el team preview.",
      answers.length ? `${answers.length} respuestas llevan cobertura supereficaz.` : "No hay cobertura supereficaz declarada en tus seis sets.",
      switchIns.length ? `${switchIns.length} cambios resisten todos sus STAB visibles.` : "No aparece un cambio que resista todos sus STAB de team preview.",
    ];
    return [{
      species: entry.species,
      score,
      tier: score >= 72 ? "crítica" : score >= 55 ? "alta" : "media",
      appearances: entry.appearances,
      usageRate: round(entry.appearances / Math.max(1, corpus.length) * 100, 1),
      weakTargets,
      answers,
      switchIns,
      reasons,
    }];
  }).sort((left, right) => right.score - left.score || right.appearances - left.appearances || left.species.localeCompare(right.species));
  return { threats: threats.slice(0, 16), unknown };
}

function coreAssessments(
  teamProfiles: CombatProfile[],
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
) {
  const cores = new Map<string, { species: [string, string]; appearances: number }>();
  for (const team of corpus) {
    const species = normalizedTeamSpecies(team);
    for (const pair of combinations(species, 2)) {
      const ordered = [...pair].sort((left, right) => toId(left).localeCompare(toId(right))) as [string, string];
      const key = ordered.map(toId).join("|");
      const current = cores.get(key) ?? { species: ordered, appearances: 0 };
      current.appearances += 1;
      cores.set(key, current);
    }
  }
  const minimumAppearances = corpus.length >= 25 ? Math.max(2, Math.ceil(corpus.length * 0.01)) : 1;
  const eligible = [...cores.entries()].filter(([, core]) => core.appearances >= minimumAppearances);
  const maxAppearances = Math.max(1, ...eligible.map(([, core]) => core.appearances));
  return eligible.flatMap(([id, core]): WarRoomCoreThreat[] => {
    const profiles = core.species.map((species) => profileFromPreview(snapshot, species));
    if (profiles.some((profile) => !profile.types.length)) return [];
    const pressuredTargets = teamProfiles
      .filter((profile) => profiles.some((opponent) => incomingMultiplier(opponent, profile) > 1))
      .map((profile) => profile.species);
    const sharedAnswers = teamProfiles
      .filter((profile) => profiles.every((opponent) => profileThreatens(profile, opponent)))
      .map((profile) => profile.species);
    const score = clamp(round(100 * (
      0.30 * (core.appearances / maxAppearances)
      + 0.40 * (pressuredTargets.length / Math.max(1, teamProfiles.length))
      + 0.30 * (1 - Math.min(sharedAnswers.length, 2) / 2)
    )));
    return [{
      id,
      species: core.species,
      score,
      appearances: core.appearances,
      usageRate: round(core.appearances / Math.max(1, corpus.length) * 100, 1),
      pressuredTargets,
      sharedAnswers,
      reasons: [
        `Aparece junto en ${core.appearances}/${corpus.length} equipos (${round(core.appearances / Math.max(1, corpus.length) * 100, 1)}%).`,
        `Entre ambos presionan a ${pressuredTargets.length} integrantes por tipo.`,
        sharedAnswers.length ? `${sharedAnswers.length} integrantes pueden amenazar a ambos por cobertura.` : "Ningún integrante amenaza de forma supereficaz a las dos mitades del core.",
      ],
    }];
  }).sort((left, right) => right.score - left.score || right.appearances - left.appearances).slice(0, 10);
}

function structuralGaps(teamProfiles: CombatProfile[], threats: WarRoomThreat[], snapshot: ShowdownSnapshot) {
  const gaps: WarRoomGap[] = [];
  for (const type of POKEMON_TYPES) {
    const weak = teamProfiles.filter((profile) => defensiveMultiplier(type, profile) > 1);
    const safe = teamProfiles.filter((profile) => defensiveMultiplier(type, profile) < 1);
    if (weak.length >= 3 && safe.length <= 1) {
      gaps.push({
        id: `defense-${toId(type)}`,
        severity: safe.length ? "medium" : "high",
        title: `Hueco defensivo frente a ${type}`,
        detail: `${weak.map((profile) => profile.species).join(", ")} comparten debilidad y solo ${safe.length} integrante${safe.length === 1 ? " ofrece" : "s ofrecen"} resistencia o inmunidad.`,
        evidence: `${weak.length} débiles · ${safe.length} cambios resistentes`,
      });
    }
  }

  const unhandled = threats.filter((threat) => threat.answers.length === 0).slice(0, 4);
  if (unhandled.length) {
    gaps.push({
      id: "coverage-meta",
      severity: unhandled.some((threat) => threat.tier === "crítica" || threat.tier === "alta") ? "high" : "medium",
      title: "Amenazas frecuentes sin cobertura supereficaz",
      detail: `Los sets declarados no muestran cobertura directa contra ${unhandled.map((threat) => threat.species).join(", ")}.`,
      evidence: `${unhandled.length} amenazas del ranking sin respuesta por tipo`,
    });
  }

  const roleOwners = (role: string) => teamProfiles.filter((profile) => profile.roles.includes(role));
  if (!roleOwners("Control de velocidad").length) {
    gaps.push({
      id: "role-speed",
      severity: "high",
      title: "Sin control de velocidad visible",
      detail: "No se detectan Tailwind, Trick Room ni otras herramientas directas de speed control en los sets exactos.",
      evidence: "0/6 integrantes con control de velocidad",
    });
  }
  if (!roleOwners("Control de posición").length) {
    gaps.push({
      id: "role-position",
      severity: "medium",
      title: "Sin control de posición",
      detail: "No aparecen Fake Out, redirección ni guards; puede costar crear turnos seguros para el plan principal.",
      evidence: "0/6 integrantes con Fake Out, redirección o guard",
    });
  }
  const protectCount = roleOwners("Protección").length;
  if (protectCount < 2) {
    gaps.push({
      id: "role-protect",
      severity: "medium",
      title: "Poca protección individual",
      detail: "Menos de dos integrantes declaran Protect o equivalentes, reduciendo líneas de posicionamiento conservadoras.",
      evidence: `${protectCount}/6 integrantes con protección`,
    });
  }

  const physical = teamProfiles.filter((profile) => profile.set?.moves.some((move) => getMoveData(snapshot, move.name, WAR_ROOM_BATTLE_FORMAT)?.category === "Physical")).length;
  const special = teamProfiles.filter((profile) => profile.set?.moves.some((move) => getMoveData(snapshot, move.name, WAR_ROOM_BATTLE_FORMAT)?.category === "Special")).length;
  if (physical === 0 || special === 0) {
    const missing = physical === 0 ? "físico" : "especial";
    gaps.push({
      id: `damage-${missing}`,
      severity: "medium",
      title: `Sin presión de daño ${missing}`,
      detail: `Todos los ataques declarados caen del mismo lado defensivo; Intimidate, burns o boosts defensivos pueden polarizar el matchup.`,
      evidence: `${physical} atacantes físicos · ${special} atacantes especiales`,
    });
  }

  return gaps.sort((left, right) => SEVERITY_ORDER[left.severity] - SEVERITY_ORDER[right.severity] || left.title.localeCompare(right.title)).slice(0, 10);
}

export function auditTeam(
  team: PokemonSet[],
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
  options: { teamFormat?: string } = {},
): WarRoomAuditResult {
  const statistics = statisticalCorpus(corpus);
  const teamProfiles = team.map((set) => profileFromSet(snapshot, set));
  const legality = legalityAudit(team, snapshot, options.teamFormat ?? WAR_ROOM_BATTLE_FORMAT);
  const { threats, unknown } = threatAssessments(teamProfiles, statistics, snapshot);
  const cores = coreAssessments(teamProfiles, statistics, snapshot);
  const gaps = structuralGaps(teamProfiles, threats, snapshot);
  return {
    regulation: CHAMPIONS_REGULATION,
    evidenceScope: ["exact-set", "team-preview", "corpus"],
    legality,
    threats,
    cores,
    gaps,
    summary: {
      corpusTeams: statistics.length,
      criticalThreats: threats.filter((threat) => threat.tier === "crítica").length,
      highThreats: threats.filter((threat) => threat.tier === "alta").length,
      unresolvedThreats: threats.filter((threat) => threat.answers.length === 0).length,
      unknownCorpusSpecies: unknown,
    },
    notes: [
      "El ranking combina frecuencia, presión de tipos, respuestas y cambios seguros; no es una probabilidad de victoria.",
      "El corpus público confirma composiciones de seis, no qué cuatro Pokémon fueron elegidos en partida.",
      "Objetos, habilidades y movimientos rivales solo se usan cuando existe un paste exacto; el audit general trabaja con team preview.",
    ],
  };
}

function rivalProfiles(
  rival: WarRoomCorpusTeam,
  exactSets: PokemonSet[] | null,
  snapshot: ShowdownSnapshot,
) {
  if (exactSets?.length === 6) return exactSets.map((set) => profileFromSet(snapshot, set));
  return rival.pokemon.map((species) => profileFromPreview(snapshot, species));
}

function strongestDamage(attacker: CombatProfile, defender: CombatProfile) {
  if (!attacker.set || !defender.set) return null;
  const attackDraft = createDamageDraft(attacker.set);
  const defenseDraft = createDamageDraft(defender.set);
  attackDraft.megaActive = attacker.megaActive;
  defenseDraft.megaActive = defender.megaActive;
  const outcomes = attacker.attacks
    .filter((move) => defensiveMultiplier(move.type, defender) > 0)
    .map((move) => calculateDamage(WAR_ROOM_BATTLE_FORMAT, attackDraft, defenseDraft, move.name, defaultDamageField()))
    .filter((outcome) => !outcome.error && outcome.maxPercent > 0)
    .sort((left, right) => right.maxPercent - left.maxPercent || left.move.localeCompare(right.move));
  return outcomes[0] ?? null;
}

function evaluatePick(profile: CombatProfile, opponents: CombatProfile[]): WarRoomMatchupPick {
  const covers = opponents.filter((opponent) => profileThreatens(profile, opponent)).map((opponent) => opponent.species);
  const pressuredBy = opponents.filter((opponent) => incomingMultiplier(opponent, profile) > 1).map((opponent) => opponent.species);
  const safeAgainst = opponents.filter((opponent) => incomingMultiplier(opponent, profile) < 1).length;
  const neutralAgainst = opponents.filter((opponent) => incomingMultiplier(opponent, profile) === 1).length;
  const faster = opponents.filter((opponent) => profile.speed !== null && opponent.speed !== null && profile.speed >= opponent.speed).length;
  const typeOffense = round(covers.length / Math.max(1, opponents.length) * 100);
  const typeSafety = round((safeAgainst + neutralAgainst * 0.55) / Math.max(1, opponents.length) * 100);
  const damage = opponents.flatMap((opponent) => {
    const outcome = strongestDamage(profile, opponent);
    return outcome ? [{ target: opponent.species, move: outcome.move, maxPercent: outcome.maxPercent }] : [];
  }).sort((left, right) => right.maxPercent - left.maxPercent || left.target.localeCompare(right.target));
  const incomingDamage = opponents.flatMap((opponent) => {
    const outcome = strongestDamage(opponent, profile);
    return outcome ? [{ source: opponent.species, move: outcome.move, maxPercent: outcome.maxPercent }] : [];
  }).sort((left, right) => right.maxPercent - left.maxPercent || left.source.localeCompare(right.source));
  const exactDamageAvailable = Boolean(profile.set) && opponents.length > 0 && opponents.every((opponent) => opponent.set);
  const damageOffense = exactDamageAvailable
    ? damage.reduce((sum, entry) => sum + Math.min(100, entry.maxPercent), 0) / opponents.length
    : null;
  const damageSafety = exactDamageAvailable
    ? 100 - incomingDamage.reduce((sum, entry) => sum + Math.min(100, entry.maxPercent), 0) / opponents.length
    : null;
  const offense = round(damageOffense === null ? typeOffense : typeOffense * 0.55 + damageOffense * 0.45);
  const safety = round(damageSafety === null ? typeSafety : typeSafety * 0.55 + damageSafety * 0.45);
  const speed = profile.roles.includes("Control de velocidad")
    ? clamp(round(faster / Math.max(1, opponents.length) * 100) + 18)
    : round(faster / Math.max(1, opponents.length) * 100);
  const utility = clamp(profile.roles.length * 22);
  const fit = clamp(round(offense * 0.35 + safety * 0.35 + speed * 0.15 + utility * 0.15));
  return {
    id: profile.id,
    slot: profile.slot,
    species: profile.species,
    fit,
    offense,
    safety,
    speed,
    utility,
    covers,
    pressuredBy,
    roles: profile.roles,
    damage,
    incomingDamage,
  };
}

function defensiveOverlap(profiles: CombatProfile[], opponents: CombatProfile[]) {
  const attackTypes = unique(opponents.flatMap((profile) => profile.attackTypes));
  return attackTypes.reduce((penalty, type) => {
    const weak = profiles.filter((profile) => defensiveMultiplier(type, profile) > 1).length;
    return penalty + Math.max(0, weak - 2) * 4;
  }, 0);
}

function lineupScore(
  profiles: CombatProfile[],
  picks: Map<string, WarRoomMatchupPick>,
  opponents: CombatProfile[],
) {
  const average = profiles.reduce((sum, profile) => sum + (picks.get(profile.id)?.fit ?? 0), 0) / Math.max(1, profiles.length);
  const covered = opponents.filter((opponent) => profiles.some((profile) => profileThreatens(profile, opponent))).length;
  const roles = new Set(profiles.flatMap((profile) => profile.roles));
  const roleBonus = Math.min(15,
    (roles.has("Control de velocidad") ? 5 : 0)
    + (roles.has("Control de posición") ? 4 : 0)
    + (roles.has("Protección") ? 3 : 0)
    + (roles.has("Disrupción") ? 3 : 0),
  );
  return clamp(round(average * 0.55 + covered / Math.max(1, opponents.length) * 30 + roleBonus - defensiveOverlap(profiles, opponents)));
}

function leadScore(
  lead: CombatProfile[],
  picks: Map<string, WarRoomMatchupPick>,
  opponents: CombatProfile[],
) {
  const average = lead.reduce((sum, profile) => sum + (picks.get(profile.id)?.fit ?? 0), 0) / Math.max(1, lead.length);
  const covered = opponents.filter((opponent) => lead.some((profile) => profileThreatens(profile, opponent))).length;
  const roles = new Set(lead.flatMap((profile) => profile.roles));
  const roleBonus = Math.min(18,
    (roles.has("Control de velocidad") ? 7 : 0)
    + (roles.has("Control de posición") ? 6 : 0)
    + (roles.has("Disrupción") ? 3 : 0)
    + (roles.has("Protección") ? 2 : 0),
  );
  return clamp(round(average * 0.55 + covered / Math.max(1, opponents.length) * 27 + roleBonus - defensiveOverlap(lead, opponents) * 0.65));
}

function planIntent(lead: CombatProfile[]) {
  const roles = new Set(lead.flatMap((profile) => profile.roles));
  if (roles.has("Control de velocidad") && roles.has("Control de posición")) return "Tomar el ritmo y proteger el primer turno";
  if (roles.has("Control de velocidad")) return "Controlar velocidad antes de convertir presión";
  if (roles.has("Control de posición") || roles.has("Disrupción")) return "Crear una ventana segura con control de mesa";
  return "Presión inmediata con cobertura complementaria";
}

export function prepareMatchup(
  team: PokemonSet[],
  rival: WarRoomCorpusTeam,
  snapshot: ShowdownSnapshot,
  exactRivalSets: PokemonSet[] | null = null,
): WarRoomMatchupResult {
  const ownProfiles = team.map((set) => profileFromSet(snapshot, set));
  const opponents = rivalProfiles(rival, exactRivalSets, snapshot);
  const pickList = ownProfiles.map((profile) => evaluatePick(profile, opponents));
  const picks = new Map(pickList.map((pick) => [pick.id, pick]));
  const planCandidates = combinations(ownProfiles, 4).flatMap((lineup) => {
    const lineupFit = lineupScore(lineup, picks, opponents);
    return combinations(lineup, 2).map((lead) => {
      const backline = lineup.filter((profile) => !lead.includes(profile));
      const fit = clamp(round(lineupFit * 0.58 + leadScore(lead, picks, opponents) * 0.42));
      const covered = opponents.filter((opponent) => lineup.some((profile) => profileThreatens(profile, opponent))).length;
      const reasons = [
        `Los cuatro cubren por tipo a ${covered}/${opponents.length} integrantes rivales.`,
        `${lead.map((profile) => profile.species).join(" + ")} obtiene índice de encaje ${leadScore(lead, picks, opponents)}/100.`,
      ];
      const roles = unique(lead.flatMap((profile) => profile.roles));
      if (roles.length) reasons.push(`El lead aporta ${roles.join(", ").toLowerCase()}.`);
      return {
        id: `${lineup.map((profile) => profile.id).sort().join("-")}::${lead.map((profile) => profile.id).sort().join("-")}`,
        label: "",
        intent: planIntent(lead),
        fit,
        lead: lead.map((profile) => profile.species),
        backline: backline.map((profile) => profile.species),
        bench: ownProfiles.filter((profile) => !lineup.includes(profile)).map((profile) => profile.species),
        reasons,
      } satisfies WarRoomMatchupPlan;
    });
  }).sort((left, right) => right.fit - left.fit || left.id.localeCompare(right.id));

  const selectedPlans: WarRoomMatchupPlan[] = [];
  const seenLeads = new Set<string>();
  for (const candidate of planCandidates) {
    const leadKey = [...candidate.lead].sort().map(toId).join("|");
    if (seenLeads.has(leadKey)) continue;
    seenLeads.add(leadKey);
    selectedPlans.push(candidate);
    if (selectedPlans.length === 3) break;
  }
  const labels = ["Plan principal", "Plan alternativo A", "Plan alternativo B"];
  selectedPlans.forEach((plan, index) => { plan.label = labels[index]; });
  const recommended = selectedPlans[0] ?? null;
  const chosen = new Set([...(recommended?.lead ?? []), ...(recommended?.backline ?? [])].map(toId));
  const orderedPicks = [...pickList].sort((left, right) => {
    const leftChosen = chosen.has(toId(left.species)) ? 1 : 0;
    const rightChosen = chosen.has(toId(right.species)) ? 1 : 0;
    return rightChosen - leftChosen || right.fit - left.fit || left.slot - right.slot;
  });

  return {
    regulation: CHAMPIONS_REGULATION,
    evidenceScope: exactRivalSets?.length === 6 ? "exact-set" : "team-preview",
    rival,
    recommended,
    alternatives: selectedPlans.slice(1),
    picks: orderedPicks,
    notes: [
      "El índice de encaje ordena líneas por tipos, velocidad base y utilidad visible; no estima la probabilidad de ganar.",
      exactRivalSets?.length === 6
        ? "Se usaron los movimientos, objetos y habilidades del PokéPaste rival para perfilar su presión."
        : "No hay set rival exacto disponible: la presión rival se aproxima únicamente con sus tipos STAB de team preview.",
      exactRivalSets?.length === 6
        ? "El daño añadido al índice usa un campo neutral de dobles; clima, terreno, boosts y decisiones de turno se validan aparte."
        : "Los cálculos de daño se activan automáticamente cuando el rival tiene un set exacto.",
      "Daño, rolls, reads y secuencias de turnos deben validarse después en la calculadora.",
    ],
  };
}

function teamDefensePenalty(profiles: CombatProfile[]) {
  return POKEMON_TYPES.reduce((penalty, type) => {
    const weak = profiles.filter((profile) => defensiveMultiplier(type, profile) > 1).length;
    const safe = profiles.filter((profile) => defensiveMultiplier(type, profile) < 1).length;
    return penalty + Math.max(0, weak - safe) + Math.max(0, weak - 2) * 1.5;
  }, 0);
}

function structuralSetScore(profile: CombatProfile, opponents: CombatProfile[]) {
  const coverage = opponents.filter((opponent) => profileThreatens(profile, opponent)).length / Math.max(1, opponents.length) * 70;
  const utility = Math.min(30, profile.roles.length * 8);
  return coverage + utility;
}

type WarRoomSetProposal = WarRoomSetSuggestion["proposal"];

type WarRoomProposalEvidence = {
  item: number | null;
  ability: number | null;
  nature: number | null;
  statPoints: number | null;
  moves: Array<number | null>;
};

type WarRoomProposalSources = {
  item: WarRoomSetChange["source"];
  ability: WarRoomSetChange["source"];
  nature: WarRoomSetChange["source"];
  statPoints: WarRoomSetChange["source"];
  moves: WarRoomSetChange["source"][];
};

type WarRoomTerrain = "electric" | "grassy" | "misty" | "psychic";
type WarRoomWeather = "rain" | "sun" | "sand" | "snow";

type WarRoomStrategyContext = {
  speedMode: "trick-room" | "tailwind" | "hybrid" | "neutral";
  terrains: Set<WarRoomTerrain>;
  weather: Set<WarRoomWeather>;
};

const FAST_NATURES = new Set(["jolly", "timid", "hasty", "naive"]);
const SLOW_NATURES = new Set(["brave", "quiet", "relaxed", "sassy"]);
const TERRAIN_SEEDS = new Map<string, WarRoomTerrain>([
  ["electricseed", "electric"],
  ["grassyseed", "grassy"],
  ["mistyseed", "misty"],
  ["psychicseed", "psychic"],
]);
const TERRAIN_ABILITIES = new Map<string, WarRoomTerrain>([
  ["electricsurge", "electric"],
  ["grassysurge", "grassy"],
  ["hadronengine", "electric"],
  ["mistysurge", "misty"],
  ["psychicsurge", "psychic"],
  ["seedsower", "grassy"],
]);
const TERRAIN_MOVES = new Map<string, WarRoomTerrain>([
  ["electricterrain", "electric"],
  ["grassyterrain", "grassy"],
  ["mistyterrain", "misty"],
  ["psychicterrain", "psychic"],
]);
const WEATHER_ABILITIES = new Map<string, WarRoomWeather>([
  ["drizzle", "rain"],
  ["drought", "sun"],
  ["orichalcumpulse", "sun"],
  ["sandstream", "sand"],
  ["snowwarning", "snow"],
]);
const WEATHER_MOVES = new Map<string, WarRoomWeather>([
  ["raindance", "rain"],
  ["sunnyday", "sun"],
  ["sandstorm", "sand"],
  ["snowscape", "snow"],
]);
const WEATHER_DEPENDENCIES = new Map<string, WarRoomWeather>([
  ["swiftswim", "rain"],
  ["raindish", "rain"],
  ["chlorophyll", "sun"],
  ["solarpower", "sun"],
  ["protosynthesis", "sun"],
  ["sandrush", "sand"],
  ["slushrush", "snow"],
  ["icebody", "snow"],
]);
const TERRAIN_DEPENDENCIES = new Map<string, WarRoomTerrain>([
  ["surgesurfer", "electric"],
  ["grassypelt", "grassy"],
  ["quarkdrive", "electric"],
]);

function proposalToSet(snapshot: ShowdownSnapshot, source: PokemonSet, proposal: WarRoomSetProposal): PokemonSet {
  return {
    ...source,
    item: proposal.item,
    ability: proposal.ability,
    nature: proposal.nature,
    evs: proposal.evs,
    moves: proposal.moves.map((name) => {
      const move = getMoveData(snapshot, name, WAR_ROOM_BATTLE_FORMAT);
      return {
        name,
        type: move?.type ?? null,
        damaging: move ? move.category !== "Status" : false,
        usage: 0,
      };
    }),
  };
}

function teamStrategyContext(team: readonly PokemonSet[]): WarRoomStrategyContext {
  const moveKeys = new Set(team.flatMap((set) => set.moves.map((move) => toId(move.name))).filter(Boolean));
  const abilityKeys = new Set(team.map((set) => toId(set.ability)).filter(Boolean));
  const hasTrickRoom = moveKeys.has("trickroom");
  const hasTailwind = moveKeys.has("tailwind");
  const terrains = new Set<WarRoomTerrain>();
  const weather = new Set<WarRoomWeather>();
  for (const ability of abilityKeys) {
    const terrain = TERRAIN_ABILITIES.get(ability);
    const activeWeather = WEATHER_ABILITIES.get(ability);
    if (terrain) terrains.add(terrain);
    if (activeWeather) weather.add(activeWeather);
  }
  for (const move of moveKeys) {
    const terrain = TERRAIN_MOVES.get(move);
    const activeWeather = WEATHER_MOVES.get(move);
    if (terrain) terrains.add(terrain);
    if (activeWeather) weather.add(activeWeather);
  }
  return {
    speedMode: hasTrickRoom && hasTailwind ? "hybrid" : hasTrickRoom ? "trick-room" : hasTailwind ? "tailwind" : "neutral",
    terrains,
    weather,
  };
}

function strategyContextLabel(context: WarRoomStrategyContext) {
  const labels = [
    context.speedMode === "trick-room" ? "Trick Room" : context.speedMode === "tailwind" ? "Tailwind" : context.speedMode === "hybrid" ? "velocidad híbrida" : "velocidad neutral",
    ...[...context.weather].map((weather) => ({ rain: "lluvia", sun: "sol", sand: "arena", snow: "nieve" })[weather]),
    ...[...context.terrains].map((terrain) => `terreno ${terrain}`),
  ];
  return labels.join(" + ");
}

function strategyContextSimilarity(target: WarRoomStrategyContext, observed: WarRoomStrategyContext) {
  let score = 48;
  if (target.speedMode === observed.speedMode) score += 28;
  else if (target.speedMode === "hybrid" || observed.speedMode === "hybrid") score += 12;
  else if (target.speedMode !== "neutral" && observed.speedMode !== "neutral") score -= 24;
  else if (target.speedMode !== "neutral" || observed.speedMode !== "neutral") score -= 8;
  for (const terrain of target.terrains) score += observed.terrains.has(terrain) ? 8 : -4;
  for (const activeWeather of target.weather) score += observed.weather.has(activeWeather) ? 8 : -4;
  return clamp(round(score));
}

function proposalContextEvaluation(
  team: PokemonSet[],
  current: PokemonSet,
  proposal: WarRoomSetProposal,
  snapshot: ShowdownSnapshot,
) {
  const proposed = proposalToSet(snapshot, current, proposal);
  const nextTeam = team.map((set) => set.id === current.id ? proposed : set);
  const context = teamStrategyContext(nextTeam);
  const reasons: string[] = [];
  let score = 70;

  const requiredTerrain = TERRAIN_SEEDS.get(toId(proposal.item));
  if (requiredTerrain && !context.terrains.has(requiredTerrain)) {
    return {
      compatible: false,
      score: 0,
      context,
      reasons: [`${proposal.item} no tiene un activador de terreno ${requiredTerrain} en el Team propuesto.`],
    };
  }

  const speedPoints = parseEvs(proposal.evs).Spe;
  const fastNature = FAST_NATURES.has(toId(proposal.nature));
  const slowNature = SLOW_NATURES.has(toId(proposal.nature));
  const ownsTrickRoom = proposal.moves.some((move) => toId(move) === "trickroom");
  if (ownsTrickRoom && context.speedMode !== "hybrid" && (speedPoints >= 24 || fastNature)) {
    return {
      compatible: false,
      score: 0,
      context,
      reasons: ["El propio setter de Trick Room invierte demasiado en Velocidad sin que el Team muestre un modo rápido alterno."],
    };
  }
  if (context.speedMode === "trick-room") {
    if (speedPoints >= 24 || fastNature) {
      score -= 24;
      reasons.push("La inversión alta en Velocidad pierde encaje con el modo principal de Trick Room.");
    } else if (speedPoints === 0 && slowNature) {
      score += 14;
      reasons.push("Naturaleza e inversión de Velocidad acompañan el modo Trick Room.");
    }
  } else if (context.speedMode === "tailwind" && slowNature && !ownsTrickRoom) {
    score -= 10;
    reasons.push("La naturaleza reductora de Velocidad aporta poco al modo Tailwind.");
  }

  const abilityKey = toId(proposal.ability);
  const weatherDependency = WEATHER_DEPENDENCIES.get(abilityKey);
  if (weatherDependency && !context.weather.has(weatherDependency) && toId(proposal.item) !== "boosterenergy") {
    score -= 22;
    reasons.push(`${proposal.ability} no tiene activador de ${weatherDependency} en el Team.`);
  }
  const terrainDependency = TERRAIN_DEPENDENCIES.get(abilityKey);
  if (terrainDependency && !context.terrains.has(terrainDependency) && toId(proposal.item) !== "boosterenergy") {
    score -= 22;
    reasons.push(`${proposal.ability} no tiene activador de terreno ${terrainDependency} en el Team.`);
  }
  if (abilityKey === "unburden" && !requiredTerrain && !/(berry|herb|seed|sash|policy|gem)$/.test(toId(proposal.item))) {
    score -= 16;
    reasons.push("Unburden no muestra una activación reproducible con el objeto propuesto.");
  }
  return { compatible: true, score: clamp(round(score)), context, reasons };
}

type CandidateMove = {
  name: string;
  evidence: number | null;
  source: WarRoomSetChange["source"];
};

function mergeCandidateMoves(
  current: PokemonSet,
  candidates: CandidateMove[],
  locks: WarRoomPokemonLocks,
) {
  const moves: Array<string | undefined> = Array.from({ length: 4 });
  const evidence: Array<number | null> = Array.from({ length: 4 }, () => null);
  const sources: WarRoomSetChange["source"][] = Array.from({ length: 4 }, () => "paste");
  const used = new Set<string>();
  const available = candidates.map((candidate) => ({
    ...candidate,
    name: candidate.name.trim(),
    key: toId(candidate.name),
  })).filter((entry) => entry.key);

  for (const slot of MOVE_SLOTS) {
    if (!locks.moves[slot]) continue;
    const name = current.moves[slot]?.name.trim() ?? "";
    const key = toId(name);
    if (!key || used.has(key)) return null;
    moves[slot] = name;
    used.add(key);
  }

  // Keep an already-present candidate move in its current slot so move order alone
  // never appears as a recommendation.
  for (const slot of MOVE_SLOTS) {
    if (moves[slot]) continue;
    const currentName = current.moves[slot]?.name.trim() ?? "";
    const currentKey = toId(currentName);
    const match = available.find((entry) => entry.key === currentKey && !used.has(entry.key));
    if (!match) continue;
    moves[slot] = currentName;
    evidence[slot] = match.evidence;
    sources[slot] = match.source;
    used.add(match.key);
  }

  for (const slot of MOVE_SLOTS) {
    if (moves[slot]) continue;
    const match = available.find((entry) => !used.has(entry.key));
    if (!match) return null;
    moves[slot] = match.name;
    evidence[slot] = match.evidence;
    sources[slot] = match.source;
    used.add(match.key);
  }

  return { moves: moves as string[], evidence, sources };
}

function proposalFromPreset(
  current: PokemonSet,
  preset: OpponentMetaPreset,
  locks: WarRoomPokemonLocks,
) {
  const mergedMoves = mergeCandidateMoves(current, preset.moves.map((name, index) => ({
    name,
    evidence: preset.evidence.moves[index] ?? null,
    source: "battle-data" as const,
  })), locks);
  if (!mergedMoves) return null;
  return {
    proposal: {
      item: locks.item ? current.item : preset.item,
      ability: locks.ability ? current.ability : preset.ability,
      nature: locks.nature ? current.nature : preset.nature,
      evs: locks.statPoints ? current.evs : preset.evs,
      moves: mergedMoves.moves,
    },
    evidence: {
      item: locks.item ? null : preset.evidence.item,
      ability: locks.ability ? null : preset.evidence.ability,
      nature: locks.nature ? null : preset.evidence.nature,
      statPoints: locks.statPoints ? null : preset.evidence.statPoints,
      moves: mergedMoves.evidence,
    } satisfies WarRoomProposalEvidence,
    sources: {
      item: "battle-data",
      ability: "battle-data",
      nature: "battle-data",
      statPoints: "battle-data",
      moves: mergedMoves.sources,
    } satisfies WarRoomProposalSources,
  };
}

function proposalFromObservedSet(
  current: PokemonSet,
  observed: PokemonSet,
  locks: WarRoomPokemonLocks,
  preset?: OpponentMetaPreset,
) {
  const field = (
    locked: boolean,
    currentValue: string,
    observedValue: string,
    metaValue: string,
    metaEvidence: number | null,
  ) => {
    if (locked) return { value: currentValue, evidence: null, source: "paste" as const };
    if (observedValue.trim()) return { value: observedValue.trim(), evidence: null, source: "paste" as const };
    return { value: metaValue.trim(), evidence: metaValue.trim() ? metaEvidence : null, source: "battle-data" as const };
  };
  const item = field(locks.item, current.item, observed.item, preset?.item ?? "", preset?.evidence.item ?? null);
  const ability = field(locks.ability, current.ability, observed.ability, preset?.ability ?? "", preset?.evidence.ability ?? null);
  const nature = field(locks.nature, current.nature, observed.nature, preset?.nature ?? "", preset?.evidence.nature ?? null);
  const statPoints = field(locks.statPoints, current.evs, observed.evs, preset?.evs ?? "", preset?.evidence.statPoints ?? null);
  const observedMoves: CandidateMove[] = observed.moves.map((move) => ({ name: move.name, evidence: null, source: "paste" }));
  const observedMoveKeys = new Set(observedMoves.map((move) => toId(move.name)));
  const knownKeys = new Set(observedMoveKeys);
  const metaMoves: CandidateMove[] = observedMoveKeys.size >= 4 ? [] : (preset?.moves ?? []).flatMap((name, index) => (
    knownKeys.has(toId(name)) ? [] : [{ name, evidence: preset?.evidence.moves[index] ?? null, source: "battle-data" as const }]
  ));
  const mergedMoves = mergeCandidateMoves(current, [...observedMoves, ...metaMoves], locks);
  if (!mergedMoves) return null;
  return {
    proposal: {
      item: item.value,
      ability: ability.value,
      nature: nature.value,
      evs: statPoints.value,
      moves: mergedMoves.moves,
    },
    evidence: {
      item: item.evidence,
      ability: ability.evidence,
      nature: nature.evidence,
      statPoints: statPoints.evidence,
      moves: mergedMoves.evidence,
    } satisfies WarRoomProposalEvidence,
    sources: {
      item: item.source,
      ability: ability.source,
      nature: nature.source,
      statPoints: statPoints.source,
      moves: mergedMoves.sources,
    } satisfies WarRoomProposalSources,
  };
}

function proposalIsLegal(
  team: PokemonSet[],
  current: PokemonSet,
  proposal: WarRoomSetProposal,
  snapshot: ShowdownSnapshot,
) {
  const itemKey = toId(proposal.item);
  if (itemKey && !isItemLegal(snapshot, proposal.item, WAR_ROOM_BATTLE_FORMAT)) return false;
  if (itemKey && team.some((set) => set.id !== current.id && toId(set.item) === itemKey)) return false;
  if (!getLegalAbilities(snapshot, current.species, WAR_ROOM_BATTLE_FORMAT).includes(proposal.ability)) return false;
  if (!NATURES.includes(proposal.nature) || !validStatPointLabel(proposal.evs)) return false;
  const moveKeys = proposal.moves.map(toId);
  if (moveKeys.length !== 4 || moveKeys.some((key) => !key) || new Set(moveKeys).size !== 4) return false;
  return proposal.moves.every((move) => isMoveLegal(snapshot, current.species, move, WAR_ROOM_BATTLE_FORMAT));
}

function setChanges(
  current: PokemonSet,
  proposal: WarRoomSetProposal,
  evidence: WarRoomProposalEvidence,
  sources: WarRoomProposalSources,
): WarRoomSetChange[] {
  const changes: WarRoomSetChange[] = [];
  if (toId(current.item) !== toId(proposal.item)) changes.push({ key: "item", field: "Objeto", current: current.item || "Sin objeto", suggested: proposal.item || "Sin objeto", evidence: evidence.item, source: sources.item });
  if (toId(current.ability) !== toId(proposal.ability)) changes.push({ key: "ability", field: "Habilidad", current: current.ability || "Sin declarar", suggested: proposal.ability || "Sin declarar", evidence: evidence.ability, source: sources.ability });
  if (toId(current.nature) !== toId(proposal.nature)) changes.push({ key: "nature", field: "Naturaleza", current: current.nature || "Sin declarar", suggested: proposal.nature || "Sin declarar", evidence: evidence.nature, source: sources.nature });
  if (current.evs.trim() !== proposal.evs.trim()) changes.push({ key: "statPoints", field: "Stat Points", current: current.evs || "0", suggested: proposal.evs || "0", evidence: evidence.statPoints, source: sources.statPoints });
  for (const slot of MOVE_SLOTS) {
    const currentMove = current.moves[slot]?.name ?? "";
    const suggestedMove = proposal.moves[slot] ?? "";
    if (toId(currentMove) === toId(suggestedMove)) continue;
    changes.push({
      key: `move-${slot}`,
      field: `Movimiento ${slot + 1}`,
      current: currentMove || "Vacío",
      suggested: suggestedMove || "Vacío",
      evidence: evidence.moves[slot] ?? null,
      source: sources.moves[slot] ?? "paste",
    });
  }
  return changes;
}

function averageChangeEvidence(changes: WarRoomSetChange[]) {
  const values = changes.flatMap((change) => change.evidence === null ? [] : [change.evidence]);
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function setSuggestions(
  team: PokemonSet[],
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
  metaBySpecies: Record<string, OpponentMetaResponse | undefined>,
  optimizationLocks: WarRoomOptimizationLocks,
  pasteEvidence: readonly WarRoomPasteEvidenceTeam[],
) {
  const opponents = usageBySpecies(corpus).slice(0, 24).map((entry) => profileFromPreview(snapshot, entry.species)).filter((profile) => profile.types.length);
  const targetContext = teamStrategyContext(team);
  return team.flatMap((set): WarRoomSetSuggestion[] => {
    const meta = metaBySpecies[toId(set.species)];
    const locks = locksForSet(optimizationLocks, set.id);
    const currentProfile = profileFromSet(snapshot, set);
    const currentScore = structuralSetScore(currentProfile, opponents);
    const observedSets = pasteEvidence.flatMap((evidenceTeam) => evidenceTeam.sets.flatMap((observed) => (
      baseSpeciesKey(observed.species) === baseSpeciesKey(set.species) ? [{ evidenceTeam, observed }] : []
    )));
    const signatureCounts = new Map<string, number>();
    for (const { evidenceTeam, observed } of observedSets) {
      const signature = [evidenceTeam.formatId, toId(observed.item), toId(observed.ability), toId(observed.nature), observed.evs.trim().toLowerCase(), ...observed.moves.map((move) => toId(move.name)).sort()].join("|");
      signatureCounts.set(signature, (signatureCounts.get(signature) ?? 0) + 1);
    }
    let hasObservedCurrentMatch = false;
    const observedCandidates = observedSets.flatMap(({ evidenceTeam, observed }) => {
      const candidate = proposalFromObservedSet(set, observed, locks, meta?.presets[0]);
      if (
        !candidate?.proposal.item
        || !candidate.proposal.ability
        || !candidate.proposal.nature
        || !candidate.proposal.evs
        || candidate.proposal.moves.length !== 4
      ) return [];
      if (!candidate || !proposalIsLegal(team, set, candidate.proposal, snapshot)) return [];
      const context = proposalContextEvaluation(team, set, candidate.proposal, snapshot);
      if (!context.compatible) return [];
      const changes = setChanges(set, candidate.proposal, candidate.evidence, candidate.sources);
      if (!changes.length) {
        hasObservedCurrentMatch = true;
        return [];
      }
      const proposed = proposalToSet(snapshot, set, candidate.proposal);
      const profile = profileFromSet(snapshot, proposed);
      const structural = structuralSetScore(profile, opponents);
      const evidenceContext = teamStrategyContext(evidenceTeam.sets);
      const contextFit = clamp(round((strategyContextSimilarity(targetContext, evidenceContext) + context.score) / 2));
      const currentKeys = new Set(team.filter((entry) => entry.id !== set.id).map((entry) => baseSpeciesKey(entry.species)));
      const overlap = evidenceTeam.pokemon.filter((species) => currentKeys.has(baseSpeciesKey(species))).length;
      const signature = [evidenceTeam.formatId, toId(observed.item), toId(observed.ability), toId(observed.nature), observed.evs.trim().toLowerCase(), ...observed.moves.map((move) => toId(move.name)).sort()].join("|");
      const observations = signatureCounts.get(signature) ?? 1;
      const patchedFields = changes.filter((change) => change.source === "battle-data").map((change) => change.key);
      return [{
        proposal: candidate.proposal,
        changes,
        profile,
        structural,
        context,
        contextFit,
        observations,
        patchedFields,
        evidenceTeam,
        rankScore: structural + contextFit * 0.55 + evidenceTeam.quality * 0.25 + overlap * 7 + Math.min(10, observations * 2),
      }];
    }).sort((left, right) => (
      Number(left.evidenceTeam.historical) - Number(right.evidenceTeam.historical)
      || right.rankScore - left.rankScore
      || right.evidenceTeam.quality - left.evidenceTeam.quality
      || left.evidenceTeam.id.localeCompare(right.evidenceTeam.id)
    ));

    const observedBest = observedCandidates[0];
    if (observedBest) {
      const currentCoverage = opponents.filter((opponent) => profileThreatens(currentProfile, opponent)).length;
      const nextCoverage = opponents.filter((opponent) => profileThreatens(observedBest.profile, opponent)).length;
      const preservedFields = lockFieldsForSet(set, locks);
      const patched = observedBest.patchedFields.length > 0;
      return [{
        setId: set.id,
        species: set.species,
        presetId: `paste-${observedBest.evidenceTeam.id}-${observedBest.observations}`,
        structuralDelta: round(observedBest.structural - currentScore, 1),
        proposal: observedBest.proposal,
        changes: observedBest.changes,
        preservedFields,
        reasons: [
          `Set completo observado en ${observedBest.evidenceTeam.sourceLabel}${observedBest.evidenceTeam.tournament ? ` · ${observedBest.evidenceTeam.tournament}` : ""}; ${observedBest.observations} ${observedBest.observations === 1 ? "aparición compatible" : "apariciones compatibles"}.`,
          `Encaje contextual ${observedBest.contextFit}/100 con ${strategyContextLabel(observedBest.context.context)}.`,
          nextCoverage > currentCoverage
            ? `Añade cobertura supereficaz contra ${nextCoverage - currentCoverage} amenazas frecuentes.`
            : `Mantiene cobertura estructural sobre ${nextCoverage}/${opponents.length} amenazas frecuentes.`,
          patched
            ? `Battle Data completa únicamente ${observedBest.patchedFields.length} ${observedBest.patchedFields.length === 1 ? "campo ausente" : "campos ausentes"}.`
            : "Objeto, habilidad, naturaleza, Stat Points y movimientos proceden juntos del paste.",
          preservedFields.length ? `Respeta ${preservedFields.length} ${preservedFields.length === 1 ? "bloqueo activo" : "bloqueos activos"}.` : "No hay campos bloqueados en este integrante.",
        ],
        methodology: patched ? "observed-paste-patched" : "observed-paste",
        source: {
          teamId: observedBest.evidenceTeam.id,
          label: observedBest.evidenceTeam.sourceLabel,
          url: observedBest.evidenceTeam.sourceUrl,
          tournament: observedBest.evidenceTeam.tournament,
          rank: observedBest.evidenceTeam.rank,
          observations: observedBest.observations,
          contextFit: observedBest.contextFit,
          formatLabel: observedBest.evidenceTeam.formatLabel,
          historical: observedBest.evidenceTeam.historical,
        },
        patchedFields: observedBest.patchedFields,
      }];
    }
    if (hasObservedCurrentMatch) return [];

    const candidates = (meta?.presets ?? []).flatMap((preset) => {
      const candidate = proposalFromPreset(set, preset, locks);
      if (!candidate || !proposalIsLegal(team, set, candidate.proposal, snapshot)) return [];
      const context = proposalContextEvaluation(team, set, candidate.proposal, snapshot);
      if (!context.compatible) return [];
      const changes = setChanges(set, candidate.proposal, candidate.evidence, candidate.sources);
      if (!changes.length) return [];
      const proposed = proposalToSet(snapshot, set, candidate.proposal);
      const profile = profileFromSet(snapshot, proposed);
      const structural = structuralSetScore(profile, opponents);
      return [{
        preset,
        proposal: candidate.proposal,
        changes,
        profile,
        structural,
        context,
        rankScore: structural + averageChangeEvidence(changes) * 0.12 + context.score * 0.35,
      }];
    }).sort((left, right) => right.rankScore - left.rankScore || left.preset.rank - right.preset.rank);
    const best = candidates[0];
    if (!best) return [];
    const currentCoverage = opponents.filter((opponent) => profileThreatens(currentProfile, opponent)).length;
    const nextCoverage = opponents.filter((opponent) => profileThreatens(best.profile, opponent)).length;
    const addedRoles = best.profile.roles.filter((role) => !currentProfile.roles.includes(role));
    const preservedFields = lockFieldsForSet(set, locks);
    const reasons = [
      nextCoverage > currentCoverage
        ? `La cobertura supereficaz alcanza ${nextCoverage - currentCoverage} amenazas frecuentes adicionales.`
        : `Mantiene cobertura estructural sobre ${nextCoverage}/${opponents.length} amenazas frecuentes.`,
      addedRoles.length ? `Añade ${addedRoles.join(", ").toLowerCase()}.` : "No añade una función táctica nueva; se apoya principalmente en frecuencia de uso.",
      `Pasó la validación contextual del Team (${best.context.score}/100; ${strategyContextLabel(best.context.context)}).`,
      preservedFields.length
        ? `Respeta ${preservedFields.length} ${preservedFields.length === 1 ? "bloqueo activo" : "bloqueos activos"}.`
        : "No hay campos bloqueados en este integrante.",
    ];
    return [{
      setId: set.id,
      species: set.species,
      presetId: best.preset.id,
      structuralDelta: round(best.structural - currentScore, 1),
      proposal: best.proposal,
      changes: best.changes,
      preservedFields,
      reasons,
      methodology: "battle-data-fallback",
      source: null,
      patchedFields: best.changes.map((change) => change.key),
    }];
  }).sort((left, right) => right.structuralDelta - left.structuralDelta || left.species.localeCompare(right.species));
}

function memberSuggestions(
  team: PokemonSet[],
  lockedIds: Set<string>,
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
  options: WarRoomOptimizationOptions,
) {
  const teamProfiles = team.map((set) => profileFromSet(snapshot, set));
  const configuredMegas = teamProfiles.filter((profile) => profile.megaActive).length;
  const recommendationSlots = Math.max(0, MAX_WAR_ROOM_TEAM_MEGAS - configuredMegas);
  const locked = team.filter((set) => lockedIds.has(set.id));
  if (!locked.length) return {
    members: [] as WarRoomMemberSuggestion[],
    sampleSize: 0,
    mode: "none" as const,
    configuredMegas,
    recommendationSlots,
  };
  const lockedKeys = new Set(locked.map((set) => baseSpeciesKey(set.species)));
  const threshold = Math.max(1, Math.ceil(lockedKeys.size / 2));
  const comparablePool = (entries: readonly WarRoomCorpusTeam[]) => {
    const exact = entries.filter((entry) => {
      const keys = new Set(normalizedTeamSpecies(entry).map(baseSpeciesKey));
      return [...lockedKeys].every((key) => keys.has(key));
    });
    const partial = exact.length ? [] : entries.filter((entry) => {
      const keys = new Set(normalizedTeamSpecies(entry).map(baseSpeciesKey));
      return [...lockedKeys].filter((key) => keys.has(key)).length >= threshold;
    });
    return { exact, pool: exact.length ? exact : partial };
  };
  const currentComparison = comparablePool(corpus);
  const historicalCorpus = (options.historicalCorpus ?? []).filter((entry) => entry.historical);
  const historicalComparison = comparablePool(historicalCorpus);
  const pool = currentComparison.pool;
  const historicalPool = historicalComparison.pool;
  const mode = currentComparison.exact.length || historicalComparison.exact.length
    ? "exact" as const
    : pool.length || historicalPool.length
      ? "partial" as const
      : "none" as const;

  const currentKeys = new Set(team.map((set) => baseSpeciesKey(set.species)));
  const excludedKeys = new Set((options.excludedMemberSpecies ?? []).map(baseSpeciesKey));
  const candidates = new Map<string, {
    species: string;
    observedAs: string;
    observedPriority: number;
    observedWeight: number;
    currentCoreAppearances: number;
    historicalCoreAppearances: number;
    currentCorpusAppearances: number;
    weightedCurrentCoreAppearances: number;
    weightedHistoricalCoreAppearances: number;
    weightedCurrentCorpusAppearances: number;
    regulations: Set<string>;
  }>();
  const sourceWeight = (entry: WarRoomCorpusTeam) => (
    entry.source === "tournament"
    || (entry.source === "vgcpastes" && Boolean(entry.rank) && Boolean(entry.tournament) && entry.tournament !== "-")
  ) ? 1.2 : entry.source === "vgcpastes" ? 1 : 0.65;
  const collectCandidates = (teams: readonly WarRoomCorpusTeam[], scope: "current-core" | "historical-core" | "current-corpus") => {
    for (const entry of teams) {
      const regulation = getWarRoomRegulationEvidence(entry.formatId || WAR_ROOM_FORMAT_ID);
      const regulationWeight = regulation?.regulationWeight ?? 1;
      const evidenceWeight = sourceWeight(entry) * regulationWeight;
      const regulationLabel = regulation?.shortLabel ?? "M-C";
      const seen = new Set<string>();
      for (const observedAs of entry.pokemon) {
        const species = baseSpeciesLabel(observedAs);
        const key = baseSpeciesKey(species);
        if (!key || seen.has(key) || currentKeys.has(key) || lockedKeys.has(key) || excludedKeys.has(key)) continue;
        seen.add(key);
        const current = candidates.get(key) ?? {
          species,
          observedAs,
          observedPriority: 0,
          observedWeight: 0,
          currentCoreAppearances: 0,
          historicalCoreAppearances: 0,
          currentCorpusAppearances: 0,
          weightedCurrentCoreAppearances: 0,
          weightedHistoricalCoreAppearances: 0,
          weightedCurrentCorpusAppearances: 0,
          regulations: new Set<string>(),
        };
        const observedPriority = scope === "historical-core" ? 1 : 2;
        if (observedPriority > current.observedPriority || (observedPriority === current.observedPriority && evidenceWeight > current.observedWeight)) {
          current.species = species;
          current.observedAs = observedAs;
          current.observedPriority = observedPriority;
          current.observedWeight = evidenceWeight;
        }
        if (scope === "current-core") {
          current.currentCoreAppearances += 1;
          current.weightedCurrentCoreAppearances += evidenceWeight;
        } else if (scope === "historical-core") {
          current.historicalCoreAppearances += 1;
          current.weightedHistoricalCoreAppearances += evidenceWeight;
          current.regulations.add(regulationLabel);
        } else {
          current.currentCorpusAppearances += 1;
          current.weightedCurrentCorpusAppearances += evidenceWeight;
        }
        candidates.set(key, current);
      }
    }
  };
  // M-C is authoritative. Historical teams can contribute only comparable
  // partner relationships; current global frequency fills any remaining cards.
  collectCandidates(pool, "current-core");
  collectCandidates(historicalPool, "historical-core");
  collectCandidates(corpus, "current-corpus");

  const currentPenalty = teamDefensePenalty(teamProfiles);
  const unlocked = teamProfiles.filter((profile) => !lockedIds.has(profile.id));
  const openSlots = unlocked.filter((profile) => !profile.species.trim());
  const replacementPool = openSlots.length ? openSlots : unlocked;
  const maxCurrentCoreAppearances = Math.max(1, ...[...candidates.values()].map((candidate) => candidate.weightedCurrentCoreAppearances));
  const maxHistoricalCoreAppearances = Math.max(1, ...[...candidates.values()].map((candidate) => candidate.weightedHistoricalCoreAppearances));
  const maxCorpusAppearances = Math.max(1, ...[...candidates.values()].map((candidate) => candidate.weightedCurrentCorpusAppearances));
  const currentPoolWeight = Math.max(1, pool.reduce((sum, entry) => sum + sourceWeight(entry), 0));
  const historicalPoolWeight = Math.max(1, historicalPool.reduce((sum, entry) => {
    const regulation = getWarRoomRegulationEvidence(entry.formatId);
    return sum + sourceWeight(entry) * (regulation?.regulationWeight ?? 0);
  }, 0));
  const members = [...candidates.values()].flatMap((candidate): WarRoomMemberSuggestion[] => {
    const profile = profileFromPreview(snapshot, candidate.observedAs);
    if (!profile.types.length || !isSpeciesAvailable(snapshot, candidate.species, WAR_ROOM_BATTLE_FORMAT)) return [];
    if (profile.megaActive) {
      const rayquazaMega = toId(candidate.species) === "rayquaza" && toId(candidate.observedAs) === "rayquazamega";
      const megaItem = itemForObservedMega(snapshot, candidate.species, candidate.observedAs);
      if (!rayquazaMega && (!megaItem || !isItemLegal(snapshot, megaItem, WAR_ROOM_BATTLE_FORMAT))) return [];
    }
    const replacements = replacementPool.map((removed) => {
      const next = [...teamProfiles.filter((entry) => entry.id !== removed.id), profile];
      return { removed, delta: currentPenalty - teamDefensePenalty(next), next };
    }).sort((left, right) => right.delta - left.delta || left.removed.species.localeCompare(right.removed.species));
    if (!replacements.length) return [];
    const evidenceMode = candidate.currentCoreAppearances > 0
      ? "core" as const
      : candidate.historicalCoreAppearances > 0
        ? "historical" as const
        : "expanded" as const;
    const appearances = evidenceMode === "core"
      ? candidate.currentCoreAppearances
      : evidenceMode === "historical"
        ? candidate.historicalCoreAppearances
        : candidate.currentCorpusAppearances;
    const weightedAppearances = evidenceMode === "core"
      ? candidate.weightedCurrentCoreAppearances
      : evidenceMode === "historical"
        ? candidate.weightedHistoricalCoreAppearances
        : candidate.weightedCurrentCorpusAppearances;
    const sampleSize = evidenceMode === "core" ? pool.length : evidenceMode === "historical" ? historicalPool.length : corpus.length;
    const synergy = evidenceMode === "core"
      ? weightedAppearances / currentPoolWeight
      : evidenceMode === "historical"
        ? weightedAppearances / historicalPoolWeight
        : 0;
    const frequency = weightedAppearances / (evidenceMode === "core"
      ? maxCurrentCoreAppearances
      : evidenceMode === "historical"
        ? maxHistoricalCoreAppearances
        : maxCorpusAppearances);
    const historicalRegulations = [...candidate.regulations];
    const persistence = historicalRegulations.length / 3;
    const sortedHistoricalRegulations = historicalRegulations.sort((left, right) => ["M-B", "M-A", "SV-I"].indexOf(left) - ["M-B", "M-A", "SV-I"].indexOf(right));
    const evidenceRegulations = evidenceMode === "core"
      ? ["M-C", ...sortedHistoricalRegulations]
      : evidenceMode === "historical"
        ? sortedHistoricalRegulations
        : ["M-C"];
    return replacements.map(({ removed, delta, next }) => {
      const patchedTypes = POKEMON_TYPES.filter((type) => {
        const beforeWeak = teamProfiles.filter((entry) => defensiveMultiplier(type, entry) > 1).length;
        const beforeSafe = teamProfiles.filter((entry) => defensiveMultiplier(type, entry) < 1).length;
        const afterWeak = next.filter((entry) => defensiveMultiplier(type, entry) > 1).length;
        const afterSafe = next.filter((entry) => defensiveMultiplier(type, entry) < 1).length;
        return afterWeak - afterSafe < beforeWeak - beforeSafe;
      });
      const balance = clamp(50 + delta * 8) / 100;
      const score = clamp(round(100 * (evidenceMode === "core"
        ? 0.41 * synergy + 0.23 * frequency + 0.30 * balance + 0.06 * persistence
        : evidenceMode === "historical"
          ? 0.38 * synergy + 0.20 * frequency + 0.32 * balance + 0.10 * persistence
          : 0.35 * frequency + 0.65 * balance)));
      const replacementLabel = removed.species || `Slot ${removed.slot}`;
      return {
        species: candidate.species,
        observedAs: candidate.observedAs,
        isMega: profile.megaActive,
        score,
        appearancesWithCore: candidate.currentCoreAppearances + candidate.historicalCoreAppearances,
        sampleSize,
        usageRate: round(appearances / Math.max(1, sampleSize) * 100, 1),
        replaces: replacementLabel,
        replacesSetId: removed.id,
        patchedTypes: patchedTypes.slice(0, 4),
        evidenceMode,
        evidenceRegulations,
        currentAppearances: candidate.currentCoreAppearances,
        historicalAppearances: candidate.historicalCoreAppearances,
        reasons: [
          evidenceMode === "core"
            ? `Aparece junto al core en ${candidate.currentCoreAppearances}/${pool.length} equipos M-C comparables${historicalRegulations.length ? ` y persiste en ${historicalRegulations.join(", ")}` : ""}.`
            : evidenceMode === "historical"
              ? `La relación con el core aparece en ${candidate.historicalCoreAppearances}/${historicalPool.length} equipos históricos (${historicalRegulations.join(", ")}); se pondera por antigüedad y solo pasa si la especie es legal en M-C.`
              : `El lote del core se agotó; aparece en ${candidate.currentCorpusAppearances}/${corpus.length} equipos M-C del corpus ampliado.`,
          delta > 0 ? `Reduce el desequilibrio defensivo al reemplazar a ${replacementLabel}.` : `La prueba estructural para este hueco no garantiza una mejora defensiva al reemplazar a ${replacementLabel}.`,
          patchedTypes.length ? `Mejora el balance frente a ${patchedTypes.slice(0, 4).join(", ")}.` : evidenceMode === "expanded" ? "Se propone por frecuencia M-C y encaje estructural; no por coaparición directa con el core." : "Su valor procede de coaparición; no corrige una debilidad de tipos directa.",
        ],
      };
    });
  }).sort((left, right) => (
    (["core", "historical", "expanded"].indexOf(left.evidenceMode) - ["core", "historical", "expanded"].indexOf(right.evidenceMode))
    || right.score - left.score
    || right.appearancesWithCore - left.appearancesWithCore
    || left.species.localeCompare(right.species)
    || left.replacesSetId.localeCompare(right.replacesSetId)
  ));
  let recommendedMegas = 0;
  const limitedMembers: WarRoomMemberSuggestion[] = [];
  for (const replacement of replacementPool) {
    let slotSuggestions = 0;
    for (const member of members) {
      if (member.replacesSetId !== replacement.id) continue;
      if (member.isMega && recommendedMegas >= recommendationSlots) continue;
      limitedMembers.push(member);
      slotSuggestions += 1;
      if (member.isMega) recommendedMegas += 1;
      if (
        slotSuggestions >= MAX_WAR_ROOM_MEMBER_SUGGESTIONS_PER_SLOT
        || limitedMembers.length >= MAX_WAR_ROOM_MEMBER_SUGGESTIONS
      ) break;
    }
    if (limitedMembers.length >= MAX_WAR_ROOM_MEMBER_SUGGESTIONS) break;
  }
  return {
    members: limitedMembers,
    sampleSize: pool.length + historicalPool.length,
    mode,
    configuredMegas,
    recommendationSlots,
  };
}

export function optimizeTeam(
  team: PokemonSet[],
  lockInput: WarRoomOptimizationLockInput,
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
  metaBySpecies: Record<string, OpponentMetaResponse | undefined> = {},
  options: WarRoomOptimizationOptions = {},
): WarRoomOptimizationResult {
  const optimizationLocks = normalizeOptimizationLocks(team, lockInput);
  const lockedIds = new Set(team.filter((set) => locksForSet(optimizationLocks, set.id).identity).map((set) => set.id));
  const statistics = statisticalCorpus(corpus);
  const memberResult = memberSuggestions(team, lockedIds, corpus, snapshot, options);
  const pasteEvidence = options.pasteEvidence ?? [];
  const setResults = setSuggestions(team, statistics, snapshot, metaBySpecies, optimizationLocks, pasteEvidence);
  const teamSpecies = new Set(team.map((set) => baseSpeciesKey(set.species)));
  const matchedSets = pasteEvidence.reduce((total, evidenceTeam) => (
    total + evidenceTeam.sets.filter((set) => teamSpecies.has(baseSpeciesKey(set.species))).length
  ), 0);
  const observedResults = setResults.filter((suggestion) => suggestion.methodology !== "battle-data-fallback");
  const evidenceMode = observedResults.some((suggestion) => suggestion.methodology === "observed-paste-patched")
    ? "patched" as const
    : observedResults.length
      ? "observed" as const
      : setResults.length
        ? "fallback" as const
        : "unavailable" as const;
  const lockedSpecies = team.filter((set) => lockedIds.has(set.id)).map((set) => set.species);
  const locks = team.map((set): WarRoomPokemonLockSummary => {
    const setLocks = locksForSet(optimizationLocks, set.id);
    return {
      setId: set.id,
      species: set.species,
      fields: lockFieldsForSet(set, setLocks),
      fullyLocked: isSetFullyLocked(setLocks),
    };
  });
  return {
    regulation: CHAMPIONS_REGULATION,
    lockedSpecies,
    megaPolicy: {
      configured: memberResult.configuredMegas,
      maximum: MAX_WAR_ROOM_TEAM_MEGAS,
      recommendationSlots: memberResult.recommendationSlots,
    },
    coreSample: { size: memberResult.sampleSize, mode: memberResult.mode },
    pasteEvidence: {
      loadedTeams: pasteEvidence.length,
      matchedSets,
      mode: evidenceMode,
    },
    members: memberResult.members,
    sets: setResults,
    locks,
    notes: [
      "Identidad controla reemplazos de integrantes; los bloqueos de set conservan objeto, habilidad, naturaleza, Stat Points y cada movimiento de forma independiente.",
      memberResult.mode === "partial"
        ? "No hay equipos con el core completo en el corpus: las altas propuestas usan coincidencia parcial y están marcadas como exploratorias."
        : "Las altas se ordenan por coaparición M-C, relación histórica ponderada y frecuencia general M-C; dentro de cada grupo decide el encaje contextual y defensivo.",
      "M-B pesa 0.65, M-A 0.45 y SV-I 0.20. El histórico nunca modifica las frecuencias actuales, la auditoría ni los matchups.",
      "Toda especie histórica pasa primero la legalidad M-C. Los sets M-C tienen prioridad; SV-I solo aporta relaciones porque su sistema de Stat Points no es transferible.",
      "Partner Search limita las alternativas Mega a dos y descuenta las Megas que ya están configuradas en el Team.",
      pasteEvidence.length
        ? `Los sets priorizan ${pasteEvidence.length} pastes completos comparables; Battle Data solo rellena campos ausentes o actúa cuando no sobrevive ningún set observado.`
        : "Aún no se cargaron pastes completos comparables; Battle Data se marca explícitamente como fallback marginal.",
      "Las dependencias imposibles se descartan: una Seed exige su terreno y un setter de Trick Room rápido necesita un modo veloz alterno visible.",
      "War Room no modifica versiones: abre el Team Builder para que revises y guardes cualquier cambio como versión nueva.",
    ],
  };
}

export function warRoomMetaKey(species: string) {
  return toId(species);
}

export function emptyWarRoomMeta(): Record<string, OpponentMetaResponse | undefined> {
  return {};
}

export function validStatPointLabel(value: string) {
  const parsed = parseEvs(value);
  const rules = getStatRules(WAR_ROOM_BATTLE_FORMAT);
  return EV_STATS.every((stat) => parsed[stat] <= rules.perStatMax)
    && Object.values(parsed).reduce((sum, points) => sum + points, 0) <= rules.totalMax;
}
