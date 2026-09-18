import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import url from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("Nana telemetry report aggregates recent BO1 bottlenecks and lambda thresholds", () => {
  const script = String.raw`
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_telemetry_report import build_report

with TemporaryDirectory() as tmp:
    profile=Path(tmp)
    sessions=profile/'sessions'
    sessions.mkdir()
    for i,(bottleneck,required,intervened) in enumerate([
        ('legacy',None,False),
        ('branch',None,False),
        ('counter',None,False),
        ('cap',0.18,False),
        ('ready',0.12,True),
    ]):
        sid=f's{i}'
        funnel={'jointTotal':10,'poolAlternatives':5,'nurseryRegretPassed':3,'counterImproved':2,'insideCap':1}
        if bottleneck=='branch':
            funnel.update(poolAlternatives=0,nurseryRegretPassed=0,counterImproved=0,insideCap=0)
        elif bottleneck=='counter':
            funnel.update(nurseryRegretPassed=3,counterImproved=0,insideCap=0)
        elif bottleneck=='cap':
            funnel.update(counterImproved=2,insideCap=0)
        selection={'reason':'nursery-live-near-light' if intervened else 'no-live-candidate-inside-nursery-cap','requiredLambdaCap':required,'lambdaGap':None if required is None else max(0,required-0.15)}
        if bottleneck != 'legacy':
            selection['candidateFunnel']=funnel
        events=[
            {'timestamp':f'2026-01-0{i+1}T00:00:00Z','sessionId':sid,'type':'session_start','payload':{'context':{'teamIdentity':{'exactTeamSignature':'team:v2:x'}}}},
            {'timestamp':f'2026-01-0{i+1}T00:00:01Z','sessionId':sid,'type':'nana_nursery_decision','payload':{'intervened':intervened,'selection':selection}},
            {'timestamp':f'2026-01-0{i+1}T00:00:02Z','sessionId':sid,'type':'session_end','payload':{'result':{'winner':'human'}}},
        ]
        (sessions/f'{sid}.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
    r=build_report(profile,last=10)
    assert r['sessions']==5
    assert r['decisions']==5
    assert r['interventions']==1
    assert r['instrumentedDecisions']==4
    assert r['legacyDecisions']==1
    assert r['bottlenecks']['telemetry-missing']==1
    assert r['bottlenecks']['branch-filter']==1
    assert r['bottlenecks']['counter-proxy']==1
    assert r['bottlenecks']['lambda-cap']==1
    assert r['bottlenecks']['ready']==1
    assert r['requiredLambda']['samples']==2
    assert r['requiredLambda']['thresholds']['<=0.20']==2
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
