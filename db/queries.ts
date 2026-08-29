import { getMoveData, getSpeciesTypes } from "@/lib/pokemon-data";
import { hashPaste, parseShowdownPaste } from "@/lib/paste";
import { DEFAULT_BATTLE_FORMAT, DEFAULT_BATTLE_MECHANICS, formatVersion, normalizeMechanics } from "@/lib/team-builder";
import { calculateLeads, decoratePokemonPerformance } from "@/lib/team-stats";
import { analyzeScoutingEvidence } from "@/lib/scouting-analysis";
import { collectScoutingReplayEvidence, fetchShowdownReplay, type ScoutingReplayEvidence } from "@/lib/showdown-replay";
import type {
  MatchRecord,
  MatchResult,
  MoveSet,
  PokemonSet,
  PokemonType,
  ScoutingAnalysis,
  ScoutingResult,
  TeamGroup,
  TeamVersion,
} from "@/lib/types";
import { POKEMON_TYPES } from "@/lib/types";
import { getDatabase } from "./raw";

interface TeamRow {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

interface VersionRow {
  id: string;
  team_id: string;
  version_number: number;
  minor_version: number;
  format: string;
  mechanics_json: string;
  paste: string;
  paste_hash: string;
  created_at: string;
}

interface PokemonRow {
  id: string;
  team_version_id: string;
  slot: number;
  nickname: string;
  species: string;
  item: string;
  ability: string;
  level: number;
  tera_type: string | null;
  mechanics_json: string;
  evs: string;
  nature: string;
  moves_json: string;
  types_json: string;
}

interface MatchRow {
  id: string;
  team_version_id: string;
  result: MatchResult;
  opponent_name: string;
  opponent_paste: string;
  replay_url: string;
  selected_json: string;
  opponent_selected_json: string;
  lead_json: string;
  moves_used_json: string | null;
  rating: number | null;
  notes: string;
  played_at: string;
}

export class DomainError extends Error {
  constructor(
    message: string,
    readonly status = 400,
  ) {
    super(message);
    this.name = "DomainError";
  }
}

export async function getShowdownNames(): Promise<string[]> {
  const db = await getDatabase();
  const row = await db
    .prepare("SELECT value FROM app_settings WHERE key = 'showdown_names'")
    .first<{ value: string }>();
  return row ? parseArray<string>(row.value) : [];
}

export async function saveShowdownNames(names: string[]): Promise<string[]> {
  const normalized = [...new Set(names.map((name) => name.trim()).filter(Boolean))];
  if (!normalized.length) {
    throw new DomainError("Agrega al menos un nombre de Showdown.");
  }
  if (normalized.length > 10 || normalized.some((name) => name.length > 30)) {
    throw new DomainError("Puedes guardar hasta 10 nombres de 30 caracteres cada uno.");
  }
  const db = await getDatabase();
  await db
    .prepare("INSERT INTO app_settings (key, value, updated_at) VALUES ('showdown_names', ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP")
    .bind(JSON.stringify(normalized))
    .run();
  return normalized;
}

function parseArray<T>(value: string, fallback: T[] = []) {
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? (parsed as T[]) : fallback;
  } catch {
    return fallback;
  }
}

interface ScoutingRow {
  id: string;
  match_id: string;
  status: ScoutingAnalysis["status"];
  progress: number;
  stage: string;
  checkpoint_json: string;
  result_json: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

function parseMovesUsed(value: string | null | undefined) {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter(([species, moves]) => species.trim() && Array.isArray(moves))
        .map(([species, moves]) => [
          species.trim(),
          [...new Set((moves as unknown[]).filter((move): move is string => typeof move === "string").map((move) => move.trim()).filter(Boolean))],
        ]),
    );
  } catch {
    return null;
  }
}

type BuilderSetInput = Partial<Pick<PokemonSet, "types" | "moves" | "mechanics">>;

