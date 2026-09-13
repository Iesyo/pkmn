import { CHAMPIONS_REGULATION } from "./champions-regulation.mjs";
import { calculateDamage, createDamageDraft, defaultDamageField } from "./damage-calculator";
import type { OpponentMetaResponse, OpponentMetaPreset } from "./opponent-meta-presets";
import { toId } from "./pokemon-data";
import {
  getItemData,
  getLegalAbilities,
  getMoveData,
  getSpecies,
  isItemLegal,
  isMoveLegal,
  isSpeciesAvailable,
  type ShowdownSnapshot,
} from "./showdown-data";
import { EV_STATS, NATURES, getStatRules, parseEvs } from "./team-builder";
import { effectiveness, POKEMON_TYPES } from "./type-chart";
import type { PokemonSet, PokemonType } from "./types";
import type { ScoutingPasteSummary } from "./scouting-paste-library";
import {
  buildVgcPastesSheetUrl,
  type VgcPastesFormat,
  type VgcPastesTeam,
} from "./vgcpastes-scouting";
import { getVgcPastesScoutingSpeciesIdentity } from "./vgcpastes-scouting-search";

export const WAR_ROOM_SCHEMA_VERSION = 1;
export const WAR_ROOM_FORMAT_ID = "champions-m-c";
export const WAR_ROOM_BATTLE_FORMAT = "champions";
export const MAX_WAR_ROOM_CORPUS_TEAMS = 5_000;

export type WarRoomEvidenceScope = "exact-set" | "team-preview" | "corpus";
export type WarRoomSeverity = "blocker" | "high" | "medium" | "low";

export interface WarRoomCorpusTeam {
  id: string;
  source: "vgcpastes" | "scouting-library";
  savedPasteId: string;
  playerName: string;
  tournament: string;
  rank: string;
  dateShared: string;
  pokepasteUrl: string;
  pokemon: string[];
}

export interface WarRoomCorpusResponse {
  schemaVersion: typeof WAR_ROOM_SCHEMA_VERSION;
  regulation: typeof CHAMPIONS_REGULATION;
  format: VgcPastesFormat;
  fetchedAt: string;
  totalTeams: number;
  publicTeamCount: number;
  savedTeamCount: number;
  source: {
    label: "VGCPastes Repository";
    url: string;
  };
  teams: WarRoomCorpusTeam[];
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
  score: number;
  appearancesWithCore: number;
  sampleSize: number;
  usageRate: number;
  replaces: string;
  patchedTypes: PokemonType[];
  reasons: string[];
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
  methodology: "marginal-frequency-composite";
}

export interface WarRoomOptimizationResult {
  regulation: typeof CHAMPIONS_REGULATION;
  lockedSpecies: string[];
  coreSample: {
    size: number;
    mode: "exact" | "partial" | "none";
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
    for (const set of team) {
      if (identities.has(set.id)) normalized[set.id] = createWarRoomPokemonLocks(true);
    }
    return normalized;
  }

