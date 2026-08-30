import { calculate, Field, MEGA_STONES, Move, Pokemon, type GenerationNum, type State } from "@smogon/calc";

import { parseEvs } from "./team-builder";
import type { PokemonSet } from "./types";

export const DAMAGE_STATS = ["atk", "def", "spa", "spd", "spe"] as const;
export type DamageStat = (typeof DAMAGE_STATS)[number];
export type DamageStatus = "" | "brn" | "par" | "psn" | "tox" | "slp" | "frz";
export type DamageWeather = "" | "Sun" | "Rain" | "Sand" | "Snow";
export type DamageTerrain = "" | "Electric" | "Grassy" | "Psychic" | "Misty";
export type DamageGender = "" | "M" | "F" | "N";
export type DamageSwitching = "" | "in" | "out";
export type SpeedOrder = "left" | "right" | "tie";
export type EffectiveStatValues = Record<"hp" | DamageStat, number>;

export interface DamagePokemonDraft {
  set: PokemonSet;
  boosts: Record<DamageStat, number>;
  hpPercent: number;
  status: DamageStatus;
  gender: DamageGender;
  abilityOn: boolean;
  alliesFainted: number;
  teraActive: boolean;
  megaActive: boolean;
  dynamaxActive: boolean;
  zMoveActive: boolean;
  critical: boolean;
}

export interface DamageSideConditions {
  reflect: boolean;
  lightScreen: boolean;
  auroraVeil: boolean;
  tailwind: boolean;
  helpingHand: boolean;
  friendGuard: boolean;
  protected: boolean;
  charge: boolean;
  flowerGift: boolean;
  powerTrick: boolean;
  battery: boolean;
  powerSpot: boolean;
  steelySpirit: boolean;
  swordOfRuin: boolean;
  beadsOfRuin: boolean;
  tabletsOfRuin: boolean;
  vesselOfRuin: boolean;
  switching: DamageSwitching;
}

export interface DamageFieldState {
  gameType: "Singles" | "Doubles";
  weather: DamageWeather;
  terrain: DamageTerrain;
  gravity: boolean;
  trickRoom: boolean;
  magicRoom: boolean;
  wonderRoom: boolean;
  auraBreak: boolean;
  fairyAura: boolean;
  darkAura: boolean;
  left: DamageSideConditions;
  right: DamageSideConditions;
}

export interface DamageOutcome {
  move: string;
  min: number;
  max: number;
  minPercent: number;
  maxPercent: number;
  koChance: string;
  description: string;
  rolls: number[];
  error?: string;
}

export interface EffectiveStatsPair {
  left: EffectiveStatValues | null;
  right: EffectiveStatValues | null;
}

const MODERN_BOOST_TABLE: ReadonlyArray<readonly [number, number]> = [
  [2, 8],
  [2, 7],
  [2, 6],
  [2, 5],
  [2, 4],
  [2, 3],
  [2, 2],
  [3, 2],
  [4, 2],
  [5, 2],
  [6, 2],
  [7, 2],
  [8, 2],
];

export function getBoostedStatValue(rawStat: number, boost: number) {
  const stage = Math.max(-6, Math.min(6, Math.trunc(boost)));
  const [numerator, denominator] = MODERN_BOOST_TABLE[stage + 6];
  return Math.floor(rawStat * numerator / denominator);
}

export function getDisplayedEffectiveStat(
  stat: DamageStat,
  rawStat: number,
  boost: number,
  tailwind = false,
) {
  const boosted = getBoostedStatValue(rawStat, boost);
  return stat === "spe" && tailwind ? Math.min(10000, boosted * 2) : boosted;
}

export function getSpeedOrder(leftSpeed: number, rightSpeed: number, trickRoom = false): SpeedOrder {
  if (leftSpeed === rightSpeed) return "tie";
  if (trickRoom) return leftSpeed < rightSpeed ? "left" : "right";
  return leftSpeed > rightSpeed ? "left" : "right";
}

export function emptySideConditions(): DamageSideConditions {
  return {
    reflect: false,
    lightScreen: false,
    auroraVeil: false,
    tailwind: false,
    helpingHand: false,
    friendGuard: false,
    protected: false,
    charge: false,
    flowerGift: false,
    powerTrick: false,
    battery: false,
    powerSpot: false,
    steelySpirit: false,
    swordOfRuin: false,
    beadsOfRuin: false,
    tabletsOfRuin: false,
    vesselOfRuin: false,
    switching: "",
  };
}

export function defaultDamageField(): DamageFieldState {
  return {
    gameType: "Doubles",
    weather: "",
    terrain: "",
    gravity: false,
    trickRoom: false,
    magicRoom: false,
    wonderRoom: false,
    auraBreak: false,
    fairyAura: false,
    darkAura: false,
    left: emptySideConditions(),
    right: emptySideConditions(),
  };
}

