import { getMoveData, getSpeciesTypes } from "./pokemon-data";
import type {
  LeadStat,
  MatchRecord,
  MoveSet,
  PokemonPerformance,
  PokemonSet,
  PokemonType,
  TeamGroup,
  TeamVersion,
} from "./types";

function moves(entries: Array<[string, number]>): MoveSet[] {
  return entries.map(([name, usage]) => {
    const data = getMoveData(name);
    return { name, usage, type: data.type, damaging: data.damaging };
  });
}

function set(
  slot: number,
  species: string,
  item: string,
  ability: string,
  teraType: PokemonType,
  nature: string,
  evs: string,
  moveEntries: Array<[string, number]>,
  performance: PokemonPerformance,
  nickname = species,
): PokemonSet {
  return {
    id: `demo-${species.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${slot}`,
    slot,
    nickname,
    species,
    item,
    ability,
    level: 50,
    teraType,
    evs,
    nature,
    moves: moves(moveEntries),
    types: getSpeciesTypes(species),
    performance,
  };
}

const replayHome = "https://replay.pokemonshowdown.com/";

const auroraMatches: MatchRecord[] = [
  {
    id: "a-1",
    result: "win",
    opponentName: "Rain Control",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Kleavor", "Miraidon", "Incineroar", "Farigiraf"],
    opponentSelected: ["Pelipper", "Archaludon", "Rillaboom", "Amoonguss"],
    lead: ["Kleavor", "Miraidon"],
    rating: 1428,
    notes: "Stone Axe aseguró el control del ritmo.",
    playedAt: "2026-08-27T21:14:00.000Z",
  },
  {
    id: "a-2",
    result: "win",
    opponentName: "Calyrex Balance",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Kleavor", "Miraidon", "Incineroar", "Urshifu-Rapid-Strike"],
    opponentSelected: ["Calyrex-Shadow", "Incineroar", "Rillaboom", "Urshifu-Rapid-Strike"],
    lead: ["Kleavor", "Miraidon"],
    rating: 1441,
    notes: "Buen turno uno; conservar Fake Out fue clave.",
    playedAt: "2026-08-26T19:42:00.000Z",
  },
  {
    id: "a-3",
    result: "loss",
    opponentName: "Hard Trick Room",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Farigiraf", "Incineroar", "Ogerpon-Wellspring", "Urshifu-Rapid-Strike"],
    opponentSelected: ["Indeedee-F", "Calyrex-Ice", "Torkoal", "Ursaluna-Bloodmoon"],
    lead: ["Farigiraf", "Incineroar"],
    rating: 1424,
    notes: "No hubo presión suficiente sobre el setter.",
    playedAt: "2026-08-25T18:10:00.000Z",
  },
  {
    id: "a-4",
    result: "win",
    opponentName: "Sun Offense",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Kleavor", "Miraidon", "Ogerpon-Wellspring", "Farigiraf"],
    opponentSelected: ["Koraidon", "Flutter Mane", "Raging Bolt", "Chi-Yu"],
    lead: ["Kleavor", "Miraidon"],
    rating: 1453,
    notes: "Tera Water de Ogerpon cerró la partida.",
    playedAt: "2026-08-24T22:01:00.000Z",
  },
  {
    id: "a-5",
    result: "win",
    opponentName: "Zamazenta Screens",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Kleavor", "Miraidon", "Incineroar", "Urshifu-Rapid-Strike"],
    opponentSelected: ["Zamazenta-Crowned", "Grimmsnarl", "Chien-Pao", "Dragonite"],
    lead: ["Incineroar", "Miraidon"],
    rating: 1476,
    notes: "Parting Shot abrió el endgame de Miraidon.",
    playedAt: "2026-08-23T20:16:00.000Z",
  },
  {
    id: "a-6",
    result: "loss",
    opponentName: "Tailwind Rain",
    opponentPaste: "",
    replayUrl: replayHome,
    selected: ["Miraidon", "Incineroar", "Ogerpon-Wellspring", "Farigiraf"],
    opponentSelected: ["Tornadus", "Kyogre", "Rillaboom", "Archaludon"],
    lead: ["Farigiraf", "Miraidon"],
    rating: 1462,
    notes: "Revisar el plan contra Tailwind + Water Spout.",
    playedAt: "2026-08-22T17:35:00.000Z",
  },
];

