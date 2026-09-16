"""Install a reviewed Battle Lab checkpoint, preserve rollback, verify live identity."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from battle_lab.mc_training import atomic_json, sha256_file, utc_now

MANIFEST = Path(__file__).with_suffix(".json")
DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
ACTIVE_NAME = "step-000196608.zip"  # Keep existing ROG launch commands compatible.
DEFAULT_ROOT = Path.home() / ".local" / "share" / "like-no-one-ever-was" / "battle-lab"


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def release_spec() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def checkpoint_identity(checkpoint: Path, digest: str, *, check_receipt: bool = True) -> dict:
    """Describe the bytes actually loaded, rejecting a stale installation receipt."""
    receipt = read_json(checkpoint.parent / "active-model.json")
    if check_receipt and receipt and Path(receipt["checkpoint"]).resolve() == checkpoint.resolve():
        if receipt["sha256"] != digest:
            raise RuntimeError("El checkpoint cambió después de instalarlo; reinstala el modelo verificado.")
        spec = receipt
    else:
        release = release_spec()
        spec = next((item for item in (release, release["previous"]) if item["sha256"] == digest),
                    {"id": "custom-" + digest[:12], "label": "Battle Lab M-C · checkpoint personalizado"})
    return {"modelId": spec["id"], "modelLabel": spec["label"], "checkpointSha256": digest}


def verify_checkpoint(path: Path, spec: dict) -> None:
    if not path.is_file():
        raise RuntimeError("No existe el checkpoint: " + str(path))
    if spec.get("bytes") is not None and path.stat().st_size != spec["bytes"]:
        raise RuntimeError("El tamaño del checkpoint no coincide con la versión promovida.")
    if sha256_file(path) != spec["sha256"]:
        raise RuntimeError("SHA-256 incorrecto: no se instalará este archivo.")
    with zipfile.ZipFile(path) as archive:
        if not {"data", "policy.pth"}.issubset(archive.namelist()):
            raise RuntimeError("El archivo no tiene la estructura de un checkpoint SB3.")


def copy_verified(source: Path, target: Path, digest: str) -> None:
    """Stage on the target filesystem and atomically replace only verified bytes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    os.close(fd)
    partial = Path(name)
    try:
        shutil.copyfile(source, partial)
        if sha256_file(partial) != digest:
            raise RuntimeError("El checkpoint cambió durante la copia.")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def find_model(spec: dict, folder: Path) -> Path:
    """Find reviewed bytes in the project's drop folder, regardless of ZIP name."""
    folder = folder.expanduser().resolve()
    paths = []
    if folder.is_dir():
        paths = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".zip"),
                       key=lambda path: (path.name != spec["fileName"], path.name))
    for path in paths:
        if path.stat().st_size == spec["bytes"] and sha256_file(path) == spec["sha256"]:
            return path
    detail = "Los ZIP encontrados no coinciden en tamaño o SHA-256. " if paths else "No hay archivos ZIP. "
    raise RuntimeError(f"{detail}Coloca {spec['fileName']} sin descomprimir en {folder}, "
                       f"o indica --source. Enlace: {spec['downloadUrl']}")


