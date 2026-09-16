import { updateMove } from "./team-builder";
import type { PokemonSet } from "./types";
import type {
  WarRoomAuditResult,
  WarRoomOptimizationResult,
  WarRoomSetChange,
} from "./war-room";

export const MAX_AUTO_LAB_VARIANTS = 4;

export type AutoLabVariant = {
  id: string;
  label: string;
  species: string;
  setId: string;
  change: WarRoomSetChange;
  pokemon: PokemonSet[];
  rationale: string[];
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

function applySingleChange(team: PokemonSet[], change: WarRoomSetChange, setId: string) {
  const next = clonePokemon(team);
  const index = next.findIndex((set) => set.id === setId);
  if (index < 0) return null;
  const current = next[index];

  if (change.key === "item") {
    next[index] = { ...current, item: change.suggested };
  } else if (change.key === "ability") {
    next[index] = { ...current, ability: change.suggested };
  } else if (change.key === "nature") {
    next[index] = { ...current, nature: change.suggested };
  } else if (change.key === "statPoints") {
    next[index] = { ...current, evs: change.suggested };
  } else if (change.key.startsWith("move-")) {
    const slot = Number(change.key.slice(5));
    if (!Number.isInteger(slot) || slot < 0 || slot > 3) return null;
    next[index] = updateMove(current, slot, change.suggested);
  } else {
    return null;
  }

  return next;
}

function changePriority(change: WarRoomSetChange) {
  if (change.key.startsWith("move-")) return 0;
  if (change.key === "item" || change.key === "ability") return 1;
  if (change.key === "nature" || change.key === "statPoints") return 2;
  return 3;
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

  const candidates = optimization.sets.flatMap((suggestion, suggestionIndex) =>
    suggestion.changes.map((change, changeIndex) => ({
      suggestion,
      suggestionIndex,
      change,
      changeIndex,
    })),
  );

  candidates.sort((left, right) =>
    changePriority(left.change) - changePriority(right.change)
    || (right.change.evidence ?? -1) - (left.change.evidence ?? -1)
    || left.suggestion.structuralDelta - right.suggestion.structuralDelta
    || left.suggestionIndex - right.suggestionIndex
    || left.changeIndex - right.changeIndex,
  );

  const output: AutoLabVariant[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    if (output.length >= maxVariants) break;
    const key = `${candidate.suggestion.setId}|${candidate.change.key}|${candidate.change.suggested}`;
    if (seen.has(key)) continue;
    const pokemon = applySingleChange(team, candidate.change, candidate.suggestion.setId);
    if (!pokemon) continue;
    seen.add(key);
    const field = candidate.change.field || candidate.change.key;
    output.push({
      id: `variant-${output.length + 1}-${slug(candidate.suggestion.species)}-${slug(String(candidate.change.key))}`,
      label: `${candidate.suggestion.species}: ${field} → ${candidate.change.suggested}`,
      species: candidate.suggestion.species,
      setId: candidate.suggestion.setId,
      change: candidate.change,
      pokemon,
      rationale: candidate.suggestion.reasons.slice(0, 3),
    });
  }
  return output;
}
