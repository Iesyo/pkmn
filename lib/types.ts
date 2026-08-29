export const POKEMON_TYPES = [
  "Normal",
  "Fire",
  "Water",
  "Electric",
  "Grass",
  "Ice",
  "Fighting",
  "Poison",
  "Ground",
  "Flying",
  "Psychic",
  "Bug",
  "Rock",
  "Ghost",
  "Dragon",
  "Dark",
  "Steel",
  "Fairy",
] as const;

export type PokemonType = (typeof POKEMON_TYPES)[number];
export type MatchResult = "win" | "loss";
export type BattleMechanic = "tera" | "dynamax" | "mega" | "zmove";

export interface PokemonMechanics {
  dynamaxLevel?: number;
  gigantamax?: boolean;
  megaEvolution?: boolean;
  zMove?: boolean;
}

export interface MoveSet {
  name: string;
  type: PokemonType | null;
  usage: number | null;
  damaging: boolean;
}

export interface PokemonPerformance {
  games: number;
  wins: number;
  leadGames: number;
  leadWins: number;
  selectionRate: number;
}

export interface PokemonSet {
  id: string;
  slot: number;
  nickname: string;
  species: string;
  item: string;
  ability: string;
  level: number;
  teraType: PokemonType | null;
  mechanics?: PokemonMechanics;
  evs: string;
  nature: string;
  moves: MoveSet[];
  types: PokemonType[];
  performance: PokemonPerformance;
}

export interface LeadStat {
  species: string[];
  games: number;
  wins: number;
}

export interface MatchRecord {
  id: string;
  result: MatchResult;
  opponentName: string;
  opponentPaste: string;
  replayUrl: string;
  selected: string[];
  opponentSelected: string[];
  lead: string[];
  movesUsed?: Record<string, string[]> | null;
  rating: number | null;
  notes: string;
  playedAt: string;
}

export type ScoutingAnalysisStatus = "queued" | "running" | "complete" | "error";

export interface ScoutingPokemonEvidence {
  species: string;
  brought: boolean;
  moves: string[];
  item: string | null;
  ability: string | null;
  teraType: PokemonType | null;
}

export interface ScoutingDamageObservation {
  turn: number;
  attacker: string;
  defender: string;
  move: string;
  direction: "outgoing" | "incoming";
  damagePercent: number;
  tolerance: number;
  critical: boolean;
}

export interface ScoutingStatInference {
  species: string;
  stat: "HP" | "Atk" | "Def" | "SpA" | "SpD";
  minimum: number;
  maximum: number;
  natures: string[];
  observationCount: number;
  confidence: "bounded" | "conditional";
  detail: string;
}

export interface ScoutingResult {
  opponentName: string;
  replayUrl: string;
  pokemon: ScoutingPokemonEvidence[];
  observations: ScoutingDamageObservation[];
  inferences: ScoutingStatInference[];
  observedPaste: string;
  notices: string[];
  completedAt: string;
}

export interface ScoutingAnalysis {
  id: string;
  matchId: string;
  status: ScoutingAnalysisStatus;
  progress: number;
  stage: string;
  result: ScoutingResult | null;
  error: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TeamVersion {
  id: string;
  teamId: string;
  name: string;
  version: number;
  minorVersion?: number;
  format?: string;
  mechanics?: BattleMechanic[];
  paste: string;
  createdAt: string;
  pokemon: PokemonSet[];
  matches: MatchRecord[];
  games: number;
  wins: number;
  leads: LeadStat[];
  demo?: boolean;
}

export interface TeamGroup {
  id: string;
  name: string;
  versions: TeamVersion[];
}

export interface TypeCount {
  type: PokemonType;
  count: number;
}

export interface DefensiveTypeCount extends TypeCount {
  resistances: number;
  immunities: number;
}

export interface TypeAnalysisResult {
  coverage: TypeCount[];
  defense: DefensiveTypeCount[];
  resistances: TypeCount[];
  immunities: TypeCount[];
  blindSpots: PokemonType[];
  conditionals: string[];
}
