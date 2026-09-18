import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const speedTier = path.join(root, "battle_lab", "sparring_speed_tier.py");
const launcher = path.join(root, "battle_lab", "nana_stage2_nursery_speed_tier_lan_runtime.py");
const viewer = path.join(root, "battle_lab", "sparring_speed_viewer.py");

test("Speed Tier applies priority brackets, Tailwind, boosts, paralysis and Trick Room", () => {
  const script = String.raw`
from battle_lab.sparring_speed_tier import build_speed_tier_snapshot

class EnumValue:
    def __init__(self, name):
        self.name = name

class Move:
    def __init__(self, ident, name, priority=0, category="PHYSICAL", move_type="NORMAL", heal=0, drain=0, flags=()):
        self.id = ident
        self.entry = {
            "name": name,
            "priority": priority,
            "category": category,
            "type": move_type,
            "flags": {flag: 1 for flag in flags},
        }
        self.category = EnumValue(category)
        self.type = EnumValue(move_type)
        self.heal = heal
        self.drain = drain
        self.flags = set(flags)

    @property
    def priority(self):
        return self.entry["priority"]

class Mon:
    def __init__(self, species, speed, *, boost=0, status=None, item="", ability="", moves=(), base=100):
        self.species = species
        self.base_species = species
        self.name = species
        self.stats = {"spe": speed}
        self.base_stats = {"spe": base}
        self.level = 50
        self.boosts = {"spe": boost}
        self.status = EnumValue(status) if status else None
        self.item = item
        self.ability = ability
        self.moves = {move.id: move for move in moves}
        self.effects = {}
        self.current_hp_fraction = 1.0
        self.fainted = False

class Battle:
    gen = 9
    turn = 3
    weather = {}
    opponent_side_conditions = {}

battle = Battle()
battle.fields = {}
battle.side_conditions = {EnumValue("TAILWIND"): 2}
battle.active_pokemon = [
    Mon("Rillaboom", 100, boost=1, moves=(Move("protect", "Protect", 4, "STATUS"),)),
]
battle.opponent_active_pokemon = [
    Mon("Incineroar", 150, status="PAR", moves=(Move("fakeout", "Fake Out", 3),)),
]

normal = build_speed_tier_snapshot(battle)
assert normal["ownTailwind"] is True
assert normal["ownTailwindTurns"] == 3
assert normal["order"][0]["species"] == "Rillaboom"
assert normal["order"][0]["effectiveSpeed"] == 300
assert normal["order"][1]["effectiveSpeed"] == 83
assert [entry["priority"] for entry in normal["priority"][:2]] == [4, 3]

battle.fields = {EnumValue("TRICK_ROOM"): 2}
battle.side_conditions = {}
trick = build_speed_tier_snapshot(battle)
assert trick["trickRoom"] is True
assert trick["trickRoomTurns"] == 4
assert [entry["species"] for entry in trick["order"]] == ["Incineroar", "Rillaboom"]
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Speed Tier derives conditional priority from live Showdown move data", () => {
  const script = String.raw`
from battle_lab.sparring_speed_tier import build_speed_tier_snapshot

class EnumValue:
    def __init__(self, name):
        self.name = name

class Move:
    def __init__(self, ident, name, priority=0, category="PHYSICAL", move_type="NORMAL", heal=0, drain=0, flags=()):
        self.id = ident
        self.entry = {"name": name, "priority": priority, "category": category, "type": move_type, "flags": {flag: 1 for flag in flags}}
        self.category = EnumValue(category)
        self.type = EnumValue(move_type)
        self.heal = heal
        self.drain = drain
        self.flags = set(flags)
    @property
    def priority(self):
        return self.entry["priority"]

class Mon:
    def __init__(self, species, ability, moves):
        self.species = species
        self.base_species = species
        self.name = species
        self.stats = {"spe": 100}
        self.base_stats = {"spe": 100}
        self.level = 50
        self.boosts = {"spe": 0}
        self.status = None
        self.item = ""
        self.ability = ability
        self.moves = {move.id: move for move in moves}
        self.effects = {}
        self.current_hp_fraction = 1.0
        self.fainted = False

class Battle:
    gen = 9
    turn = 2
    weather = {}
    side_conditions = {}
    opponent_side_conditions = {}
    fields = {EnumValue("GRASSY_TERRAIN"): 1}

battle = Battle()
battle.active_pokemon = [
    Mon("Whimsicott", "prankster", [Move("tailwind", "Tailwind", category="STATUS", move_type="FLYING")]),
    Mon("Rillaboom", "", [Move("grassyglide", "Grassy Glide", move_type="GRASS")]),
]
battle.opponent_active_pokemon = [
    Mon("Comfey", "triage", [Move("drainingkiss", "Draining Kiss", move_type="FAIRY", drain=0.75)]),
]
snapshot = build_speed_tier_snapshot(battle)
priorities = {(entry["species"], entry["label"]): entry["priority"] for entry in snapshot["priority"]}
assert priorities[("Whimsicott", "Tailwind")] == 1
assert priorities[("Rillaboom", "Grassy Glide")] == 1
assert priorities[("Comfey", "Draining Kiss")] == 3
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Champions fallback uses Stat Points formula and marks fractional-order uncertainty", () => {
  const script = String.raw`
from battle_lab.sparring_speed_tier import _champions_raw_speed, _parse_paste, build_speed_tier_snapshot

paste = """Rillaboom @ Quick Claw
Ability: Grassy Surge
Level: 50
EVs: 4 HP / 28 Atk / 32 Spe
Adamant Nature
- Fake Out
- Grassy Glide
- Wood Hammer
- U-turn
"""
entry = _parse_paste(paste)["rillaboom"]

class Mon:
    species = "rillaboom"
    base_species = "rillaboom"
    name = "Rillaboom"
    stats = {"spe": None}
    base_stats = {"spe": 85}
    level = 50
    boosts = {"spe": 0}
    status = None
    item = "quickclaw"
    ability = "grassysurge"
    moves = {}
    effects = {}
    current_hp_fraction = 1.0
    fainted = False

mon = Mon()
assert _champions_raw_speed(mon, entry) == 137

class Battle:
    gen = 9
    turn = 1
    fields = {}
    weather = {}
    side_conditions = {}
    opponent_side_conditions = {}
    active_pokemon = [mon]
    opponent_active_pokemon = []

snapshot = build_speed_tier_snapshot(Battle(), own_paste=paste)
assert snapshot["order"][0]["rawSpeed"] == 137
assert any("quickclaw" in note for note in snapshot["order"][0]["uncertainty"])
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("opponent Speed uses the maximum legal Champions stat instead of a hidden floor", () => {
  const script = String.raw`
from battle_lab.sparring_speed_tier import build_speed_tier_snapshot

class Mon:
    def __init__(self, species, speed, item=""):
        self.species = species
        self.base_species = species
        self.name = species
        self.stats = {"spe": speed}
        self.base_stats = {"spe": 85}
        self.level = 50
        self.boosts = {"spe": 0}
        self.status = None
        self.item = item
        self.ability = ""
        self.moves = {}
        self.effects = {}
        self.current_hp_fraction = 1.0
        self.fainted = False

class Battle:
    gen = 9
    turn = 1
    fields = {}
    weather = {}
    side_conditions = {}
    opponent_side_conditions = {}
    active_pokemon = [Mon("Rillaboom", 90)]
    opponent_active_pokemon = [Mon("Rillaboom", 70, "choicescarf")]

snapshot = build_speed_tier_snapshot(Battle())
own = next(row for row in snapshot["order"] if row["side"] == "own")
opponent = next(row for row in snapshot["order"] if row["side"] == "opponent")
assert own["rawSpeed"] == 90
assert opponent["rawSpeed"] == 150
assert opponent["effectiveSpeed"] == 225
assert "Rival: Speed máxima posible" in opponent["modifiers"]
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("opponent exact paste keeps unrevealed priority threats", () => {
  const script = String.raw`
import battle_lab.sparring_speed_tier as speed

class EnumValue:
    def __init__(self, name): self.name = name

class FakeMove:
    def __init__(self, ident, name, priority):
        self.id = ident
        self.priority = priority
        self.entry = {"name": name, "priority": priority, "category": "Physical", "type": "Normal", "flags": {}}
        self.category = EnumValue("PHYSICAL")
        self.type = EnumValue("NORMAL")
        self.heal = 0
        self.drain = 0
        self.flags = set()

speed._fallback_moves = lambda entry, gen: [FakeMove("fakeout", "Fake Out", 3)]

class RevealedMove(FakeMove):
    def __init__(self): super().__init__("woodhammer", "Wood Hammer", 0)

class Mon:
    species = "rillaboom"
    base_species = "rillaboom"
    name = "Rillaboom"
    stats = {"spe": None}
    base_stats = {"spe": 85}
    level = 50
    boosts = {"spe": 0}
    status = None
    item = "unknown_item"
    ability = None
    moves = {"woodhammer": RevealedMove()}
    effects = {}
    current_hp_fraction = 1.0
    fainted = False

class Battle:
    gen = 9
    turn = 1
    fields = {}
    weather = {}
    side_conditions = {}
    opponent_side_conditions = {}
    active_pokemon = []
    opponent_active_pokemon = [Mon()]

paste = """Rillaboom @ Assault Vest
Ability: Grassy Surge
Level: 50
EVs: 32 Spe
Adamant Nature
- Fake Out
- Grassy Glide
- Wood Hammer
- U-turn
"""
snapshot = speed.build_speed_tier_snapshot(Battle(), opponent_paste=paste)
labels = {entry["label"]: entry["priority"] for entry in snapshot["priority"]}
assert labels["Fake Out"] == 3
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("feature launcher layers Speed Tier after native Showdown controls", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /from battle_lab\.sparring_speed_tier import install_speed_tier_snapshot/);
  assert.match(source, /service_class = original_install\(\)[\s\S]*install_speed_tier_snapshot\(\)/);
  assert.match(source, /local_runtime\.start_viewer_server = _start_speed_viewer/);
});

test("viewer renders Speed Tier in recovered left-side space", () => {
  const source = readFileSync(viewer, "utf8");
  assert.match(source, /id="speed-tier"/);
  assert.match(source, /function renderSpeedTier/);
  assert.match(source, /ORDEN DE TURNO/);
  assert.match(source, /Prioridad/);
  assert.match(source, /Trick Room/);
  assert.match(source, /Tailwind/);
  assert.match(source, /singlePanelMode/);
});

test("Speed Tier Python modules compile", () => {
  for (const filename of [speedTier, launcher, viewer]) {
    execFileSync(python, ["-m", "py_compile", filename], { cwd: root, encoding: "utf8" });
  }
});
