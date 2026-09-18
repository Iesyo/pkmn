"""Single model-aware boundary between Nana and the frozen VGC-Bench teacher.

Everything above this module should consume structured actions and stable
contract identities instead of importing ``action_map`` or relying on native
model indices as persistent keys.
"""

from __future__ import annotations

import hashlib
import inspect
import math
from pathlib import Path
from typing import Any, Callable, Protocol

from battle_lab.nana_contracts import FINGERPRINT_SPEC_VERSION, fingerprint_payload, order_key


ADAPTER_CONTRACT_VERSION = 1
ACTION_SPACE_SPEC_VERSION = 1
FEATURE_SCHEMA_SPEC_VERSION = 1
TEACHER_FAMILY = "vgc-bench-masked-actor-critic"
SELECTION_RULE = "sequential-greedy"
FAKE_RATING = 2000
CAPABILITIES = (
    "action-mask",
    "branch-probabilities",
    "joint-scores",
    "logprobs",
    "value-head",
)


class TeacherAdapter(Protocol):
    def inspect(self, battle: Any, *, include_joint_scores: bool = False) -> dict[str, Any]: ...

    def raw_choose(self, battle: Any) -> Any: ...

    def supports(self, capability: str) -> bool: ...


class VgcBenchMaskedActorCriticAdapter:
    """Adapter for the currently pinned VGC-Bench masked actor-critic policy."""

    def __init__(
        self,
        player: Any,
        *,
        raw_choose: Callable[[Any], Any] | None = None,
    ) -> None:
        self.player = player
        self._raw_choose = raw_choose

    def inspect(self, battle: Any, *, include_joint_scores: bool = False) -> dict[str, Any]:
        return _inspect_vgc_bench_decision(
            self.player,
            battle,
            include_joint_scores=include_joint_scores,
        )

    def raw_choose(self, battle: Any) -> Any:
        if self._raw_choose is None:
            raise RuntimeError("TeacherAdapter.raw_choose requires an injected frozen chooser.")
        return self._raw_choose(battle)

    def supports(self, capability: str) -> bool:
        return str(capability or "") in CAPABILITIES


def _as_float(value: Any) -> float:
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


def action_space_contract() -> dict[str, Any]:
    """Describe the native teacher catalog without making imports mandatory at boot."""

    try:
        from vgc_bench.src.policy import action_map

        actions = [str(value) for value in action_map]
        resolved = True
    except Exception:
        actions = []
        resolved = False
    return {
        "specVersion": ACTION_SPACE_SPEC_VERSION,
        "source": "vgc_bench.src.policy.action_map",
        "resolved": resolved,
        "actions": actions,
    }


def action_space_identity() -> dict[str, Any]:
    contract = action_space_contract()
    resolved = bool(contract.get("resolved"))
    return {
        "resolved": resolved,
        "id": (
            f"action-space:v{ACTION_SPACE_SPEC_VERSION}:{fingerprint_payload(contract)}"
            if resolved
            else ""
        ),
        "contract": contract,
    }


def action_space_id() -> str:
    return str(action_space_identity().get("id") or "")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feature_source_identity(service: Any | None) -> dict[str, Any]:
    runtime = getattr(service, "runtime", None) if service is not None else None
    player_class = getattr(runtime, "player_class", None)
    embed = getattr(player_class, "embed_battle", None)
    if embed is None:
        return {"resolved": False, "source": "PolicyPlayer.embed_battle"}

    try:
        signature = str(inspect.signature(embed))
    except (TypeError, ValueError):
        signature = ""
    try:
        source = inspect.getsource(embed)
    except (OSError, TypeError):
        source = ""
    try:
        source_file_raw = inspect.getsourcefile(embed) or inspect.getfile(embed)
        source_file = Path(source_file_raw).resolve() if source_file_raw else None
        module_sha256 = _file_sha256(source_file) if source_file and source_file.is_file() else ""
    except (OSError, TypeError):
        source_file = None
        module_sha256 = ""

    # The module hash intentionally covers helpers called by embed_battle too.
    # A readable implementation file is required before this identity is trusted.
    resolved = bool(module_sha256 and signature)
    return {
        "resolved": resolved,
        "module": str(getattr(embed, "__module__", "")),
        "qualname": str(getattr(embed, "__qualname__", "")),
        "signature": signature,
        "sourceFile": str(source_file) if source_file else "",
        "moduleFileSha256": module_sha256,
        "sourceSha256": fingerprint_payload({"source": source}) if source else "",
    }


