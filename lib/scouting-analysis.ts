import { Move } from "@smogon/calc";

import {
  calculateDamage,
  createDamageDraft,
  defaultDamageField,
  generationForFormat,
} from "./damage-calculator";
import { getSpeciesTypes, toId } from "./pokemon-data";
import type { ScoutingReplayEvidence } from "./showdown-replay";
import type {
  PokemonSet,
  ScoutingDamageObservation,
  ScoutingPokemonEvidence,
  ScoutingResult,
  ScoutingStatInference,
} from "./types";

type InferredStat = ScoutingStatInference["stat"];

interface CandidateConstraint {
  species: string;
  stat: InferredStat;
  points: Set<number>;
  natures: Set<string>;
  detail: string;
}

function compatibleSpecies(left: string, right: string) {
  const leftId = toId(left);
  const rightId = toId(right);
  return Boolean(leftId && rightId && (leftId === rightId || leftId.startsWith(rightId) || rightId.startsWith(leftId)));
}

function moveCategory(format: string, moveName: string) {
  try {
    return new Move(generationForFormat(format), moveName).category;
  } catch {
    return "Status";
  }
}

function naturesFor(stat: Exclude<InferredStat, "HP">) {
  if (stat === "Atk") return ["Adamant", "Serious", "Modest"];
  if (stat === "SpA") return ["Modest", "Serious", "Adamant"];
  if (stat === "Def") return ["Bold", "Serious", "Lonely"];
  return ["Calm", "Serious", "Naughty"];
}

function evText(values: Partial<Record<InferredStat, number>>) {
  return (["HP", "Atk", "Def", "SpA", "SpD"] as const)
    .filter((stat) => (values[stat] ?? 0) > 0)
    .map((stat) => `${values[stat]} ${stat}`)
    .join(" / ");
}

function opponentSet(evidence: ScoutingPokemonEvidence, values: Partial<Record<InferredStat, number>>, nature: string, moveName: string): PokemonSet {
  return {
    id: `scout-${toId(evidence.species)}`,
    slot: 0,
    nickname: evidence.species,
    species: evidence.species,
    item: evidence.item ?? "",
    ability: evidence.ability ?? "",
    level: 50,
    teraType: evidence.teraType,
    mechanics: {},
    evs: evText(values),
    nature,
    moves: [{ name: moveName, type: null, damaging: true, usage: 0 }],
    types: getSpeciesTypes(evidence.species),
    performance: { games: 0, wins: 0, leadGames: 0, leadWins: 0, selectionRate: 0 },
  };
}

function outcomeMatches(observation: ScoutingDamageObservation, minPercent: number, maxPercent: number) {
  return minPercent <= observation.damagePercent + observation.tolerance
    && maxPercent >= observation.damagePercent - observation.tolerance;
}

function offensiveConstraint(
  format: string,
  observation: ScoutingDamageObservation,
  opponent: ScoutingPokemonEvidence,
  ownDefender: PokemonSet,
): CandidateConstraint | null {
  const category = moveCategory(format, observation.move);
  const stat: InferredStat = category === "Physical" ? "Atk" : category === "Special" ? "SpA" : "Atk";
  if (category === "Status") return null;
  const points = new Set<number>();
  const natures = new Set<string>();
  const field = defaultDamageField();

  for (const nature of naturesFor(stat as "Atk" | "SpA")) {
    for (let point = 0; point <= 32; point += 1) {
      const attacker = createDamageDraft(opponentSet(opponent, { [stat]: point }, nature, observation.move));
      attacker.critical = observation.critical;
      attacker.teraActive = Boolean(opponent.teraType);
      const outcome = calculateDamage(format, attacker, createDamageDraft(ownDefender), observation.move, field);
      if (!outcome.error && outcomeMatches(observation, outcome.minPercent, outcome.maxPercent)) {
        points.add(point);
        natures.add(nature);
      }
    }
  }
  if (!points.size) return null;
  return {
    species: opponent.species,
    stat,
    points,
    natures,
    detail: `${observation.move} causó ${observation.damagePercent}% a ${ownDefender.species}.`,
  };
}

