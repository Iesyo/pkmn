import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("NanaScorer ranks teacher-independent candidates in common board-delta space", () => {
  const script = String.raw`
from battle_lab.nana_scorer import (
    CandidateEvidence, NanaScorer, board_delta_term, map_to_board_delta
)

scorer=NanaScorer(blind_uncertainty_penalty=0.10, blind_margin=0.25)
teacher=CandidateEvidence(
    key='teacher',
    terms=(board_delta_term(name='experience',value=0.10,confidence=1.0),),
    teacher_represented=True,
    context_evidence=True,
)
independent=CandidateEvidence(
    key='independent',
    terms=(board_delta_term(name='experience',value=0.40,confidence=1.0),),
    teacher_represented=False,
    context_evidence=True,
)
ranked=scorer.rank([teacher,independent])
assert ranked[0].key == 'independent'
assert ranked[0].blind is False

mapped=CandidateEvidence(
    key='mapped',
    terms=(
        map_to_board_delta(
            name='counter',
            source_value=0.8,
            confidence=1.0,
            source_space='response-utility-v1',
            mapper_id='linear-test-v1',
            mapper=lambda value:value*0.5,
        ),
    ),
    teacher_represented=False,
    context_evidence=True,
)
assert scorer.score(mapped).score == 0.4
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("teacher reference only breaks close common-score ties and never filters candidates", () => {
  const script = String.raw`
from battle_lab.nana_scorer import CandidateEvidence, NanaScorer, board_delta_term

scorer=NanaScorer(min_improvement_margin=0.05)
reference=CandidateEvidence(
    key='teacher',
    terms=(board_delta_term(name='experience',value=0.20,confidence=1.0),),
    teacher_represented=True,
    context_evidence=True,
)
close=CandidateEvidence(
    key='other-close',
    terms=(board_delta_term(name='experience',value=0.23,confidence=1.0),),
    teacher_represented=False,
    context_evidence=True,
)
r=scorer.select([reference,close],reference_key='teacher')
assert r['selected']['orderKey'] == 'teacher'
assert r['reason'] == 'common-margin-not-met'

clear=CandidateEvidence(
    key='other-clear',
    terms=(board_delta_term(name='experience',value=0.40,confidence=1.0),),
    teacher_represented=False,
    context_evidence=True,
)
r=scorer.select([reference,clear],reference_key='teacher')
assert r['selected']['orderKey'] == 'other-clear'
assert r['reason'] == 'best-common-score'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("blind picks need explicit margin over an evidenced reference", () => {
  const script = String.raw`
from battle_lab.nana_scorer import CandidateEvidence, NanaScorer, board_delta_term

scorer=NanaScorer(blind_uncertainty_penalty=0.10, blind_margin=0.25)
reference=CandidateEvidence(
    key='reference',
    terms=(board_delta_term(name='experience',value=0.20,confidence=1.0),),
    teacher_represented=True,
    context_evidence=True,
)
blind_close=CandidateEvidence(
    key='blind-close',
    terms=(board_delta_term(name='heuristicFloor',value=0.45,confidence=1.0),),
    teacher_represented=False,
    context_evidence=False,
)
r=scorer.select([reference,blind_close],reference_key='reference')
assert r['reason'] == 'blind-margin-not-met'
assert r['selected']['orderKey'] == 'reference'

blind_far=CandidateEvidence(
    key='blind-far',
    terms=(board_delta_term(name='heuristicFloor',value=0.70,confidence=1.0),),
    teacher_represented=False,
    context_evidence=False,
)
r=scorer.select([reference,blind_far],reference_key='reference')
assert r['reason'] == 'blind-margin-met'
assert r['selected']['orderKey'] == 'blind-far'

only_blind=CandidateEvidence(
    key='only-blind',
    terms=(board_delta_term(name='heuristicFloor',value=1.0,confidence=1.0),),
    teacher_represented=False,
    context_evidence=False,
)
r=scorer.select([only_blind])
assert r['selected'] is None
assert r['reason'] == 'blind-without-evidenced-reference'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("unmapped cross-space evidence remains impossible to combine", () => {
  const script = String.raw`
from battle_lab.nana_scorer import CandidateEvidence, NanaScorer, _CommonScoreTerm

# Forged/private terms cannot pass the proof token used by combine_common_terms.
fake=_CommonScoreTerm(
    name='counter', value=1.0, confidence=1.0, weight=1.0,
    source_space='response-utility-v1', mapper_id='forged', _proof=object()
)
try:
    NanaScorer().score(CandidateEvidence(
        key='x',terms=(fake,),teacher_represented=False,context_evidence=True
    ))
except ValueError:
    pass
else:
    raise AssertionError('raw cross-space evidence must stay blocked')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
