import { toId } from "./pokemon-data";
import type { MatchResult } from "./types";

type PlayerSlot = "p1" | "p2";

export interface ShowdownReplayDocument {
  log: string;
  inputlog?: string | null;
  uploadtime?: number | string | null;
  p1?: string;
  p2?: string;
  p1rating?: unknown;
  p2rating?: unknown;
  format?: string;
}

interface ReplaySide {
  slot: PlayerSlot;
  name: string;
  initialRating: number | null;
  finalRating: number | null;
  team: string[];
  selected: string[];
  lead: string[];
  movesUsed: Record<string, string[]>;
}

export interface ImportedReplayMatch {
  replayUrl: string;
  result: MatchResult;
  playerName: string;
  opponentName: string;
  opponentSelected: string[];
  selected: string[];
  lead: string[];
  movesUsed: Record<string, string[]>;
  rating: number | null;
  playedAt: string | null;
  format: string;
  warnings: string[];
}

export class ReplayValidationError extends Error {
  constructor(
    message: string,
    readonly status = 400,
  ) {
    super(message);
    this.name = "ReplayValidationError";
  }
}

export function normalizeShowdownReplayUrl(value: string) {
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new ReplayValidationError("Pega un enlace válido de replay.pokemonshowdown.com.");
  }

  if (
    parsed.protocol !== "https:" ||
    parsed.hostname.toLowerCase() !== "replay.pokemonshowdown.com" ||
    parsed.port ||
    parsed.username ||
    parsed.password
  ) {
    throw new ReplayValidationError("El replay debe pertenecer a replay.pokemonshowdown.com.");
  }

  const replayId = decodeURIComponent(parsed.pathname)
    .replace(/^\/+|\/+$/g, "")
    .replace(/\.(?:json|log|inputlog)$/i, "");
  if (!/^[a-z0-9][a-z0-9-]*$/i.test(replayId)) {
    throw new ReplayValidationError("La URL no contiene un identificador de replay válido.");
  }

  const replayUrl = `https://replay.pokemonshowdown.com/${replayId}`;
  return { replayId, replayUrl, jsonUrl: `${replayUrl}.json` };
}

const MAX_REPLAY_BYTES = 5_000_000;

export async function fetchShowdownReplay(value: string) {
  const urls = normalizeShowdownReplayUrl(value);
  let response: Response;
  try {
    response = await fetch(urls.jsonUrl, {
      headers: { accept: "application/json" },
      redirect: "manual",
      signal: AbortSignal.timeout(12_000),
    });
  } catch (error) {
    const timedOut = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    throw new ReplayValidationError(
      timedOut
        ? "Showdown tardó demasiado en responder. Intenta nuevamente."
        : "No pudimos conectar con Showdown en este momento.",
      timedOut ? 504 : 502,
    );
  }
  if (response.status >= 300 && response.status < 400) {
    throw new ReplayValidationError("Showdown intentó redirigir el replay a una ubicación no permitida.", 502);
  }
  if (response.status === 404) {
    throw new ReplayValidationError("Showdown no encontró ese replay o ya no está disponible.", 404);
  }
  if (!response.ok) {
    throw new ReplayValidationError("Showdown no pudo entregar el replay en este momento.", 502);
  }

  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > MAX_REPLAY_BYTES) {
    throw new ReplayValidationError("El replay excede el tamaño permitido.", 413);
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_REPLAY_BYTES) {
    throw new ReplayValidationError("El replay excede el tamaño permitido.", 413);
  }

  let rawReplay: ShowdownReplayDocument & { log: unknown };
  try {
    rawReplay = JSON.parse(body) as ShowdownReplayDocument & { log: unknown };
  } catch {
    throw new ReplayValidationError("Showdown devolvió un replay ilegible.", 502);
  }
  const log = Array.isArray(rawReplay.log)
    ? rawReplay.log.filter((line): line is string => typeof line === "string").join("\n")
    : typeof rawReplay.log === "string"
      ? rawReplay.log
      : "";
  return { urls, replay: { ...rawReplay, log } as ShowdownReplayDocument };
}