export function createDamageDraft(set: PokemonSet): DamagePokemonDraft {
  return {
    set: {
      ...set,
      mechanics: { ...set.mechanics },
      moves: set.moves.map((move) => ({ ...move })),
      performance: { ...set.performance },
    },
    boosts: { atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
    hpPercent: 100,
    status: "",
    gender: "",
    abilityOn: false,
    alliesFainted: 0,
    teraActive: false,
    megaActive: false,
    dynamaxActive: false,
    zMoveActive: false,
    critical: false,
  };
}

export function generationForFormat(format: string): GenerationNum {
  if (format === "champions") return 0;
  const parsed = Number(format.match(/^gen([6-9])$/)?.[1] ?? 9);
  return parsed as GenerationNum;
}

function knowsDragonAscent(set: PokemonSet) {
  return set.moves.some((move) => move.name === "Dragon Ascent");
}

export function getMegaForm(set: PokemonSet): string | null {
  if (!set.species) return null;
  if (set.species === "Rayquaza") {
    return knowsDragonAscent(set) ? "Rayquaza-Mega" : null;
  }
  if (!set.item) return null;
  return MEGA_STONES[set.item]?.[set.species] ?? null;
}

export function canMegaEvolve(set: PokemonSet) {
  return Boolean(getMegaForm(set));
}

export function resolveBattleSpeciesName(
  format: string,
  draft: DamagePokemonDraft,
  moveName?: string,
) {
  const generation = generationForFormat(format);
  const set = draft.set;
  const megaForm = getMegaForm(set);
  if (draft.megaActive && megaForm) return megaForm;

  if (format === "champions" && set.species === "Aegislash") {
    return moveName ? "Aegislash-Blade" : "Aegislash-Shield";
  }

  const stoneEquipped = Boolean(set.item && MEGA_STONES[set.item]?.[set.species]);
  const itemForForme = stoneEquipped ? undefined : set.item || undefined;
  const moveForForme = set.species === "Rayquaza" ? undefined : moveName;
  return Pokemon.getForme(generation, set.species, itemForForme, moveForForme);
}

function allocationsFor(set: PokemonSet) {
  const values = parseEvs(set.evs);
  return {
    hp: values.HP,
    atk: values.Atk,
    def: values.Def,
    spa: values.SpA,
    spd: values.SpD,
    spe: values.Spe,
  };
}

function sideToCalc(side: DamageSideConditions): State.Side {
  return {
    isReflect: side.reflect,
    isLightScreen: side.lightScreen,
    isAuroraVeil: side.auroraVeil,
    isTailwind: side.tailwind,
    isHelpingHand: side.helpingHand,
    isFriendGuard: side.friendGuard,
    isProtected: side.protected,
    isCharge: side.charge,
    isFlowerGift: side.flowerGift,
    isPowerTrick: side.powerTrick,
    isBattery: side.battery,
    isPowerSpot: side.powerSpot,
    isSteelySpirit: side.steelySpirit,
    isSwitching: side.switching || undefined,
  };
}

function createField(state: DamageFieldState, reverse: boolean) {
  const attacker = reverse ? state.right : state.left;
  const defender = reverse ? state.left : state.right;
  return new Field({
    gameType: state.gameType,
    weather: state.weather || undefined,
    terrain: state.terrain || undefined,
    isGravity: state.gravity,
    isMagicRoom: Boolean(state.magicRoom),
    isWonderRoom: Boolean(state.wonderRoom),
    isAuraBreak: Boolean(state.auraBreak),
    isFairyAura: Boolean(state.fairyAura),
    isDarkAura: Boolean(state.darkAura),
    isSwordOfRuin: Boolean(attacker.swordOfRuin),
    isBeadsOfRuin: Boolean(attacker.beadsOfRuin),
    isTabletsOfRuin: Boolean(defender.tabletsOfRuin),
    isVesselOfRuin: Boolean(defender.vesselOfRuin),
    attackerSide: sideToCalc(attacker),
    defenderSide: sideToCalc(defender),
  });
}

function createPokemon(
  format: string,
  draft: DamagePokemonDraft,
  moveName?: string,
) {
  const generation = generationForFormat(format);
  const set = draft.set;
  const speciesName = resolveBattleSpeciesName(format, draft, moveName);
  const transformed = speciesName !== set.species;
  const protoQuark = set.ability === "Protosynthesis" || set.ability === "Quark Drive";
  const options = {
    level: format === "champions" ? 50 : set.level || 50,
    ability: transformed ? undefined : set.ability || undefined,
    abilityOn: Boolean(draft.abilityOn),
    alliesFainted: Math.max(0, Math.min(5, Math.trunc(draft.alliesFainted ?? 0))),
    boostedStat: protoQuark ? "auto" as const : undefined,
    gender: draft.gender || undefined,
    item: set.item || undefined,
    nature: set.nature || "Serious",
    evs: allocationsFor(set),
    boosts: { hp: 0, ...draft.boosts },
    status: draft.status,
    teraType: draft.teraActive ? set.teraType || undefined : undefined,
    isDynamaxed: draft.dynamaxActive
      ? (set.mechanics?.gigantamax ? "gmax" as const : true)
      : false,
    dynamaxLevel: set.mechanics?.dynamaxLevel ?? 10,
  };
  const fullHealth = new Pokemon(generation, speciesName, options);
  if (draft.hpPercent >= 100) return fullHealth;
  return new Pokemon(generation, speciesName, {
    ...options,
    curHP: Math.max(1, Math.floor(fullHealth.maxHP() * draft.hpPercent / 100)),
  });
}

function statsFromPokemon(pokemon: Pokemon): EffectiveStatValues {
  return {
    hp: pokemon.stats.hp,
    atk: pokemon.stats.atk,
    def: pokemon.stats.def,
    spa: pokemon.stats.spa,
    spd: pokemon.stats.spd,
    spe: pokemon.stats.spe,
  };
}

export function calculateEffectiveStats(
  format: string,
  left: DamagePokemonDraft,
  right: DamagePokemonDraft,
  fieldState: DamageFieldState,
): EffectiveStatsPair {
  if (!left.set.species || !right.set.species) return { left: null, right: null };

  try {
    const generation = generationForFormat(format);
    const leftPokemon = createPokemon(format, left);
    const rightPokemon = createPokemon(format, right);
    const probeMove = new Move(generation, "Protect");
    const result = calculate(generation, leftPokemon, rightPokemon, probeMove, createField(fieldState, false));
    return {
      left: statsFromPokemon(result.attacker),
      right: statsFromPokemon(result.defender),
    };
  } catch {
    return { left: null, right: null };
  }
}

function summarizeRolls(damage: number | number[] | number[][], range: [number, number]) {
  if (typeof damage === "number") return [damage];
  if (damage.every((value) => typeof value === "number")) {
    return [...new Set(damage as number[])].sort((left, right) => left - right);
  }
  return range[0] === range[1] ? [range[0]] : range;
}

export function calculateDamage(
  format: string,
  attacker: DamagePokemonDraft,
  defender: DamagePokemonDraft,
  moveName: string,
  fieldState: DamageFieldState,
  reverse = false,
): DamageOutcome {
  if (!attacker.set.species || !defender.set.species || !moveName) {
    return {
      move: moveName,
      min: 0,
      max: 0,
      minPercent: 0,
      maxPercent: 0,
      koChance: "Completa ambos Pokémon",
      description: "Selecciona un atacante, un rival y un movimiento.",
      rolls: [],
    };
  }

  try {
    const generation = generationForFormat(format);
    const attackPokemon = createPokemon(format, attacker, moveName);
    const defensePokemon = createPokemon(format, defender);
    const move = new Move(generation, moveName, {
      ability: attackPokemon.ability,
      item: attackPokemon.item,
      species: attackPokemon.name,
      useZ: attacker.zMoveActive,
      useMax: attacker.dynamaxActive
        ? (attacker.set.mechanics?.gigantamax ? "gmax" : true)
        : false,
      isCrit: attacker.critical,
    });
    const result = calculate(
      generation,
      attackPokemon,
      defensePokemon,
      move,
      createField(fieldState, reverse),
    );
    const range = result.range();
    const maxHp = result.defender.maxHP();
    const ko = result.kochance(false);
    return {
      move: moveName,
      min: range[0],
      max: range[1],
      minPercent: Number((range[0] / maxHp * 100).toFixed(1)),
      maxPercent: Number((range[1] / maxHp * 100).toFixed(1)),
      koChance: ko.text || "Sin KO estimado",
      description: result.fullDesc("%", false),
      rolls: summarizeRolls(result.damage, range),
    };
  } catch {
    return {
      move: moveName,
      min: 0,
      max: 0,
      minPercent: 0,
      maxPercent: 0,
      koChance: "No calculable",
      description: "No fue posible calcular este cruce con la configuración actual.",
      rolls: [],
      error: "Esta combinación todavía no es compatible con el motor de cálculo.",
    };
  }
}

export function calculateMoves(
  format: string,
  attacker: DamagePokemonDraft,
  defender: DamagePokemonDraft,
  field: DamageFieldState,
  reverse = false,
) {
  return attacker.set.moves
    .filter((move) => move.name)
    .map((move) => calculateDamage(format, attacker, defender, move.name, field, reverse));
}
