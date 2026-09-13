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

function pasteEvidenceTeam(id, sets, overrides = {}) {
  return {
    id,
    source: "vgcpastes",
    savedPasteId: "",
    playerName: `Paste ${id}`,
    tournament: "Context Cup",
    rank: "Top 8",
    pokepasteUrl: `https://pokepast.es/${id.toLowerCase().replace(/[^a-z0-9]/g, "").padEnd(8, "0")}`,
    pokemon: sets.map((pokemon) => pokemon.species),
    sourceTier: "curated",
    sourceLabel: "VGCPastes",
    sourceUrl: `https://pokepast.es/${id.toLowerCase().replace(/[^a-z0-9]/g, "").padEnd(8, "0")}`,
    quality: 82,
    sets,
    ...overrides,
  };
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
  assert.equal(response.vgcPastesTeamCount, 1);
  assert.equal(response.tournamentTeamCount, 0);
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

test("gives a current-regulation tournament paste precedence over a duplicated VGCPastes row", async () => {
  const { buildWarRoomCorpusResponse } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { VGCPASTES_FORMATS } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const url = "https://pokepast.es/abcdef1234567890";
  const publicTeam = {
    ...corpusTeam("PUBLIC", ["Charizard", "Rillaboom", "Incineroar", "Amoonguss", "Garchomp", "Gholdengo"]),
    pokepasteUrl: url,
  };
  const tournamentSnapshot = {
    regulation: "M-C",
    generatedAt: "2026-09-13T10:00:00.000Z",
    tournaments: [{
      id: "event-1",
      name: "Finals",
      teams: [{
        id: "team-1",
        playerName: "Champion",
        placement: 1,
        record: "10-1",
        pokemon: [...publicTeam.pokemon],
        pokepasteUrl: url,
      }],
    }],
  };

  const result = buildWarRoomCorpusResponse(VGCPASTES_FORMATS[0], [publicTeam], "2026-09-13T10:00:00.000Z", [], tournamentSnapshot);
  assert.equal(result.totalTeams, 1);
  assert.equal(result.tournamentTeamCount, 1);
  assert.equal(result.vgcPastesTeamCount, 0);
  assert.equal(result.teams[0].source, "tournament");
  assert.match(result.teams[0].rank, /#1/);
});

test("selects a bounded diverse paste batch with exact-core and source priority", async () => {
  const { selectWarRoomPasteEvidenceCandidates } = await vite.ssrLoadModule("/lib/war-room-paste-evidence.ts");
  const candidates = [
    corpusTeam("generic", ["Charizard", "Kyogre", "Tornadus", "Amoonguss", "Incineroar", "Rillaboom"], { pokepasteUrl: "https://pokepast.es/1111111111111111" }),
    corpusTeam("tournament", ["Charizard", "Rillaboom", "Garchomp", "Amoonguss", "Incineroar", "Pelipper"], { source: "tournament", rank: "#1", pokepasteUrl: "https://pokepast.es/2222222222222222" }),
    corpusTeam("curated", ["Charizard", "Rillaboom", "Garchomp", "Amoonguss", "Incineroar", "Pelipper"], { pokepasteUrl: "https://pokepast.es/3333333333333333" }),
  ];
  const selected = selectWarRoomPasteEvidenceCandidates(
    ["Charizard", "Rillaboom", "Garchomp"],
    ["Charizard", "Rillaboom"],
    ["Pelipper"],
    candidates,
    2,
  );

  assert.equal(selected.length, 2);
  assert.equal(selected[0].id, "tournament");
  assert.ok(selected.every((team) => team.pokemon.includes("Pelipper")));
});

test("loads and validates only the requested full Pokepastes", async () => {
  const { POST } = await vite.ssrLoadModule("/app/api/war-room/paste-evidence/route.ts");
  const paste = ["Charizard", "Rillaboom", "Incineroar", "Amoonguss", "Garchomp", "Gholdengo"]
    .map((species) => `${species} @ Sitrus Berry\nAbility: Blaze\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n- Protect`)
    .join("\n\n");
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return new Response(paste, { headers: { "content-type": "text/plain" } });
  };
  try {
    const candidate = corpusTeam("evidence-route", ["Charizard", "Rillaboom", "Incineroar", "Amoonguss", "Garchomp", "Gholdengo"], {
      source: "tournament",
      rank: "#1",
      pokepasteUrl: "https://pokepast.es/4444444444444444",
    });
    const response = await POST(new Request("http://localhost/api/war-room/paste-evidence", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ candidates: [candidate] }),
    }));
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(requestedUrl, "https://pokepast.es/4444444444444444/raw");
    assert.equal(payload.loaded, 1);
    assert.equal(payload.failed, 0);
    assert.equal(payload.teams[0].sets.length, 6);
    assert.equal(payload.teams[0].sourceTier, "tournament");
    assert.equal(payload.teams[0].quality, 100);
  } finally {
    globalThis.fetch = originalFetch;
  }
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

