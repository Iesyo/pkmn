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

const catalogLabels: Record<string, string> = {
  kleavor: "Kleavor", miraidon: "Miraidon", incineroar: "Incineroar",
  ogerponwellspring: "Ogerpon-Wellspring", farigiraf: "Farigiraf",
  urshifurapidstrike: "Urshifu-Rapid-Strike", koraidon: "Koraidon",
  fluttermane: "Flutter Mane", rillaboom: "Rillaboom", ragingbolt: "Raging Bolt",
  amoonguss: "Amoonguss", chiyu: "Chi-Yu", calyrexshadow: "Calyrex-Shadow",
  calyrexice: "Calyrex-Ice", ironhands: "Iron Hands", tornadus: "Tornadus",
  landorustherian: "Landorus-Therian", whimsicott: "Whimsicott", pelipper: "Pelipper",
  archaludon: "Archaludon", chienpao: "Chien-Pao", dragonite: "Dragonite",
  gholdengo: "Gholdengo", indeedeef: "Indeedee-F", indeedee: "Indeedee",
  ursalunabloodmoon: "Ursaluna-Bloodmoon", ironcrown: "Iron Crown", terapagos: "Terapagos",
  zamazentacrowned: "Zamazenta-Crowned", groudon: "Groudon", kyogre: "Kyogre",
};

export const POKEMON_CATALOG = Object.keys(speciesTypes).map((id) => ({
  name: catalogLabels[id] ?? id,
  types: speciesTypes[id],
}));

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

const moveLabels: Record<string, string> = {
    stoneaxe: "Stone Axe", xscissor: "X-Scissor", closecombat: "Close Combat", uturn: "U-turn",
    electrodrift: "Electro Drift", dracometeor: "Draco Meteor", voltswitch: "Volt Switch",
    fakeout: "Fake Out", flareblitz: "Flare Blitz", knockoff: "Knock Off", partingshot: "Parting Shot",
    ivycudgel: "Ivy Cudgel", hornleech: "Horn Leech", followme: "Follow Me", spikyshield: "Spiky Shield",
    trickroom: "Trick Room", helpinghand: "Helping Hand", surgingstrikes: "Surging Strikes",
    aquajet: "Aqua Jet", collisioncourse: "Collision Course", flamecharge: "Flame Charge",
    dragonclaw: "Dragon Claw", moonblast: "Moonblast", shadowball: "Shadow Ball", icywind: "Icy Wind",
    grassyglide: "Grassy Glide", woodhammer: "Wood Hammer", thunderclap: "Thunderclap",
    ragepowder: "Rage Powder", pollenpuff: "Pollen Puff", heatwave: "Heat Wave",
    darkpulse: "Dark Pulse", bodypress: "Body Press", heavyslam: "Heavy Slam",
    behemothbash: "Behemoth Bash", earthpower: "Earth Power", hypervoice: "Hyper Voice",
    dazzlinggleam: "Dazzling Gleam", expandingforce: "Expanding Force",
};

export const MOVE_CATALOG = Object.keys(moveTypes).map((id) => {
  const spaced = id.replace(/([a-z])([A-Z])/g, "$1 $2");
  const known = moveLabels[id];
  return known ?? spaced.charAt(0).toUpperCase() + spaced.slice(1);
}).sort();

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
  calyrexshadow: "calyrex-shadow",
  calyrexice: "calyrex-ice",
  landorustherian: "landorus-therian",
  indeedeef: "indeedee-f",
  ursalunabloodmoon: "ursaluna-bloodmoon",
  zamazentacrowned: "zamazenta-crowned",
};

const megaSpriteAliases: Record<string, string> = {
  meowsticmmega: "meowstic-mmega",
  meowsticfmega: "meowstic-fmega",
  magearnaoriginalmega: "magearna-originalmega",
  tatsugiricurlymega: "tatsugiri-curly-mega",
  tatsugiridroopymega: "tatsugiri-droopy-mega",
  tatsugiristretchymega: "tatsugiri-stretchy-mega",
};

const animatedMegaSpriteIds = new Set([
  "malamarmega",
  "raichumegax",
  "raichumegay",
]);

const megaZBaseFallbacks: Record<string, string> = {
  absolmegaz: "absol",
  garchompmegaz: "garchomp",
  lucariomegaz: "lucario",
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

function getMegaSpriteSlug(species: string) {
  const explicit = megaSpriteAliases[toId(species)];
  if (explicit) return explicit;
  const match = species.match(/^(.*)-Mega(?:-([XYZ]))?$/i);
  if (!match) return null;
  const base = match[1].toLowerCase().replace(/[^a-z0-9-]+/g, "");
  const variant = match[2]?.toLowerCase();
  return variant ? `${base}-mega${variant}` : `${base}-mega`;
}

export function getSpriteUrl(species: string) {
  const id = toId(species);
  const zFallback = megaZBaseFallbacks[id];
  if (zFallback) {
    // Pokémon Showdown's standard sprite catalogs do not yet expose these
    // Legends Z-A Mega-Z art assets. Keep the UI intact until they land.
    return `https://play.pokemonshowdown.com/sprites/gen5/${zFallback}.png`;
  }

  const slug = getMegaSpriteSlug(species) ?? spriteAliases[id] ?? id;
  if (animatedMegaSpriteIds.has(id)) {
    // Some current Z-A Megas are available in Showdown's animated catalog
    // before they reach the Gen 5-style static catalog.
    return `https://play.pokemonshowdown.com/sprites/ani/${slug}.gif`;
  }

  // The Gen 5-style static catalog is the default because it is visually
  // consistent across the rest of the app. Mega X/Y/Z use -megax/-megay/-megaz.
  return `https://play.pokemonshowdown.com/sprites/gen5/${slug}.png`;
}

export function getConditionalEffect(ability: string, item: string) {
  return [conditionalEffects[toId(ability)], conditionalEffects[toId(item)]].filter(
    (value): value is string => Boolean(value),
  );
}
