"""Nana 2.3 Nursery LIVE + Speed Tier, isolated feature launcher.

This keeps the current Nana/Showdown stack intact and layers the advisory Speed
Tier plus its local viewer on top.  Once the ROG smoke is accepted, this small
bootstrap can be folded into the standard LAN launcher without changing the
battle protocol or native Showdown controls.
"""

from __future__ import annotations

import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Sequence

from battle_lab import local_runtime
from battle_lab import nana_stage2_nursery_lan_runtime as nursery
from battle_lab.showdown_smoke import port_is_open, tail
from battle_lab.sparring_speed_tier import install_speed_tier_snapshot

SPEED_VIEWER_MARKER = "battle-lab-native-showdown-controls-v3-speed-tier"


def _speed_bridge_available(port: int) -> bool:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/battle-lab-native-controls-health",
        headers={"User-Agent": "like-no-one-ever-was-speed-tier/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = response.read().decode("utf-8", errors="replace").strip()
            return int(getattr(response, "status", 200)) == 200 and payload == SPEED_VIEWER_MARKER
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _start_speed_viewer(*, checkout: Path, logs_dir: Path, port: int) -> tuple[Any, Any]:
    if port_is_open(port):
        if _speed_bridge_available(port):
            print(f"Speed Tier viewer ya activo en 127.0.0.1:{port}; se reutiliza.", flush=True)
            return local_runtime.BorrowedNativeViewerProcess(), local_runtime.BorrowedNativeViewerLog()
        raise RuntimeError(
            f"El puerto local {port} ya está ocupado por un renderer anterior. "
            "Detén el runtime viejo para activar Speed Tier."
        )

    log_path = logs_dir / "showdown-speed-tier-http.log"
    handle = log_path.open("w", encoding="utf-8")
    viewer_server = Path(__file__).with_name("sparring_speed_viewer.py")
    process = subprocess.Popen(
        [sys.executable, str(viewer_server), "--port", str(port), "--bind", "127.0.0.1", "--root", str(checkout)],
        cwd=checkout,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    while time.monotonic() - started < 20:
        if process.poll() is not None:
            handle.close()
            raise RuntimeError("El Speed Tier viewer terminó durante el arranque.\n" + tail(log_path))
        if port_is_open(port) and _speed_bridge_available(port):
            return process, handle
        time.sleep(0.1)
    process.terminate()
    with suppress(Exception):
        process.wait(timeout=2)
    handle.close()
    raise TimeoutError(f"Speed Tier viewer no abrió en 127.0.0.1:{port}.")


def _install_speed_layer() -> None:
    if getattr(local_runtime, "_battle_lab_speed_tier_bootstrap", False):
        return
    original_install = local_runtime.install_native_showdown_controls

    def install_native_then_speed() -> type:
        service_class = original_install()
        install_speed_tier_snapshot()
        return service_class

    local_runtime.install_native_showdown_controls = install_native_then_speed
    local_runtime.start_viewer_server = _start_speed_viewer
    local_runtime.NATIVE_BRIDGE_MARKER = SPEED_VIEWER_MARKER
    local_runtime._battle_lab_speed_tier_bootstrap = True


def main(argv: Sequence[str] | None = None) -> int:
    _install_speed_layer()
    print("", flush=True)
    print("=== Battle Lab · Speed Tier ===", flush=True)
    print("Panel izquierdo: prioridad → Speed efectiva; Trick Room/Tailwind en vivo.", flush=True)
    print("Showdown conserva controles, reglas y resolución del turno.", flush=True)
    print("================================", flush=True)
    print("", flush=True)
    return nursery.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