test("keeps locked identities, searches real partners and labels Battle Data as fallback", async () => {
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
  assert.ok(result.sets.every((suggestion) => suggestion.methodology === "battle-data-fallback"));
  assert.ok(result.sets.every((suggestion) => suggestion.proposal.moves.length === 4));
  assert.match(result.notes.join(" "), /fallback marginal/i);
});

test("prefers a complete observed paste set over a Battle Data composite", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam, warRoomMetaKey } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const legalMoves = getLegalMoves(snapshot, "Charizard", "champions");
  const ability = getLegalAbilities(snapshot, "Charizard", "champions")[0];
  assert.ok(legalMoves.length >= 8);
  team[0] = set("Charizard", legalMoves.slice(0, 4), 1, {
    item: "Leftovers",
    ability,
    nature: "Jolly",
    evs: "2 HP / 32 SpA / 32 Spe",
  });
  const observed = set("Charizard", legalMoves.slice(4, 8), 1, {
    item: "Sitrus Berry",
    ability,
    nature: "Modest",
    evs: "32 HP / 32 SpA / 2 SpD",
  });
  const evidence = pasteEvidenceTeam("complete-observed", [observed, ...team.slice(1)]);
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
      id: "charizard-marginal",
      rank: 1,
      label: "Marginal",
      item: "Focus Sash",
      ability,
      nature: "Timid",
      evs: "2 HP / 32 SpA / 32 Spe",
      moves: legalMoves.slice(0, 4),
      evidence: { item: 70, ability: 80, nature: 60, statPoints: 50, moves: [80, 70, 60, 50] },
    }],
  };

  const result = optimizeTeam(team, [], corpus(), snapshot, { [warRoomMetaKey("Charizard")]: meta }, { pasteEvidence: [evidence] });
  const suggestion = result.sets.find((entry) => entry.setId === team[0].id);
  assert.ok(suggestion);
  assert.equal(suggestion.methodology, "observed-paste");
  assert.equal(suggestion.proposal.item, "Sitrus Berry");
  assert.deepEqual(suggestion.proposal.moves, legalMoves.slice(4, 8));
  assert.equal(suggestion.source.teamId, evidence.id);
  assert.ok(suggestion.changes.every((change) => change.source === "paste"));
});

test("uses Battle Data only to patch fields absent from an observed paste", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam, warRoomMetaKey } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const moves = getLegalMoves(snapshot, "Charizard", "champions").slice(0, 4);
  const ability = getLegalAbilities(snapshot, "Charizard", "champions")[0];
  team[0] = set("Charizard", moves, 1, { item: "Leftovers", ability, nature: "Jolly", evs: "2 HP / 32 SpA / 32 Spe" });
  const observed = set("Charizard", [...moves].reverse(), 1, { item: "Sitrus Berry", ability, nature: "Modest", evs: "" });
  const evidence = pasteEvidenceTeam("missing-spread", [observed, ...team.slice(1)]);
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
      id: "spread-patch",
      rank: 1,
      label: "Spread patch",
      item: "Focus Sash",
      ability,
      nature: "Timid",
      evs: "32 HP / 32 SpA / 2 SpD",
      moves,
      evidence: { item: 70, ability: 80, nature: 60, statPoints: 44, moves: [80, 70, 60, 50] },
    }],
  };

  const result = optimizeTeam(team, [], corpus(), snapshot, { [warRoomMetaKey("Charizard")]: meta }, { pasteEvidence: [evidence] });
  const suggestion = result.sets.find((entry) => entry.setId === team[0].id);
  assert.ok(suggestion);
  assert.equal(suggestion.methodology, "observed-paste-patched");
  assert.equal(suggestion.proposal.item, "Sitrus Berry", "an observed field must not be replaced by the marginal item");
  assert.equal(suggestion.proposal.evs, "32 HP / 32 SpA / 2 SpD");
  assert.deepEqual(suggestion.patchedFields, ["statPoints"]);
  assert.equal(suggestion.changes.find((change) => change.key === "statPoints").source, "battle-data");
  assert.equal(suggestion.changes.find((change) => change.key === "item").source, "paste");
});

