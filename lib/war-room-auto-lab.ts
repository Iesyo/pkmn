import { updateMove } from "./team-builder";
import type { PokemonSet } from "./types";
import type {
  WarRoomAuditResult,
  WarRoomCorpusTeam,
  WarRoomOptimizationResult,
  WarRoomSetChange,
  WarRoomSetSuggestion,
} from "./war-room";

export const MAX_AUTO_LAB_VARIANTS = 4;

export type AutoLabVariant = {
  id: string;
  label: string;
  species: string;
  setId: string;
  changes: WarRoomSetChange[];
  pokemon: PokemonSet[];
  rationale: string[];
  methodology: WarRoomSetSuggestion["methodology"];
  sourceLabel: string;
};

export type AutoLabDiagnosis = {
  blockers: number;
  warnings: number;
  criticalThreats: number;
  highThreats: number;
  unresolvedThreats: number;
  gaps: number;
  priority: string;
};

const AUTO_LAB_SOURCES = ["tournament", "scouting-library", "vgcpastes"] as const;

type AutoLabSource = (typeof AUTO_LAB_SOURCES)[number];

function clonePokemon(team: PokemonSet[]) {
  return team.map((set) => ({
    ...set,
    mechanics: set.mechanics ? { ...set.mechanics } : undefined,
    moves: set.moves.map((move) => ({ ...move })),
    performance: { ...set.performance },
  }));
}

function slug(value: string) {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function applySetPackage(team: PokemonSet[], suggestion: WarRoomSetSuggestion) {
  const next = clonePokemon(team);
  const index = next.findIndex((set) => set.id === suggestion.setId);
  if (index < 0 || suggestion.proposal.moves.length !== 4) return null;

  let replacement: PokemonSet = {
    ...next[index],
    item: suggestion.proposal.item,
    ability: suggestion.proposal.ability,
    nature: suggestion.proposal.nature,
    evs: suggestion.proposal.evs,
  };
  suggestion.proposal.moves.forEach((move, slot) => {
    replacement = updateMove(replacement, slot, move);
  });
  next[index] = replacement;
  return next;
}

function methodologyPriority(value: WarRoomSetSuggestion["methodology"]) {
  if (value === "observed-paste") return 0;
  if (value === "observed-paste-patched") return 1;
  return 2;
}

export function diagnoseAutoLab(audit: WarRoomAuditResult): AutoLabDiagnosis {
  const highestGap = audit.gaps.find((gap) => gap.severity === "high")
    ?? audit.gaps.find((gap) => gap.severity === "medium")
    ?? audit.gaps[0];
  const firstThreat = audit.threats.find((threat) => threat.tier === "crítica")
    ?? audit.threats.find((threat) => threat.tier === "alta")
    ?? audit.threats[0];
  const priority = highestGap?.title
    ?? (firstThreat ? `Responder mejor a ${firstThreat.species}` : "Validar estabilidad general del Team");

  return {
    blockers: audit.legality.blockers,
    warnings: audit.legality.warnings,
    criticalThreats: audit.summary.criticalThreats,
    highThreats: audit.summary.highThreats,
    unresolvedThreats: audit.summary.unresolvedThreats,
    gaps: audit.gaps.length,
    priority,
  };
}

export function buildAutoLabVariants(
  team: PokemonSet[],
  optimization: WarRoomOptimizationResult,
  maxVariants = MAX_AUTO_LAB_VARIANTS,
): AutoLabVariant[] {
  if (maxVariants < 1) return [];

  const candidates = optimization.sets
    .filter((suggestion) => suggestion.changes.length > 0 && suggestion.proposal.moves.length === 4)
    .map((suggestion, index) => ({ suggestion, index }));

  candidates.sort((left, right) =>
    methodologyPriority(left.suggestion.methodology) - methodologyPriority(right.suggestion.methodology)
    || (right.suggestion.source?.contextFit ?? -1) - (left.suggestion.source?.contextFit ?? -1)
    || right.suggestion.structuralDelta - left.suggestion.structuralDelta
    || right.suggestion.changes.length - left.suggestion.changes.length
    || left.index - right.index,
  );

  const output: AutoLabVariant[] = [];
  const seen = new Set<string>();
  for (const { suggestion } of candidates) {
    if (output.length >= maxVariants) break;
    const packageKey = [
      suggestion.setId,
      suggestion.proposal.item,
      suggestion.proposal.ability,
      suggestion.proposal.nature,
      suggestion.proposal.evs,
      ...suggestion.proposal.moves,
    ].join("|");
    if (seen.has(packageKey)) continue;
    const pokemon = applySetPackage(team, suggestion);
    if (!pokemon) continue;
    seen.add(packageKey);
    const sourceLabel = suggestion.source?.label
      ?? (suggestion.methodology === "battle-data-fallback" ? "Battle Data" : "evidencia contextual");
    output.push({
      id: `variant-${output.length + 1}-${slug(suggestion.species)}-full-set`,
      label: `${suggestion.species}: paquete de set completo`,
      species: suggestion.species,
      setId: suggestion.setId,
      changes: suggestion.changes,
      pokemon,
      rationale: suggestion.reasons.slice(0, 3),
      methodology: suggestion.methodology,
      sourceLabel,
    });
  }
  return output;
}

export function autoLabCorpusCandidates(teams: WarRoomCorpusTeam[]) {
  return teams.filter((team) =>
    !team.historical
    && AUTO_LAB_SOURCES.includes(team.source as AutoLabSource)
    && Boolean(team.savedPasteId || team.pokepasteUrl),
  );
}

export function selectAutoLabOpponentCandidates(
  teams: WarRoomCorpusTeam[],
  limit: number,
  seedKey: string,
) {
  if (limit <= 0) return [];
  const buckets = new Map<AutoLabSource, WarRoomCorpusTeam[]>();
  AUTO_LAB_SOURCES.forEach((source) => buckets.set(source, []));
  for (const team of autoLabCorpusCandidates(teams)) {
    const source = team.source as AutoLabSource;
    buckets.get(source)?.push(team);
  }
  for (const source of AUTO_LAB_SOURCES) {
    buckets.get(source)?.sort((left, right) =>
      stableHash(`${seedKey}|${left.id}`) - stableHash(`${seedKey}|${right.id}`)
      || left.id.localeCompare(right.id),
    );
  }

  const output: WarRoomCorpusTeam[] = [];
  const seen = new Set<string>();
  let cursor = 0;
  while (output.length < limit) {
    let added = false;
    for (const source of AUTO_LAB_SOURCES) {
      const team = buckets.get(source)?.[cursor];
      if (!team || seen.has(team.id)) continue;
      output.push(team);
      seen.add(team.id);
      added = true;
      if (output.length >= limit) break;
    }
    if (!added) break;
    cursor += 1;
  }

  if (output.length < limit) {
    const leftovers = autoLabCorpusCandidates(teams)
      .filter((team) => !seen.has(team.id))
      .sort((left, right) => stableHash(`${seedKey}|all|${left.id}`) - stableHash(`${seedKey}|all|${right.id}`));
    for (const team of leftovers) {
      output.push(team);
      if (output.length >= limit) break;
    }
  }
  return output;
}
