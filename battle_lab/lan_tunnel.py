#!/usr/bin/env python3
"""Authenticated TCP tunnel for remote Battle Lab use over a private LAN.

Battle Lab deliberately keeps its existing services bound to 127.0.0.1. This
module exposes a *single* authenticated gateway port on the host (for example,
the ROG Ally) and recreates selected loopback ports on a second computer.

That preserves every existing Battle Lab/Showdown URL, including the classic
client's 127.0.0.1 references, without publishing the API, Showdown websocket,
or viewer directly on the LAN.

Typical host side (normally launched by the Nana LAN wrapper):

    python -m battle_lab.lan_tunnel gateway \
      --token <ephemeral-token> --allow-ports 11829 8765 8766 8767

Typical remote PC:

    python -m battle_lab.lan_tunnel client \
      --server 192.168.1.50 --ports 11829 8765 8766 8767

If --token is omitted, the client prompts without echo. The gateway can also
read BATTLE_LAB_LAN_TOKEN so the token does not need to appear in its process
arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hmac
import ipaddress
import os
import socket
from contextlib import suppress
from typing import Iterable, Mapping, Sequence


MAGIC = "BLT1"
DEFAULT_GATEWAY_PORT = 9876
DEFAULT_BATTLE_LAB_PORTS = (8765, 8766, 8767)
HANDSHAKE_TIMEOUT_SECONDS = 5.0
BUFFER_SIZE = 64 * 1024
MAX_HANDSHAKE_BYTES = 512
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _normalize_ports(values: Iterable[int]) -> tuple[int, ...]:
    rendered: list[int] = []
    seen: set[int] = set()
    for raw in values:
        value = int(raw)
        if not 1 <= value <= 65535:
            raise ValueError(f"Puerto inválido: {value}")
        if value not in seen:
            seen.add(value)
            rendered.append(value)
    if not rendered:
        raise ValueError("Se requiere al menos un puerto.")
    return tuple(rendered)


def peer_is_private(value: str) -> bool:
    """Allow loopback/private/link-local/CGNAT peers, reject public Internet peers."""

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in _CGNAT
    )


def build_handshake(token: str, target_port: int) -> bytes:
    if not token or any(character.isspace() for character in token):
        raise ValueError("El token LAN no puede estar vacío ni contener espacios.")
    port = _normalize_ports((target_port,))[0]
    return f"{MAGIC} {token} {port}\n".encode("utf-8")


def parse_handshake(payload: bytes) -> tuple[str, int] | None:
    if not payload or len(payload) > MAX_HANDSHAKE_BYTES:
        return None
    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    parts = text.split(" ")
    if len(parts) != 3 or parts[0] != MAGIC:
        return None
    token = parts[1]
    try:
        port = int(parts[2])
    except ValueError:
        return None
    if not token or any(character.isspace() for character in token):
        return None
    if not 1 <= port <= 65535:
        return None
    return token, port


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    with suppress(Exception):
        writer.close()
        await writer.wait_closed()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        with suppress(Exception):
            writer.write_eof()
            await writer.drain()


async def _relay(
    left_reader: asyncio.StreamReader,
    left_writer: asyncio.StreamWriter,
    right_reader: asyncio.StreamReader,
    right_writer: asyncio.StreamWriter,
) -> None:
    try:
        await asyncio.gather(
            _pipe(left_reader, right_writer),
            _pipe(right_reader, left_writer),
        )
    finally:
        await _close_writer(left_writer)
        await _close_writer(right_writer)


async def _gateway_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    token: str,
    allowed_ports: frozenset[int],
    allow_public_peers: bool,
) -> None:
    peer = writer.get_extra_info("peername")
    peer_host = str(peer[0]) if isinstance(peer, tuple) and peer else ""
    if not allow_public_peers and not peer_is_private(peer_host):
        writer.write(b"ERR peer-not-private\n")
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)
        return

    try:
        line = await asyncio.wait_for(reader.readline(), HANDSHAKE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, ConnectionError):
        await _close_writer(writer)
        return
    parsed = parse_handshake(line)
    if parsed is None:
        writer.write(b"ERR handshake\n")
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)
        return
    supplied_token, target_port = parsed
    if not hmac.compare_digest(supplied_token, token):
        writer.write(b"ERR auth\n")
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)
        return
    if target_port not in allowed_ports:
        writer.write(b"ERR port-not-allowed\n")
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)
        return

    try:
        target_reader, target_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", target_port),
            HANDSHAKE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, ConnectionError, OSError):
        writer.write(b"ERR upstream-unavailable\n")
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)
        return

    writer.write(b"OK\n")
    await writer.drain()
    await _relay(reader, writer, target_reader, target_writer)


async def start_gateway_server(
    *,
    bind: str,
    port: int,
    token: str,
    allowed_ports: Iterable[int],
    allow_public_peers: bool = False,
) -> asyncio.AbstractServer:
    normalized = frozenset(_normalize_ports(allowed_ports))
    if not token or len(token) < 16:
        raise ValueError("El token LAN debe tener al menos 16 caracteres.")

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _gateway_connection(
            reader,
            writer,
            token=token,
            allowed_ports=normalized,
            allow_public_peers=allow_public_peers,
        )

    return await asyncio.start_server(handler, bind, port)


async def _client_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    server: str,
    gateway_port: int,
    token: str,
    target_port: int,
) -> None:
    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(server, gateway_port),
            HANDSHAKE_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, ConnectionError, OSError):
        await _close_writer(writer)
        return

    try:
        remote_writer.write(build_handshake(token, target_port))
        await remote_writer.drain()
        response = await asyncio.wait_for(
            remote_reader.readline(), HANDSHAKE_TIMEOUT_SECONDS
        )
        if response != b"OK\n":
            await _close_writer(writer)
            await _close_writer(remote_writer)
            return
        await _relay(reader, writer, remote_reader, remote_writer)
    except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
        await _close_writer(writer)
        await _close_writer(remote_writer)


async def start_client_forwarders(
    *,
    server: str,
    gateway_port: int,
    token: str,
    mappings: Mapping[int, int],
    bind: str = "127.0.0.1",
) -> list[asyncio.AbstractServer]:
    if not token or len(token) < 16:
        raise ValueError("El token LAN debe tener al menos 16 caracteres.")
    if not mappings:
        raise ValueError("Se requiere al menos un mapeo local→remoto.")

    servers: list[asyncio.AbstractServer] = []
    try:
        for local_port, target_port in mappings.items():
            local = _normalize_ports((local_port,))[0]
            target = _normalize_ports((target_port,))[0]

            async def handler(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
                *,
                selected_target: int = target,
            ) -> None:
                await _client_connection(
                    reader,
                    writer,
                    server=server,
                    gateway_port=gateway_port,
                    token=token,
                    target_port=selected_target,
                )

            servers.append(await asyncio.start_server(handler, bind, local))
    except BaseException:
        for item in servers:
            item.close()
        await asyncio.gather(*(item.wait_closed() for item in servers), return_exceptions=True)
        raise
    return servers


def _socket_port(server: asyncio.AbstractServer) -> int:
    sockets = server.sockets or []
    if not sockets:
        return 0
    return int(sockets[0].getsockname()[1])


async def run_gateway(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("BATTLE_LAB_LAN_TOKEN", "")
    if not token:
        raise SystemExit(
            "Falta el token LAN: usa --token o la variable BATTLE_LAB_LAN_TOKEN."
        )
    allowed = _normalize_ports(args.allow_ports)
    server = await start_gateway_server(
        bind=args.bind,
        port=args.port,
        token=token,
        allowed_ports=allowed,
        allow_public_peers=args.allow_public_peers,
    )
    print(
        f"Battle Lab LAN gateway listo en {args.bind}:{_socket_port(server)} · "
        f"puertos permitidos: {', '.join(map(str, allowed))}",
        flush=True,
    )
    async with server:
        await server.serve_forever()
    return 0


async def run_client(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("BATTLE_LAB_LAN_TOKEN", "")
    if not token:
        token = getpass.getpass("Token LAN de Battle Lab: ")
    ports = _normalize_ports(args.ports)
    mappings = {port: port for port in ports}
    servers = await start_client_forwarders(
        server=args.server,
        gateway_port=args.gateway_port,
        token=token,
        mappings=mappings,
        bind="127.0.0.1",
    )
    print(
        "Túnel LAN activo. Esta PC ahora expone en loopback: "
        + ", ".join(f"127.0.0.1:{port}" for port in ports),
        flush=True,
    )
    print("Mantén esta terminal abierta mientras juegas. Ctrl+C para cerrar.", flush=True)
    try:
        await asyncio.gather(*(server.serve_forever() for server in servers))
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers), return_exceptions=True)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    gateway = subparsers.add_parser("gateway", help="Ejecutar en la ROG/host de Battle Lab")
    gateway.add_argument("--bind", default="0.0.0.0")
    gateway.add_argument("--port", type=int, default=DEFAULT_GATEWAY_PORT)
    gateway.add_argument("--token", default="")
    gateway.add_argument(
        "--allow-ports",
        type=int,
        nargs="+",
        default=list(DEFAULT_BATTLE_LAB_PORTS),
    )
    gateway.add_argument(
        "--allow-public-peers",
        action="store_true",
        help="Permitir IPs públicas. Desactivado por defecto y no recomendado.",
    )

    client = subparsers.add_parser("client", help="Ejecutar en la PC desde la que jugarás")
    client.add_argument("--server", required=True, help="IP privada/Tailscale de la ROG")
    client.add_argument("--gateway-port", type=int, default=DEFAULT_GATEWAY_PORT)
    client.add_argument("--token", default="")
    client.add_argument(
        "--ports",
        type=int,
        nargs="+",
        default=list(DEFAULT_BATTLE_LAB_PORTS),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.mode == "gateway":
            return asyncio.run(run_gateway(args))
        return asyncio.run(run_client(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