function applyBuilderMetadata(pokemon: PokemonSet[], builderSets?: BuilderSetInput[]) {
  if (!builderSets) return pokemon;
  return pokemon.map((set, index) => {
    const metadata = builderSets[index];
    if (!metadata) return set;
    const types = (metadata.types ?? []).filter((type): type is PokemonType => POKEMON_TYPES.includes(type as PokemonType)).slice(0, 2);
    const moves = set.moves.map((move, moveIndex) => {
      const supplied = metadata.moves?.[moveIndex];
      const type = supplied?.type && POKEMON_TYPES.includes(supplied.type) ? supplied.type : move.type;
      return { ...move, type, damaging: supplied?.damaging ?? move.damaging };
    });
    const dynamaxLevel = Math.min(10, Math.max(0, Number(metadata.mechanics?.dynamaxLevel ?? 10)));
    return {
      ...set,
      types: types.length ? types : set.types,
      moves,
      mechanics: {
        dynamaxLevel,
        gigantamax: Boolean(metadata.mechanics?.gigantamax),
      },
    };
  });
}

function builderSignature(pokemon: PokemonSet[]) {
  return JSON.stringify(pokemon.map((set) => ({
    types: set.types,
    moves: set.moves.map((move) => ({ type: move.type, damaging: move.damaging })),
    mechanics: {
      dynamaxLevel: set.mechanics?.dynamaxLevel ?? 10,
      gigantamax: Boolean(set.mechanics?.gigantamax),
    },
  })));
}

function toMatch(row: MatchRow): MatchRecord {
  return {
    id: row.id,
    result: row.result,
    opponentName: row.opponent_name,
    opponentPaste: row.opponent_paste,
    replayUrl: row.replay_url,
    selected: parseArray<string>(row.selected_json),
    opponentSelected: parseArray<string>(row.opponent_selected_json),
    lead: parseArray<string>(row.lead_json),
    movesUsed: parseMovesUsed(row.moves_used_json),
    rating: row.rating,
    notes: row.notes,
    playedAt: row.played_at,
  };
}

function toPokemon(row: PokemonRow): PokemonSet {
  const rawMoves = parseArray<Partial<MoveSet>>(row.moves_json);
  return {
    id: row.id,
    slot: row.slot,
    nickname: row.nickname,
    species: row.species,
    item: row.item,
    ability: row.ability,
    level: row.level,
    teraType: row.tera_type as PokemonType | null,
    mechanics: JSON.parse(row.mechanics_json || "{}") as PokemonSet["mechanics"],
    evs: row.evs,
    nature: row.nature,
    moves: rawMoves.map((move) => {
      const name = move.name ?? "Movimiento";
      const data = getMoveData(name);
      return {
        name,
        type: (move.type as PokemonType | null | undefined) ?? data.type,
        damaging: move.damaging ?? data.damaging,
        usage: move.usage ?? 0,
      };
    }),
    types: parseArray<PokemonType>(row.types_json, getSpeciesTypes(row.species)),
    performance: {
      games: 0,
      wins: 0,
      leadGames: 0,
      leadWins: 0,
      selectionRate: 0,
    },
  };
}

function assembleVersion(
  team: TeamRow,
  version: VersionRow,
  pokemonRows: PokemonRow[],
  matchRows: MatchRow[],
): TeamVersion {
  const matches = matchRows
    .filter((match) => match.team_version_id === version.id)
    .map(toMatch)
    .sort((a, b) => b.playedAt.localeCompare(a.playedAt));
  const pokemon = decoratePokemonPerformance(
    pokemonRows
      .filter((set) => set.team_version_id === version.id)
      .map(toPokemon)
      .sort((a, b) => a.slot - b.slot),
    matches,
  );

  return {
    id: version.id,
    teamId: team.id,
    name: team.name,
    version: version.version_number,
    minorVersion: version.minor_version,
    format: version.format,
    mechanics: normalizeMechanics(parseArray<string>(version.mechanics_json)),
    paste: version.paste,
    createdAt: version.created_at,
    pokemon,
    matches,
    games: matches.length,
    wins: matches.filter((match) => match.result === "win").length,
    leads: calculateLeads(matches),
  };
}

export async function listTeamGroups(): Promise<TeamGroup[]> {
  const db = await getDatabase();
  const [teamResult, versionResult, pokemonResult, matchResult] = await Promise.all([
    db.prepare("SELECT id, name, created_at, updated_at FROM teams ORDER BY updated_at DESC, name ASC").all<TeamRow>(),
    db.prepare("SELECT id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash, created_at FROM team_versions ORDER BY team_id, version_number DESC, minor_version DESC").all<VersionRow>(),
    db.prepare("SELECT id, team_version_id, slot, nickname, species, item, ability, level, tera_type, mechanics_json, evs, nature, moves_json, types_json FROM pokemon_sets ORDER BY team_version_id, slot").all<PokemonRow>(),
    db.prepare("SELECT id, team_version_id, result, opponent_name, opponent_paste, replay_url, selected_json, opponent_selected_json, lead_json, moves_used_json, rating, notes, played_at FROM matches ORDER BY played_at DESC").all<MatchRow>(),
  ]);

  return teamResult.results.map((team) => ({
    id: team.id,
    name: team.name,
    versions: versionResult.results
      .filter((version) => version.team_id === team.id)
      .map((version) =>
        assembleVersion(team, version, pokemonResult.results, matchResult.results),
      ),
  }));
}

