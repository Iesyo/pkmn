"""Nana 2.3 Nursery LIVE + Speed Tier, isolated feature launcher.

This keeps the current Nana/Showdown stack intact and layers the advisory Speed
Tier plus its LAN viewer on top. Once the ROG smoke is accepted, this small
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
from battle_lab import nana_stage2_shadow_v21_lan_runtime as lan
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard
from battle_lab.nana_stage2_nursery_runtime import install_nursery_service
from battle_lab.showdown_smoke import port_is_open, tail
from battle_lab.sparring_speed_tier import install_speed_tier_snapshot

SPEED_VIEWER_MARKER = "battle-lab-native-showdown-controls-v4-speed-tier"


def _speed_bridge_available(port: int) -> bool:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/battle-lab-native-controls-health",
        headers={"User-Agent": "like-no-one-ever-was-speed-tier/2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = response.read().decode("utf-8", errors="replace").strip()
            return (
                int(getattr(response, "status", 200)) == 200
                and payload == SPEED_VIEWER_MARKER
            )
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _start_speed_viewer(
    *,
    checkout: Path,
    logs_dir: Path,
    port: int,
) -> tuple[Any, Any]:
    """Serve the Speed Tier wrapper on the same trusted-LAN boundary as War Room."""

    if port_is_open(port):
        if _speed_bridge_available(port):
            print(
                f"Speed Tier viewer v2 ya activo en 127.0.0.1:{port}; se reutiliza.",
                flush=True,
            )
            return (
                local_runtime.BorrowedNativeViewerProcess(),
                local_runtime.BorrowedNativeViewerLog(),
            )
        raise RuntimeError(
            f"Speed Tier requiere reemplazar el renderer activo en 127.0.0.1:{port}. "
            "Detén el runtime viejo y vuelve a iniciar este launcher."
        )

    log_path = logs_dir / "showdown-speed-tier-http.log"
    handle = log_path.open("w", encoding="utf-8")
    project_root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "battle_lab.sparring_speed_viewer_v2",
            "--port",
            str(port),
            "--bind",
            "0.0.0.0",
            "--root",
            str(checkout),
        ],
        cwd=project_root,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()
    while time.monotonic() - started < 20:
        if process.poll() is not None:
            handle.close()
            raise RuntimeError(
                "El Speed Tier viewer v2 terminó durante el arranque.\n"
                + tail(log_path)
            )
        if port_is_open(port) and _speed_bridge_available(port):
            return process, handle
        time.sleep(0.1)

    process.terminate()
    with suppress(Exception):
        process.wait(timeout=2)
    handle.close()
    raise TimeoutError(f"Speed Tier viewer v2 no abrió en 127.0.0.1:{port}.")


def _install_speed_layer_after_lan() -> None:
    """Install Speed Tier after the LAN transport has installed its own viewer.

    The LAN bootstrap deliberately replaces ``local_runtime.start_viewer_server``.
    Installing Speed Tier before that bootstrap made the LAN viewer win, while
    ``NATIVE_BRIDGE_MARKER`` still expected the Speed Tier marker. The stock
    viewer therefore opened port 8767 with the wrong health marker and the
    launcher timed out. Order is part of the contract here: LAN first, Speed Tier
    second, Nana's reusable-viewer wrapper last.
    """

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
    # Reproduce the supported Nursery LAN entrypoint explicitly so the transport
    # is installed before Speed Tier replaces only the renderer layer.
    lan_args, remaining = lan.parse_lan_args(argv)
    nana_args, remaining = parse_nana_args(remaining)
    service_class = install_nursery_service(profile_id=nana_args.nana_profile)
    install_nursery_lan_request_guard(service_class)

    lan.install_direct_lan(local_runtime)
    _install_speed_layer_after_lan()
    install_reusable_viewer(local_runtime)

    addresses = lan._candidate_lan_addresses()
    if lan_args.lan_address and lan_args.lan_address not in addresses:
        addresses.insert(0, lan_args.lan_address)

    print("", flush=True)
    print("=== Battle Lab LAN · Nana 2.3 Nursery LIVE + Speed Tier v2 ===", flush=True)
    print(
        "Nana puede ejecutar hasta 1 intervención near-LIGHT por BO1; "
        "todo lo demás cae a LIGHT.",
        flush=True,
    )
    print(
        "Speed Tier reserva la columna izquierda y usa polling independiente; "
        "Showdown conserva controles, reglas y resolución.",
        flush=True,
    )
    print(
        "La segunda PC solo abre la URL Network de Vite; no ejecutes nada allí.",
        flush=True,
    )
    if addresses:
        print("IPs privadas detectadas: " + ", ".join(addresses), flush=True)
    print("API :8765 · Showdown :8766 · renderer Speed Tier :8767", flush=True)
    print(
        "Guard activo: un solo envío por prompt; retries/forced-switch/timeouts "
        "sin prompt humano se registran como skips benignos.",
        flush=True,
    )
    print(
        "Cada intervención real queda registrada para que Nana aprenda de su "
        "propia experiencia en partidas posteriores.",
        flush=True,
    )
    print("Fallback absoluto: LIGHT. Promoción de autonomía: NO automática.", flush=True)
    print("===============================================================", flush=True)
    print("", flush=True)

    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