function addUnique(list: string[], value: string) {
  const id = toId(value);
  if (!id || list.some((entry) => toId(entry) === id)) return;
  list.push(value);
}

function replaceSpecies(list: string[], previous: string | undefined, next: string) {
  if (!previous) {
    addUnique(list, next);
    return;
  }
  const index = list.findIndex((entry) => toId(entry) === toId(previous));
  if (index < 0) {
    addUnique(list, next);
    return;
  }
  if (list.some((entry, entryIndex) => entryIndex !== index && toId(entry) === toId(next))) {
    list.splice(index, 1);
    return;
  }
  list[index] = next;
}

function ensureMovesUsed(movesUsed: Record<string, string[]>, species: string) {
  const existing = Object.keys(movesUsed).find((name) => toId(name) === toId(species));
  if (existing) return movesUsed[existing];
  movesUsed[species] = [];
  return movesUsed[species];
}

function replaceMovesUsed(movesUsed: Record<string, string[]>, previous: string | undefined, next: string) {
  const nextMoves = ensureMovesUsed(movesUsed, next);
  if (!previous || toId(previous) === toId(next)) return;
  const previousKey = Object.keys(movesUsed).find((name) => toId(name) === toId(previous));
  if (!previousKey) return;
  for (const move of movesUsed[previousKey]) addUnique(nextMoves, move);
  delete movesUsed[previousKey];
}

function detailsSpecies(details: string) {
  return (details.split(",", 1)[0]?.trim() ?? "").replace(/-\*$/, "");
}

function playerSlot(value: string): PlayerSlot | null {
  const slot = value.slice(0, 2);
  return slot === "p1" || slot === "p2" ? slot : null;
}

