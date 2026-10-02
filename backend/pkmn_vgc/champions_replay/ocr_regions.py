"""Known Champions text areas in normalized, oriented video coordinates.

The pixel mask and the text consumers share this layout. A known word does
not make text outside these areas useful. The original video is never masked.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

OCR_REGIONS_VERSION = "champions-text-regions-v1"

# Rectangles are (left, right, top, bottom). Ownership uses the text origin,
# so a clock merged with a party icon cannot move into another text area.
NAME_HUD_AREAS = {
    "p1a": (.05, .27, .82, .91), "p1b": (.27, .49, .82, .91),
    "p2a": (.57, .81, .02, .10), "p2b": (.81, .99, .02, .10),
}
HP_HUD_AREAS = {
    "p1a": (.10, .27, .87, .98), "p1b": (.30, .48, .87, .98),
    "p2a": (.66, .82, .08, .20), "p2b": (.89, .99, .08, .20),
}
CLOCK_AREAS = {"p1_clock": (.15, .25, .78, .855),
               "p2_clock": (.77, .86, .145, .205)}

# Pixel areas include the whole text box and animation margins, rather than
# cropping each word tightly. Keep the image dimensions for all downstream
# icon readers and coordinates. Menu labels are useful as exclusion cues.
BATTLE_TEXT_AREAS = {
    "own_hud": (.015, .55, .55, 1.0),
    "opponent_hud": (.55, 1.0, .015, .245),
    "mobile_opponent_hud": (.52, 1.0, .215, .41),
    "own_banner": (0.0, .34, .26, .56),
    "opponent_banner": (.65, 1.0, .26, .56),
    "narration": (.02, .97, .58, .85),
    "command_menu": (.74, 1.0, .25, .98),
    "move_description": (.02, .97, .46, .67),
    "menu_heading": (.78, 1.0, .015, .16),
    "menu_footer": (.02, 1.0, .90, 1.0),
}
SCREEN_CUE_AREAS = {
    "preview_prompt": (.30, .80, .15, .32),
    "result_title": (.03, .97, .17, .31),
    "result_center": (.30, .70, .36, .60),
}
PREVIEW_TEXT_AREAS = {
    "own_preview": (.02, .31, .075, .91),
    "opponent_preview": (.70, 1.0, .075, .91),
    "player_names": (.10, .97, .03, .16),
    "preview_prompt": SCREEN_CUE_AREAS["preview_prompt"],
    "menu_footer": BATTLE_TEXT_AREAS["menu_footer"],
}
STATUS_TEXT_AREAS = {
    "status_heading": SCREEN_CUE_AREAS["preview_prompt"],
    "menu_footer": (.70, 1.0, .90, 1.0),
}
RESULT_TEXT_AREAS = {
    "result_card": (.03, .97, .17, .65),
    "narration": BATTLE_TEXT_AREAS["narration"],
    "menu_footer": BATTLE_TEXT_AREAS["menu_footer"],
}


def value(line: Any, field: str, default: Any = None) -> Any:
    return line.get(field, default) if isinstance(line, Mapping) else getattr(line, field, default)


def text_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    return "".join(char for char in text if char.isalnum())


def phase_label_seen(keys: Sequence[str], label: str) -> bool:
    return any(key and len(key) <= len(label) + 4 and
               (key == label or SequenceMatcher(None, key, label).ratio() >= .85) for key in keys)


def in_area(line: Any, area: tuple[float, float, float, float]) -> bool:
    x, y = value(line, "left"), value(line, "top")
    return (isinstance(x, (int, float)) and isinstance(y, (int, float)) and
            area[0] <= x <= area[1] and area[2] <= y <= area[3])


def is_clock(line: Any) -> bool:
    return any(in_area(line, area) for area in CLOCK_AREAS.values()) and bool(
        re.search(r"\d", str(value(line, "text", ""))))


def text_screen(lines: Sequence[Any]) -> str:
    cues = [line for line in lines if in_area(line, SCREEN_CUE_AREAS["preview_prompt"])]
    keys = [text_key(value(line, "text", "")) for line in cues]
    status_keys = [text_key(value(line, "text", "")) for line in cues if value(line, "confidence", 0) >= .9]
    if "activestatuseseffects" in status_keys or "activestatusesandeffects" in status_keys:
        return "status"
    if phase_label_seen(keys, "select4pokemon") and phase_label_seen(keys, "sendintobattle"):
        return "preview"
    for line in lines:
        if value(line, "confidence", 0) < .9 or not (
            in_area(line, RESULT_TEXT_AREAS["result_card"]) or
            in_area(line, RESULT_TEXT_AREAS["narration"])):
            continue
        text = value(line, "text", "").strip()
        if (text_key(text) in {"win", "won", "victory", "lose", "lost", "defeat", "defeated"} or
            re.match(r"You (?:won|defeated|beat |lost|were defeated)", text, re.I)):
            return "result"
    return "battle"


def text_areas(screen: str, *, include_screen_cues: bool = False) -> dict[str, tuple[float, float, float, float]]:
    areas = {"preview": PREVIEW_TEXT_AREAS, "status": STATUS_TEXT_AREAS,
             "result": RESULT_TEXT_AREAS}.get(screen, BATTLE_TEXT_AREAS)
    return {**areas, **SCREEN_CUE_AREAS} if include_screen_cues else areas


def useful_text(line: Any, screen: str, *, allow_unlocated: bool = False) -> bool:
    # Legacy hand-authored traces may omit coordinates. They are retained,
    # never used as proof of a located HUD. New OCR always supplies geometry.
    if value(line, "left") is None or value(line, "top") is None:
        return allow_unlocated
    if screen == "battle" and is_clock(line):
        return False
    return any(in_area(line, area) for area in text_areas(screen).values())


def useful_lines(lines: Sequence[Any], *, screen: str | None = None,
                 allow_unlocated: bool = False) -> tuple[Any, ...]:
    selected = screen or text_screen(lines)
    return tuple(line for line in lines if useful_text(line, selected, allow_unlocated=allow_unlocated))


def mask_text_areas(image: Any, screen: str) -> Any:
    """Copy useful pixels; black out the rest before OCR, without resizing."""
    from math import ceil

    height, width = image.shape[:2]
    masked = image.copy()
    masked[...] = 0
    for left, right, top, bottom in text_areas(screen, include_screen_cues=True).values():
        x0, x1 = int(left * width), min(width, ceil(right * width))
        y0, y1 = int(top * height), min(height, ceil(bottom * height))
        masked[y0:y1, x0:x1] = image[y0:y1, x0:x1]
    if screen == "battle":
        for left, right, top, bottom in CLOCK_AREAS.values():
            masked[int(top * height):ceil(bottom * height), int(left * width):ceil(right * width)] = 0
    return masked
