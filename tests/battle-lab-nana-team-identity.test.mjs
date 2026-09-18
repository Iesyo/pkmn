import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";
const runtime = path.join(root, "battle_lab", "nana_runtime.py");
const teamIdentity = path.join(root, "battle_lab", "nana_team_identity.py");

const teamA = "Garchomp @ Garchompite\nAbility: Rough Skin\nLevel: 50\nTera Type: Ground\nEVs: 4 HP / 252 Atk / 252 Spe\nJolly Nature\n- Earthquake\n- Dragon Claw\n- Protect\n- Swords Dance\n\nIncineroar @ Safety Goggles\nAbility: Intimidate\nLevel: 50\nTera Type: Grass\nEVs: 252 HP / 4 Atk / 252 SpD\nCareful Nature\n- Fake Out\n- Parting Shot\n- Flare Blitz\n- Protect\n\nRillaboom @ Assault Vest\nAbility: Grassy Surge\nLevel: 50\nTera Type: Fire\nEVs: 252 HP / 252 Atk / 4 SpD\nAdamant Nature\n- Fake Out\n- Grassy Glide\n- Wood Hammer\n- U-turn\n\nFlutter Mane @ Booster Energy\nAbility: Protosynthesis\nLevel: 50\nTera Type: Fairy\nEVs: 4 HP / 252 SpA / 252 Spe\nTimid Nature\n- Moonblast\n- Shadow Ball\n- Icy Wind\n- Protect\n\nAmoonguss @ Sitrus Berry\nAbility: Regenerator\nLevel: 50\nTera Type: Water\nEVs: 252 HP / 156 Def / 100 SpD\nCalm Nature\n- Spore\n- Rage Powder\n- Pollen Puff\n- Protect\n\nUrshifu-Rapid-Strike @ Focus Sash\nAbility: Unseen Fist\nLevel: 50\nTera Type: Water\nEVs: 4 HP / 252 Atk / 252 Spe\nJolly Nature\n- Surging Strikes\n- Close Combat\n- Aqua Jet\n- Protect\n";

test("M2 Team identity keeps roster memory across set edits and ignores move ordering", () => {
  const script = String.raw`
from battle_lab.nana_team_identity import team_identity
team=${JSON.stringify(teamA)}
a=team_identity(team)
reordered=team.replace("- Earthquake\n- Dragon Claw\n- Protect\n- Swords Dance", "- Protect\n- Swords Dance\n- Dragon Claw\n- Earthquake")
b=team_identity(reordered)
changed=team.replace("EVs: 4 HP / 252 Atk / 252 Spe", "EVs: 12 HP / 244 Atk / 252 Spe", 1)
c=team_identity(changed)
renamed=team.replace("Garchomp @ Garchompite", "Chompy (Garchomp) @ Garchompite", 1)
d=team_identity(renamed)
assert a['rosterSignature'] == b['rosterSignature'] == c['rosterSignature'] == d['rosterSignature']
assert a['exactTeamSignature'] == b['exactTeamSignature'] == d['exactTeamSignature']
assert a['exactTeamSignature'] != c['exactTeamSignature']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("M2 persists one canonical Team record and session context only stores signatures", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_team_identity import team_identity, persist_team_identity, session_team_context, team_scope_keys
team=${JSON.stringify(teamA)}
identity=team_identity(team)
with TemporaryDirectory() as tmp:
    first=persist_team_identity(Path(tmp),identity)
    second=persist_team_identity(Path(tmp),identity)
    assert first == second and first.is_file()
    data=first.read_text(encoding='utf-8')
    assert 'Garchomp' in data
context=session_team_context(identity)
assert 'normalizedPaste' not in context and 'canonicalTeam' not in context
assert context['rosterSignature'].startswith('roster:v1:')
assert context['exactTeamSignature'].startswith('team:v1:')
keys=team_scope_keys(roster_signature=context['rosterSignature'],exact_team_signature=context['exactTeamSignature'],opponent_archetype='balance')
assert keys[0] == 'global'
assert any(value.startswith('archetype:') for value in keys)
assert any(value.startswith('roster:') for value in keys)
assert any(value.startswith('team:') for value in keys)
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana runtime records M2 identity but keeps it non-blocking", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /team_identity\(session\.own_team\)/);
  assert.match(source, /persist_team_identity\(self\.nana\.profile_root, identity\)/);
  assert.match(source, /"teamIdentity": team_context/);
  assert.match(source, /"fallback": "L1-only"/);
});

test("M2 modules compile", () => {
  for (const file of [teamIdentity, path.join(root, "battle_lab", "mc_team_split.py")]) {
    execFileSync(python, ["-m", "py_compile", file], { cwd: root, encoding: "utf8" });
  }
});
