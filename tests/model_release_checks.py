"""Checkpoint installation safety without loading a policy or requiring CUDA."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from battle_lab import model_release as release


class ModelReleaseChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime"
        self.active = self.runtime / "models" / release.ACTIVE_NAME
        self.receipt = self.active.parent / "active-model.json"
        self.source = self.root / "candidate.zip"
        self.make_zip(self.active, "old weights")
        self.make_zip(self.source, "new weights")
        self.old_sha = release.sha256_file(self.active)
        self.spec = {
            "id": "new-release", "label": "New model", "format": "test-format",
            "sha256": release.sha256_file(self.source), "bytes": self.source.stat().st_size,
            "fileName": self.source.name, "downloadUrl": "https://example.test/model",
        }

    @staticmethod
    def make_zip(path, weights):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("data", "{}")
            archive.writestr("policy.pth", weights)

    def test_install_is_reversible_idempotent_and_preserves_nana(self):
        nana = self.runtime / "nana" / "history.jsonl"
        nana.parent.mkdir()
        nana.write_text('"human history"\n')
        activation = self.active.parent / "activation.json"
        release.atomic_json(activation, {"expectedSha256": self.old_sha})
        installed = release.install_release(self.source, self.runtime, self.spec)
        self.assertFalse(activation.exists())
        self.assertEqual(release.sha256_file(self.active), self.spec["sha256"])
        backup = Path(installed["previous"]["checkpoint"])
        self.assertEqual(release.sha256_file(backup), self.old_sha)
        self.assertEqual(release.install_release(self.source, self.runtime, self.spec), installed)
        identity = release.checkpoint_identity(self.active, self.spec["sha256"])
        self.assertEqual(identity["modelId"], "new-release")
        restored = release.rollback(self.runtime)
        self.assertEqual(release.sha256_file(self.active), self.old_sha)
        self.assertEqual(restored["previous"]["sha256"], self.spec["sha256"])
        release.rollback(self.runtime)
        self.assertEqual(release.sha256_file(self.active), self.spec["sha256"])
        self.assertEqual(nana.read_text(), '"human history"\n')
        self.assertFalse((self.active.parent / "model-install.lock").exists())

    def test_bad_checksum_and_incomplete_archive_never_replace_active(self):
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            release.install_release(self.source, self.runtime, {**self.spec, "sha256": "0" * 64})
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr("data", "{}")
        incomplete = {**self.spec, "bytes": self.source.stat().st_size,
                      "sha256": release.sha256_file(self.source)}
        with self.assertRaisesRegex(RuntimeError, "estructura"):
            release.install_release(self.source, self.runtime, incomplete)
        self.assertEqual(release.sha256_file(self.active), self.old_sha)
        self.assertFalse(self.receipt.exists())

    def test_receipt_failure_restores_prior_bytes_and_receipt(self):
        old_receipt = {"id": "old", "sha256": self.old_sha}
        release.atomic_json(self.receipt, old_receipt)
        original = release.atomic_json
        failed = False

        def fail_once(path, payload):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("disk write failure")
            return original(path, payload)

        with patch.object(release, "atomic_json", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "disk write failure"):
                release.install_release(self.source, self.runtime, self.spec)
        self.assertEqual(release.sha256_file(self.active), self.old_sha)
        self.assertEqual(release.read_json(self.receipt), old_receipt)
        self.assertFalse((self.active.parent / "model-install.lock").exists())

    def test_download_suffix_is_accepted_and_stale_receipt_is_rejected(self):
        renamed = self.source.with_name("candidate (1).zip")
        self.source.rename(renamed)
        self.assertEqual(release.find_download(self.spec, self.root), renamed)
        release.install_release(renamed, self.runtime, self.spec)
        self.make_zip(self.active, "unverified replacement")
        with self.assertRaisesRegex(RuntimeError, "cambió"):
            release.checkpoint_identity(self.active, release.sha256_file(self.active))

    def test_live_verification_rejects_old_hash_and_records_loaded_model(self):
        release.install_release(self.source, self.runtime, self.spec)
        activation = self.active.parent / "activation.json"
        correct = {"ready": True, "checkpointSha256": self.spec["sha256"], "format": self.spec["format"]}
        for invalid in ({**correct, "checkpointSha256": self.old_sha},
                        {**correct, "format": "other-format"}, {**correct, "ready": False}):
            with patch.object(release.urllib.request, "urlopen", return_value=io.StringIO(json.dumps(invalid))):
                with self.assertRaises(RuntimeError):
                    release.verify_running_model(self.runtime, "http://127.0.0.1:8765")
            self.assertFalse(activation.exists())
        with patch.object(release.urllib.request, "urlopen", return_value=io.StringIO(json.dumps(correct))):
            proof = release.verify_running_model(self.runtime, "http://127.0.0.1:8765")
        self.assertEqual(proof, release.read_json(activation))
        self.assertEqual(proof["releaseId"], self.spec["id"])

    def test_running_service_and_concurrent_install_block_replacement(self):
        with patch.object(release.urllib.request, "urlopen", return_value=io.StringIO('{"checkpointExists":true}')):
            with self.assertRaisesRegex(RuntimeError, "Detén el runtime"):
                release.require_stopped("http://127.0.0.1:8765")
        lock = self.active.parent / "model-install.lock"
        lock.write_text("another process")
        with self.assertRaisesRegex(RuntimeError, "model-install.lock"):
            release.install_release(self.source, self.runtime, self.spec)
        self.assertEqual(release.sha256_file(self.active), self.old_sha)
        self.assertEqual(lock.read_text(), "another process")


if __name__ == "__main__":
    unittest.main()