test("rejects a fast Trick Room setter even when its paste has higher source quality", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const legalMoves = getLegalMoves(snapshot, "Gardevoir", "champions");
  const wanted = ["Dazzling Gleam", "Psychic", "Trick Room", "Protect"];
  assert.ok(wanted.every((move) => legalMoves.includes(move)));
  const ability = getLegalAbilities(snapshot, "Gardevoir", "champions")[0];
  team[0] = set("Gardevoir", wanted, 1, { item: "Leftovers", ability, nature: "Modest", evs: "32 HP / 32 SpA / 2 SpD" });
  const fast = set("Gardevoir", wanted, 1, { item: "Focus Sash", ability, nature: "Timid", evs: "2 HP / 32 SpA / 32 Spe" });
  const slow = set("Gardevoir", wanted, 1, { item: "Sitrus Berry", ability, nature: "Quiet", evs: "32 HP / 32 SpA / 2 SpD" });
  const fastEvidence = pasteEvidenceTeam("fast-tr", [fast, ...team.slice(1)], { source: "tournament", sourceTier: "tournament", sourceLabel: "Champion", quality: 100 });
  const slowEvidence = pasteEvidenceTeam("slow-tr", [slow, ...team.slice(1)], { sourceTier: "collection", sourceLabel: "Colección", quality: 65 });

  const result = optimizeTeam(team, [], corpus(), snapshot, {}, { pasteEvidence: [fastEvidence, slowEvidence] });
  const suggestion = result.sets.find((entry) => entry.setId === team[0].id);
  assert.ok(suggestion);
  assert.equal(suggestion.source.teamId, "slow-tr");
  assert.equal(suggestion.proposal.nature, "Quiet");
  assert.doesNotMatch(suggestion.proposal.evs, /Spe/);
});