function pokemonInsertStatements(
  db: D1Database,
  teamVersionId: string,
  pokemon: PokemonSet[],
) {
  return pokemon.map((set) =>
    db
      .prepare(
        "INSERT INTO pokemon_sets (id, team_version_id, slot, nickname, species, item, ability, level, tera_type, mechanics_json, evs, nature, moves_json, types_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        crypto.randomUUID(),
        teamVersionId,
        set.slot,
        set.nickname,
        set.species,
        set.item,
        set.ability,
        set.level,
        set.teraType,
        JSON.stringify(set.mechanics ?? {}),
        set.evs,
        set.nature,
        JSON.stringify(set.moves),
        JSON.stringify(set.types),
      ),
  );
}

export async function createTeam(name: string, paste: string, format = DEFAULT_BATTLE_FORMAT, mechanicValues: string[] = [...DEFAULT_BATTLE_MECHANICS], builderSets?: BuilderSetInput[]) {
  const cleanName = name.trim();
  if (cleanName.length < 2 || cleanName.length > 80) {
    throw new DomainError("El nombre debe tener entre 2 y 80 caracteres.");
  }
  const pokemon = applyBuilderMetadata(parseShowdownPaste(paste), builderSets);
  const db = await getDatabase();
  const teamId = crypto.randomUUID();
  const versionId = crypto.randomUUID();
  const normalizedPaste = paste.replace(/\r\n?/g, "\n").trim();
  const mechanics = normalizeMechanics(mechanicValues).sort();
  const pasteHash = await hashPaste(`${normalizedPaste}\n#${format}\n#${JSON.stringify([...mechanics].sort())}\n#${builderSignature(pokemon)}`);

  await db.batch([
    db.prepare("INSERT INTO teams (id, name) VALUES (?, ?)").bind(teamId, cleanName),
    db
      .prepare(
        "INSERT INTO team_versions (id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash) VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
      )
      .bind(versionId, teamId, 1, format, JSON.stringify(mechanics), normalizedPaste, pasteHash),
    ...pokemonInsertStatements(db, versionId, pokemon),
  ]);

  return (await listTeamGroups()).find((team) => team.id === teamId)!;
}

