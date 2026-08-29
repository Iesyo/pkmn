import { calculate, Field, Move, Pokemon, type GenerationNum, type State } from "@smogon/calc";

import { parseEvs } from "./team-builder";
import type { PokemonSet } from "./types";

export const DAMAGE_STATS = ["atk", "def", "spa", "spd", "spe"] as const;
export type DamageStat = (typeof DAMAGE_STATS)[number];
export type DamageStatus = "" | "brn" | "par" | "psn" | "tox" | "slp" | "frz";
export type DamageWeather = "" | "Sun" | "Rain" | "Sand" | "Snow";
export type DamageTerrain = "" | "Electric" | "Grassy" | "Psychic" | "Misty";

export interface DamagePokemonDraft {
  set: PokemonSet;
  boosts: Record<DamageStat, number>;
  hpPercent: number;
  status: DamageStatus;
  teraActive: boolean;
  dynamaxActive: boolean;
  zMoveActive: boolean;
  critical: boolean;
}

export interface DamageSideConditions {
  reflect: boolean;
  lightScreen: boolean;
  auroraVeil: boolean;
  helpingHand: boolean;
  friendGuard: boolean;
  protected: boolean;
}

export interface DamageFieldState {
  gameType: "Singles" | "Doubles";
  weather: DamageWeather;
  terrain: DamageTerrain;
  gravity: boolean;
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

export function emptySideConditions(): DamageSideConditions {
  return {
    reflect: false,
    lightScreen: false,
    auroraVeil: false,
    helpingHand: false,
    friendGuard: false,
    protected: false,
  };
}

export function defaultDamageField(): DamageFieldState {
  return {
    gameType: "Doubles",
    weather: "",
    terrain: "",
    gravity: false,
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
    teraActive: false,
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
    isHelpingHand: side.helpingHand,
    isFriendGuard: side.friendGuard,
    isProtected: side.protected,
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
  const speciesName = format === "champions" && set.species === "Aegislash"
    ? (moveName ? "Aegislash-Blade" : "Aegislash-Shield")
    : set.species;
  const species = Pokemon.getForme(generation, speciesName, set.item || undefined, moveName);
  const options = {
    level: format === "champions" ? 50 : set.level || 50,
    ability: set.ability || undefined,
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
  const fullHealth = new Pokemon(generation, species, options);
  if (draft.hpPercent >= 100) return fullHealth;
  return new Pokemon(generation, species, {
    ...options,
    curHP: Math.max(1, Math.floor(fullHealth.maxHP() * draft.hpPercent / 100)),
  });
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
    const maxHp = defensePokemon.maxHP();
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
