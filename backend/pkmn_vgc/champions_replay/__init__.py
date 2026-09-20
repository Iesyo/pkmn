"""Reconstrucción de combates de Pokémon Champions como replays de Showdown."""

from .detector import HudAlias, OllamaHudAliasResolver
from .models import (
    BattleEvent,
    BattleSide,
    CapturedBattle,
    FrameDetections,
    ReplayDocument,
)
from .ocr_detector import ChampionsOcrDetector, ChampionsTextParser, OcrLine, OcrTraceDetector
from .showdown import build_replay_document, render_replay_html

__all__ = [
    "BattleEvent",
    "BattleSide",
    "CapturedBattle",
    "ChampionsOcrDetector",
    "ChampionsTextParser",
    "FrameDetections",
    "HudAlias",
    "OcrLine",
    "OcrTraceDetector",
    "OllamaHudAliasResolver",
    "ReplayDocument",
    "build_replay_document",
    "render_replay_html",
]
