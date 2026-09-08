export const MAX_OPPONENT_META_PRESETS = 3;

const MAX_MOVE_POOL = 7;
const MAX_CATEGORY_OPTIONS = 3;
const MAX_SPREAD_OPTIONS = 5;
const MIN_RELATIVE_SCORE = 0.1;

const STAT_POINT_FIELDS = [
  ["HP", "hp_points"],
  ["Atk", "attack_points"],
  ["Def", "defense_points"],
  ["SpA", "sp_atk_points"],
  ["SpD", "sp_def_points"],
  ["Spe", "speed_points"],
] as const;

type RankedValue = {
  name: string;
  percentage: number;
  rank: number;
};

type RankedSpread = {
  statPoints: Record<(typeof STAT_POINT_FIELDS)[number][0], number>;
  evs: string;
  percentage: number;
  rank: number;
};

export type OpponentMetaPreset = {
  id: string;
  rank: number;
  label: string;
  item: string;
  ability: string;
  nature: string;
  evs: string;
  moves: string[];
  evidence: {
    item: number;
    ability: number;
    nature: number;
    statPoints: number;
    moves: number[];
  };
};

export type OpponentMetaResponse = {
  pokemon: string;
  format: "Doubles";
  season: string;
  retrievedAt: string;
  stale: boolean;
  methodology: "marginal-frequency-composite";
  source: {
    label: string;
    url: string;
  };
  presets: OpponentMetaPreset[];
};

export type OpponentMetaEstimate = {
  nature: string;
  evs: string;
  evidence: {
    nature: number | null;
    statPoints: number | null;
  };
};

