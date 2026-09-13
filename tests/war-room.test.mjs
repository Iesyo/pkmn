import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

async function readSnapshot() {
  const compressed = await readFile(new URL("../public/data/showdown-dex.json.gz", import.meta.url));
  return JSON.parse(gunzipSync(compressed).toString("utf8"));
}

function set(species, moves, slot, overrides = {}) {
  return {
    id: `set-${slot}`,
    slot,
    nickname: species,
    species,
    item: "",
    ability: "",
    level: 50,
    teraType: null,
    mechanics: { megaEvolution: false },
    evs: "32 HP / 32 Spe / 2 Def",
    nature: "Jolly",
    moves: moves.map((name) => ({ name, type: null, damaging: true, usage: 0 })),
    types: [],
    performance: { games: 0, wins: 0, leadGames: 0, leadWins: 0, selectionRate: 0 },
    ...overrides,
  };
}

function ownTeam() {
  return [
    set("Charizard", ["Heat Wave", "Air Slash", "Protect", "Dragon Pulse"], 1),
    set("Rillaboom", ["Grassy Glide", "Wood Hammer", "Fake Out", "U-turn"], 2),
    set("Incineroar", ["Flare Blitz", "Knock Off", "Fake Out", "Parting Shot"], 3),
    set("Amoonguss", ["Spore", "Rage Powder", "Pollen Puff", "Protect"], 4),
    set("Garchomp", ["Earthquake", "Dragon Claw", "Rock Slide", "Protect"], 5),
    set("Gholdengo", ["Make It Rain", "Shadow Ball", "Thunderbolt", "Protect"], 6),
  ];
}

function corpusTeam(id, pokemon, overrides = {}) {
  return {
    id,
    source: "vgcpastes",
    savedPasteId: "",
    playerName: `Player ${id}`,
    tournament: "War Room Open",
    rank: "Top 8",
    dateShared: "13 Sep 2026",
    pokepasteUrl: "",
    pokemon,
    ...overrides,
  };
}

function corpus() {
  return [
    corpusTeam("A", ["Kyogre", "Tornadus", "Pelipper", "Flutter Mane", "Amoonguss", "Rillaboom"]),
    corpusTeam("B", ["Kyogre", "Tornadus", "Pelipper", "Incineroar", "Rillaboom", "Gholdengo"]),
    corpusTeam("C", ["Landorus-Therian", "Flutter Mane", "Pelipper", "Amoonguss", "Rillaboom", "Charizard"]),
    corpusTeam("D", ["Landorus-Therian", "Chien-Pao", "Pelipper", "Dragonite", "Rillaboom", "Charizard"]),
    corpusTeam("E", ["Kyogre", "Tornadus", "Pelipper", "Farigiraf", "Rillaboom", "Charizard"]),
    corpusTeam("F", ["Koraidon", "Flutter Mane", "Farigiraf", "Amoonguss", "Rillaboom", "Charizard"]),
  ];
}