const auroraPokemon = [
  set(1, "Kleavor", "Choice Scarf", "Sharpness", "Water", "Adamant", "4 HP / 252 Atk / 252 Spe", [["Stone Axe", 100], ["X-Scissor", 33.3], ["Close Combat", 50], ["U-turn", 16.7]], { games: 4, wins: 4, leadGames: 3, leadWins: 3, selectionRate: 66.7 }),
  set(2, "Miraidon", "Choice Specs", "Hadron Engine", "Fairy", "Modest", "44 HP / 4 Def / 252 SpA / 4 SpD / 204 Spe", [["Electro Drift", 83.3], ["Draco Meteor", 50], ["Volt Switch", 50], ["Dazzling Gleam", 16.7]], { games: 5, wins: 4, leadGames: 5, leadWins: 4, selectionRate: 83.3 }),
  set(3, "Incineroar", "Safety Goggles", "Intimidate", "Ghost", "Careful", "236 HP / 4 Atk / 100 Def / 156 SpD / 12 Spe", [["Fake Out", 66.7], ["Flare Blitz", 33.3], ["Knock Off", 50], ["Parting Shot", 66.7]], { games: 4, wins: 3, leadGames: 2, leadWins: 1, selectionRate: 66.7 }),
  set(4, "Ogerpon-Wellspring", "Wellspring Mask", "Water Absorb", "Water", "Jolly", "4 HP / 252 Atk / 252 Spe", [["Ivy Cudgel", 66.7], ["Horn Leech", 50], ["Follow Me", 33.3], ["Spiky Shield", 50]], { games: 3, wins: 1, leadGames: 0, leadWins: 0, selectionRate: 50 }),
  set(5, "Farigiraf", "Throat Spray", "Armor Tail", "Fairy", "Modest", "180 HP / 76 Def / 252 SpA", [["Trick Room", 33.3], ["Psychic", 33.3], ["Helping Hand", 16.7], ["Protect", 50]], { games: 4, wins: 2, leadGames: 2, leadWins: 0, selectionRate: 66.7 }),
  set(6, "Urshifu-Rapid-Strike", "Mystic Water", "Unseen Fist", "Water", "Adamant", "4 HP / 252 Atk / 252 Spe", [["Surging Strikes", 50], ["Close Combat", 33.3], ["Aqua Jet", 16.7], ["Detect", 33.3]], { games: 3, wins: 2, leadGames: 0, leadWins: 0, selectionRate: 50 }),
];

const auroraPaste = `Kleavor @ Choice Scarf
Ability: Sharpness
Level: 50
Tera Type: Water
EVs: 4 HP / 252 Atk / 252 Spe
Adamant Nature
- Stone Axe
- X-Scissor
- Close Combat
- U-turn

Miraidon @ Choice Specs
Ability: Hadron Engine
Level: 50
Tera Type: Fairy
EVs: 44 HP / 4 Def / 252 SpA / 4 SpD / 204 Spe
Modest Nature
- Electro Drift
- Draco Meteor
- Volt Switch
- Dazzling Gleam

Incineroar @ Safety Goggles
Ability: Intimidate
Level: 50
Tera Type: Ghost
EVs: 236 HP / 4 Atk / 100 Def / 156 SpD / 12 Spe
Careful Nature
- Fake Out
- Flare Blitz
- Knock Off
- Parting Shot

Ogerpon-Wellspring @ Wellspring Mask
Ability: Water Absorb
Level: 50
Tera Type: Water
EVs: 4 HP / 252 Atk / 252 Spe
Jolly Nature
- Ivy Cudgel
- Horn Leech
- Follow Me
- Spiky Shield

Farigiraf @ Throat Spray
Ability: Armor Tail
Level: 50
Tera Type: Fairy
EVs: 180 HP / 76 Def / 252 SpA
Modest Nature
- Trick Room
- Psychic
- Helping Hand
- Protect

Urshifu-Rapid-Strike @ Mystic Water
Ability: Unseen Fist
Level: 50
Tera Type: Water
EVs: 4 HP / 252 Atk / 252 Spe
Adamant Nature
- Surging Strikes
- Close Combat
- Aqua Jet
- Detect`;

