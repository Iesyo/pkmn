#!/usr/bin/env python3
"""Battle Lab local launcher with the classic Pokémon Showdown client.

The interactive policy/API stays in ``local_sparring_service``. This wrapper
provisions the official Pokémon Showdown client at a pinned revision and serves
its vendor assets unchanged from a dedicated loopback port. A tiny Battle Lab
wrapper, served outside the vendor checkout contents, feeds poke-env's private
player request into the classic BattleRoom so War Room can use Showdown's native
team-preview/move/target/switch controls without duplicating that UI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any, Sequence

from battle_lab.local_sparring_service import (
    DEFAULT_API_PORT,
    DEFAULT_CHECKPOINT,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SEED,
    DEFAULT_SHOWDOWN_PORT,
    main as sparring_main,
)
from battle_lab.native_showdown_controls import install_native_showdown_controls
from battle_lab.showdown_smoke import (
    git_revision,
    installed_node_version,
    port_is_open,
    run_checked,
    run_checked_with_retries,
    run_long_command,
    run_long_command_with_retries,
    tail,
    utc_now,
)


SHOWDOWN_CLIENT_REPOSITORY = "https://github.com/smogon/pokemon-showdown-client.git"
SHOWDOWN_CLIENT_COMMIT = "e47b8be4103b5e027cd191a024e383be88f37bfe"
DEFAULT_VIEWER_PORT = 8767
NATIVE_BRIDGE_MARKER = "battle-lab-native-showdown-controls-v2"


class BorrowedNativeViewerProcess:
    """Completed-like process adapter for a verified bridge owned elsewhere."""

    @staticmethod
    def poll() -> int:
        return 0


class BorrowedNativeViewerLog:
    @staticmethod
    def close() -> None:
        return None


def ensure_showdown_client(
    *,
    checkout: Path,
    logs_dir: Path,
) -> dict[str, Any]:
    """Provision the unmodified classic Showdown client at a pinned SHA."""

    checkout.parent.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, Any] = {}

    if not (checkout / ".git").is_dir():
        if checkout.exists() and any(checkout.iterdir()):
            raise RuntimeError(
                f"La ruta del cliente Showdown existe pero no es un checkout Git: {checkout}"
            )
        started = time.monotonic()
        run_long_command(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--depth",
                "1",
                SHOWDOWN_CLIENT_REPOSITORY,
                str(checkout),
            ],
            cwd=checkout.parent,
            log_path=logs_dir / "showdown-client-clone.log",
            label="Clonando cliente clásico de Pokémon Showdown",
            expected_seconds=45,
        )
        timings["cloneSeconds"] = round(time.monotonic() - started, 3)

    tracked = run_checked(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout
    ).stdout.strip()
    if tracked:
        raise RuntimeError(
            "El checkout dedicado del cliente Showdown contiene cambios rastreados; "
            "Battle Lab no los sobrescribirá."
        )

    started = time.monotonic()
    run_checked_with_retries(
        ["git", "fetch", "--depth", "1", "origin", SHOWDOWN_CLIENT_COMMIT],
        cwd=checkout,
        timeout=180,
    )
    if git_revision(checkout) != SHOWDOWN_CLIENT_COMMIT:
        run_checked(
            ["git", "checkout", "--detach", SHOWDOWN_CLIENT_COMMIT],
            cwd=checkout,
            timeout=60,
        )
    timings["checkoutSeconds"] = round(time.monotonic() - started, 3)

    package_lock = checkout / "package-lock.json"
    if not package_lock.is_file():
        raise RuntimeError("El checkout del cliente Showdown no contiene package-lock.json.")
    lock_hash = hashlib.sha256(package_lock.read_bytes()).hexdigest()
    node_major, node_version = installed_node_version()

    install_marker = checkout / ".battle-lab-client-install.json"
    installed = False
    if install_marker.is_file() and (checkout / "node_modules").is_dir():
        with suppress(json.JSONDecodeError, OSError):
            marker = json.loads(install_marker.read_text(encoding="utf-8"))
            installed = (
                marker.get("lockHash") == lock_hash
                and marker.get("nodeMajor") == node_major
            )
    if not installed:
        started = time.monotonic()
        run_long_command_with_retries(
            ["npm", "ci"],
            cwd=checkout,
            log_path=logs_dir / "showdown-client-npm-ci.log",
            label="Instalando dependencias del cliente Showdown",
            expected_seconds=120,
            attempts=3,
        )
        timings["npmCiSeconds"] = round(time.monotonic() - started, 3)
        install_marker.write_text(
            json.dumps(
                {
                    "lockHash": lock_hash,
                    "nodeMajor": node_major,
                    "nodeVersion": node_version,
                    "installedAt": utc_now(),
                }
            ),
            encoding="utf-8",
        )
    else:
        timings["npmCiSeconds"] = 0.0

    classic_html = checkout / "play.pokemonshowdown.com" / "testclient-old.html"
    classic_js = checkout / "play.pokemonshowdown.com" / "js" / "battle.js"
    graphics_js = checkout / "play.pokemonshowdown.com" / "data" / "graphics.js"
    build_marker = checkout / ".battle-lab-client-build.json"
    built = False
    if build_marker.is_file() and classic_html.is_file() and classic_js.is_file() and graphics_js.is_file():
        with suppress(json.JSONDecodeError, OSError):
            marker = json.loads(build_marker.read_text(encoding="utf-8"))
            built = (
                marker.get("commit") == SHOWDOWN_CLIENT_COMMIT
                and marker.get("nodeMajor") == node_major
            )
    if not built:
        started = time.monotonic()
        run_long_command_with_retries(
            ["node", "build"],
            cwd=checkout,
            log_path=logs_dir / "showdown-client-build.log",
            label="Compilando renderer clásico de Showdown",
            expected_seconds=75,
            attempts=2,
        )
        timings["buildSeconds"] = round(time.monotonic() - started, 3)
        if not classic_js.is_file():
            raise RuntimeError(
                "El build del cliente Showdown terminó sin generar play.pokemonshowdown.com/js/battle.js.\n"
                + tail(logs_dir / "showdown-client-build.log")
            )
        build_marker.write_text(
            json.dumps(
                {
                    "commit": SHOWDOWN_CLIENT_COMMIT,
                    "nodeMajor": node_major,
                    "nodeVersion": node_version,
                    "builtAt": utc_now(),
                }
            ),
            encoding="utf-8",
        )
    else:
        timings["buildSeconds"] = 0.0

    actual = git_revision(checkout)
    if actual != SHOWDOWN_CLIENT_COMMIT:
        raise RuntimeError(
            f"Cliente Showdown quedó en {actual}; se esperaba {SHOWDOWN_CLIENT_COMMIT}."
        )
    return timings


def _native_bridge_available(port: int) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/battle-lab-native-controls-health",
        headers={"User-Agent": "like-no-one-ever-was-native-controls/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = response.read().decode("utf-8", errors="replace").strip()
            return int(getattr(response, "status", 200)) == 200 and payload == NATIVE_BRIDGE_MARKER
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def start_viewer_server(
    *,
    checkout: Path,
    logs_dir: Path,
    port: int,
) -> tuple[Any, Any]:
    if port_is_open(port):
        if _native_bridge_available(port):
            print(
                f"Bridge de controles nativos ya activo en 127.0.0.1:{port}; "
                "Battle Lab lo reutilizará sin tomar propiedad del proceso.",
                flush=True,
            )
            return BorrowedNativeViewerProcess(), BorrowedNativeViewerLog()
        raise RuntimeError(
            f"El renderer existente en el puerto local {port} no expone el bridge "
            "de controles nativos; reinicia el runtime anterior antes de continuar."
        )
    log_path = logs_dir / "showdown-client-http.log"
    handle = log_path.open("w", encoding="utf-8")
    viewer_server = Path(__file__).with_name("showdown_native_viewer.py")
    process = subprocess.Popen(
        [
            sys.executable,
            str(viewer_server),
            "--port",
            str(port),
            "--bind",
            "127.0.0.1",
            "--root",
            str(checkout),
        ],
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
            raise RuntimeError(
                "El servidor del cliente Showdown terminó durante el arranque.\n"
                + tail(log_path)
            )
        if port_is_open(port) and _native_bridge_available(port):
            return process, handle
        time.sleep(0.1)
    process.terminate()
    handle.close()
    raise TimeoutError(
        f"El renderer Showdown no abrió el bridge nativo en 127.0.0.1:{port}."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--showdown-port", type=int, default=DEFAULT_SHOWDOWN_PORT)
    parser.add_argument("--viewer-port", type=int, default=DEFAULT_VIEWER_PORT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Battle Lab local solo puede escuchar en loopback.")

    # Layer native Showdown controls above the currently installed service
    # (plain Sparring or any Nana stage). Existing submit hooks remain intact.
    install_native_showdown_controls()

    runtime_root = args.runtime_root.expanduser().resolve()
    logs_dir = runtime_root / "logs"
    client_root = runtime_root / "pokemon-showdown-client"
    ensure_showdown_client(checkout=client_root, logs_dir=logs_dir)
    viewer_process, viewer_log = start_viewer_server(
        checkout=client_root,
        logs_dir=logs_dir,
        port=args.viewer_port,
    )

    print(
        "Cliente clásico + controles nativos listo en "
        f"http://127.0.0.1:{args.viewer_port}/play.pokemonshowdown.com/testclient-old.html",
        flush=True,
    )
    print(
        "Licencia: los assets de Pokémon Showdown Client se sirven sin modificar desde "
        f"su checkout AGPLv3 fijado en {SHOWDOWN_CLIENT_COMMIT[:12]}; Battle Lab añade "
        "solo un bridge loopback externo al código vendor.",
        flush=True,
    )

    forwarded = [
        "--runtime-root",
        str(args.runtime_root),
        "--checkpoint",
        str(args.checkpoint),
        "--device",
        args.device,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--showdown-port",
        str(args.showdown_port),
        "--seed",
        str(args.seed),
    ]
    try:
        return sparring_main(forwarded)
    finally:
        if viewer_process.poll() is None:
            viewer_process.terminate()
            with suppress(subprocess.TimeoutExpired):
                viewer_process.wait(timeout=5)
            if viewer_process.poll() is None:
                viewer_process.kill()
        viewer_log.close()


if __name__ == "__main__":
    raise SystemExit(main())