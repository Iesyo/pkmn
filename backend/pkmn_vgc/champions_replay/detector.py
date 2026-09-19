from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
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
class DetectorContext:
    p1_name: str = "Jugador"
    p2_name: str = "Rival"
    p1_team: tuple[str, ...] = ()
    p2_team: tuple[str, ...] = ()
    language: str = "es"

    def prompt_context(self) -> str:
        return json.dumps(
            {
                "perspective": "p1 es el jugador local y p2 es el rival",
                "players": {"p1": self.p1_name, "p2": self.p2_name},
                "known_teams": {"p1": self.p1_team, "p2": self.p2_team},
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
      "kind": "turn|switch|drag|move|damage|heal|status|curestatus|faint|ability|item|enditem|terastallize|crit|weather|fieldstart|fieldend|sidestart|sideend|message",
      "slot": "p1a|p1b|p2a|p2b|null",
      "target_slot": "p1a|p1b|p2a|p2b|null",
      "species": null,
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


def _extract_json(value: str) -> Mapping[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise DetectionError("El modelo visual no devolvió JSON.")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise DetectionError(f"El modelo visual devolvió JSON inválido: {error.msg}.") from error
    if not isinstance(parsed, Mapping):
        raise DetectionError("La respuesta visual debe ser un objeto JSON.")
    return parsed


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
        payload = {
            "model": self.model,
            "prompt": f"{DETECTION_PROMPT}{self.context.prompt_context()}",
            "images": [base64.b64encode(frame.image).decode("ascii")],
            "format": "json",
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
        response_text = body.get("response") if isinstance(body, Mapping) else None
        if not isinstance(response_text, str):
            raise DetectionError("Ollama no devolvió el campo response esperado.")
        return FrameDetections.from_mapping(_extract_json(response_text), timestamp_ms=frame.timestamp_ms)