const emberMatches: MatchRecord[] = [
  { id: "b-1", result: "win", opponentName: "Miraidon Balance", opponentPaste: "", replayUrl: replayHome, selected: ["Koraidon", "Flutter Mane", "Rillaboom", "Raging Bolt"], opponentSelected: ["Miraidon", "Incineroar", "Farigiraf", "Urshifu-Rapid-Strike"], lead: ["Koraidon", "Flutter Mane"], rating: 1398, notes: "", playedAt: "2026-08-27T17:30:00.000Z" },
  { id: "b-2", result: "loss", opponentName: "Calyrex Ice Room", opponentPaste: "", replayUrl: replayHome, selected: ["Koraidon", "Flutter Mane", "Amoonguss", "Chi-Yu"], opponentSelected: ["Calyrex-Ice", "Indeedee-F", "Incineroar", "Amoonguss"], lead: ["Koraidon", "Flutter Mane"], rating: 1381, notes: "", playedAt: "2026-08-26T16:21:00.000Z" },
  { id: "b-3", result: "win", opponentName: "Rain Balance", opponentPaste: "", replayUrl: replayHome, selected: ["Koraidon", "Rillaboom", "Raging Bolt", "Chi-Yu"], opponentSelected: ["Kyogre", "Tornadus", "Rillaboom", "Archaludon"], lead: ["Rillaboom", "Raging Bolt"], rating: 1410, notes: "", playedAt: "2026-08-25T20:05:00.000Z" },
  { id: "b-4", result: "win", opponentName: "Zamazenta Balance", opponentPaste: "", replayUrl: replayHome, selected: ["Koraidon", "Flutter Mane", "Rillaboom", "Chi-Yu"], opponentSelected: ["Zamazenta-Crowned", "Chien-Pao", "Dragonite", "Incineroar"], lead: ["Koraidon", "Flutter Mane"], rating: 1432, notes: "", playedAt: "2026-08-24T19:44:00.000Z" },
  { id: "b-5", result: "loss", opponentName: "Miraidon Screens", opponentPaste: "", replayUrl: replayHome, selected: ["Koraidon", "Flutter Mane", "Raging Bolt", "Amoonguss"], opponentSelected: ["Miraidon", "Grimmsnarl", "Iron Hands", "Ogerpon-Wellspring"], lead: ["Amoonguss", "Koraidon"], rating: 1417, notes: "", playedAt: "2026-08-23T15:14:00.000Z" },
];

const emberPokemon = [
  set(1, "Koraidon", "Clear Amulet", "Orichalcum Pulse", "Fire", "Jolly", "4 HP / 252 Atk / 252 Spe", [["Collision Course", 80], ["Flame Charge", 40], ["Dragon Claw", 40], ["Protect", 60]], { games: 5, wins: 3, leadGames: 4, leadWins: 2, selectionRate: 100 }),
  set(2, "Flutter Mane", "Focus Sash", "Protosynthesis", "Fairy", "Timid", "4 HP / 252 SpA / 252 Spe", [["Moonblast", 80], ["Shadow Ball", 60], ["Icy Wind", 40], ["Protect", 40]], { games: 4, wins: 2, leadGames: 3, leadWins: 2, selectionRate: 80 }),
  set(3, "Rillaboom", "Assault Vest", "Grassy Surge", "Fire", "Adamant", "236 HP / 196 Atk / 76 SpD", [["Fake Out", 60], ["Grassy Glide", 80], ["Wood Hammer", 40], ["U-turn", 20]], { games: 3, wins: 3, leadGames: 1, leadWins: 1, selectionRate: 60 }),
  set(4, "Raging Bolt", "Booster Energy", "Protosynthesis", "Fairy", "Modest", "196 HP / 252 SpA / 60 Spe", [["Thunderclap", 60], ["Draco Meteor", 40], ["Snarl", 40], ["Protect", 40]], { games: 3, wins: 2, leadGames: 1, leadWins: 1, selectionRate: 60 }),
  set(5, "Amoonguss", "Rocky Helmet", "Regenerator", "Water", "Sassy", "236 HP / 156 Def / 116 SpD", [["Spore", 60], ["Rage Powder", 40], ["Pollen Puff", 20], ["Protect", 20]], { games: 2, wins: 0, leadGames: 1, leadWins: 0, selectionRate: 40 }),
  set(6, "Chi-Yu", "Choice Specs", "Beads of Ruin", "Ghost", "Timid", "4 HP / 252 SpA / 252 Spe", [["Heat Wave", 80], ["Dark Pulse", 40], ["Overheat", 40], ["Snarl", 20]], { games: 3, wins: 2, leadGames: 0, leadWins: 0, selectionRate: 60 }),
];

