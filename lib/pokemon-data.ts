import type { PokemonType } from "./types";

export const SHOWDOWN_SNAPSHOT = {
  source: "Pokémon Showdown data",
  label: "snapshot 2026-08",
  url: "https://github.com/smogon/pokemon-showdown/tree/master/data",
} as const;

export function toId(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

const speciesTypes: Record<string, PokemonType[]> = {
  kleavor: ["Bug", "Rock"],
  miraidon: ["Electric", "Dragon"],
  incineroar: ["Fire", "Dark"],
  ogerponwellspring: ["Grass", "Water"],
  farigiraf: ["Normal", "Psychic"],
  urshifurapidstrike: ["Fighting", "Water"],
  urshifurapid: ["Fighting", "Water"],
  koraidon: ["Fighting", "Dragon"],
  fluttermane: ["Ghost", "Fairy"],
  rillaboom: ["Grass"],
  ragingbolt: ["Electric", "Dragon"],
  amoonguss: ["Grass", "Poison"],
  chiyu: ["Dark", "Fire"],
  calyrexshadow: ["Psychic", "Ghost"],
  calyrexice: ["Psychic", "Ice"],
  ironhands: ["Fighting", "Electric"],
  tornadus: ["Flying"],
  landorustherian: ["Ground", "Flying"],
  whimsicott: ["Grass", "Fairy"],
  pelipper: ["Water", "Flying"],
  archaludon: ["Steel", "Dragon"],
  chienpao: ["Dark", "Ice"],
  dragonite: ["Dragon", "Flying"],
  gholdengo: ["Steel", "Ghost"],
  indeedeef: ["Psychic", "Normal"],
  indeedee: ["Psychic", "Normal"],
  ursalunabloodmoon: ["Ground", "Normal"],
  ironcrown: ["Steel", "Psychic"],
  terapagos: ["Normal"],
  zamazentacrowned: ["Fighting", "Steel"],
  groudon: ["Ground"],
  kyogre: ["Water"],
};

const moveTypes: Record<string, PokemonType> = {
  stoneaxe: "Rock",
  xscissor: "Bug",
  closecombat: "Fighting",
  uturn: "Bug",
  electrodrift: "Electric",
  dracometeor: "Dragon",
  voltswitch: "Electric",
  protect: "Normal",
  fakeout: "Normal",
  flareblitz: "Fire",
  knockoff: "Dark",
  partingshot: "Dark",
  ivycudgel: "Water",
  hornleech: "Grass",
  followme: "Normal",
  spikyshield: "Grass",
  trickroom: "Psychic",
  psychic: "Psychic",
  helpinghand: "Normal",
  surgingstrikes: "Water",
  aquajet: "Water",
  detect: "Fighting",
  collisioncourse: "Fighting",
  flamecharge: "Fire",
  dragonclaw: "Dragon",
  moonblast: "Fairy",
  shadowball: "Ghost",
  icywind: "Ice",
  grassyglide: "Grass",
  woodhammer: "Grass",
  thunderclap: "Electric",
  snarl: "Dark",
  spore: "Grass",
  ragepowder: "Bug",
  pollenpuff: "Bug",
  heatwave: "Fire",
  darkpulse: "Dark",
  overheat: "Fire",
  bodypress: "Fighting",
  heavyslam: "Steel",
  behemothbash: "Steel",
  earthpower: "Ground",
  hypervoice: "Normal",
  dazzlinggleam: "Fairy",
  expandingforce: "Psychic",
};

const statusMoves = new Set([
  "protect",
  "detect",
  "partingshot",
  "followme",
  "spikyshield",
  "trickroom",
  "helpinghand",
  "spore",
  "ragepowder",
]);

const spriteAliases: Record<string, string> = {
  urshifurapidstrike: "urshifu-rapidstrike",
  urshifurapid: "urshifu-rapidstrike",
  ogerponwellspring: "ogerpon-wellspring",
  fluttermane: "flutter-mane",
  ragingbolt: "raging-bolt",
  chiyu: "chi-yu",
  calyrexshadow: "calyrex-shadow",
  calyrexice: "calyrex-ice",
  ironhands: "iron-hands",
  landorustherian: "landorus-therian",
  chienpao: "chien-pao",
  indeedeef: "indeedee-f",
  ursalunabloodmoon: "ursaluna-bloodmoon",
  ironcrown: "iron-crown",
  zamazentacrowned: "zamazenta-crowned",
};

const conditionalEffects: Record<string, string> = {
  levitate: "Levitate: inmunidad condicional a Ground",
  flashfire: "Flash Fire: inmunidad condicional a Fire",
  waterabsorb: "Water Absorb: inmunidad condicional a Water",
  stormdrain: "Storm Drain: inmunidad condicional a Water",
  lightningrod: "Lightning Rod: inmunidad condicional a Electric",
  motordrive: "Motor Drive: inmunidad condicional a Electric",
  sapsipper: "Sap Sipper: inmunidad condicional a Grass",
  airballoon: "Air Balloon: inmunidad temporal a Ground",
};

export function getSpeciesTypes(species: string): PokemonType[] {
  return speciesTypes[toId(species)] ?? ["Normal"];
}

export function getMoveData(move: string) {
  const id = toId(move);
  return {
    type: moveTypes[id] ?? null,
    damaging: !statusMoves.has(id),
  };
}

export function getSpriteUrl(species: string) {
  const id = toId(species);
  const slug = spriteAliases[id] ?? species.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  return `https://play.pokemonshowdown.com/sprites/ani/${slug}.gif`;
}

export function getConditionalEffect(ability: string, item: string) {
  return [conditionalEffects[toId(ability)], conditionalEffects[toId(item)]].filter(
    (value): value is string => Boolean(value),
  );
}