function defensiveConstraints(
  format: string,
  observation: ScoutingDamageObservation,
  ownAttacker: PokemonSet,
  opponent: ScoutingPokemonEvidence,
): CandidateConstraint[] {
  const category = moveCategory(format, observation.move);
  const defenseStat: InferredStat = category === "Physical" ? "Def" : category === "Special" ? "SpD" : "Def";
  if (category === "Status") return [];
  const hpPoints = new Set<number>();
  const defensePoints = new Set<number>();
  const natures = new Set<string>();
  const field = defaultDamageField();
  const attacker = createDamageDraft(ownAttacker);
  attacker.critical = observation.critical;

  for (const nature of naturesFor(defenseStat as "Def" | "SpD")) {
    for (let hp = 0; hp <= 32; hp += 1) {
      for (let defense = 0; defense <= 32; defense += 1) {
        const defender = createDamageDraft(opponentSet(opponent, { HP: hp, [defenseStat]: defense }, nature, observation.move));
        defender.teraActive = Boolean(opponent.teraType);
        const outcome = calculateDamage(format, attacker, defender, observation.move, field);
        if (!outcome.error && outcomeMatches(observation, outcome.minPercent, outcome.maxPercent)) {
          hpPoints.add(hp);
          defensePoints.add(defense);
          natures.add(nature);
        }
      }
    }
  }
  if (!hpPoints.size) return [];
  const detail = `${observation.move} de ${ownAttacker.species} causó ${observation.damagePercent}%.`;
  return [
    { species: opponent.species, stat: "HP", points: hpPoints, natures, detail },
    { species: opponent.species, stat: defenseStat, points: defensePoints, natures, detail },
  ];
}

function mergeConstraints(constraints: CandidateConstraint[]): ScoutingStatInference[] {
  const grouped = new Map<string, CandidateConstraint & { count: number }>();
  for (const constraint of constraints) {
    const key = `${toId(constraint.species)}:${constraint.stat}`;
    const current = grouped.get(key);
    if (!current) {
      grouped.set(key, { ...constraint, points: new Set(constraint.points), natures: new Set(constraint.natures), count: 1 });
      continue;
    }
    const intersection = new Set([...current.points].filter((point) => constraint.points.has(point)));
    if (intersection.size) current.points = intersection;
    for (const nature of constraint.natures) current.natures.add(nature);
    current.count += 1;
    current.detail = `${current.count} impactos compatibles con el intervalo.`;
  }

  return [...grouped.values()].map((entry) => {
    const points = [...entry.points].sort((left, right) => left - right);
    return {
      species: entry.species,
      stat: entry.stat,
      minimum: points[0],
      maximum: points.at(-1) ?? points[0],
      natures: [...entry.natures],
      observationCount: entry.count,
      confidence: "conditional",
      detail: entry.detail,
    };
  });
}

function partialPaste(pokemon: ScoutingPokemonEvidence[]) {
  return pokemon.map((entry) => {
    const lines = [`${entry.species}${entry.item ? ` @ ${entry.item}` : ""}`];
    if (entry.ability) lines.push(`Ability: ${entry.ability}`);
    lines.push("Level: 50");
    if (entry.teraType) lines.push(`Tera Type: ${entry.teraType}`);
    for (const move of entry.moves) lines.push(`- ${move}`);
    return lines.join("\n");
  }).join("\n\n");
}

export function analyzeScoutingEvidence(
  evidence: ScoutingReplayEvidence,
  options: { replayUrl: string; format: string; ownTeam: PokemonSet[] },
): ScoutingResult {
  const constraints: CandidateConstraint[] = [];
  const usable = evidence.observations.slice(0, 8);

  for (const observation of usable) {
    if (observation.direction === "incoming") {
      const opponent = evidence.pokemon.find((entry) => compatibleSpecies(entry.species, observation.attacker));
      const ownDefender = options.ownTeam.find((set) => compatibleSpecies(set.species, observation.defender));
      if (opponent && ownDefender) {
        const constraint = offensiveConstraint(options.format, observation, opponent, ownDefender);
        if (constraint) constraints.push(constraint);
      }
    } else {
      const ownAttacker = options.ownTeam.find((set) => compatibleSpecies(set.species, observation.attacker));
      const opponent = evidence.pokemon.find((entry) => compatibleSpecies(entry.species, observation.defender));
      if (ownAttacker && opponent) {
        constraints.push(...defensiveConstraints(options.format, observation, ownAttacker, opponent));
      }
    }
  }

  const notices = [
    "El paste contiene únicamente información observada; los campos ocultos no se rellenan por suposición.",
    "Los Stat Points son intervalos compatibles con los rolls de daño, no una reconstrucción única.",
  ];
  if (evidence.observations.length > usable.length) notices.push(`Se usaron los primeros ${usable.length} impactos directos para limitar el costo del análisis.`);
  if (!constraints.length) notices.push("Este replay no contiene impactos directos suficientes para acotar Stat Points con seguridad.");

  return {
    opponentName: evidence.opponentName,
    replayUrl: options.replayUrl,
    pokemon: evidence.pokemon,
    observations: evidence.observations,
    inferences: mergeConstraints(constraints),
    observedPaste: partialPaste(evidence.pokemon),
    notices,
    completedAt: new Date().toISOString(),
  };
}