test("builds a bounded, validated M-C corpus contract", async () => {
  const { buildWarRoomCorpusResponse, isWarRoomCorpusResponse } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { VGCPASTES_FORMATS } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const sourceTeam = {
    ...corpusTeam("MC001", ["Kyogre", "Tornadus", "Pelipper", "Flutter Mane", "Amoonguss", "Rillaboom"]),
    pokepasteUrl: "https://pokepast.es/0123456789abcdef",
    description: "Private detail not needed by War Room",
    owner: "owner",
    hasEvs: true,
    replicaCode: "CODE",
    sourceUrl: "https://example.com/source",
  };
  const savedPaste = {
    id: "private-1",
    name: "Practice rival",
    creator: "Local player",
    format: "Champions M-C",
    sourceUrl: "",
    sourceLabel: "Manual",
    notes: "",
    createdAt: "2026-09-13T09:00:00.000Z",
    updatedAt: "2026-09-13T09:30:00.000Z",
    pokemon: ["Charizard", "Rillaboom", "Incineroar", "Amoonguss", "Garchomp", "Gholdengo"],
  };
  const duplicatedPublicPaste = {
    ...savedPaste,
    id: "private-duplicate",
    sourceUrl: "https://pokepast.es/0123456789abcdef/",
  };
  const response = buildWarRoomCorpusResponse(VGCPASTES_FORMATS[0], [sourceTeam], "2026-09-13T10:00:00.000Z", [savedPaste, duplicatedPublicPaste]);

  assert.equal(response.regulation, "M-C");
  assert.equal(response.totalTeams, 2);
  assert.equal(response.publicTeamCount, 1);
  assert.equal(response.savedTeamCount, 1);
  assert.equal(response.teams[0].description, undefined);
  assert.equal(response.teams[0].owner, undefined);
  assert.equal(response.teams[1].source, "scouting-library");
  assert.equal(response.teams[1].savedPasteId, "private-1");
  assert.equal(isWarRoomCorpusResponse(response), true);

  const malformed = structuredClone(response);
  malformed.teams[0].pokemon.pop();
  assert.equal(isWarRoomCorpusResponse(malformed), false);
});

