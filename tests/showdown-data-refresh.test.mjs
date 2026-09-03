import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildShowdownSnapshot,
  parseEs3Export,
  parseTeambuilderSource,
} from "../lib/showdown-snapshot-builder.mjs";

const clientUrl = new URL("../lib/showdown-data.ts", import.meta.url);
const storageUrl = new URL("../db/showdown-snapshot.ts", import.meta.url);
const routeUrl = new URL("../app/api/showdown-data/route.ts", import.meta.url);
const scriptUrl = new URL("../scripts/update-showdown-data.mjs", import.meta.url);

test("parses Showdown generated JS without eval or vm", () => {
  const items = parseEs3Export(
    'exports.BattleItems = {choiceband:{name:"Choice Band",desc:"Boosts, foo: stays text"},"return":{name:"Return"},x:{flag:true}};',
    "BattleItems",
  );
  assert.equal(items.choiceband.name, "Choice Band");
  assert.equal(items.choiceband.desc, "Boosts, foo: stays text");
  assert.equal(items.return.name, "Return");
  assert.equal(items.x.flag, true);

  const abilities = parseEs3Export(
    'exports.BattleAbilities = {\n  static: {name:"Static", shortDesc:"May paralyze on contact."},\n  lightningrod: {name:"Lightning Rod", rating:3},\n};',
    "BattleAbilities",
  );
  assert.equal(abilities.static.name, "Static");
  assert.equal(abilities.static.shortDesc, "May paralyze on contact.");
  assert.equal(abilities.lightningrod.rating, 3);

  const table = {
    champions: { tiers: ["pikachu"], learnsets: { pikachu: { thunderbolt: "a" } }, items: ["lightball"] },
    gen9vgc: { tiers: ["pikachu"] },
    gen8vgc: { tiers: ["pikachu"] },
    gen7vgc: { tiers: ["pikachu"] },
    gen6vgc: { tiers: ["pikachu"] },
    items: ["lightball"],
    gen8: { items: ["lightball"] },
    gen7: { items: ["lightball"] },
    gen6: { items: ["lightball"] },
    learnsets: { pikachu: { thunderbolt: "a" } },
  };
  const encoded = JSON.stringify(table).replace(/['\\]/g, "\\$&");
  const source = `// DO NOT EDIT - automatically built with build-tools/build-indexes\nexports.BattleTeambuilderTable = JSON.parse('${encoded}');\n`;
  assert.deepEqual(parseTeambuilderSource(source).champions.tiers, ["pikachu"]);
});

test("builds a normalized rich snapshot from fetched Showdown sources", async () => {
  const table = {
    champions: {
      tiers: ["pikachu"],
      learnsets: { pikachu: { thunderbolt: "a" } },
      items: ["lightball"],
      overrideMoveData: { thunderbolt: { type: "Electric", basePower: 95 } },
    },
    gen9vgc: { tiers: ["pikachu"] },
    gen8vgc: { tiers: ["pikachu"] },
    gen7vgc: { tiers: ["pikachu"] },
    gen6vgc: { tiers: ["pikachu"] },
    items: ["lightball"],
    gen8: { items: ["lightball"] },
    gen7: { items: ["lightball"] },
    gen6: { items: ["lightball"] },
    learnsets: { pikachu: { thunderbolt: "6789" } },
  };
  const tableEncoded = JSON.stringify(table).replace(/['\\]/g, "\\$&");
  const responses = new Map([
    ["pokedex.json", JSON.stringify({ pikachu: { name: "Pikachu", types: ["Electric"], baseStats: { hp: 35, atk: 55, def: 40, spa: 50, spd: 50, spe: 90 }, abilities: { 0: "Static" } } })],
    ["moves.json", JSON.stringify({ thunderbolt: {
      name: "Thunderbolt",
      type: "Electric",
      category: "Special",
      basePower: 90,
      accuracy: 100,
      pp: 15,
      priority: 0,
      target: "normal",
      flags: { protect: 1, mirror: 1 },
      secondary: { chance: 10, status: "par" },
      desc: "Has a 10% chance to paralyze the target.",
      shortDesc: "10% chance to paralyze the target.",
    } })],
    ["abilities.js", 'exports.BattleAbilities = {static:{name:"Static",desc:"Contact may paralyze the attacker.",shortDesc:"Contact may paralyze the attacker.",rating:2,num:9}};'],
    ["items.js", 'exports.BattleItems = {lightball:{name:"Light Ball",desc:"Doubles Pikachu attacking stats.",shortDesc:"Doubles Pikachu attacking stats."}};'],
    ["teambuilder-tables.js", `// DO NOT EDIT - automatically built with build-tools/build-indexes\nexports.BattleTeambuilderTable = JSON.parse('${tableEncoded}');\n`],
  ]);
  const fetcher = async (url) => {
    const key = [...responses.keys()].find((suffix) => url.endsWith(suffix));
    return key ? new Response(responses.get(key), { status: 200 }) : new Response("missing", { status: 404 });
  };

  const snapshot = await buildShowdownSnapshot(fetcher);
  assert.equal(snapshot.metadata.schema, 3);
  assert.equal(snapshot.species.pikachu.name, "Pikachu");
  assert.deepEqual(snapshot.species.pikachu.championsMoves, ["thunderbolt"]);
  assert.equal(snapshot.moves.thunderbolt.type, "Electric");
  assert.equal(snapshot.moves.thunderbolt.basePower, 95);
  assert.equal(snapshot.moves.thunderbolt.accuracy, 100);
  assert.equal(snapshot.moves.thunderbolt.pp, 15);
  assert.equal(snapshot.moves.thunderbolt.priority, 0);
  assert.deepEqual(snapshot.moves.thunderbolt.flags, ["mirror", "protect"]);
  assert.deepEqual(snapshot.moves.thunderbolt.effects.secondary, { chance: 10, status: "par" });
  assert.equal(snapshot.moves.thunderbolt.shortDesc, "10% chance to paralyze the target.");
  assert.equal(snapshot.moves.thunderbolt.championsOverride.basePower, 95);
  assert.equal(snapshot.abilities.static.name, "Static");
  assert.equal(snapshot.abilities.static.shortDesc, "Contact may paralyze the attacker.");
  assert.equal(snapshot.abilities.static.rating, 2);
  assert.equal(snapshot.items.lightball.name, "Light Ball");
  assert.equal(snapshot.items.lightball.shortDesc, "Doubles Pikachu attacking stats.");
  assert.deepEqual(snapshot.formats.champions, ["pikachu"]);
});

test("wires runtime refresh through the server and persists compressed rich data in D1", async () => {
  const [client, storage, route, script] = await Promise.all([
    readFile(clientUrl, "utf8"),
    readFile(storageUrl, "utf8"),
    readFile(routeUrl, "utf8"),
    readFile(scriptUrl, "utf8"),
  ]);

  assert.ok(client.includes('fetch("/api/showdown-data"'));
  assert.ok(client.includes('method: "POST"'));
  assert.ok(client.includes('fetch("/data/showdown-dex.json.gz?schema=3"'));
  assert.ok(client.includes("abilities: snapshot.abilities ?? {}"));
  assert.ok(client.includes("getMoveData"));
  assert.ok(client.includes("getAbilityData"));
  assert.ok(storage.includes("showdown_snapshot_gzip_base64_v1"));
  assert.ok(storage.includes('new CompressionStream("gzip")'));
  assert.ok(storage.includes("app_settings"));
  assert.ok(route.includes("buildShowdownSnapshot"));
  assert.ok(route.includes("saveStoredShowdownSnapshot"));
  assert.ok(script.includes('from "../lib/showdown-snapshot-builder.mjs"'));
  assert.ok(!script.includes('node:vm'));
});
