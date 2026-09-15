#!/usr/bin/env python3
"""Nana 2 v2.1 plus an authenticated single-port LAN gateway.

The underlying Battle Lab runtime is intentionally unchanged and remains bound
to loopback. This launcher starts an authenticated tunnel gateway on the ROG
Ally, then runs the normal Nana v2.1 shadow runtime. A second computer can run
``battle_lab.lan_tunnel client`` to recreate the required loopback ports locally.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import suppress
from typing import Sequence

from battle_lab import nana_stage2_shadow_v21_runtime as nana_runtime
from battle_lab.lan_tunnel import DEFAULT_BATTLE_LAB_PORTS, DEFAULT_GATEWAY_PORT


DEFAULT_WAR_ROOM_PORT = 11829


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_port(port: int, process: subprocess.Popen[bytes], timeout: float = 8.0) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if process.poll() is not None:
            raise RuntimeError(
                f"El gateway LAN terminó durante el arranque (código {process.returncode})."
            )
        if _port_is_open(port):
            return
        time.sleep(0.1)
    raise TimeoutError(f"El gateway LAN no abrió 127.0.0.1:{port} a tiempo.")


def _candidate_lan_addresses() -> list[str]:
    candidates: set[str] = set()
    with suppress(OSError):
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.add(str(item[4][0]))
    with suppress(OSError):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            candidates.add(str(probe.getsockname()[0]))
        finally:
            probe.close()

    def useful(value: str) -> bool:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return not address.is_loopback and (
            address.is_private
            or address.is_link_local
            or address in ipaddress.ip_network("100.64.0.0/10")
        )

    return sorted(value for value in candidates if useful(value))


def parse_lan_args(
    argv: Sequence[str] | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lan-bind", default="0.0.0.0")
    parser.add_argument("--lan-port", type=int, default=DEFAULT_GATEWAY_PORT)
    parser.add_argument(
        "--war-room-port",
        type=int,
        default=int(os.environ.get("WAR_ROOM_PORT", DEFAULT_WAR_ROOM_PORT)),
    )
    parser.add_argument(
        "--lan-extra-port",
        action="append",
        type=int,
        default=[],
        help="Puerto loopback adicional que debe estar disponible desde la PC remota.",
    )
    return parser.parse_known_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    lan_args, remaining = parse_lan_args(argv)
    if _port_is_open(lan_args.lan_port):
        raise SystemExit(
            f"El puerto LAN {lan_args.lan_port} ya está ocupado. "
            "Cierra el gateway anterior o usa --lan-port con otro valor."
        )

    ports = []
    for value in (
        lan_args.war_room_port,
        *DEFAULT_BATTLE_LAB_PORTS,
        *lan_args.lan_extra_port,
    ):
        if value not in ports:
            ports.append(int(value))

    token = secrets.token_urlsafe(24)
    environment = os.environ.copy()
    environment["BATTLE_LAB_LAN_TOKEN"] = token
    command = [
        sys.executable,
        "-m",
        "battle_lab.lan_tunnel",
        "gateway",
        "--bind",
        lan_args.lan_bind,
        "--port",
        str(lan_args.lan_port),
        "--allow-ports",
        *[str(port) for port in ports],
    ]
    gateway = subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_port(lan_args.lan_port, gateway)
        addresses = _candidate_lan_addresses()
        preferred = addresses[0] if addresses else "<IP-DE-LA-ROG>"
        print("", flush=True)
        print("=== Battle Lab LAN ===", flush=True)
        print(
            "Gateway autenticado activo. Battle Lab/Showdown siguen escuchando "
            "solo en 127.0.0.1 dentro de la ROG.",
            flush=True,
        )
        print(
            f"Puerto LAN único: {lan_args.lan_port} · destinos: "
            + ", ".join(map(str, ports)),
            flush=True,
        )
        if addresses:
            print("IPs detectadas de la ROG: " + ", ".join(addresses), flush=True)
        print("Token efímero (no lo guardes en Git/Notion):", flush=True)
        print(token, flush=True)
        print("", flush=True)
        print("En la otra PC ejecuta:", flush=True)
        print(
            "python -m battle_lab.lan_tunnel client "
            f"--server {preferred} --gateway-port {lan_args.lan_port} --ports "
            + " ".join(map(str, ports)),
            flush=True,
        )
        print("El cliente te pedirá el token sin mostrarlo en pantalla.", flush=True)
        print(
            f"Luego abre en esa PC: http://127.0.0.1:{lan_args.war_room_port}",
            flush=True,
        )
        print(
            "Si Windows pregunta por Firewall, permite Python solo en redes privadas.",
            flush=True,
        )
        print("======================", flush=True)
        print("", flush=True)
        return nana_runtime.main(remaining)
    finally:
        if gateway.poll() is None:
            gateway.terminate()
            with suppress(subprocess.TimeoutExpired):
                gateway.wait(timeout=5)
            if gateway.poll() is None:
                gateway.kill()
                with suppress(subprocess.TimeoutExpired):
                    gateway.wait(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