  for (const set of team) {
    const source = input[set.id];
    if (!source) continue;
    const locks: WarRoomPokemonLocks = {
      identity: source.identity === true,
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
  const publicTeams = teams.filter((team) => team.source === "vgcpastes");
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
    megaActive: toId(battleSpecies) !== toId(set.species),
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
    megaActive: false,
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
  return Boolean(
    team
    && typeof team.id === "string"
    && team.id.length > 0
    && (team.source === "vgcpastes" || team.source === "scouting-library")
    && typeof team.savedPasteId === "string"
    && typeof team.playerName === "string"
    && typeof team.tournament === "string"
    && typeof team.rank === "string"
    && typeof team.dateShared === "string"
    && typeof team.pokepasteUrl === "string"
    && Array.isArray(team.pokemon)
    && team.pokemon.length === 6
    && team.pokemon.every((species) => typeof species === "string" && species.length > 0 && species.length <= 64),
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
): WarRoomCorpusResponse {
  if (format.id !== WAR_ROOM_FORMAT_ID) throw new Error("War Room v1 solo admite la regulación vigente M-C");
  if (teams.length > MAX_WAR_ROOM_CORPUS_TEAMS) throw new Error("El corpus público de War Room supera el límite seguro");
  const publicTeams: WarRoomCorpusTeam[] = teams.map((team) => ({
    id: team.id,
    source: "vgcpastes",
    savedPasteId: "",
    playerName: team.playerName,
    tournament: team.tournament,
    rank: team.rank,
    dateShared: team.dateShared,
    pokepasteUrl: team.pokepasteUrl,
    pokemon: [...team.pokemon],
  }));
  const publicPasteUrls = new Set(publicTeams.map((team) => safePokepasteUrl(team.pokepasteUrl)).filter(Boolean));
  const privateTeams: WarRoomCorpusTeam[] = savedPastes.flatMap((paste): WarRoomCorpusTeam[] => {
    const formatKey = toId(paste.format);
    const currentFormat = formatKey === "mc" || formatKey.includes("championsmc");
    const pokepasteUrl = safePokepasteUrl(paste.sourceUrl);
    if (!currentFormat || paste.pokemon.length !== 6 || (pokepasteUrl && publicPasteUrls.has(pokepasteUrl))) return [];
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
    }];
  }).slice(0, MAX_WAR_ROOM_CORPUS_TEAMS - publicTeams.length);
  const merged = [...publicTeams, ...privateTeams];
  return {
    schemaVersion: WAR_ROOM_SCHEMA_VERSION,
    regulation: CHAMPIONS_REGULATION,
    format,
    fetchedAt,
    totalTeams: merged.length,
    publicTeamCount: publicTeams.length,
    savedTeamCount: privateTeams.length,
    source: {
      label: "VGCPastes Repository",
      url: buildVgcPastesSheetUrl(format),
    },
    teams: merged,
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
    && typeof root.savedTeamCount === "number"
    && Number.isInteger(root.savedTeamCount)
    && root.savedTeamCount >= 0
    && root.publicTeamCount + root.savedTeamCount === root.totalTeams
    && source?.label === "VGCPastes Repository"
    && typeof source.url === "string"
    && Array.isArray(root.teams)
    && root.teams.length === root.totalTeams
    && root.teams.every(isValidCorpusTeam),
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

function mergePresetMoves(
  current: PokemonSet,
  preset: OpponentMetaPreset,
  locks: WarRoomPokemonLocks,
) {
  const moves: Array<string | undefined> = Array.from({ length: 4 });
  const evidence: Array<number | null> = Array.from({ length: 4 }, () => null);
  const used = new Set<string>();
  const presetMoves = preset.moves.map((name, index) => ({
    name: name.trim(),
    key: toId(name),
    evidence: preset.evidence.moves[index] ?? null,
  })).filter((entry) => entry.key);

  for (const slot of MOVE_SLOTS) {
    if (!locks.moves[slot]) continue;
    const name = current.moves[slot]?.name.trim() ?? "";
    const key = toId(name);
    if (!key || used.has(key)) return null;
    moves[slot] = name;
    used.add(key);
  }

  // Keep an already-present meta move in its current slot so move order alone
  // never appears as a recommendation.
  for (const slot of MOVE_SLOTS) {
    if (moves[slot]) continue;
    const currentName = current.moves[slot]?.name.trim() ?? "";
    const currentKey = toId(currentName);
    const match = presetMoves.find((entry) => entry.key === currentKey && !used.has(entry.key));
    if (!match) continue;
    moves[slot] = currentName;
    evidence[slot] = match.evidence;
    used.add(match.key);
  }

  for (const slot of MOVE_SLOTS) {
    if (moves[slot]) continue;
    const match = presetMoves.find((entry) => !used.has(entry.key));
    if (!match) return null;
    moves[slot] = match.name;
    evidence[slot] = match.evidence;
    used.add(match.key);
  }

  return { moves: moves as string[], evidence };
}

function proposalFromPreset(
  current: PokemonSet,
  preset: OpponentMetaPreset,
  locks: WarRoomPokemonLocks,
) {
  const mergedMoves = mergePresetMoves(current, preset, locks);
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
): WarRoomSetChange[] {
  const changes: WarRoomSetChange[] = [];
  if (toId(current.item) !== toId(proposal.item)) changes.push({ key: "item", field: "Objeto", current: current.item || "Sin objeto", suggested: proposal.item || "Sin objeto", evidence: evidence.item });
  if (toId(current.ability) !== toId(proposal.ability)) changes.push({ key: "ability", field: "Habilidad", current: current.ability || "Sin declarar", suggested: proposal.ability || "Sin declarar", evidence: evidence.ability });
  if (toId(current.nature) !== toId(proposal.nature)) changes.push({ key: "nature", field: "Naturaleza", current: current.nature || "Sin declarar", suggested: proposal.nature || "Sin declarar", evidence: evidence.nature });
  if (current.evs.trim() !== proposal.evs.trim()) changes.push({ key: "statPoints", field: "Stat Points", current: current.evs || "0", suggested: proposal.evs || "0", evidence: evidence.statPoints });
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
) {
  const opponents = usageBySpecies(corpus).slice(0, 24).map((entry) => profileFromPreview(snapshot, entry.species)).filter((profile) => profile.types.length);
  return team.flatMap((set): WarRoomSetSuggestion[] => {
    const meta = metaBySpecies[toId(set.species)];
    if (!meta?.presets.length) return [];
    const locks = locksForSet(optimizationLocks, set.id);
    const currentProfile = profileFromSet(snapshot, set);
    const currentScore = structuralSetScore(currentProfile, opponents);
    const candidates = meta.presets.flatMap((preset) => {
      const candidate = proposalFromPreset(set, preset, locks);
      if (!candidate || !proposalIsLegal(team, set, candidate.proposal, snapshot)) return [];
      const changes = setChanges(set, candidate.proposal, candidate.evidence);
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
        rankScore: structural + averageChangeEvidence(changes) * 0.12,
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
      methodology: "marginal-frequency-composite",
    }];
  }).sort((left, right) => right.structuralDelta - left.structuralDelta || left.species.localeCompare(right.species));
}

function memberSuggestions(
  team: PokemonSet[],
  lockedIds: Set<string>,
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
) {
  const locked = team.filter((set) => lockedIds.has(set.id));
  if (!locked.length) return { members: [] as WarRoomMemberSuggestion[], sampleSize: 0, mode: "none" as const };
  const lockedKeys = new Set(locked.map((set) => baseSpeciesKey(set.species)));
  const exact = corpus.filter((entry) => {
    const keys = new Set(normalizedTeamSpecies(entry).map(baseSpeciesKey));
    return [...lockedKeys].every((key) => keys.has(key));
  });
  const threshold = Math.max(1, Math.ceil(lockedKeys.size / 2));
  const pool = exact.length ? exact : corpus.filter((entry) => {
    const keys = new Set(normalizedTeamSpecies(entry).map(baseSpeciesKey));
    return [...lockedKeys].filter((key) => keys.has(key)).length >= threshold;
  });
  const mode = exact.length ? "exact" as const : pool.length ? "partial" as const : "none" as const;
  if (!pool.length) return { members: [] as WarRoomMemberSuggestion[], sampleSize: 0, mode };

  const currentKeys = new Set(team.map((set) => baseSpeciesKey(set.species)));
  const candidates = new Map<string, { species: string; observedAs: string; appearances: number }>();
  for (const entry of pool) {
    const seen = new Set<string>();
    for (const observedAs of entry.pokemon) {
      const species = baseSpeciesLabel(observedAs);
      const key = baseSpeciesKey(species);
      if (!key || seen.has(key) || currentKeys.has(key) || lockedKeys.has(key)) continue;
      seen.add(key);
      const current = candidates.get(key) ?? { species, observedAs, appearances: 0 };
      current.appearances += 1;
      candidates.set(key, current);
    }
  }

  const teamProfiles = team.map((set) => profileFromSet(snapshot, set));
  const currentPenalty = teamDefensePenalty(teamProfiles);
  const unlocked = teamProfiles.filter((profile) => !lockedIds.has(profile.id));
  const maxAppearances = Math.max(1, ...[...candidates.values()].map((candidate) => candidate.appearances));
  const members = [...candidates.values()].flatMap((candidate): WarRoomMemberSuggestion[] => {
    const profile = profileFromPreview(snapshot, candidate.observedAs);
    if (!profile.types.length || !isSpeciesAvailable(snapshot, candidate.species, WAR_ROOM_BATTLE_FORMAT)) return [];
    const replacements = unlocked.map((removed) => {
      const next = [...teamProfiles.filter((entry) => entry.id !== removed.id), profile];
      return { removed, delta: currentPenalty - teamDefensePenalty(next), next };
    }).sort((left, right) => right.delta - left.delta || left.removed.species.localeCompare(right.removed.species));
    const best = replacements[0];
    if (!best) return [];
    const patchedTypes = POKEMON_TYPES.filter((type) => {
      const beforeWeak = teamProfiles.filter((entry) => defensiveMultiplier(type, entry) > 1).length;
      const beforeSafe = teamProfiles.filter((entry) => defensiveMultiplier(type, entry) < 1).length;
      const afterWeak = best.next.filter((entry) => defensiveMultiplier(type, entry) > 1).length;
      const afterSafe = best.next.filter((entry) => defensiveMultiplier(type, entry) < 1).length;
      return afterWeak - afterSafe < beforeWeak - beforeSafe;
    });
    const synergy = candidate.appearances / Math.max(1, pool.length);
    const frequency = candidate.appearances / maxAppearances;
    const balance = clamp(50 + best.delta * 8) / 100;
    const score = clamp(round(100 * (0.45 * synergy + 0.25 * frequency + 0.30 * balance)));
    return [{
      species: candidate.species,
      observedAs: candidate.observedAs,
      score,
      appearancesWithCore: candidate.appearances,
      sampleSize: pool.length,
      usageRate: round(candidate.appearances / Math.max(1, pool.length) * 100, 1),
      replaces: best.removed.species,
      patchedTypes: patchedTypes.slice(0, 4),
      reasons: [
        `Aparece junto al core en ${candidate.appearances}/${pool.length} equipos comparables.`,
        best.delta > 0 ? `Reduce el desequilibrio defensivo al reemplazar a ${best.removed.species}.` : `La mejor prueba estructural es reemplazar a ${best.removed.species}, sin mejora defensiva garantizada.`,
        patchedTypes.length ? `Mejora el balance frente a ${patchedTypes.slice(0, 4).join(", ")}.` : "Su valor procede de coaparición; no corrige una debilidad de tipos directa.",
      ],
    }];
  }).sort((left, right) => right.score - left.score || right.appearancesWithCore - left.appearancesWithCore || left.species.localeCompare(right.species)).slice(0, 8);
  return { members, sampleSize: pool.length, mode };
}

export function optimizeTeam(
  team: PokemonSet[],
  lockInput: WarRoomOptimizationLockInput,
  corpus: WarRoomCorpusTeam[],
  snapshot: ShowdownSnapshot,
  metaBySpecies: Record<string, OpponentMetaResponse | undefined> = {},
): WarRoomOptimizationResult {
  const optimizationLocks = normalizeOptimizationLocks(team, lockInput);
  const lockedIds = new Set(team.filter((set) => locksForSet(optimizationLocks, set.id).identity).map((set) => set.id));
  const statistics = statisticalCorpus(corpus);
  const memberResult = memberSuggestions(team, lockedIds, statistics, snapshot);
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
    coreSample: { size: memberResult.sampleSize, mode: memberResult.mode },
    members: memberResult.members,
    sets: setSuggestions(team, statistics, snapshot, metaBySpecies, optimizationLocks),
    locks,
    notes: [
      "Identidad controla reemplazos de integrantes; los bloqueos de set conservan objeto, habilidad, naturaleza, Stat Points y cada movimiento de forma independiente.",
      memberResult.mode === "partial"
        ? "No hay equipos con el core completo en el corpus: las altas propuestas usan coincidencia parcial y están marcadas como exploratorias."
        : "Las altas se ordenan por coaparición real con el core y por balance defensivo de tipos.",
      "Los paquetes de set combinan frecuencias marginales de Battle Data; no representan sets observados ni una combinación garantizada.",
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