export async function createTeamVersion(teamId: string, paste: string, format?: string, mechanicValues?: string[], builderSets?: BuilderSetInput[]) {
  const pokemon = applyBuilderMetadata(parseShowdownPaste(paste), builderSets);
  const normalizedPaste = paste.replace(/\r\n?/g, "\n").trim();
  const db = await getDatabase();
  const team = await db
    .prepare(
      "SELECT id, name, created_at, updated_at FROM teams WHERE id = ?",
    )
    .bind(teamId)
    .first<TeamRow>();

  if (!team) throw new DomainError("No encontramos ese equipo.", 404);
  const latest = await db
    .prepare("SELECT id, version_number, minor_version, format, mechanics_json, paste FROM team_versions WHERE team_id = ? ORDER BY version_number DESC, minor_version DESC LIMIT 1")
    .bind(teamId)
    .first<{ id: string; version_number: number; minor_version: number; format: string; mechanics_json: string; paste: string }>();
  if (!latest) throw new DomainError("No encontramos una versión base.", 404);
  const currentPokemonRows = await db
    .prepare("SELECT id, team_version_id, slot, nickname, species, item, ability, level, tera_type, mechanics_json, evs, nature, moves_json, types_json FROM pokemon_sets WHERE team_version_id = ? ORDER BY slot")
    .bind(latest.id)
    .all<PokemonRow>();
  const currentPokemon = currentPokemonRows.results.map(toPokemon);
  const nextFormat = format ?? latest.format;
  const nextMechanics = (mechanicValues ? normalizeMechanics(mechanicValues) : normalizeMechanics(parseArray<string>(latest.mechanics_json))).sort();
  const currentMechanics = normalizeMechanics(parseArray<string>(latest.mechanics_json)).sort();
  const sameConfiguration = normalizedPaste === latest.paste && nextFormat === latest.format && JSON.stringify(nextMechanics) === JSON.stringify(currentMechanics) && builderSignature(pokemon) === builderSignature(currentPokemon);
  if (sameConfiguration) {
    throw new DomainError(`No hay cambios; esta configuración ya es v${formatVersion({ version: latest.version_number, minorVersion: latest.minor_version })}.`, 409);
  }
  const pasteHash = await hashPaste(`${normalizedPaste}\n#${nextFormat}\n#${JSON.stringify([...nextMechanics].sort())}\n#${builderSignature(pokemon)}`);
  const duplicate = await db
    .prepare("SELECT id, version_number, minor_version FROM team_versions WHERE team_id = ? AND (paste_hash = ? OR (? = 0 AND paste = ? AND format = ? AND mechanics_json = ?))")
    .bind(teamId, pasteHash, builderSets ? 1 : 0, normalizedPaste, nextFormat, JSON.stringify(nextMechanics))
    .first<{ id: string; version_number: number; minor_version: number }>();
  if (duplicate) {
    throw new DomainError(
      `Esta configuración ya existe como v${formatVersion({ version: duplicate.version_number, minorVersion: duplicate.minor_version })}.`,
      409,
    );
  }
  const rosterChanged = currentPokemon.map((set) => set.species.toLowerCase()).sort().join("|") !== pokemon.map((set) => set.species.toLowerCase()).sort().join("|");
  const rulesChanged = nextFormat !== latest.format || JSON.stringify(nextMechanics) !== JSON.stringify(currentMechanics);
  const versionId = crypto.randomUUID();
  const versionNumber = rosterChanged || rulesChanged ? latest.version_number + 1 : latest.version_number;
  const minorVersion = rosterChanged || rulesChanged ? 0 : latest.minor_version + 1;
  await db.batch([
    db
      .prepare(
        "INSERT INTO team_versions (id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(versionId, teamId, versionNumber, minorVersion, nextFormat, JSON.stringify(nextMechanics), normalizedPaste, pasteHash),
    ...pokemonInsertStatements(db, versionId, pokemon),
    db.prepare("UPDATE teams SET updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(teamId),
  ]);

  const group = (await listTeamGroups()).find((entry) => entry.id === teamId)!;
  return group.versions.find((version) => version.id === versionId)!;
}

export interface CreateMatchInput {
  teamVersionId: string;
  result: MatchResult;
  opponentName?: string;
  opponentPaste?: string;
  replayUrl?: string;
  selected?: string[];
  opponentSelected?: string[];
  lead?: string[];
  movesUsed?: Record<string, string[]> | null;
  rating?: number | null;
  notes?: string;
  playedAt?: string;
}

export async function createMatch(input: CreateMatchInput) {
  if (input.result !== "win" && input.result !== "loss") {
    throw new DomainError("El resultado debe ser victoria o derrota.");
  }
  if (
    input.replayUrl &&
    !input.replayUrl.startsWith("https://replay.pokemonshowdown.com/")
  ) {
    throw new DomainError("El replay debe pertenecer a replay.pokemonshowdown.com.");
  }
  if ((input.opponentSelected?.length ?? 0) > 6) {
    throw new DomainError("El equipo rival puede contener como máximo 6 Pokémon.");
  }
  const movesUsed = input.movesUsed
    ? Object.fromEntries(
        Object.entries(input.movesUsed)
          .slice(0, 6)
          .map(([species, moves]) => [
            species.trim().slice(0, 80),
            [...new Set(moves.map((move) => move.trim()).filter(Boolean))].slice(0, 24),
          ])
          .filter(([species]) => species),
      )
    : null;

  const db = await getDatabase();
  const version = await db
    .prepare("SELECT id FROM team_versions WHERE id = ?")
    .bind(input.teamVersionId)
    .first<{ id: string }>();
  if (!version) throw new DomainError("No encontramos esa versión del equipo.", 404);

  const match: MatchRecord = {
    id: crypto.randomUUID(),
    result: input.result,
    opponentName: input.opponentName?.trim() || "Rival",
    opponentPaste: input.opponentPaste?.trim() || "",
    replayUrl: input.replayUrl?.trim() || "",
    selected: input.selected ?? [],
    opponentSelected: input.opponentSelected ?? [],
    lead: input.lead ?? [],
    movesUsed,
    rating: input.rating ?? null,
    notes: input.notes?.trim() || "",
    playedAt: input.playedAt ?? new Date().toISOString(),
  };

  await db
    .prepare(
      "INSERT INTO matches (id, team_version_id, result, opponent_name, opponent_paste, replay_url, selected_json, opponent_selected_json, lead_json, moves_used_json, rating, notes, played_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      match.id,
      input.teamVersionId,
      match.result,
      match.opponentName,
      match.opponentPaste,
      match.replayUrl,
      JSON.stringify(match.selected),
      JSON.stringify(match.opponentSelected),
      JSON.stringify(match.lead),
      match.movesUsed ? JSON.stringify(match.movesUsed) : null,
      match.rating,
      match.notes,
      match.playedAt,
    )
    .run();

  return match;
}

function parseObject<T>(value: string | null | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    const parsed = JSON.parse(value) as T;
    return parsed && typeof parsed === "object" ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function toScoutingAnalysis(row: ScoutingRow): ScoutingAnalysis {
  return {
    id: row.id,
    matchId: row.match_id,
    status: row.status,
    progress: row.progress,
    stage: row.stage,
    result: parseObject<ScoutingResult | null>(row.result_json, null),
    error: row.error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

const SCOUTING_COLUMNS = "id, match_id, status, progress, stage, checkpoint_json, result_json, error, created_at, updated_at";

async function scoutingRow(matchId: string) {
  const db = await getDatabase();
  return db
    .prepare(`SELECT ${SCOUTING_COLUMNS} FROM scouting_analyses WHERE match_id = ?`)
    .bind(matchId)
    .first<ScoutingRow>();
}

export async function getScoutingAnalysis(matchId: string): Promise<ScoutingAnalysis | null> {
  const row = await scoutingRow(matchId);
  return row ? toScoutingAnalysis(row) : null;
}

export async function listActiveScoutingAnalyses(): Promise<ScoutingAnalysis[]> {
  const db = await getDatabase();
  const rows = await db
    .prepare(`SELECT ${SCOUTING_COLUMNS} FROM scouting_analyses WHERE status IN ('queued', 'running') ORDER BY updated_at ASC`)
    .all<ScoutingRow>();
  return rows.results.map(toScoutingAnalysis);
}

async function scoutingContext(matchId: string) {
  const db = await getDatabase();
  const match = await db
    .prepare(
      `SELECT m.id, m.replay_url, m.opponent_name, m.team_version_id, v.format
       FROM matches m
       JOIN team_versions v ON v.id = m.team_version_id
       WHERE m.id = ?`,
    )
    .bind(matchId)
    .first<{ id: string; replay_url: string; opponent_name: string; team_version_id: string; format: string }>();
  if (!match) throw new DomainError("No encontramos esa partida.", 404);
  if (!match.replay_url) throw new DomainError("Esta partida no tiene un replay de Showdown para analizar.", 422);
  const ownRows = await db
    .prepare("SELECT id, team_version_id, slot, nickname, species, item, ability, level, tera_type, mechanics_json, evs, nature, moves_json, types_json FROM pokemon_sets WHERE team_version_id = ? ORDER BY slot")
    .bind(match.team_version_id)
    .all<PokemonRow>();
  return { match, ownTeam: ownRows.results.map(toPokemon) };
}

export async function startScoutingAnalysis(matchId: string): Promise<ScoutingAnalysis> {
  await scoutingContext(matchId);
  const db = await getDatabase();
  const id = crypto.randomUUID();
  await db
    .prepare(
      `INSERT INTO scouting_analyses (id, match_id, status, progress, stage, checkpoint_json, result_json, error, updated_at)
       VALUES (?, ?, 'queued', 0, 'Preparando replay', '{}', NULL, NULL, CURRENT_TIMESTAMP)
       ON CONFLICT(match_id) DO UPDATE SET
         status = 'queued', progress = 0, stage = 'Preparando replay', checkpoint_json = '{}',
         result_json = NULL, error = NULL, calculator_revision = 'champions-v1', updated_at = CURRENT_TIMESTAMP`,
    )
    .bind(id, matchId)
    .run();
  return (await getScoutingAnalysis(matchId))!;
}

export async function runScoutingAnalysisStep(matchId: string): Promise<ScoutingAnalysis> {
  const current = await scoutingRow(matchId);
  if (!current) throw new DomainError("Inicia primero el análisis de esta partida.", 404);
  if (current.status === "complete" || current.status === "error") return toScoutingAnalysis(current);
  const db = await getDatabase();

  try {
    const context = await scoutingContext(matchId);
    if (current.progress < 40) {
      const [{ replay }, showdownNames] = await Promise.all([
        fetchShowdownReplay(context.match.replay_url),
        getShowdownNames(),
      ]);
      const evidence = collectScoutingReplayEvidence(replay, {
        showdownNames,
        teamSpecies: context.ownTeam.map((set) => set.species),
      });
      await db
        .prepare("UPDATE scouting_analyses SET status = 'running', progress = 40, stage = 'Evidencia del replay extraída', checkpoint_json = ?, updated_at = CURRENT_TIMESTAMP WHERE match_id = ?")
        .bind(JSON.stringify({ evidence }), matchId)
        .run();
      return (await getScoutingAnalysis(matchId))!;
    }

    if (current.progress < 85) {
      const checkpoint = parseObject<{ evidence?: ScoutingReplayEvidence }>(current.checkpoint_json, {});
      if (!checkpoint.evidence) throw new Error("El checkpoint del replay está incompleto.");
      const result = analyzeScoutingEvidence(checkpoint.evidence, {
        replayUrl: context.match.replay_url,
        format: context.match.format || "champions",
        ownTeam: context.ownTeam,
      });
      await db
        .prepare("UPDATE scouting_analyses SET status = 'running', progress = 85, stage = 'Intervalos de daño calculados', result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE match_id = ?")
        .bind(JSON.stringify(result), matchId)
        .run();
      return (await getScoutingAnalysis(matchId))!;
    }

    await db
      .prepare("UPDATE scouting_analyses SET status = 'complete', progress = 100, stage = 'Análisis listo', updated_at = CURRENT_TIMESTAMP WHERE match_id = ?")
      .bind(matchId)
      .run();
    return (await getScoutingAnalysis(matchId))!;
  } catch (error) {
    const message = error instanceof Error ? error.message : "No pudimos analizar este replay.";
    await db
      .prepare("UPDATE scouting_analyses SET status = 'error', stage = 'Análisis detenido', error = ?, updated_at = CURRENT_TIMESTAMP WHERE match_id = ?")
      .bind(message.slice(0, 500), matchId)
      .run();
    return (await getScoutingAnalysis(matchId))!;
  }
}

export interface MoveUsageBackfillCandidate {
  matchId: string;
  replayUrl: string;
  teamSpecies: string[];
}

export async function listMoveUsageBackfillCandidates(limit = 12): Promise<MoveUsageBackfillCandidate[]> {
  const db = await getDatabase();
  const safeLimit = Math.min(50, Math.max(1, Math.trunc(limit)));
  const rows = await db
    .prepare(
      `SELECT m.id AS match_id, m.replay_url, p.species, p.slot
       FROM matches m
       JOIN pokemon_sets p ON p.team_version_id = m.team_version_id
       WHERE m.id IN (
         SELECT id FROM matches
         WHERE moves_used_json IS NULL AND replay_url <> ''
         ORDER BY played_at DESC
         LIMIT ?
       )
       ORDER BY m.played_at DESC, p.slot ASC`,
    )
    .bind(safeLimit)
    .all<{ match_id: string; replay_url: string; species: string; slot: number }>();

  const candidates = new Map<string, MoveUsageBackfillCandidate>();
  for (const row of rows.results) {
    const current = candidates.get(row.match_id) ?? {
      matchId: row.match_id,
      replayUrl: row.replay_url,
      teamSpecies: [],
    };
    current.teamSpecies.push(row.species);
    candidates.set(row.match_id, current);
  }
  return [...candidates.values()].filter((candidate) => candidate.teamSpecies.length === 6);
}

export async function saveBackfilledMoveUsage(matchId: string, movesUsed: Record<string, string[]>) {
  const db = await getDatabase();
  await db
    .prepare("UPDATE matches SET moves_used_json = ? WHERE id = ? AND moves_used_json IS NULL")
    .bind(JSON.stringify(movesUsed), matchId)
    .run();
}
