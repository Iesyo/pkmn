"""Nana 2.2 LIGHT-critic runtime exposed on a trusted private LAN."""

from __future__ import annotations

from typing import Sequence

from battle_lab import nana_stage2_shadow_v21_lan_runtime as lan
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_shadow_v22_runtime import install_light_critic_service


def main(argv: Sequence[str] | None = None) -> int:
    lan_args, remaining = lan.parse_lan_args(argv)
    nana_args, remaining = parse_nana_args(remaining)
    install_light_critic_service(profile_id=nana_args.nana_profile)

    from battle_lab import local_runtime

    lan.install_direct_lan(local_runtime)
    install_reusable_viewer(local_runtime)

    addresses = lan._candidate_lan_addresses()
    if lan_args.lan_address and lan_args.lan_address not in addresses:
        addresses.insert(0, lan_args.lan_address)

    print("", flush=True)
    print(
        "=== Battle Lab LAN directo · Nana 2.2 LIGHT critic ===",
        flush=True,
    )
    print(
        "No ejecutes nada en la segunda PC: abre allí la URL Network que ya imprime Vite.",
        flush=True,
    )
    if addresses:
        print(
            "IPs privadas detectadas de la ROG: " + ", ".join(addresses),
            flush=True,
        )
    print(
        "Battle Lab API: :8765 · Showdown: :8766 · renderer nativo: :8767",
        flush=True,
    )
    print(
        "LIGHT sigue siendo la política real; Nana aprende trust observacional "
        "entre partidas con influence=0.0.",
        flush=True,
    )
    print(
        "Windows Firewall: permitir Python únicamente en redes Privadas.",
        flush=True,
    )
    print("No publicar 8765/8766/8767 en el router/Internet.", flush=True)
    print(
        "=====================================================",
        flush=True,
    )
    print("", flush=True)

    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
