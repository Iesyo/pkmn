import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";
const teamMemory = path.join(root, "battle_lab", "nana_team_memory.py");
const nurseryRuntime = path.join(root, "battle_lab", "nana_stage2_nursery_runtime.py");

test("TeamMemory uses most-specific ready scope and backs off without double counting", () => {
  const script = String.raw`
from battle_lab.nana_team_memory import team_trust_for, blend_self_with_team

ctx={
 'resolved':True,
 'rosterSignature':'roster:v1:r1',
 'exactTeamSignature':'team:v2:t1',
}
summary={'buckets':{
 'global:global':{'samples':8,'trust':0.60,'confidence':0.50},
 'roster:roster:v1:r1':{'samples':4,'trust':0.70,'confidence':0.40},
 'exactTeam:team:v2:t1':{'samples':2,'trust':0.90,'confidence':0.30},
}}
first=team_trust_for(summary,ctx)
assert first['eligible'] is True
assert first['selectedScope'] == 'roster'
assert first['trust'] == 0.70
summary['buckets']['exactTeam:team:v2:t1']['samples']=3
second=team_trust_for(summary,ctx)
assert second['selectedScope'] == 'exactTeam'
assert second['trust'] == 0.90
blended=blend_self_with_team({'trust':0.50,'confidence':0.20}, second)
assert blended['trust'] == 0.50
assert blended['confidence'] == 0.20
assert blended['teamMemoryBlend'] == 0.0
assert blended['teamMemoryEffect'] == 'no-relaxation'
negative={**second,'trust':0.10,'confidence':0.50}
cautious=blend_self_with_team({'trust':0.50,'confidence':0.20}, negative)
assert 0.10 < cautious['trust'] < 0.50
assert cautious['teamMemoryBlend'] > 0.0
assert cautious['teamMemoryEffect'] == 'added-caution'
cold=team_trust_for({'buckets':{}},ctx)
unchanged=blend_self_with_team({'trust':0.42,'confidence':0.11},cold)
assert unchanged['trust'] == 0.42
assert unchanged['confidence'] == 0.11
assert unchanged['teamMemoryBlend'] == 0.0

from battle_lab.nana_nursery import choose_candidate
plan={
 'eligible':True,'confidence':1.0,'confidenceScale':1.0,
 'canonical':{'expectedCounter':0.0},
 'candidatePool':[{
   'selectedByLight':False,'lightRegretLog':-0.01,
   'expectedCounter':1.0,'probability':0.5,'action':{},'indices':[0,0]
 }],
}
light={'trust':0.5,'confidence':0.0}
raw={'trust':0.30,'confidence':0.40}
assert choose_candidate(plan,light_trust=light,self_trust=raw)['reason'] == 'nana-self-low-trust-veto'
positive_team={'eligible':True,'trust':0.90,'confidence':1.0,'selectedScope':'exactTeam'}
safe=blend_self_with_team(raw,positive_team)
assert safe['trust'] == raw['trust']
assert choose_candidate(plan,light_trust=light,self_trust=safe)['reason'] == 'nana-self-low-trust-veto'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("TeamMemory ignores legacy team:v1 sessions and rebuilds from real Nana outcomes", () => {
  const script = String.raw`
from battle_lab.nana_team_memory import build_team_memory

def state(turn, own_hp=100, opp_hp=100):
    return {
      'tag':'battle-x','turn':turn,'finished':False,'won':False,'lost':False,
      'weather':[],'fields':[],
      'ownActive':[{'species':'A','hp':own_hp},{'species':'B','hp':own_hp}],
      'opponentActive':[{'species':'C','hp':opp_hp},{'species':'D','hp':opp_hp}],
      'ownTeam':[{'species':'A','hp':own_hp,'fainted':False},{'species':'B','hp':own_hp,'fainted':False}],
      'opponentTeam':[{'species':'C','hp':opp_hp,'fainted':False},{'species':'D','hp':opp_hp,'fainted':False}],
    }

def session(sid, exact, start_index):
    roster='roster:v1:r1'
    teacher={'key':'teacher-1'}
    action={'first':{'kind':'move','value':'protect','target':0,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
    light={'canonicalAction':action,'branches':[]}
    return [
      {'timestamp':f'2026-01-01T00:00:{start_index:02d}Z','sessionId':sid,'type':'session_start','payload':{'context':{'teamIdentity':{'resolved':True,'rosterSignature':roster,'exactTeamSignature':exact}}}},
      {'timestamp':f'2026-01-01T00:00:{start_index+1:02d}Z','sessionId':sid,'type':'nana_teacher_version','payload':{'teacher':teacher}},
      {'timestamp':f'2026-01-01T00:00:{start_index+2:02d}Z','sessionId':sid,'type':'turn_choice','payload':{'turn':1,'generation':1,'state':state(1),'modelAction':action,'modelActor':'nana','teacher':teacher,'light':light}},
      {'timestamp':f'2026-01-01T00:00:{start_index+3:02d}Z','sessionId':sid,'type':'human_choice_observed','payload':{'turn':2,'generation':2,'state':state(2,50,100)}},
    ]

events=[]
events += session('v2a','team:v2:t1',0)
events += session('v2b','team:v2:t1',10)
events += session('v2c','team:v2:t1',20)
events += session('legacy','team:v1:old',30)
summary=build_team_memory(events)
assert summary['observations'] == 3
assert summary['ignoredLegacyTeamSessions'] == 1
exact=summary['buckets']['exactTeam:team:v2:t1']
assert exact['samples'] == 3
assert exact['positive'] == 3
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nursery wires TeamMemory into N2 but keeps scorer/common autonomy untouched", () => {
  const source = readFileSync(nurseryRuntime, "utf8");
  assert.match(source, /memory_contract=team_memory_contract\(\)/);
  assert.match(source, /team_trust_for\(/);
  assert.match(source, /blend_self_with_team\(/);
  assert.match(source, /rebuild_team_memory_for_recorder\(/);
  assert.match(source, /choose_candidate\(/);
  assert.doesNotMatch(source, /combine_common_terms\(/);
});

test("TeamMemory module compiles", () => {
  execFileSync(python, ["-m", "py_compile", teamMemory], { cwd: root, encoding: "utf8" });
});
