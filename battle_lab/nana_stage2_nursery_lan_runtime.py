"""Nana 2.3 Nursery live runtime on a trusted private LAN."""

from __future__ import annotations

from typing import Sequence

from battle_lab import nana_stage2_shadow_v21_lan_runtime as lan
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_nursery_runtime import install_nursery_service


def main(argv: Sequence[str] | None = None) -> int:
    lan_args, remaining = lan.parse_lan_args(argv)
    nana_args, remaining = parse_nana_args(remaining)
    install_nursery_service(profile_id=nana_args.nana_profile)

    from battle_lab import local_runtime

    lan.install_direct_lan(local_runtime)
    install_reusable_viewer(local_runtime)

    addresses = lan._candidate_lan_addresses()
    if lan_args.lan_address and lan_args.lan_address not in addresses:
        addresses.insert(0, lan_args.lan_address)

    print("", flush=True)
    print("=== Battle Lab LAN · Nana 2.3 Nursery LIVE ===", flush=True)
    print(
        "Nana ya puede ejecutar hasta 1 intervención near-LIGHT por BO1; "
        "todo lo demás cae a LIGHT.",
        flush=True,
    )
    print(
        "La segunda PC solo abre la URL Network de Vite; no ejecutes nada allí.",
        flush=True,
    )
    if addresses:
        print("IPs privadas detectadas: " + ", ".join(addresses), flush=True)
    print("API :8765 · Showdown :8766 · renderer :8767", flush=True)
    print(
        "Cada intervención real queda registrada para que Nana aprenda de su "
        "propia experiencia en partidas posteriores.",
        flush=True,
    )
    print("Fallback absoluto: LIGHT. Promoción de autonomía: NO automática.", flush=True)
    print("================================================", flush=True)
    print("", flush=True)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
