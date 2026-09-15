#!/usr/bin/env python3
"""Nana 2 v2.1 exposed directly to a trusted private LAN.

This launcher matches the real local workflow: LikeNoOneEverWas/Vite already
prints a Network URL such as ``http://192.168.1.4:5173`` and the second computer
only opens that URL in a browser. Nothing is installed or executed on the
second computer.

Normal Battle Lab stays loopback-only. This explicit LAN entrypoint changes only
the transport boundary while it is running:
- Battle Lab API listens on 0.0.0.0:8765;
- the private Showdown server listens on 0.0.0.0:8766;
- the native Showdown viewer listens on 0.0.0.0:8767;
- Nana/LIGHT, persistence and battle policy remain unchanged.

Use only on a trusted private network. If Windows Firewall asks, allow Python on
Private networks only. Do not forward these ports from the router to Internet.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Sequence

from fastapi.middleware.cors import CORSMiddleware

from battle_lab import local_sparring_service as sparring
from battle_lab import nana_stage2_shadow_v2_runtime as stage2_v2
from battle_lab.nana_stage2_shadow_v21_runtime import STAGE2_MODEL_VERSION


PRIVATE_ORIGIN_REGEX = (
    r"^https?://(?:"
    r"localhost|127\.0\.0\.1|"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r")(?::\d+)?$"
)


def _candidate_lan_addresses() -> list[str]:
    ordered: list[str] = []

    def add(value: str) -> None:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return
        if address.is_loopback or not address.is_private:
            return
        if value not in ordered:
            ordered.append(value)

    with suppress(OSError):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            add(str(probe.getsockname()[0]))
        finally:
            probe.close()
    with suppress(OSError):
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(item[4][0]))
    return ordered


def _install_lan_showdown_config() -> None:
    original = sparring.install_runtime_config
    if getattr(original, "_battle_lab_lan_direct", False):
        return

    def install_runtime_config(checkout: Path) -> None:
        original(checkout)
        path = checkout / "config" / "config.js"
        source = path.read_text(encoding="utf-8")
        expected = "exports.bindaddress = '127.0.0.1';"
        replacement = "exports.bindaddress = '0.0.0.0';"
        if expected not in source and replacement not in source:
            raise RuntimeError(
                "La configuración dedicada de Showdown no contiene el bindaddress esperado."
            )
        if expected in source:
            path.write_text(source.replace(expected, replacement, 1), encoding="utf-8")

    install_runtime_config._battle_lab_lan_direct = True  # type: ignore[attr-defined]
    sparring.install_runtime_config = install_runtime_config


def _install_lan_cors() -> None:
    original = sparring.build_app
    if getattr(original, "_battle_lab_lan_direct", False):
        return

    def build_app(service: Any):
        app = original(service)
        # Outer CORS layer for the native viewer served from the same private IP
        # on :8767. War Room itself continues using its same-origin /api proxy.
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=PRIVATE_ORIGIN_REGEX,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
        )
        return app

    build_app._battle_lab_lan_direct = True  # type: ignore[attr-defined]
    sparring.build_app = build_app


def _install_lan_viewer(local_runtime: Any) -> None:
    if getattr(local_runtime.start_viewer_server, "_battle_lab_lan_direct", False):
        return

    def start_viewer_server(
        *,
        checkout: Path,
        logs_dir: Path,
        port: int,
    ) -> tuple[Any, Any]:
        if local_runtime.port_is_open(port):
            if local_runtime._native_bridge_available(port):
                print(
                    f"Bridge nativo ya activo en 127.0.0.1:{port}; se reutilizará.",
                    flush=True,
                )
                return (
                    local_runtime.BorrowedNativeViewerProcess(),
                    local_runtime.BorrowedNativeViewerLog(),
                )
            raise RuntimeError(
                f"El puerto {port} ya está ocupado por un renderer incompatible."
            )

        log_path = logs_dir / "showdown-client-http.log"
        handle = log_path.open("w", encoding="utf-8")
        viewer_server = Path(local_runtime.__file__).with_name("showdown_native_viewer.py")
        process = subprocess.Popen(
            [
                sys.executable,
                str(viewer_server),
                "--port",
                str(port),
                "--bind",
                "0.0.0.0",
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
                    "El servidor LAN del cliente Showdown terminó durante el arranque.\n"
                    + local_runtime.tail(log_path)
                )
            if local_runtime.port_is_open(port) and local_runtime._native_bridge_available(port):
                return process, handle
            time.sleep(0.1)
        process.terminate()
        handle.close()
        raise TimeoutError(f"El renderer LAN no abrió el puerto {port}.")

    start_viewer_server._battle_lab_lan_direct = True  # type: ignore[attr-defined]
    local_runtime.start_viewer_server = start_viewer_server


def _install_lan_api(local_runtime: Any) -> None:
    if getattr(local_runtime.sparring_main, "_battle_lab_lan_direct", False):
        return

    def lan_sparring_main(argv: Sequence[str] | None = None) -> int:
        args = sparring.parse_args(argv)
        import uvicorn

        service = sparring.BattleLabLocalService(
            runtime_root=args.runtime_root,
            checkpoint=args.checkpoint,
            device=args.device,
            showdown_port=args.showdown_port,
            seed=args.seed,
        )
        uvicorn.run(
            sparring.build_app(service),
            host="0.0.0.0",
            port=args.port,
            log_level="info",
        )
        return 0

    lan_sparring_main._battle_lab_lan_direct = True  # type: ignore[attr-defined]
    local_runtime.sparring_main = lan_sparring_main


def install_direct_lan(local_runtime: Any) -> None:
    _install_lan_showdown_config()
    _install_lan_cors()
    _install_lan_viewer(local_runtime)
    _install_lan_api(local_runtime)


def parse_lan_args(
    argv: Sequence[str] | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--lan-address",
        default="",
        help="Solo informativo: IP privada de la ROG que muestra Vite/War Room.",
    )
    return parser.parse_known_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    lan_args, remaining = parse_lan_args(argv)
    nana_args, remaining = stage2_v2.parse_nana_args(remaining)

    stage2_v2.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION
    stage2_v2.install_nana_stage2_shadow_v2_service(profile_id=nana_args.nana_profile)

    from battle_lab import local_runtime

    install_direct_lan(local_runtime)
    stage2_v2.install_reusable_viewer(local_runtime)

    addresses = _candidate_lan_addresses()
    if lan_args.lan_address and lan_args.lan_address not in addresses:
        addresses.insert(0, lan_args.lan_address)

    print("", flush=True)
    print("=== Battle Lab LAN directo ===", flush=True)
    print(
        "No ejecutes nada en la segunda PC: abre allí la URL Network que ya imprime Vite.",
        flush=True,
    )
    if addresses:
        print("IPs privadas detectadas de la ROG: " + ", ".join(addresses), flush=True)
    print("Battle Lab API: :8765 · Showdown: :8766 · renderer nativo: :8767", flush=True)
    print(
        "El War Room cambiará automáticamente los 127.0.0.1 del iframe por el host de la URL Network.",
        flush=True,
    )
    print("Windows Firewall: permitir Python únicamente en redes Privadas.", flush=True)
    print("No publicar 8765/8766/8767 en el router/Internet.", flush=True)
    print("================================", flush=True)
    print("", flush=True)

    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
