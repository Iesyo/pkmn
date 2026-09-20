from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import FrameDetections
from .sources import FramePacket


class DetectionError(RuntimeError):
    """El detector visual no pudo producir observaciones válidas."""


class FrameDetector(Protocol):
    def detect(self, frame: FramePacket) -> FrameDetections: ...


@dataclass(frozen=True, slots=True)
class HudAlias:
    side: str
    nickname: str
    species: str
    gender: str | None = None
    confidence: float = 0.0


class HudAliasResolver(Protocol):
    def resolve(
        self,
        frame: FramePacket,
        candidates: Sequence[tuple[str, str]],
    ) -> tuple[HudAlias, ...]: ...


@dataclass(frozen=True, slots=True)
class DetectorContext:
    p1_name: str = "Player"
    p2_name: str = "Rival"
    p1_team: tuple[str, ...] = ()
    p2_team: tuple[str, ...] = ()
    p1_aliases: tuple[tuple[str, str], ...] = ()
    p2_aliases: tuple[tuple[str, str], ...] = ()
    language: str = "en"

    def prompt_context(self) -> str:
        return json.dumps(
            {
                "perspective": "p1 es el jugador local y p2 es el rival",
                "players": {"p1": self.p1_name, "p2": self.p2_name},
                "known_teams": {"p1": self.p1_team, "p2": self.p2_team},
                "known_aliases": {
                    "p1": dict(self.p1_aliases),
                    "p2": dict(self.p2_aliases),
                },
                "game_language": self.language,
            },
            ensure_ascii=False,
        )


DETECTION_PROMPT = """Analiza este único frame de una batalla DOBLE de Pokémon Champions.
Devuelve exclusivamente JSON válido. No inventes datos ocultos ni sucesos que no sean visibles.
Usa nombres oficiales EN INGLÉS para Pokémon, movimientos, objetos, habilidades, estados y clima,
aunque el juego esté en otro idioma. p1 es el jugador local; p2 es el rival.

Esquema:
{
  "players": {"p1": null, "p2": null},
  "teams": {"p1": [], "p2": []},
  "selected": {"p1": [], "p2": []},
  "battle_started": false,
  "battle_complete": false,
  "winner": null,
  "events": [
    {
      "kind": "turn|switch|drag|move|damage|heal|status|curestatus|faint|ability|item|enditem|mega|terastallize|crit|weather|fieldstart|fieldend|sidestart|sideend|message",
      "slot": "p1a|p1b|p2a|p2b|null",
      "target_slot": "p1a|p1b|p2a|p2b|null",
      "species": null,
      "forme": null,
      "move": null,
      "health": null,
      "value": null,
      "turn": null,
      "amount": null,
      "confidence": 0.0
    }
  ]
}

Reglas:
- team/selected sólo contiene especies que la interfaz marque de forma explícita.
- switch identifica Pokémon que entran al campo y su slot.
- move se reporta únicamente cuando el mensaje confirma que se usó.
- health usa exacto/max si es visible; para el rival usa porcentaje/100.
- winner vale p1 o p2 sólo si el resultado está confirmado.
- confidence expresa confianza visual entre 0 y 1.
- Si el frame no aporta información, deja listas vacías y valores null/false.
Contexto conocido:
"""


DETECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "players": {
            "type": "object",
            "properties": {
                "p1": {"type": ["string", "null"]},
                "p2": {"type": ["string", "null"]},
            },
            "required": ["p1", "p2"],
            "additionalProperties": False,
        },
        "teams": {
            "type": "object",
            "properties": {
                "p1": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                "p2": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
            },
            "required": ["p1", "p2"],
            "additionalProperties": False,
        },
        "selected": {
            "type": "object",
            "properties": {
                "p1": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                "p2": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            },
            "required": ["p1", "p2"],
            "additionalProperties": False,
        },
        "battle_started": {"type": "boolean"},
        "battle_complete": {"type": "boolean"},
        "winner": {"type": ["string", "null"], "enum": ["p1", "p2", None]},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "turn",
                            "switch",
                            "drag",
                            "move",
                            "damage",
                            "heal",
                            "status",
                            "curestatus",
                            "faint",
                            "ability",
                            "item",
                            "enditem",
                            "mega",
                            "terastallize",
                            "crit",
                            "weather",
                            "fieldstart",
                            "fieldend",
                            "sidestart",
                            "sideend",
                            "message",
                        ],
                    },
                    "slot": {
                        "type": ["string", "null"],
                        "enum": ["p1a", "p1b", "p2a", "p2b", None],
                    },
                    "target_slot": {
                        "type": ["string", "null"],
                        "enum": ["p1a", "p1b", "p2a", "p2b", None],
                    },
                    "species": {"type": ["string", "null"]},
                    "forme": {"type": ["string", "null"]},
                    "move": {"type": ["string", "null"]},
                    "health": {"type": ["string", "null"]},
                    "value": {"type": ["string", "null"]},
                    "turn": {"type": ["integer", "null"], "minimum": 1},
                    "amount": {"type": ["integer", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "kind",
                    "slot",
                    "target_slot",
                    "species",
                    "forme",
                    "move",
                    "health",
                    "value",
                    "turn",
                    "amount",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "players",
        "teams",
        "selected",
        "battle_started",
        "battle_complete",
        "winner",
        "events",
    ],
    "additionalProperties": False,
}