const emberPaste = `Koraidon @ Clear Amulet
Ability: Orichalcum Pulse
Level: 50
Tera Type: Fire
EVs: 4 HP / 252 Atk / 252 Spe
Jolly Nature
- Collision Course
- Flame Charge
- Dragon Claw
- Protect

Flutter Mane @ Focus Sash
Ability: Protosynthesis
Level: 50
Tera Type: Fairy
EVs: 4 HP / 252 SpA / 252 Spe
Timid Nature
- Moonblast
- Shadow Ball
- Icy Wind
- Protect

Rillaboom @ Assault Vest
Ability: Grassy Surge
Level: 50
Tera Type: Fire
EVs: 236 HP / 196 Atk / 76 SpD
Adamant Nature
- Fake Out
- Grassy Glide
- Wood Hammer
- U-turn

Raging Bolt @ Booster Energy
Ability: Protosynthesis
Level: 50
Tera Type: Fairy
EVs: 196 HP / 252 SpA / 60 Spe
Modest Nature
- Thunderclap
- Draco Meteor
- Snarl
- Protect

Amoonguss @ Rocky Helmet
Ability: Regenerator
Level: 50
Tera Type: Water
EVs: 236 HP / 156 Def / 116 SpD
Sassy Nature
- Spore
- Rage Powder
- Pollen Puff
- Protect

Chi-Yu @ Choice Specs
Ability: Beads of Ruin
Level: 50
Tera Type: Ghost
EVs: 4 HP / 252 SpA / 252 Spe
Timid Nature
- Heat Wave
- Dark Pulse
- Overheat
- Snarl`;

function version(
  id: string,
  teamId: string,
  name: string,
  number: number,
  paste: string,
  pokemon: PokemonSet[],
  matches: MatchRecord[],
  leads: LeadStat[],
): TeamVersion {
  return {
    id,
    teamId,
    name,
    version: number,
    paste,
    pokemon,
    matches,
    games: matches.length,
    wins: matches.filter((match) => match.result === "win").length,
    leads,
    createdAt: "2026-08-28T00:00:00.000Z",
    demo: true,
  };
}

const auroraV2 = version(
  "demo-aurora-v2",
  "demo-aurora",
  "Aurora Protocol",
  2,
  auroraPaste,
  auroraPokemon,
  auroraMatches,
  [
    { species: ["Kleavor", "Miraidon"], games: 3, wins: 3 },
    { species: ["Farigiraf", "Incineroar"], games: 1, wins: 0 },
    { species: ["Incineroar", "Miraidon"], games: 1, wins: 1 },
  ],
);

const auroraV1 = {
  ...auroraV2,
  id: "demo-aurora-v1",
  version: 1,
  paste: auroraPaste.replace("Choice Scarf", "Focus Sash"),
  pokemon: auroraPokemon.map((pokemon) =>
    pokemon.species === "Kleavor" ? { ...pokemon, item: "Focus Sash" } : pokemon,
  ),
  games: 4,
  wins: 2,
  matches: auroraMatches.slice(2),
  createdAt: "2026-08-18T00:00:00.000Z",
};

const emberV1 = version(
  "demo-ember-v1",
  "demo-ember",
  "Ember Circuit",
  1,
  emberPaste,
  emberPokemon,
  emberMatches,
  [
    { species: ["Koraidon", "Flutter Mane"], games: 3, wins: 2 },
    { species: ["Rillaboom", "Raging Bolt"], games: 1, wins: 1 },
    { species: ["Amoonguss", "Koraidon"], games: 1, wins: 0 },
  ],
);

export const DEMO_GROUPS: TeamGroup[] = [
  { id: "demo-aurora", name: "Aurora Protocol", versions: [auroraV2, auroraV1] },
  { id: "demo-ember", name: "Ember Circuit", versions: [emberV1] },
];

export const DEFAULT_LEFT_VERSION_ID = auroraV2.id;
export const DEFAULT_RIGHT_VERSION_ID = emberV1.id;