function decodeHtmlText(value: string) {
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/&rarr;|&#8594;|&#x2192;/gi, "→")
    .replace(/&apos;|&#39;|&#x27;/gi, "'")
    .replace(/&quot;|&#34;/gi, '"')
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">");
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function ratingFromObject(value: unknown) {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  for (const key of ["r", "elo", "rating"]) {
    const candidate = Number(record[key]);
    if (Number.isFinite(candidate) && candidate > 0) return Math.round(candidate);
  }
  return null;
}

function extractFinalRating(log: string, playerName: string) {
  if (!playerName) return null;
  const text = decodeHtmlText(log);
  const pattern = new RegExp(
    `${escapeRegExp(playerName)}(?:'s|’s) rating:\\s*\\d+\\s*(?:→|->)\\s*(\\d+)`,
    "i",
  );
  const match = text.match(pattern);
  return match ? Number(match[1]) : null;
}

function parseTeamChoice(inputLog: string | null | undefined, slot: PlayerSlot, team: string[]) {
  if (!inputLog || !team.length) return null;
  const match = inputLog.match(new RegExp(`^>${slot}\\s+team\\s+([1-6]+)\\s*$`, "m"));
  if (!match) return null;

  const ordered = [...match[1]]
    .map((digit) => team[Number(digit) - 1])
    .filter((species): species is string => Boolean(species));
  const selected: string[] = [];
  for (const species of ordered.slice(0, 4)) addUnique(selected, species);
  return { selected, lead: selected.slice(0, 2) };
}

function parseReplay(document: ShowdownReplayDocument) {
  const sides: Record<PlayerSlot, ReplaySide> = {
    p1: { slot: "p1", name: document.p1?.trim() ?? "", initialRating: null, finalRating: null, team: [], selected: [], lead: [], movesUsed: {} },
    p2: { slot: "p2", name: document.p2?.trim() ?? "", initialRating: null, finalRating: null, team: [], selected: [], lead: [], movesUsed: {} },
  };
  const activeSpecies = new Map<string, string>();
  let started = false;
  let turnStarted = false;
  let winnerName = "";
  let tied = false;
  let timestamp: number | null = null;

  for (const line of document.log.split(/\r?\n/)) {
    if (!line.startsWith("|")) continue;
    const parts = line.split("|");
    const command = parts[1];

    if (command === "player") {
      const slot = playerSlot(parts[2] ?? "");
      if (!slot) continue;
      sides[slot].name = parts[3]?.trim() || sides[slot].name;
      const rating = Number(parts[5]);
      sides[slot].initialRating = Number.isFinite(rating) && rating > 0 ? Math.round(rating) : null;
      continue;
    }
    if (command === "poke") {
      const slot = playerSlot(parts[2] ?? "");
      const species = detailsSpecies(parts[3] ?? "");
      if (slot && species) addUnique(sides[slot].team, species);
      continue;
    }
    if (command === "start") {
      started = true;
      continue;
    }
    if (command === "turn") {
      turnStarted = true;
      continue;
    }
    if (command === "switch" || command === "drag" || command === "replace") {
      const identifier = parts[2] ?? "";
      const slot = playerSlot(identifier);
      const species = detailsSpecies(parts[3] ?? "");
      if (!slot || !species) continue;
      const position = identifier.split(":", 1)[0];
      const previous = activeSpecies.get(position);
      if (command === "replace") {
        replaceSpecies(sides[slot].selected, previous, species);
        if (started && !turnStarted) replaceSpecies(sides[slot].lead, previous, species);
        replaceMovesUsed(sides[slot].movesUsed, previous, species);
      } else {
        addUnique(sides[slot].selected, species);
        if (started && !turnStarted) addUnique(sides[slot].lead, species);
        ensureMovesUsed(sides[slot].movesUsed, species);
      }
      activeSpecies.set(position, species);
      continue;
    }
    if (command === "move") {
      const identifier = parts[2] ?? "";
      const slot = playerSlot(identifier);
      const position = identifier.split(":", 1)[0];
      const species = activeSpecies.get(position);
      const sourceMove = parts
        .slice(5)
        .find((part) => /^\[from\]\s*move:/i.test(part))
        ?.replace(/^\[from\]\s*move:\s*/i, "")
        .trim();
      const move = sourceMove || parts[3]?.trim();
      if (slot && species && move) addUnique(ensureMovesUsed(sides[slot].movesUsed, species), move);
      continue;
    }
    if (command === "win") {
      winnerName = parts.slice(2).join("|").trim();
      continue;
    }
    if (command === "tie") {
      tied = true;
      continue;
    }
    if (command === "t:") {
      const candidate = Number(parts[2]);
      if (!timestamp && Number.isFinite(candidate) && candidate > 0) timestamp = candidate;
    }
  }

  for (const slot of ["p1", "p2"] as const) {
    const teamChoice = parseTeamChoice(document.inputlog, slot, sides[slot].team);
    if (teamChoice?.selected.length) {
      sides[slot].selected = teamChoice.selected;
      sides[slot].lead = teamChoice.lead;
    }
    for (const species of sides[slot].selected) ensureMovesUsed(sides[slot].movesUsed, species);
    sides[slot].finalRating =
      extractFinalRating(document.log, sides[slot].name) ??
      ratingFromObject(document[`${slot}rating`]);
  }

  return { sides, winnerName, tied, timestamp };
}

function speciesCompatible(left: string, right: string) {
  const leftId = toId(left);
  const rightId = toId(right);
  return Boolean(leftId && rightId && (leftId === rightId || leftId.startsWith(rightId) || rightId.startsWith(leftId)));
}

function mapToCanonicalSpecies(species: string[], canonicalTeam: string[]) {
  return species.map((name) => {
    const exact = canonicalTeam.find((candidate) => toId(candidate) === toId(name));
    if (exact) return exact;
    const compatible = canonicalTeam.filter((candidate) => speciesCompatible(candidate, name));
    return compatible.length === 1 ? compatible[0] : name;
  });
}

function mapMovesUsedToCanonical(
  movesUsed: Record<string, string[]>,
  selected: string[],
  canonicalTeam: string[],
) {
  const result: Record<string, string[]> = {};
  for (const species of selected) {
    const canonical = mapToCanonicalSpecies([species], canonicalTeam)[0];
    const moves: string[] = [];
    for (const [sourceSpecies, sourceMoves] of Object.entries(movesUsed)) {
      if (!speciesCompatible(sourceSpecies, canonical)) continue;
      for (const move of sourceMoves) addUnique(moves, move);
    }
    result[canonical] = moves;
  }
  return result;
}

function teamOverlap(side: ReplaySide, canonicalTeam: string[]) {
  const matched = new Set<string>();
  for (const replaySpecies of side.team) {
    const canonical = canonicalTeam.find((candidate) => speciesCompatible(candidate, replaySpecies));
    if (canonical) matched.add(toId(canonical));
  }
  return matched.size;
}

function identifyOwnSlot(sides: Record<PlayerSlot, ReplaySide>, showdownNames: string[], canonicalTeam: string[]) {
  const configuredNames = new Set(showdownNames.map(toId).filter(Boolean));
  const byName = (["p1", "p2"] as const).filter((slot) => configuredNames.has(toId(sides[slot].name)));
  if (byName.length === 1) return byName[0];

  const p1Score = teamOverlap(sides.p1, canonicalTeam);
  const p2Score = teamOverlap(sides.p2, canonicalTeam);
  if (p1Score !== p2Score && Math.max(p1Score, p2Score) >= 2) {
    return p1Score > p2Score ? "p1" : "p2";
  }

  throw new ReplayValidationError(
    showdownNames.length
      ? "El replay no coincide con tus Showdown Name(s) ni con esta versión del Team."
      : "Añade tu Showdown Name en Trainer para identificar tu lado del replay.",
    422,
  );
}

function replayDate(document: ShowdownReplayDocument, timestamp: number | null) {
  const uploadTime = Number(document.uploadtime);
  const seconds = Number.isFinite(uploadTime) && uploadTime > 0 ? uploadTime : timestamp;
  if (!seconds) return null;
  const milliseconds = seconds > 1_000_000_000_000 ? seconds : seconds * 1000;
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function importShowdownReplay(
  document: ShowdownReplayDocument,
  options: { replayUrl: string; showdownNames: string[]; teamSpecies: string[] },
): ImportedReplayMatch {
  if (!document.log?.trim()) {
    throw new ReplayValidationError("Showdown no devolvió el registro de esta partida.", 422);
  }

  const parsed = parseReplay(document);
  if (parsed.tied) {
    throw new ReplayValidationError("La partida terminó en empate y el historial todavía admite victoria o derrota.", 422);
  }
  if (!parsed.winnerName) {
    throw new ReplayValidationError("El replay no contiene un resultado final.", 422);
  }

  const ownSlot = identifyOwnSlot(parsed.sides, options.showdownNames, options.teamSpecies);
  const opponentSlot: PlayerSlot = ownSlot === "p1" ? "p2" : "p1";
  const own = parsed.sides[ownSlot];
  const opponent = parsed.sides[opponentSlot];
  const selected = mapToCanonicalSpecies(own.selected, options.teamSpecies).slice(0, 4);
  const lead = mapToCanonicalSpecies(own.lead, options.teamSpecies).slice(0, 2);
  const movesUsed = mapMovesUsedToCanonical(own.movesUsed, selected, options.teamSpecies);
  const opponentSelected = (opponent.team.length ? opponent.team : opponent.selected).slice(0, 6);
  const warnings: string[] = [];

  if (selected.length !== 4) warnings.push("El log público no reveló los cuatro picks; completa únicamente los que falten.");
  if (lead.length !== 2) warnings.push("El log público no reveló ambos leads; completa únicamente los que falten.");
  if (own.finalRating === null) warnings.push("Showdown no publicó el rating final para esta partida.");

  return {
    replayUrl: options.replayUrl,
    result: toId(parsed.winnerName) === toId(own.name) ? "win" : "loss",
    playerName: own.name,
    opponentName: opponent.name || "Rival",
    opponentSelected,
    selected,
    lead,
    movesUsed,
    rating: own.finalRating,
    playedAt: replayDate(document, parsed.timestamp),
    format: document.format?.trim() ?? "",
    warnings,
  };
}
