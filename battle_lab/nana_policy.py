"""Read-only LIGHT policy introspection for Nana.

This module exposes the scores already produced by the frozen VGC-Bench policy.
It deliberately does not override ``PolicyPlayer.choose_move`` and never mutates
checkpoint weights. Nana can therefore observe LIGHT before it is allowed to
influence any battle decision.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _as_float(value: Any) -> float:
    """Convert a scalar tensor/number into a JSON-safe float."""

    item = value.item() if hasattr(value, "item") else value
    return float(item)


def _legal_branch_scores(
    *,
    labels: list[str],
    logits: Any,
    probabilities: Any,
    legal_mask: Any,
    selected_index: int,
) -> list[dict[str, Any]]:
    """Serialize one action branch while omitting masked-out actions."""

    scores: list[dict[str, Any]] = []
    for index, legal in enumerate(legal_mask):
        if not bool(legal):
            continue
        probability = _as_float(probabilities[index])
        scores.append(
            {
                "index": index,
                "label": labels[index],
                "logit": _as_float(logits[index]),
                "probability": probability,
                "logProbability": math.log(max(probability, 1e-45)),
                "selected": index == selected_index,
            }
        )
    scores.sort(key=lambda candidate: candidate["probability"], reverse=True)
    return scores


def inspect_light_decision(
    player: Any,
    battle: Any,
    *,
    include_joint_scores: bool = False,
) -> dict[str, Any]:
    """Inspect LIGHT's deterministic decision without changing it.

    VGC-Bench models a doubles order as two categorical branches. The second
    branch is re-masked after the first branch is selected, so its probabilities
    are conditional on that first action. The canonical LIGHT decision is the
    same sequential greedy decision used by ``MaskedActorCriticPolicy.forward``.

    ``jointScores`` is optional because globally sorting P(a1) * P(a2 | a1) is a
    diagnostic view of the factorized policy, not the policy's canonical
    sequential-argmax selection rule. Nana must not silently replace one rule
    with the other.
    """

    from poke_env.environment import DoublesEnv
    from vgc_bench.src.policy import action_map

    policy = player.policy
    if policy is None:
        raise RuntimeError("LIGHT policy is not loaded.")
    if getattr(battle, "_wait", False):
        return {
            "waiting": True,
            "selectionRule": "showdown-wait",
            "canonicalAction": None,
            "value": None,
            "branches": [],
            "jointScores": [],
        }

    torch = __import__("torch")
    observation = player.embed_battle(battle, fake_rating=2000)
    action_mask = np.asarray(DoublesEnv.get_action_mask(battle))
    if action_mask.size != len(action_map) * 2:
        raise RuntimeError(
            "LIGHT action mask has an unexpected size: "
            f"{action_mask.size}; expected {len(action_map) * 2}."
        )

    with torch.no_grad():
        obs_dict = {
            "observation": torch.as_tensor(
                observation, device=policy.device
            ).unsqueeze(0),
            "action_mask": torch.as_tensor(
                action_mask, device=policy.device
            ).unsqueeze(0),
        }
        action_logits, value_logits = policy.get_logits(obs_dict, actor_grad=False)

        first_distribution = policy.get_dist_from_logits(
            action_logits, obs_dict["action_mask"]
        )
        first_sample = first_distribution.get_actions(deterministic=True)
        first_index = int(first_sample[0, 0].item())

        conditional_distribution = policy.get_dist_from_logits(
            action_logits,
            obs_dict["action_mask"],
            first_sample[:, :1],
        )
        second_sample = conditional_distribution.get_actions(deterministic=True)
        second_index = int(second_sample[0, 1].item())

        canonical_action = np.array([first_index, second_index], dtype=np.int64)
        canonical_order = DoublesEnv.action_to_order(canonical_action, battle)

        branch_size = len(action_map)
        logits = action_logits[0].reshape(2, branch_size)
        mask = obs_dict["action_mask"][0].reshape(2, branch_size)
        conditional_mask = policy._update_mask(
            obs_dict["action_mask"], first_sample[:, :1]
        )[0].reshape(2, branch_size)

        first_probabilities = first_distribution.distribution[0].probs[0]
        second_probabilities = conditional_distribution.distribution[1].probs[0]

        branches = [
            {
                "slot": 1,
                "conditionedOn": None,
                "selectedIndex": first_index,
                "selectedLabel": action_map[first_index],
                "scores": _legal_branch_scores(
                    labels=action_map,
                    logits=logits[0],
                    probabilities=first_probabilities,
                    legal_mask=mask[0],
                    selected_index=first_index,
                ),
            },
            {
                "slot": 2,
                "conditionedOn": first_index,
                "selectedIndex": second_index,
                "selectedLabel": action_map[second_index],
                "scores": _legal_branch_scores(
                    labels=action_map,
                    logits=logits[1],
                    probabilities=second_probabilities,
                    legal_mask=conditional_mask[1],
                    selected_index=second_index,
                ),
            },
        ]

        snapshot: dict[str, Any] = {
            "waiting": False,
            "selectionRule": "sequential-greedy",
            "branch2ConditionedOnFirst": True,
            "canonicalAction": {
                "indices": [first_index, second_index],
                "labels": [action_map[first_index], action_map[second_index]],
                "order": str(canonical_order),
            },
            "value": _as_float(value_logits.reshape(-1)[0]),
            "branches": branches,
            "jointScores": [],
        }

        if include_joint_scores:
            joint_scores: list[dict[str, Any]] = []
            first_legal = [
                candidate
                for candidate in branches[0]["scores"]
                if candidate["probability"] > 0
            ]
            for first_candidate in first_legal:
                candidate_first = int(first_candidate["index"])
                first_tensor = torch.as_tensor(
                    [[candidate_first]],
                    device=policy.device,
                    dtype=first_sample.dtype,
                )
                candidate_distribution = policy.get_dist_from_logits(
                    action_logits,
                    obs_dict["action_mask"],
                    first_tensor,
                )
                candidate_mask = policy._update_mask(
                    obs_dict["action_mask"], first_tensor
                )[0].reshape(2, branch_size)
                second_probs = candidate_distribution.distribution[1].probs[0]
                for candidate_second, legal in enumerate(candidate_mask[1]):
                    if not bool(legal):
                        continue
                    second_probability = _as_float(second_probs[candidate_second])
                    if second_probability <= 0:
                        continue
                    first_probability = float(first_candidate["probability"])
                    probability = first_probability * second_probability
                    action = np.array(
                        [candidate_first, candidate_second], dtype=np.int64
                    )
                    order = DoublesEnv.action_to_order(action, battle)
                    joint_scores.append(
                        {
                            "indices": [candidate_first, candidate_second],
                            "labels": [
                                action_map[candidate_first],
                                action_map[candidate_second],
                            ],
                            "probability": probability,
                            "logProbability": math.log(max(probability, 1e-45)),
                            "selectedByLight": (
                                candidate_first == first_index
                                and candidate_second == second_index
                            ),
                            "order": str(order),
                        }
                    )
            joint_scores.sort(
                key=lambda candidate: candidate["probability"], reverse=True
            )
            snapshot["jointScores"] = joint_scores

        return snapshot