test("rejects non-current formats before contacting the War Room source", async () => {
  const { GET } = await vite.ssrLoadModule("/app/api/war-room/route.ts");
  const originalFetch = globalThis.fetch;
  let contacted = false;
  globalThis.fetch = async () => {
    contacted = true;
    throw new Error("should not fetch");
  };
  try {
    const response = await GET(new Request("http://localhost/api/war-room?format=champions-m-b"));
    assert.equal(response.status, 400);
    assert.equal(contacted, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("audits legality, frequent threats, recurring cores and structural gaps without emitting a win probability", async () => {
  const snapshot = await readSnapshot();
  const { auditTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const team = ownTeam();
  team[0].item = "Leftovers";
  team[1].item = "Leftovers";
  team[0].evs = "40 Spe / 32 SpA";
  const savedReference = corpusTeam("saved-local", ["Kyogre", "Tornadus", "Pelipper", "Flutter Mane", "Amoonguss", "Rillaboom"], { source: "scouting-library", savedPasteId: "local" });
  const result = auditTeam(team, [...corpus(), savedReference], snapshot, { teamFormat: "champions" });

  assert.equal(result.regulation, "M-C");
  assert.ok(result.legality.issues.some((issue) => issue.id === "item-clause-leftovers"));
  assert.ok(result.legality.issues.some((issue) => issue.id === "stats-set-1"));
  assert.ok(result.threats.some((threat) => threat.species === "Kyogre"));
  assert.ok(result.cores.some((core) => core.appearances >= 3));
  assert.ok(result.gaps.length > 0);
  assert.equal(result.summary.corpusTeams, 6, "saved references must not inflate public usage frequencies");
  assert.equal("winProbability" in result, false);
  assert.match(result.notes.join(" "), /no es una probabilidad de victoria/i);
});

test("prepares four picks, a two-Pokemon lead, backline and distinct alternate leads deterministically", async () => {
  const snapshot = await readSnapshot();
  const { prepareMatchup } = await vite.ssrLoadModule("/lib/war-room.ts");
  const rival = corpus()[0];
  const first = prepareMatchup(ownTeam(), rival, snapshot);
  const second = prepareMatchup(ownTeam(), rival, snapshot);

  assert.deepEqual(first, second);
  assert.equal(first.evidenceScope, "team-preview");
  assert.ok(first.recommended);
  assert.equal(first.recommended.lead.length, 2);
  assert.equal(first.recommended.backline.length, 2);
  assert.equal(new Set([...first.recommended.lead, ...first.recommended.backline]).size, 4);
  assert.equal(first.picks.length, 6);
  assert.ok(first.alternatives.length >= 1);
  assert.notDeepEqual(
    [...first.alternatives[0].lead].sort(),
    [...first.recommended.lead].sort(),
  );
  assert.match(first.notes.join(" "), /no estima la probabilidad de ganar/i);

  const exactRival = [
    set("Kyogre", ["Origin Pulse", "Ice Beam", "Thunder", "Protect"], 1),
    set("Tornadus", ["Bleakwind Storm", "Air Slash", "Taunt", "Tailwind"], 2),
    set("Pelipper", ["Weather Ball", "Hurricane", "Wide Guard", "Protect"], 3),
    set("Flutter Mane", ["Moonblast", "Shadow Ball", "Dazzling Gleam", "Protect"], 4),
    set("Amoonguss", ["Spore", "Rage Powder", "Pollen Puff", "Protect"], 5),
    set("Rillaboom", ["Grassy Glide", "Wood Hammer", "Fake Out", "U-turn"], 6),
  ];
  const exact = prepareMatchup(ownTeam(), rival, snapshot, exactRival);
  assert.equal(exact.evidenceScope, "exact-set");
  assert.ok(exact.picks.some((pick) => pick.damage.length > 0));
  assert.ok(exact.picks.some((pick) => pick.incomingDamage.length > 0));
  assert.match(exact.notes.join(" "), /campo neutral de dobles/i);
});

test("keeps locked identities, searches real partners and labels set packages as marginal composites", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam, warRoomMetaKey } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const charizardMoves = getLegalMoves(snapshot, "Charizard", "champions").slice(0, 4);
  const charizardAbility = getLegalAbilities(snapshot, "Charizard", "champions")[0];
  const meta = {
    pokemon: "Charizard",
    format: "Doubles",
    regulation: "M-C",
    season: "Current",
    retrievedAt: "2026-09-13T10:00:00.000Z",
    stale: false,
    methodology: "marginal-frequency-composite",
    source: { label: "Pokémon Champions Battle Data", url: "https://championsbattledata.com/" },
    presets: [{
      id: "charizard-meta-1",
      rank: 1,
      label: "Meta 1",
      item: "Sitrus Berry",
      ability: charizardAbility,
      nature: "Modest",
      evs: "2 HP / 32 SpA / 32 Spe",
      moves: charizardMoves,
      evidence: { item: 41, ability: 92, nature: 54, statPoints: 28, moves: [70, 62, 51, 49] },
    }],
  };
  const result = optimizeTeam(
    team,
    [team[0].id, team[1].id],
    corpus(),
    snapshot,
    { [warRoomMetaKey("Charizard")]: meta },
  );

  assert.deepEqual(result.lockedSpecies, ["Charizard", "Rillaboom"]);
  assert.equal(result.coreSample.mode, "exact");
  assert.ok(result.members.some((member) => member.species === "Pelipper"));
  assert.ok(result.members.every((member) => !result.lockedSpecies.includes(member.replaces)));
  assert.ok(result.sets.some((suggestion) => suggestion.species === "Charizard"));
  assert.ok(result.sets.every((suggestion) => suggestion.methodology === "marginal-frequency-composite"));
  assert.ok(result.sets.every((suggestion) => suggestion.proposal.moves.length === 4));
  assert.match(result.notes.join(" "), /no representan sets observados/i);
});

test("limits Mega partner cards according to the Megas already configured on the team", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const partnerCorpus = [
    corpusTeam("mega-a", ["Rillaboom", "Incineroar", "Salamence-Mega", "Golisopod-Mega", "Sneasler", "Pelipper"]),
    corpusTeam("mega-b", ["Rillaboom", "Incineroar", "Mawile-Mega", "Venusaur-Mega", "Basculegion", "Indeedee-F"]),
    corpusTeam("mega-c", ["Rillaboom", "Incineroar", "Metagross-Mega", "Blastoise-Mega", "Sneasler", "Basculegion"]),
    corpusTeam("mega-d", ["Rillaboom", "Incineroar", "Salamence-Mega", "Mawile-Mega", "Pelipper", "Basculegion"]),
  ];
  const locks = (team) => [team[1].id, team[2].id];

  const noMegas = ownTeam();
  const noMegaResult = optimizeTeam(noMegas, locks(noMegas), partnerCorpus, snapshot);
  assert.deepEqual(noMegaResult.megaPolicy, { configured: 0, maximum: 2, recommendationSlots: 2 });
  assert.ok(noMegaResult.members.filter((member) => member.isMega).length <= 2);

  const oneMega = ownTeam();
  oneMega[0].item = "Charizardite X";
  const oneMegaResult = optimizeTeam(oneMega, locks(oneMega), partnerCorpus, snapshot);
  assert.deepEqual(oneMegaResult.megaPolicy, { configured: 1, maximum: 2, recommendationSlots: 1 });
  assert.ok(oneMegaResult.members.filter((member) => member.isMega).length <= 1);

  const twoMegas = ownTeam();
  twoMegas[0].item = "Charizardite X";
  twoMegas[4].item = "Garchompite Z";
  const twoMegaResult = optimizeTeam(twoMegas, locks(twoMegas), partnerCorpus, snapshot);
  assert.deepEqual(twoMegaResult.megaPolicy, { configured: 2, maximum: 2, recommendationSlots: 0 });
  assert.ok(twoMegaResult.members.length > 0);
  assert.ok(twoMegaResult.members.every((member) => !member.isMega));
  assert.match(twoMegaResult.notes.join(" "), /limita las alternativas Mega a dos/i);
});

test("preserves individual fields and move slots while building a legal set proposal", async () => {
  const snapshot = await readSnapshot();
  const {
    createWarRoomPokemonLocks,
    optimizeTeam,
    warRoomMetaKey,
  } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves, isMoveLegal } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const legalMoves = getLegalMoves(snapshot, "Charizard", "champions");
  const legalAbilities = getLegalAbilities(snapshot, "Charizard", "champions");
  assert.ok(legalMoves.length >= 7);
  assert.ok(legalAbilities.length >= 1);

  team[0] = set("Charizard", legalMoves.slice(0, 4), 1, {
    item: "Leftovers",
    ability: legalAbilities[0],
    nature: "Jolly",
    evs: "32 HP / 2 Def / 32 Spe",
  });
  const presetMoves = [legalMoves[0], ...legalMoves.slice(4, 7)];
  const meta = {
    pokemon: "Charizard",
    format: "Doubles",
    regulation: "M-C",
    season: "Current",
    retrievedAt: "2026-09-13T10:00:00.000Z",
    stale: false,
    methodology: "marginal-frequency-composite",
    source: { label: "Pokémon Champions Battle Data", url: "https://championsbattledata.com/" },
    presets: [{
      id: "charizard-field-locks",
      rank: 1,
      label: "Meta con restricciones",
      item: "Sitrus Berry",
      ability: legalAbilities.at(-1),
      nature: "Modest",
      evs: "2 HP / 32 SpA / 32 Spe",
      moves: presetMoves,
      evidence: { item: 41, ability: 92, nature: 54, statPoints: 28, moves: [70, 62, 51, 49] },
    }],
  };
  const locks = createWarRoomPokemonLocks(true);
  locks.item = true;
  locks.ability = true;
  locks.nature = true;
  locks.statPoints = true;
  locks.moves = [true, false, false, true];

  const result = optimizeTeam(
    team,
    { [team[0].id]: locks },
    corpus(),
    snapshot,
    { [warRoomMetaKey("Charizard")]: meta },
  );
  const suggestion = result.sets.find((entry) => entry.setId === team[0].id);
  assert.ok(suggestion);
  assert.equal(suggestion.proposal.item, team[0].item);
  assert.equal(suggestion.proposal.ability, team[0].ability);
  assert.equal(suggestion.proposal.nature, team[0].nature);
  assert.equal(suggestion.proposal.evs, team[0].evs);
  assert.equal(suggestion.proposal.moves[0], team[0].moves[0].name);
  assert.equal(suggestion.proposal.moves[3], team[0].moves[3].name);
  assert.equal(new Set(suggestion.proposal.moves.map((move) => move.toLowerCase())).size, 4);
  assert.ok(suggestion.proposal.moves.every((move) => isMoveLegal(snapshot, "Charizard", move, "champions")));
  assert.ok(suggestion.changes.some((change) => change.key === "move-1"));
  assert.ok(suggestion.changes.some((change) => change.key === "move-2"));
  assert.ok(suggestion.changes.every((change) => !["item", "ability", "nature", "statPoints", "move-0", "move-3"].includes(change.key)));
  assert.deepEqual(
    suggestion.preservedFields.map((field) => field.key),
    ["identity", "item", "ability", "nature", "statPoints", "move-0", "move-3"],
  );
});

test("does not emit an empty proposal when every set field is locked", async () => {
  const snapshot = await readSnapshot();
  const {
    createWarRoomPokemonLocks,
    optimizeTeam,
    warRoomMetaKey,
  } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const legalMoves = getLegalMoves(snapshot, "Charizard", "champions").slice(0, 4);
  const ability = getLegalAbilities(snapshot, "Charizard", "champions")[0];
  team[0] = set("Charizard", legalMoves, 1, { item: "Leftovers", ability });
  const meta = {
    pokemon: "Charizard",
    format: "Doubles",
    regulation: "M-C",
    season: "Current",
    retrievedAt: "2026-09-13T10:00:00.000Z",
    stale: false,
    methodology: "marginal-frequency-composite",
    source: { label: "Pokémon Champions Battle Data", url: "https://championsbattledata.com/" },
    presets: [{
      id: "charizard-fully-locked",
      rank: 1,
      label: "Meta 1",
      item: "Sitrus Berry",
      ability,
      nature: "Modest",
      evs: "2 HP / 32 SpA / 32 Spe",
      moves: [...legalMoves].reverse(),
      evidence: { item: 41, ability: 92, nature: 54, statPoints: 28, moves: [70, 62, 51, 49] },
    }],
  };
  const locks = createWarRoomPokemonLocks(true);
  locks.item = true;
  locks.ability = true;
  locks.nature = true;
  locks.statPoints = true;
  locks.moves = [true, true, true, true];

  const result = optimizeTeam(
    team,
    { [team[0].id]: locks },
    corpus(),
    snapshot,
    { [warRoomMetaKey("Charizard")]: meta },
  );

  assert.equal(result.sets.some((entry) => entry.setId === team[0].id), false);
  const summary = result.locks.find((entry) => entry.setId === team[0].id);
  assert.equal(summary?.fullyLocked, true);
  assert.equal(summary?.fields.length, 9);
});

test("exposes War Room as a top-level dashboard section, separate from Scouting", async () => {
  const [dashboard, warRoom] = await Promise.all([
    readFile(new URL("../app/vgc-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/war-room.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(dashboard, /<TabsTrigger value="war-room"/);
  assert.match(dashboard, /<TabsContent value="war-room"/);
  assert.match(dashboard, /<WarRoom groups=\{storedGroups\}/);
  assert.match(dashboard, /onBuildDraft=\{importTournamentTeam\}/);
  assert.match(dashboard, /<ScoutingView /);
  assert.match(warRoom, /Auditar mi Team/);
  assert.match(warRoom, /Preparar un matchup/);
  assert.match(warRoom, /Optimizar o construir/);
  assert.match(warRoom, /Mis pastes/);
  assert.match(warRoom, /Bloqueos por Pokémon/);
  assert.match(warRoom, /Movimiento 4/);
  assert.match(warRoom, /Preservado por tus bloqueos/);
  assert.match(warRoom, /Probar este set en Builder/);
  assert.match(warRoom, /dos Megas por Team/);
  assert.match(warRoom, /member\.isMega/);
  assert.match(warRoom, /serializeShowdownPaste/);
});
