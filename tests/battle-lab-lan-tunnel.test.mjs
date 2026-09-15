import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const tunnel = path.join(root, "battle_lab", "lan_tunnel.py");
const launcher = path.join(root, "battle_lab", "nana_stage2_shadow_v21_lan_runtime.py");

test("LAN tunnel validates private peers, handshake and allowlisted ports", () => {
  const script = String.raw`
from battle_lab.lan_tunnel import build_handshake, parse_handshake, peer_is_private

assert peer_is_private('127.0.0.1')
assert peer_is_private('192.168.1.25')
assert peer_is_private('10.10.1.5')
assert peer_is_private('100.64.10.20')
assert not peer_is_private('8.8.8.8')

payload = build_handshake('abcdefghijklmnop', 8767)
assert parse_handshake(payload) == ('abcdefghijklmnop', 8767)
assert parse_handshake(b'garbage\n') is None
assert parse_handshake(b'BLT1 token nope\n') is None
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LAN tunnel performs an authenticated TCP round-trip without exposing target service", () => {
  const script = String.raw`
import asyncio
import socket
from contextlib import suppress
from battle_lab.lan_tunnel import start_client_forwarders, start_gateway_server


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def main():
    target_port = free_port()
    gateway_port = free_port()
    local_port = free_port()
    token = 'abcdefghijklmnopqrstuvwxyz123456'

    async def echo(reader, writer):
        data = await reader.read(1024)
        writer.write(data)
        await writer.drain()
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    echo_server = await asyncio.start_server(echo, '127.0.0.1', target_port)
    gateway = await start_gateway_server(
        bind='127.0.0.1',
        port=gateway_port,
        token=token,
        allowed_ports=[target_port],
    )
    forwarders = await start_client_forwarders(
        server='127.0.0.1',
        gateway_port=gateway_port,
        token=token,
        mappings={local_port: target_port},
    )
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', local_port)
        writer.write(b'nana-lan-roundtrip')
        await writer.drain()
        result = await asyncio.wait_for(reader.readexactly(len(b'nana-lan-roundtrip')), 3)
        assert result == b'nana-lan-roundtrip'
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    finally:
        for server in forwarders:
            server.close()
        gateway.close()
        echo_server.close()
        await asyncio.gather(
            *(server.wait_closed() for server in forwarders),
            gateway.wait_closed(),
            echo_server.wait_closed(),
            return_exceptions=True,
        )

asyncio.run(main())
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8", timeout: 15_000 });
});

test("Nana LAN launcher keeps the canonical runtime and exposes only one authenticated gateway", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /nana_stage2_shadow_v21_runtime/);
  assert.match(source, /battle_lab\.lan_tunnel/);
  assert.match(source, /BATTLE_LAB_LAN_TOKEN/);
  assert.match(source, /DEFAULT_GATEWAY_PORT/);
  assert.match(source, /127\.0\.0\.1:/);
  assert.doesNotMatch(source, /--host\s+0\.0\.0\.0/);
});

test("LAN modules compile", () => {
  for (const filename of [tunnel, launcher]) {
    execFileSync(python, ["-m", "py_compile", filename], { cwd: root, encoding: "utf8" });
  }
});