def install_release(source: Path, runtime_root: Path, spec: dict) -> dict:
    source, runtime_root = source.expanduser().resolve(), runtime_root.expanduser().resolve()
    verify_checkpoint(source, spec)
    models = runtime_root / "models"
    models.mkdir(parents=True, exist_ok=True)
    lock = models / "model-install.lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise RuntimeError("Existe model-install.lock: otra instalación está activa o fue interrumpida.") from None
    try:
        with handle:
            handle.write(str(os.getpid()))
        active, receipt_path = models / ACTIVE_NAME, models / "active-model.json"
        old_receipt = read_json(receipt_path)
        old_digest = sha256_file(active) if active.is_file() else None
        if old_digest == spec["sha256"] and old_receipt and old_receipt.get("sha256") == old_digest:
            return old_receipt
        previous = None
        if old_digest and old_digest != spec["sha256"]:
            backup = models / "backups" / (old_digest + ".zip")
            if not backup.is_file() or sha256_file(backup) != old_digest:
                copy_verified(active, backup, old_digest)
            label = checkpoint_identity(active, old_digest, check_receipt=False)
            previous = {"id": label["modelId"], "label": label["modelLabel"],
                        "sha256": old_digest, "checkpoint": str(backup), "bytes": backup.stat().st_size}
        elif old_receipt:
            previous = old_receipt.get("previous")
        receipt = {**spec, "checkpoint": str(active), "installedAt": utc_now(),
                   "previous": previous, "activation": "requires-runtime-restart"}
        try:
            copy_verified(source, active, spec["sha256"])
            atomic_json(receipt_path, receipt)
            (models / "activation.json").unlink(missing_ok=True)
        except BaseException:
            # Restore a usable prior installation if receipt publication fails.
            if previous and old_digest != spec["sha256"]:
                copy_verified(Path(previous["checkpoint"]), active, previous["sha256"])
            elif old_digest is None:
                active.unlink(missing_ok=True)
            if old_receipt:
                atomic_json(receipt_path, old_receipt)
            else:
                receipt_path.unlink(missing_ok=True)
            raise
        return receipt
    finally:
        lock.unlink(missing_ok=True)


def rollback(runtime_root: Path) -> dict:
    models = runtime_root.expanduser().resolve() / "models"
    receipt = read_json(models / "active-model.json") or {}
    previous = receipt.get("previous")
    if not previous:
        raise RuntimeError("No hay un checkpoint anterior registrado para restaurar.")
    source = Path(previous["checkpoint"]).resolve()
    if not source.is_relative_to(models / "backups"):
        raise RuntimeError("El respaldo registrado está fuera de la carpeta de modelos.")
    return install_release(source, runtime_root, {**previous, "format": receipt["format"], "schemaVersion": 1})


def verify_running_model(runtime_root: Path, service_url: str) -> dict:
    models = runtime_root.expanduser().resolve() / "models"
    receipt = read_json(models / "active-model.json")
    if not receipt:
        raise RuntimeError("Instala primero una versión verificable.")
    verify_checkpoint(Path(receipt["checkpoint"]), receipt)
    with urllib.request.urlopen(service_url.rstrip("/") + "/model-info", timeout=60) as response:
        info = json.load(response)
    if not info.get("ready") or info.get("checkpointSha256") != receipt["sha256"]:
        raise RuntimeError("El runtime todavía no cargó el modelo instalado. Reinícialo y vuelve a verificar.")
    if info.get("format") != receipt["format"]:
        raise RuntimeError("El runtime usa otra regulación.")
    proof = {"verifiedAt": utc_now(), "serviceUrl": service_url, "model": info,
             "expectedSha256": receipt["sha256"], "releaseId": receipt["id"]}
    atomic_json(models / "activation.json", proof)
    return proof


def require_stopped(service_url: str) -> None:
    try:
        with urllib.request.urlopen(service_url.rstrip("/") + "/health", timeout=2) as response:
            health = json.load(response)
    except (OSError, urllib.error.URLError):
        return
    if "checkpointExists" in health:
        raise RuntimeError("Detén el runtime Battle Lab con Ctrl+C antes de cambiar el modelo; después reinícialo.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rollback", "verify"))
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--models-dir", "--downloads", dest="models_dir", type=Path, default=DEFAULT_MODELS_DIR,
                        help="Carpeta de ZIP; por defecto, models/ dentro del proyecto.")
    parser.add_argument("--service-url", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            result = verify_running_model(args.runtime_root, args.service_url)
            print("Modelo activo verificado: " + result["expectedSha256"], flush=True)
        else:
            require_stopped(args.service_url)
            spec = release_spec()
            result = (rollback(args.runtime_root) if args.action == "rollback" else
                      install_release(args.source or find_model(spec, args.models_dir), args.runtime_root, spec))
            print("Instalado: " + result["label"] + " · " + result["sha256"], flush=True)
            if result.get("previous"):
                print("Respaldo: " + result["previous"]["checkpoint"], flush=True)
            print("Reinicia tu runtime habitual: la ruta --checkpoint se conserva. Después ejecuta model_release verify.", flush=True)
    except (RuntimeError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