test("rejects Grassy Seed without a terrain activator when applying a partner", async () => {
  const snapshot = await readSnapshot();
  const { buildWarRoomMemberReplacement } = await vite.ssrLoadModule("/lib/war-room.ts");
  const team = ownTeam();
  const sneaslerMoves = ["Close Combat", "Dire Claw", "Protect", "Fake Out"];
  const bad = set("Sneasler", sneaslerMoves, 1, { item: "Grassy Seed", ability: "Unburden", nature: "Jolly", evs: "2 HP / 32 Atk / 32 Spe" });
  const good = set("Sneasler", sneaslerMoves, 1, { item: "Focus Sash", ability: "Unburden", nature: "Jolly", evs: "2 HP / 32 Atk / 32 Spe" });
  const badEvidence = pasteEvidenceTeam("seed-no-terrain", [bad, ...team.slice(1)], { source: "tournament", sourceTier: "tournament", quality: 100 });
  const goodEvidence = pasteEvidenceTeam("sash-viable", [good, ...team.slice(1)], { sourceTier: "collection", quality: 65 });
  const suggestion = {
    species: "Sneasler",
    observedAs: "Sneasler",
    isMega: false,
    score: 80,
    appearancesWithCore: 2,
    sampleSize: 2,
    usageRate: 100,
    replaces: team[0].species,
    replacesSetId: team[0].id,
    patchedTypes: [],
    evidenceMode: "core",
    reasons: [],
  };

  const result = buildWarRoomMemberReplacement(team, suggestion, snapshot, [], [badEvidence, goodEvidence]);
  const replacement = result.pokemon.find((pokemon) => pokemon.id === suggestion.replacesSetId);
  assert.equal(result.setSource, "observed-paste");
  assert.equal(result.evidenceTeamId, "sash-viable");
  assert.equal(replacement.item, "Focus Sash");
  assert.notEqual(replacement.item, "Grassy Seed");
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

test("caps identity locks at five even when a caller submits all six", async () => {
  const snapshot = await readSnapshot();
  const { createWarRoomPokemonLocks, MAX_WAR_ROOM_LOCKED_IDENTITIES, optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const team = ownTeam();
  const requestedLocks = Object.fromEntries(team.map((pokemon) => [pokemon.id, createWarRoomPokemonLocks(true)]));
  const result = optimizeTeam(team, requestedLocks, corpus(), snapshot);

  assert.equal(MAX_WAR_ROOM_LOCKED_IDENTITIES, 5);
  assert.deepEqual(result.lockedSpecies, team.slice(0, 5).map((pokemon) => pokemon.species));
  assert.equal(result.locks[5].fields.some((field) => field.key === "identity"), false);
});

test("offers up to twelve partners and can consume the previous recommendation batch", async () => {
  const snapshot = await readSnapshot();
  const { MAX_WAR_ROOM_MEMBER_SUGGESTIONS, optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const team = ownTeam();
  const candidates = [
    "Abomasnow", "Absol", "Aegislash", "Aerodactyl", "Aggron",
    "Alakazam", "Alcremie", "Altaria", "Ampharos", "Annihilape",
    "Appletun", "Araquanid", "Arbok", "Arboliva", "Arcanine",
  ];
  const partnerCorpus = Array.from({ length: 3 }, (_, index) => corpusTeam(
    `large-${index}`,
    ["Rillaboom", ...candidates.slice(index * 5, index * 5 + 5)],
  ));
  const first = optimizeTeam(team, [team[1].id], partnerCorpus, snapshot);

  assert.equal(MAX_WAR_ROOM_MEMBER_SUGGESTIONS, 12);
  assert.equal(first.members.length, 12);
  assert.ok(first.members.every((member) => member.replacesSetId));

  const previousBatch = first.members.map((member) => member.species);
  const next = optimizeTeam(team, [team[1].id], partnerCorpus, snapshot, {}, { excludedMemberSpecies: previousBatch });
  assert.equal(next.members.length, 3);
  assert.ok(next.members.every((member) => !previousBatch.includes(member.species)));
});

test("expands beyond an exhausted exact-core sample without repeating earlier batches", async () => {
  const snapshot = await readSnapshot();
  const { optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const team = ownTeam();
  const current = new Set(team.map((pokemon) => pokemon.species.toLowerCase()));
  const candidates = [...new Set(snapshot.formats.champions
    .map((id) => snapshot.species[id]?.name)
    .filter((name) => name && !name.includes("-Mega") && !current.has(name.toLowerCase())))]
    .slice(0, 36);
  assert.equal(candidates.length, 36);
  const partnerCorpus = [
    corpusTeam("exact-small", ["Rillaboom", ...candidates.slice(0, 5)]),
    ...Array.from({ length: 5 }, (_, index) => corpusTeam(
      `expanded-${index}`,
      candidates.slice(5 + index * 6, 11 + index * 6),
    )),
  ];

  const first = optimizeTeam(team, [team[1].id], partnerCorpus, snapshot);
  assert.equal(first.members.length, 12);
  assert.ok(first.members.some((member) => member.evidenceMode === "core"));
  assert.ok(first.members.some((member) => member.evidenceMode === "expanded"));

  const previousBatch = first.members.map((member) => member.species);
  const second = optimizeTeam(team, [team[1].id], partnerCorpus, snapshot, {}, { excludedMemberSpecies: previousBatch });
  assert.equal(second.members.length, 12);
  assert.ok(second.members.every((member) => member.evidenceMode === "expanded"));
  assert.ok(second.members.every((member) => !previousBatch.includes(member.species)));
});

test("applies a partner to its recommended slot while preserving slot identity and Mega intent", async () => {
  const snapshot = await readSnapshot();
  const { applyWarRoomMemberSuggestion, buildWarRoomMemberReplacement, optimizeTeam } = await vite.ssrLoadModule("/lib/war-room.ts");
  const { getLegalAbilities, getLegalMoves, getMoveData, isItemLegal, isMoveLegal } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const team = ownTeam();
  const partnerCorpus = [
    corpusTeam("partners-a", ["Rillaboom", "Sneasler", "Basculegion", "Pelipper", "Farigiraf", "Salamence-Mega"]),
    corpusTeam("partners-b", ["Rillaboom", "Sneasler", "Basculegion", "Dragonite", "Indeedee-F", "Salamence-Mega"]),
  ];
  const result = optimizeTeam(team, [team[1].id], partnerCorpus, snapshot);
  const suggestion = result.members.find((member) => !member.isMega);
  assert.ok(suggestion);
  const previous = team.find((pokemon) => pokemon.id === suggestion.replacesSetId);
  const changed = applyWarRoomMemberSuggestion(team, suggestion, snapshot);
  const replacement = changed.find((pokemon) => pokemon.id === suggestion.replacesSetId);

  assert.ok(previous);
  assert.ok(replacement);
  assert.equal(replacement.id, previous.id);
  assert.equal(replacement.slot, previous.slot);
  assert.equal(replacement.species, suggestion.species);
  assert.equal(replacement.moves.length, 4);
  assert.ok(replacement.item);
  assert.ok(replacement.ability);
  assert.ok(replacement.nature);
  assert.ok(replacement.evs);
  assert.ok(replacement.moves.every((move) => move.name));
  assert.equal(isItemLegal(snapshot, replacement.item, "champions"), true);
  assert.ok(replacement.moves.every((move) => isMoveLegal(snapshot, replacement.species, move.name, "champions")));
  assert.ok(replacement.moves.filter((move) => getMoveData(snapshot, move.name, "champions")?.category !== "Status").length >= 2);
  assert.notEqual(changed, team);
  assert.equal(team.find((pokemon) => pokemon.id === suggestion.replacesSetId)?.species, previous.species);

  const recalculated = optimizeTeam(changed, [team[1].id], partnerCorpus, snapshot);
  assert.equal(recalculated.members.some((member) => member.species === suggestion.species), false);

  const metaMoves = getLegalMoves(snapshot, suggestion.species, "champions").slice(0, 4);
  const metaAbility = getLegalAbilities(snapshot, suggestion.species, "champions")[0];
  const metaApplication = buildWarRoomMemberReplacement(team, suggestion, snapshot, [{
    id: "meta-1",
    rank: 1,
    label: "Meta popular",
    item: "Leftovers",
    ability: metaAbility,
    nature: "Jolly",
    evs: "2 HP / 32 Atk / 32 Spe",
    moves: metaMoves,
    evidence: { item: 50, ability: 50, nature: 50, statPoints: 50, moves: [50, 50, 50, 50] },
  }]);
  assert.equal(metaApplication.setSource, "battle-data-fallback");
  assert.equal(metaApplication.presetId, "meta-1");
  assert.deepEqual(metaApplication.pokemon.find((pokemon) => pokemon.id === suggestion.replacesSetId).moves.map((move) => move.name), metaMoves);

  const megaSuggestion = {
    species: "Salamence",
    observedAs: "Salamence-Mega",
    isMega: true,
    score: 80,
    appearancesWithCore: 2,
    sampleSize: 2,
    usageRate: 100,
    replaces: team[5].species,
    replacesSetId: team[5].id,
    patchedTypes: [],
    evidenceMode: "core",
    reasons: [],
  };
  const megaTeam = applyWarRoomMemberSuggestion(team, megaSuggestion, snapshot);
  assert.equal(megaTeam[5].species, "Salamence");
  assert.equal(megaTeam[5].item, "Salamencite");
  assert.equal(optimizeTeam(megaTeam, [team[1].id], partnerCorpus, snapshot).megaPolicy.configured, 1);
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
  const [dashboard, warRoom, builder] = await Promise.all([
    readFile(new URL("../app/vgc-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/war-room.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(dashboard, /<TabsTrigger value="war-room"/);
  assert.match(dashboard, /<TabsContent value="war-room"/);
  assert.match(dashboard, /<WarRoom key=\{warRoomTeam/);
  assert.match(dashboard, /groups=\{storedGroups\} initialTeam=\{warRoomTeam\?\.team\}/);
  assert.match(dashboard, /onBuildDraft=\{importTournamentTeam\}/);
  assert.match(dashboard, /function openInWarRoom\(team: TeamVersion\)/);
  assert.match(dashboard, /Enviar a War Room/);
  assert.match(dashboard, /onOpenWarRoom=\{openInWarRoom\}/);
  assert.match(builder, /function openWarRoom\(\)/);
  assert.match(builder, /onOpenWarRoom\(\{/);
  assert.match(builder, /Enviar a War Room/);
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
  assert.match(warRoom, /MAX_WAR_ROOM_LOCKED_IDENTITIES/);
  assert.match(warRoom, /MAX_WAR_ROOM_MEMBER_SUGGESTIONS/);
  assert.match(warRoom, /Elegir y recalcular/);
  assert.match(warRoom, /buildWarRoomMemberReplacement/);
  assert.match(warRoom, /api\/opponent-meta/);
  assert.match(warRoom, /api\/war-room\/paste-evidence/);
  assert.match(warRoom, /Busca primero sets completos en pastes comparables/);
  assert.match(warRoom, /Battle Data solo rellena campos ausentes/);
  assert.match(warRoom, /Paste observado/);
  assert.match(warRoom, /Armando set viable/);
  assert.match(warRoom, /Corpus ampliado/);
  assert.match(warRoom, /function undoMember\(setId: string\)/);
  assert.match(warRoom, /Deshacer cambio de/);
  assert.match(warRoom, /excludedMemberSpecies: optimization\.members\.map/);
  assert.match(warRoom, /serializeShowdownPaste/);
});