def feature_schema_contract(service: Any | None = None) -> dict[str, Any]:
    return {
        "specVersion": FEATURE_SCHEMA_SPEC_VERSION,
        "source": "PolicyPlayer.embed_battle",
        "fakeRating": FAKE_RATING,
        "implementation": _feature_source_identity(service),
    }


def feature_schema_identity(service: Any | None = None) -> dict[str, Any]:
    contract = feature_schema_contract(service)
    resolved = bool((contract.get("implementation") or {}).get("resolved"))
    return {
        "resolved": resolved,
        "id": (
            f"feature-schema:v{FEATURE_SCHEMA_SPEC_VERSION}:{fingerprint_payload(contract)}"
            if resolved
            else ""
        ),
        "contract": contract,
    }


def feature_schema_id(service: Any | None = None) -> str:
    return str(feature_schema_identity(service).get("id") or "")


def teacher_behavior_contract(
    *,
    service: Any | None,
    battle_format: str,
    checkpoint_sha256: str,
) -> dict[str, Any]:
    action_identity = action_space_identity()
    feature_identity = feature_schema_identity(service)
    checksum = str(checkpoint_sha256 or "")
    resolved = bool(
        checksum
        and checksum != "unknown"
        and action_identity["resolved"]
        and feature_identity["resolved"]
    )
    return {
        "fingerprintSpecVersion": FINGERPRINT_SPEC_VERSION,
        "resolved": resolved,
        "family": TEACHER_FAMILY,
        "format": str(battle_format),
        "checkpointSha256": checksum or "unknown",
        "actionSpaceId": action_identity["id"],
        "featureSchemaId": feature_identity["id"],
        "adapterContractVersion": ADAPTER_CONTRACT_VERSION,
        "selectionRule": SELECTION_RULE,
        "inferenceParams": {
            "fakeRating": FAKE_RATING,
            "deterministic": True,
        },
        "resolution": {
            "actionSpace": bool(action_identity["resolved"]),
            "featureSchema": bool(feature_identity["resolved"]),
            "checkpoint": bool(checksum and checksum != "unknown"),
        },
    }


def teacher_behavior_key(contract: dict[str, Any]) -> str:
    if not isinstance(contract, dict) or contract.get("resolved") is not True:
        return ""
    return f"teacher-behavior:v1:{fingerprint_payload(contract)}"


def structured_action(battle: Any, indices: list[int]) -> dict[str, Any]:
    """Translate native teacher indices into Nana's model-agnostic action shape."""

    import numpy as np
    from poke_env.environment import DoublesEnv
    # Deliberately lazy: a module-level import would recreate adapter ↔ sparring cycle.
    from battle_lab import local_sparring_service as sparring

    order = DoublesEnv.action_to_order(np.asarray(indices, dtype=np.int64), battle)
    return {
        "first": sparring._single_order_payload(order.first_order),
        "second": sparring._single_order_payload(order.second_order),
    }


def structured_order_key(battle: Any, indices: list[int]) -> str:
    return order_key(structured_action(battle, indices))


def _inspect_vgc_bench_decision(
    player: Any,
    battle: Any,
    *,
    include_joint_scores: bool = False,
) -> dict[str, Any]:
    """Inspect the frozen teacher while preserving its sequential-greedy semantics."""

    import numpy as np
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
    observation = player.embed_battle(battle, fake_rating=FAKE_RATING)
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
            "selectionRule": SELECTION_RULE,
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


def inspect_light_decision(
    player: Any,
    battle: Any,
    *,
    include_joint_scores: bool = False,
) -> dict[str, Any]:
    """Compatibility function used by existing Nana runtimes."""

    return VgcBenchMaskedActorCriticAdapter(player).inspect(
        battle,
        include_joint_scores=include_joint_scores,
    )