type Candidate = {
  item: RankedValue;
  ability: RankedValue;
  nature: RankedValue;
  spread: RankedSpread;
  moves: RankedValue[];
  score: number;
  signature: string;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function asFiniteNumber(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const parsed = Number(value.replace(/%$/, "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function readPercentage(row: Record<string, unknown>) {
  const value = asFiniteNumber(row.percentage_value) ?? asFiniteNumber(row.percentage);
  return value !== null && value > 0 && value <= 100 ? value : null;
}

function readRank(row: Record<string, unknown>) {
  const rank = asFiniteNumber(row.rank);
  return rank !== null && Number.isInteger(rank) && rank > 0 ? rank : Number.MAX_SAFE_INTEGER;
}

function isPercentage(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 100;
}

function rankedValues(rows: Record<string, unknown>[], category: string, limit: number) {
  const seen = new Set<string>();
  return rows
    .filter((row) => asString(row.category) === category)
    .map((row): RankedValue | null => {
      const name = asString(row.name);
      const percentage = readPercentage(row);
      if (!name || percentage === null) return null;
      return { name, percentage, rank: readRank(row) };
    })
    .filter((entry): entry is RankedValue => Boolean(entry))
    .sort((left, right) => left.rank - right.rank || right.percentage - left.percentage || left.name.localeCompare(right.name))
    .filter((entry) => {
      const key = entry.name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, limit);
}

function rankedSpreads(rows: Record<string, unknown>[], limit = MAX_SPREAD_OPTIONS) {
  const seen = new Set<string>();
  return rows
    .filter((row) => asString(row.category) === "stat_points")
    .map((row): RankedSpread | null => {
      const percentage = readPercentage(row);
      if (percentage === null) return null;

      const statPoints = Object.fromEntries(STAT_POINT_FIELDS.map(([stat, field]) => {
        const value = asFiniteNumber(row[field]);
        return [stat, value];
      })) as Record<(typeof STAT_POINT_FIELDS)[number][0], number | null>;
      const values = Object.values(statPoints);
      if (values.some((value) => value === null || !Number.isInteger(value) || value < 0 || value > 32)) return null;
      const total = values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
      if (total > 66) return null;

      const complete = statPoints as RankedSpread["statPoints"];
      const evs = STAT_POINT_FIELDS
        .filter(([stat]) => complete[stat] > 0)
        .map(([stat]) => `${complete[stat]} ${stat}`)
        .join(" / ");
      return { statPoints: complete, evs, percentage, rank: readRank(row) };
    })
    .filter((entry): entry is RankedSpread => Boolean(entry))
    .sort((left, right) => left.rank - right.rank || right.percentage - left.percentage)
    .filter((entry) => {
      const key = STAT_POINT_FIELDS.map(([stat]) => entry.statPoints[stat]).join("/");
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, limit);
}

function payloadRows(payload: unknown) {
  const root = asRecord(payload);
  const rawRows = root && Array.isArray(root.rows) ? root.rows : [];
  return rawRows.map(asRecord).filter((row): row is Record<string, unknown> => Boolean(row));
}

export function buildMostUsedOpponentMetaEstimate(payload: unknown): OpponentMetaEstimate | null {
  const rows = payloadRows(payload);
  const nature = rankedValues(rows, "stat_alignment", Number.MAX_SAFE_INTEGER)
    .sort((left, right) => right.percentage - left.percentage || left.rank - right.rank || left.name.localeCompare(right.name))[0];
  const spread = rankedSpreads(rows, Number.MAX_SAFE_INTEGER)
    .sort((left, right) => right.percentage - left.percentage || left.rank - right.rank)[0];

  if (!nature && !spread) return null;
  return {
    nature: nature?.name ?? "",
    evs: spread?.evs ?? "",
    evidence: {
      nature: nature?.percentage ?? null,
      statPoints: spread?.percentage ?? null,
    },
  };
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

function scorePercentages(percentages: number[]) {
  // Battle Data publishes marginal frequencies, not observed joint sets. Ranking
  // their independent products yields useful composites without inventing an
  // overall usage percentage, which is intentionally never exposed.
  return percentages.reduce((score, percentage) => score + Math.log(percentage / 100), 0);
}

export function buildOpponentMetaPresets(payload: unknown): OpponentMetaPreset[] {
  const rows = payloadRows(payload);
  const moves = rankedValues(rows, "move", MAX_MOVE_POOL);
  const items = rankedValues(rows, "held_item", MAX_CATEGORY_OPTIONS);
  const abilities = rankedValues(rows, "ability", MAX_CATEGORY_OPTIONS);
  const natures = rankedValues(rows, "stat_alignment", MAX_CATEGORY_OPTIONS);
  const spreads = rankedSpreads(rows);
  if (moves.length < 4 || !items.length || !abilities.length || !natures.length || !spreads.length) return [];

  const candidates: Candidate[] = [];
  for (const moveSet of combinations(moves, 4)) {
    for (const item of items) {
      for (const ability of abilities) {
        for (const nature of natures) {
          for (const spread of spreads) {
            const percentages = [
              ...moveSet.map((move) => move.percentage),
              item.percentage,
              ability.percentage,
              nature.percentage,
              spread.percentage,
            ];
            const signature = [
              item.name,
              ability.name,
              nature.name,
              spread.evs,
              ...moveSet.map((move) => move.name),
            ].join("|").toLowerCase();
            candidates.push({
              item,
              ability,
              nature,
              spread,
              moves: moveSet,
              score: scorePercentages(percentages),
              signature,
            });
          }
        }
      }
    }
  }

  candidates.sort((left, right) => right.score - left.score || left.signature.localeCompare(right.signature));
  const bestScore = candidates[0]?.score;
  if (bestScore === undefined) return [];

  const seen = new Set<string>();
  return candidates
    .filter((candidate) => Math.exp(candidate.score - bestScore) >= MIN_RELATIVE_SCORE)
    .filter((candidate) => {
      if (seen.has(candidate.signature)) return false;
      seen.add(candidate.signature);
      return true;
    })
    .slice(0, MAX_OPPONENT_META_PRESETS)
    .map((candidate, index) => ({
      id: `meta-${index + 1}`,
      rank: index + 1,
      label: index === 0 ? "Meta popular" : "Meta alternativo",
      item: candidate.item.name,
      ability: candidate.ability.name,
      nature: candidate.nature.name,
      evs: candidate.spread.evs,
      moves: candidate.moves.map((move) => move.name),
      evidence: {
        item: candidate.item.percentage,
        ability: candidate.ability.percentage,
        nature: candidate.nature.percentage,
        statPoints: candidate.spread.percentage,
        moves: candidate.moves.map((move) => move.percentage),
      },
    }));
}

export function isOpponentMetaResponse(value: unknown): value is OpponentMetaResponse {
  const root = asRecord(value);
  if (!root || !Array.isArray(root.presets) || root.presets.length > MAX_OPPONENT_META_PRESETS) return false;
  if (!asString(root.pokemon) || root.format !== "Doubles" || !asString(root.season) || !asString(root.retrievedAt)) return false;
  const source = asRecord(root.source);
  if (
    typeof root.stale !== "boolean"
    || root.methodology !== "marginal-frequency-composite"
    || !source
    || !asString(source.label)
    || !asString(source.url)
  ) return false;
  return root.presets.every((rawPreset) => {
    const preset = asRecord(rawPreset);
    const evidence = preset ? asRecord(preset.evidence) : null;
    return Boolean(
      preset
      && asString(preset.id)
      && typeof preset.rank === "number"
      && Number.isInteger(preset.rank)
      && preset.rank > 0
      && preset.rank <= MAX_OPPONENT_META_PRESETS
      && asString(preset.label)
      && asString(preset.item)
      && asString(preset.ability)
      && asString(preset.nature)
      && typeof preset.evs === "string"
      && Array.isArray(preset.moves)
      && preset.moves.length === 4
      && preset.moves.every((move) => Boolean(asString(move)))
      && evidence
      && isPercentage(evidence.item)
      && isPercentage(evidence.ability)
      && isPercentage(evidence.nature)
      && isPercentage(evidence.statPoints)
      && Array.isArray(evidence.moves)
      && evidence.moves.length === 4
      && evidence.moves.every(isPercentage),
    );
  });
}