def _response_excerpt(value: str, *, limit: int = 240) -> str:
    excerpt = " ".join(value.strip().split())
    return excerpt[:limit] or "<respuesta vacía>"


def _ollama_output_candidates(body: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(body, Mapping):
        raise DetectionError("Ollama devolvió una respuesta que no es un objeto JSON.")

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in ("response", "thinking"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip() or value in seen:
            continue
        candidates.append((field, value))
        seen.add(value)

    if candidates:
        return tuple(candidates)

    done_reason = body.get("done_reason") or "desconocido"
    eval_count = body.get("eval_count")
    generated = f", tokens generados={eval_count}" if isinstance(eval_count, int) else ""
    raise DetectionError(
        f"Ollama devolvió response y thinking vacíos (fin={done_reason}{generated})."
    )


def _extract_json(
    value: str,
    *,
    expected_keys: set[str] | None = None,
) -> Mapping[str, Any]:
    text = value.strip()
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    detection_keys = expected_keys or {
        "players",
        "teams",
        "selected",
        "battle_started",
        "battle_complete",
        "winner",
        "events",
    }
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError as error:
            last_error = error
            continue
        if isinstance(parsed, Mapping) and detection_keys.intersection(parsed):
            return parsed
    excerpt = _response_excerpt(text)
    if last_error:
        raise DetectionError(
            f"El modelo visual devolvió JSON inválido ({last_error.msg}). Respuesta: {excerpt}"
        ) from last_error
    raise DetectionError(f"El modelo visual no devolvió JSON. Respuesta: {excerpt}")


HUD_ALIAS_PROMPT = """Analiza exclusivamente los iconos del HUD de esta batalla DOBLE de Pokémon Champions.
Cada nickname indicado aparece junto a un pequeño icono de su Pokémon y, cuando aplica, un símbolo de género.
Asocia cada nickname con la especie visible. Usa nombres oficiales EN INGLÉS y no uses el nickname como especie.
La imagen puede ser una composición con el frame completo arriba y una ampliación del HUD abajo.
Usa los modelos 3D del frame completo para reconocer las especies y los iconos ampliados para asociarlas al nickname correcto.
El HUD de p2 está arriba y el de p1 abajo; usa el icono inmediatamente a la izquierda de cada nickname.
No intercambies las asociaciones por el orden de los modelos 3D que aparecen en el campo.
Devuelve el candidate_id recibido sin volver a escribir ni traducir el nickname. Devuelve exclusivamente JSON válido.

Esquema:
{
    "aliases": [
    {
      "candidate_id": "p2-1",
      "species": "English species",
      "gender": "M|F|null",
      "confidence": 0.0
    }
  ]
}

Candidatos visibles:
"""


HUD_ALIAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "aliases": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "species": {"type": "string"},
                    "gender": {"type": ["string", "null"], "enum": ["M", "F", None]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["candidate_id", "species", "gender", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["aliases"],
    "additionalProperties": False,
}


class OllamaHudAliasResolver:
    """Lectura visual puntual del icono unido a cada nickname del HUD."""

    def __init__(
        self,
        *,
        model: str = "qwen3-vl:4b",
        endpoint: str = "http://127.0.0.1:11434",
        context: DetectorContext | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("El detector de aliases debe ejecutarse localmente en localhost.")
        self.endpoint = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        self.model = model.strip()
        self.context = context or DetectorContext()
        self.timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Indica el modelo visual de Ollama.")

    def resolve(
        self,
        frame: FramePacket,
        candidates: Sequence[tuple[str, str]],
    ) -> tuple[HudAlias, ...]:
        requested: dict[str, tuple[str, str]] = {}
        for index, (side, nickname) in enumerate(candidates, start=1):
            if side in {"p1", "p2"} and nickname.strip():
                requested[f"{side}-{index}"] = (side, nickname)
        if not requested:
            return ()
        candidate_context = [
            {"candidate_id": candidate_id, "side": side, "nickname": nickname}
            for candidate_id, (side, nickname) in requested.items()
        ]
        response_schema = deepcopy(HUD_ALIAS_SCHEMA)
        aliases_schema = response_schema["properties"]["aliases"]
        aliases_schema["maxItems"] = len(requested)
        aliases_schema["items"]["properties"]["candidate_id"]["enum"] = list(requested)
        last_error: DetectionError | None = None
        for attempt in range(2):
            correction = (
                "\nCORRECCIÓN: la respuesta anterior no asoció ningún candidato. "
                "Examina otra vez cada icono, conserva su candidate_id y devuelve tu mejor identificación en JSON."
                if attempt
                else ""
            )
            payload = {
                "model": self.model,
                "prompt": (
                    f"{HUD_ALIAS_PROMPT}{json.dumps(candidate_context, ensure_ascii=False)}\n"
                    f"Contexto conocido: {self.context.prompt_context()}{correction}"
                ),
                "images": [base64.b64encode(frame.image).decode("ascii")],
                "format": response_schema,
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {"temperature": 0},
            }
            request = Request(
                f"{self.endpoint}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - endpoint validated above
                    body = json.load(response)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise DetectionError(f"Ollama respondió {error.code} al leer aliases: {detail[:300]}") from error
            except TimeoutError as error:
                last_error = DetectionError(
                    f"Ollama agotó {self.timeout_seconds:g} segundos leyendo el HUD con {self.model}."
                )
                continue
            except URLError as error:
                reason = getattr(error, "reason", error)
                last_error = DetectionError(
                    f"No pudimos conectar con Ollama para leer el HUD ({reason})."
                )
                continue

            try:
                output_candidates = _ollama_output_candidates(body)
            except DetectionError as error:
                last_error = error
                continue

            candidate_errors: list[str] = []
            mapping: Mapping[str, Any] | None = None
            for field, response_text in output_candidates:
                try:
                    mapping = _extract_json(response_text, expected_keys={"aliases"})
                    break
                except DetectionError as error:
                    candidate_errors.append(f"{field}: {error}")
            if mapping is None:
                last_error = DetectionError("; ".join(candidate_errors))
                continue

            raw_aliases = mapping.get("aliases")
            if not isinstance(raw_aliases, list):
                last_error = DetectionError("Ollama no devolvió la lista de aliases del HUD.")
                continue
            aliases: list[HudAlias] = []
            seen: set[str] = set()
            for value in raw_aliases:
                if not isinstance(value, Mapping):
                    continue
                candidate_id = value.get("candidate_id")
                requested_candidate = requested.get(candidate_id) if isinstance(candidate_id, str) else None
                species = value.get("species")
                confidence = value.get("confidence")
                if (
                    requested_candidate is None
                    or not isinstance(species, str)
                    or not species.strip()
                    or not isinstance(confidence, (int, float))
                    or float(confidence) < 0.7
                    or candidate_id in seen
                ):
                    continue
                side, requested_nickname = requested_candidate
                gender = value.get("gender")
                aliases.append(
                    HudAlias(
                        side=side,
                        nickname=requested_nickname,
                        species=species.strip(),
                        gender=gender if gender in {"M", "F"} else None,
                        confidence=max(0.0, min(1.0, float(confidence))),
                    )
                )
                seen.add(candidate_id)
            if aliases:
                return tuple(aliases)
            last_error = DetectionError(
                "Ollama devolvió aliases sin una asociación utilizable: "
                f"{_response_excerpt(json.dumps(raw_aliases, ensure_ascii=False), limit=420)}"
            )

        raise DetectionError(
            f"Frame {frame.index + 1}: lectura visual de nicknames con {self.model} fallida "
            f"tras 2 intentos. {last_error}"
        ) from last_error


class OllamaVisionDetector:
    """Detector local y reemplazable; por defecto usa Qwen3-VL mediante Ollama."""

    def __init__(
        self,
        *,
        model: str = "qwen3-vl:4b",
        endpoint: str = "http://127.0.0.1:11434",
        context: DetectorContext | None = None,
        timeout_seconds: float = 90,
    ) -> None:
        parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("El detector Ollama debe ejecutarse localmente en localhost.")
        self.endpoint = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        self.model = model.strip()
        self.context = context or DetectorContext()
        self.timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Indica el modelo visual de Ollama.")

    def detect(self, frame: FramePacket) -> FrameDetections:
        last_error: DetectionError | None = None
        for attempt in range(2):
            correction = (
                "\nCORRECCIÓN: responde ahora únicamente con el objeto JSON solicitado, sin explicación ni Markdown."
                if attempt else ""
            )
            payload = {
                "model": self.model,
                "prompt": f"{DETECTION_PROMPT}{self.context.prompt_context()}{correction}",
                "images": [base64.b64encode(frame.image).decode("ascii")],
                "format": DETECTION_SCHEMA,
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {"temperature": 0},
            }
            request = Request(
                f"{self.endpoint}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - endpoint validated above
                    body = json.load(response)
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise DetectionError(f"Ollama respondió {error.code}: {detail[:300]}") from error
            except (URLError, TimeoutError) as error:
                raise DetectionError(
                    "No pudimos conectar con Ollama local. Verifica que esté iniciado y que el modelo esté descargado."
                ) from error
            try:
                candidates = _ollama_output_candidates(body)
            except DetectionError as error:
                last_error = error
                continue

            candidate_errors: list[str] = []
            for field, response_text in candidates:
                try:
                    mapping = _extract_json(response_text)
                    return FrameDetections.from_mapping(mapping, timestamp_ms=frame.timestamp_ms)
                except (DetectionError, TypeError, ValueError) as error:
                    candidate_errors.append(f"{field}: {error}")
            last_error = DetectionError("; ".join(candidate_errors))
        raise DetectionError(
            f"Frame {frame.index + 1}: Ollama no produjo observaciones válidas tras 2 intentos. {last_error}"
        ) from last_error
