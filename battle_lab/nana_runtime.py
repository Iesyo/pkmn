"""Drop-in Battle Lab local runtime with Nana 0 observation enabled.

The underlying LIGHT M-C player remains the canonical decision maker. Nana only
records human/model choices and LIGHT diagnostics; it does not rerank actions.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import os
from typing import Any, Sequence

from battle_lab import local_sparring_service as sparring
from battle_lab.nana_policy import inspect_light_decision
from battle_lab.nana_recorder import NanaRecorder


DEFAULT_NANA_PROFILE = "default"


def _preview_order(value: Any) -> list[int]:
    text = str(value or "")
    if text.startswith("/team "):
        text = text[6:]
    return [int(character) for character in text if character.isdigit()]


def install_nana_service(*, profile_id: str) -> type:
    """Replace the local service class with an observational Nana subclass."""

    base_service_class = sparring.BattleLabLocalService
    if getattr(base_service_class, "_nana_service", False):
        base_service_class.nana_profile_id = profile_id
        return base_service_class

    class NanaBattleLabLocalService(base_service_class):
        _nana_service = True
        nana_profile_id = profile_id

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.nana = NanaRecorder(
                self.runtime_root / "nana",
                profile_id=self.nana_profile_id,
            )
            self._nana_pending: dict[str, dict[int, dict[str, Any]]] = {}
            self._nana_finished: set[str] = set()
            self._nana_player_wrapped = False

        async def ensure_ready(self) -> None:
            await super().ensure_ready()
            if self._nana_player_wrapped:
                return
            assert self.runtime is not None
            original_player_class = self.runtime.player_class
            service = self

            class NanaObservedPlayer(original_player_class):
                _nana_observed_player = True

                def choose_move(self, current: Any):
                    if getattr(current, "teampreview", False):
                        return super().choose_move(current)
                    light = inspect_light_decision(self, current)
                    order = super().choose_move(current)
                    session = service.active_session
                    if session is not None and not light.get("waiting"):
                        service._nana_note_model_turn(
                            session.id,
                            int(getattr(current, "turn", 0) or 0),
                            light,
                        )
                    return order

                def teampreview(self, current: Any):
                    result = super().teampreview(current)
                    session = service.active_session
                    if session is not None and not asyncio.iscoroutine(result):
                        order = _preview_order(result)
                        if order:
                            service.nana.record_team_preview(
                                session.id,
                                side="model",
                                order=order,
                                state=sparring._battle_snapshot(current),
                            )
                    return result

            NanaObservedPlayer.__name__ = "NanaObservedPlayer"
            self.runtime.player_class = NanaObservedPlayer
            self._nana_player_wrapped = True
            self.runtime_metadata = {
                **self.runtime_metadata,
                "nana": {
                    "enabled": True,
                    "stage": 0,
                    "profileId": self.nana.profile_id,
                    "influence": 0.0,
                },
            }

        async def start(self, request: Any):
            session = await super().start(request)
            self._nana_pending[session.id] = {}
            self.nana.start_session(
                session.id,
                opponent=session.opponent,
                context={
                    "format": sparring.DEFAULT_FORMAT,
                    "checkpoint": str(self.checkpoint),
                    "mode": "Nana 0 observational",
                },
            )
            if session.task is not None:
                session.task.add_done_callback(
                    lambda _task, active=session: self._nana_finish(active)
                )
            return session

        async def submit_preview(self, session_id: str, order: list[int]):
            session = self.get_session(session_id)
            state = copy.deepcopy(session.battle_state)
            result = await super().submit_preview(session_id, order)
            self.nana.record_team_preview(
                session_id,
                side="human",
                order=list(order),
                state=state,
            )
            return result

        async def submit_choice(self, session_id: str, choice_id: str):
            session = self.get_session(session_id)
            action = next(
                (
                    copy.deepcopy(candidate)
                    for candidate in session.legal_actions
                    if candidate.get("id") == choice_id
                ),
                None,
            )
            turn = int(session.battle_state.get("turn", 0) or 0)
            state = copy.deepcopy(session.battle_state)
            legal_actions = copy.deepcopy(session.legal_actions)
            result = await super().submit_choice(session_id, choice_id)
            if action is not None:
                self._nana_note_human_turn(
                    session_id,
                    turn,
                    state=state,
                    legal_actions=legal_actions,
                    action=action,
                )
            return result

        def snapshot(self, session: Any) -> dict[str, Any]:
            data = super().snapshot(session)
            data["nana"] = {
                "enabled": True,
                "stage": 0,
                "profileId": self.nana.profile_id,
                "influence": 0.0,
            }
            return data

        def _nana_turn_entry(self, session_id: str, turn: int) -> dict[str, Any]:
            turns = self._nana_pending.setdefault(session_id, {})
            return turns.setdefault(turn, {})

        def _nana_note_human_turn(
            self,
            session_id: str,
            turn: int,
            *,
            state: dict[str, Any],
            legal_actions: list[dict[str, Any]],
            action: dict[str, Any],
        ) -> None:
            entry = self._nana_turn_entry(session_id, turn)
            entry["human"] = {
                "state": state,
                "legalActions": legal_actions,
                "action": action,
            }
            self._nana_flush_turn(session_id, turn)

        def _nana_note_model_turn(
            self,
            session_id: str,
            turn: int,
            light: dict[str, Any],
        ) -> None:
            entry = self._nana_turn_entry(session_id, turn)
            entry["model"] = {
                "action": copy.deepcopy(light.get("canonicalAction")),
                "light": copy.deepcopy(light),
            }
            self._nana_flush_turn(session_id, turn)

        def _nana_flush_turn(self, session_id: str, turn: int) -> None:
            entry = self._nana_turn_entry(session_id, turn)
            human = entry.get("human")
            model = entry.get("model")
            if human is None or model is None:
                return
            self.nana.record_turn(
                session_id,
                turn=turn,
                state=human["state"],
                legal_actions=human["legalActions"],
                human_action=human["action"],
                model_action=model["action"],
                light=model["light"],
            )
            self._nana_pending.get(session_id, {}).pop(turn, None)

        def _nana_finish(self, session: Any) -> None:
            if session.id in self._nana_finished:
                return
            self._nana_finished.add(session.id)
            if session.result is not None:
                self.nana.finish_session(
                    session.id,
                    result=copy.deepcopy(session.result),
                    final_state=copy.deepcopy(session.battle_state),
                )
            else:
                self.nana.append_event(
                    session.id,
                    "session_abort",
                    {
                        "phase": session.phase,
                        "error": session.error,
                        "finalState": copy.deepcopy(session.battle_state),
                    },
                )
                self.nana.rebuild_habits()

    sparring.BattleLabLocalService = NanaBattleLabLocalService
    return NanaBattleLabLocalService


def parse_nana_args(
    argv: Sequence[str] | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--nana-profile",
        default=os.environ.get("NANA_PROFILE", DEFAULT_NANA_PROFILE),
    )
    return parser.parse_known_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_nana_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
