"""Offline behavioral checks for the periodic M-C pipeline; no GPU or network."""
import copy
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from battle_lab import mc_refresh as pipeline
from battle_lab import mc_refresh_data as data
from battle_lab.mc_training import DEFAULT_FORMAT as FMT, DEFAULT_FORMAT_BO3 as BO3, atomic_json, sha256_file


def roster(tag):
    return tuple(sorted([f"mon{tag}{i}" for i in range(6)]))


def team(names, move="Protect"):
    return "\n\n".join(f"{name} @ Leftovers\nAbility: Pressure\nEVs: 4 HP / 252 Atk / 252 Spe\nJolly Nature\n- {move}\n- Tackle\n- Rest\n- Sleep Talk" for name in names) + "\n"


def log(a, b, rating=1400):
    return "\n".join([f"|player|p1|Alice|1|{rating}", "|player|p2|Bob|1|1400",
                      "|showteam|p1|sheet", "|showteam|p2|sheet",
                      *[f"|poke|p1|{name}, L50|" for name in a],
                      *[f"|poke|p2|{name}, L50|" for name in b], "|turn|1", "|win|Alice"])


class RefreshChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_timing_separates_active_work_from_pauses_and_preserves_interrupted_attempts(self):
        status = {"phaseAttempts": [
            {"stage": "rl", "startedAt": "2026-09-16T00:00:00+00:00", "heartbeatAt": "2026-09-16T00:00:10+00:00",
             "elapsedSeconds": 10, "state": "running", "measurementComplete": False}]}
        with patch.object(pipeline, "utc_now", return_value="2026-09-16T01:00:00+00:00"):
            second = pipeline.start_phase_timing(status, "rl")
        self.assertEqual(status["phaseAttempts"][0]["state"], "interrupted")
        second.update(elapsedSeconds=20, finishedAt="2026-09-16T01:00:20+00:00",
                      state="completed", measurementComplete=True)
        summary = pipeline.timing_summary(status)
        self.assertEqual(summary["observedActiveSeconds"], 30)
        self.assertEqual(summary["observedTrainingSeconds"], 30)
        self.assertEqual(summary["wallSeconds"], 3620)
        self.assertEqual(summary["phaseSeconds"], {"rl": 30})
        self.assertFalse(summary["measurementComplete"])
        self.assertFalse(pipeline.timing_summary({})["available"])

    def test_child_persists_failed_and_resumed_timings_without_using_eta_cache(self):
        run = self.root / "run"
        status = {"completedStages": [], "totalStages": 8}
        estimates = {"rl": 999999}
        processes = [SimpleNamespace(stdout=io.StringIO("worker done\n"), poll=lambda code=code: code, returncode=code)
                     for code in (1, 0)]
        with patch.object(pipeline.subprocess, "Popen", side_effect=processes):
            with self.assertRaisesRegex(RuntimeError, "código 1"):
                pipeline.run_child("rl", run, status, estimates)
            status = data.read_json(run / "status.json")
            self.assertEqual(status["phaseAttempts"][0]["state"], "failed")
            self.assertEqual(estimates["rl"], 999999)
            pipeline.run_child("rl", run, status, estimates)
        saved = data.read_json(run / "status.json")
        self.assertEqual([item["state"] for item in saved["phaseAttempts"]], ["failed", "completed"])
        summary = pipeline.timing_summary(saved)
        self.assertTrue(summary["measurementComplete"])
        self.assertLess(summary["observedActiveSeconds"], 5)
        self.assertEqual(estimates["rl"], saved["phaseAttempts"][-1]["elapsedSeconds"])

    def test_human_pilot_keeps_quality_gate_and_freezes_its_threshold(self):
        self.assertFalse(pipeline.bc_data_gate(1056, 9695)["bcEligible"])
        self.assertTrue(pipeline.bc_data_gate(1056, 9695, 9500)["bcEligible"])
        self.assertFalse(pipeline.bc_data_gate(999, 9695, 9500)["bcEligible"])
        self.assertFalse(pipeline.bc_data_gate(1056, 9499, 9500)["bcEligible"])
        with self.assertRaises(ValueError):
            pipeline.bc_data_gate(1056, 9695, 1)
        config = {"codeSha": "pilot", "bcMinTransitions": 9500}
        run = pipeline.select_run(self.root, config)
        with self.assertRaisesRegex(RuntimeError, "configuración"):
            pipeline.select_run(self.root, {**config, "bcMinTransitions": 10000}, run_id=run.name)
        self.assertEqual(data.read_json(run / "config.json"), config)

    def test_bc_small_and_uneven_blocks_use_every_example_without_oversized_batches(self):
        from battle_lab.mc_training import train_bc_block
        class Learner:
            batch_size = minibatch_size = 1024
            def __init__(self):
                self.trained, self.sizes = [], []
            def set_demonstrations(self, transitions):
                if len(transitions) < self.minibatch_size:
                    raise ValueError("fewer transitions than batch size")
                self.current = transitions
            def train(self, n_epochs):
                self.assert_complete_batch = len(self.current) == self.batch_size == self.minibatch_size
                if not self.assert_complete_batch or n_epochs != 1:
                    raise AssertionError("lost tail or repeated epoch")
                self.trained.extend(self.current)
                self.sizes.append(len(self.current))
        # This mirrors the failure in imitation's pinned make_data_loader.
        learner = Learner()
        with self.assertRaises(ValueError):
            learner.set_demonstrations(list(range(987)))
        for count in (1, 987, 1024, 1025, 2049, 9695):
            learner = Learner()
            stats = train_bc_block(learner, list(range(count)))
            self.assertEqual(learner.trained, list(range(count)))
            self.assertEqual(stats["transitions"], count)
            self.assertTrue(all(0 < n <= 1024 for n in learner.sizes))
        with self.assertRaises(ValueError):
            train_bc_block(Learner(), [])

    def legacy(self):
        source = self.root / "legacy"
        manifest = {"signatureLeakage": 0}
        for side, tags in (("train", ("a", "b")), ("holdout", ("c", "d"))):
            (source / side).mkdir(parents=True)
            names = []
            for i, tag in enumerate(tags):
                name = f"mc{i}.txt"
                (source / side / name).write_text(team(roster(tag)))
                names.append(name)
            manifest.update({side + "Files": names, side + "Teams": len(names)})
        atomic_json(source / "split_manifest.json", manifest)
        return source

    def test_pastes_require_real_complete_sets(self):
        full = team(roster("a"))
        self.assertTrue(data.complete_team(full))
        self.assertFalse(data.complete_team(full.replace("EVs:", "Unknown:")))
        self.assertFalse(data.complete_team(full.replace("- Protect\n", "")))
        self.assertFalse(data.complete_team(full + "\nPikachu"))

    def test_paste_sync_deduplicates_and_removes_stale_partial_output(self):
        output, cache = self.root / "teams", self.root / "cache"
        output.mkdir()
        (output / "mc999.txt").write_text("stale")
        entries = [{"Team ID": f"MC{i}", "Pokepaste": f"https://pokepast.es/{i:016x}"} for i in range(1, 4)]
        validated = []
        def fetch(url, **kwargs):
            return "fixture CSV" if "export?" in url else team(roster("a"))
        with patch.object(data, "fetch_text", side_effect=fetch), patch.object(data, "parse_vgcpastes_mc", return_value=entries):
            result = data.sync_teams(output=output, cache=cache, validate=validated.append, workers=2, minimum=1)
        self.assertEqual(result["usableTeams"], 1)
        self.assertEqual(len(validated), 1)
        self.assertEqual(len(list(output.glob("mc*.txt"))), 1)

    def test_partition_preserves_legacy_and_alternative_pastes_across_cycles(self):
        legacy = self.legacy()
        registry = self.root / "partitions.json"
        teams = self.root / "teams"
        teams.mkdir()
        for i, tag in enumerate(("a", "b", "c", "e", "f", "g", "h")):
            (teams / f"mc{i}.txt").write_text(team(roster(tag), "Detect"))
        first = data.snapshot_split(teams=teams, output=self.root / "s1", registry=registry, legacy_split=legacy)
        state = data.read_json(registry)
        self.assertEqual(state["assignments"][data.signature_key(roster("a"))], "train")
        self.assertEqual(state["assignments"][data.signature_key(roster("c"))], "holdout")
        second = data.snapshot_split(teams=teams, output=self.root / "s2", registry=registry, legacy_split=legacy)
        self.assertEqual(first["fileHashes"], second["fileHashes"])
        self.assertEqual(second["freshHoldoutTeams"], 0)
        self.assertEqual(data.read_json(registry)["assignments"], state["assignments"])
        sides = [{data.team_signature(p) for p in (self.root / "s2" / side).glob("*.txt")} for side in ("train", "holdout")]
        self.assertFalse(sides[0] & sides[1])

    def test_historical_mutation_and_overlap_fail(self):
        legacy = self.legacy()
        registry = self.root / "partitions.json"
        state = data.load_partitions(registry, legacy)
        atomic_json(registry, state)
        (legacy / "train" / "mc0.txt").write_text(team(roster("z")))
        with self.assertRaisesRegex(RuntimeError, "modificado"):
            data.load_partitions(registry, legacy)
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            data.assign_signature(state, roster("a"), "holdout")

    def test_snapshot_deduplicates_normalized_legacy_and_current_pastes(self):
        legacy = self.legacy()
        teams = self.root / "teams"
        teams.mkdir()
        for i, tag in enumerate(("a", "b", "c", "d")):
            (teams / f"mc{i}.txt").write_text(team(roster(tag)).replace("\n", "  \n"))
        result = data.snapshot_split(teams=teams, output=self.root / "split",
                                     registry=self.root / "partitions.json", legacy_split=legacy)
        self.assertEqual(result["trainTeams"], 2)
        self.assertEqual(result["holdoutTeams"], 2)

    def test_evaluation_view_fixes_duplicates_without_mutating_frozen_snapshot(self):
        from battle_lab.mc_refresh_eval import evaluation_corpus
        from battle_lab.mc_holdout_benchmark import load_holdout_corpus
        legacy = self.legacy()
        teams = self.root / "teams"
        teams.mkdir()
        for i, tag in enumerate(("a", "b")):
            (teams / f"mc{i}.txt").write_text(team(roster(tag)))
        split_root = self.root / "split"
        split = data.snapshot_split(teams=teams, output=split_root,
                                    registry=self.root / "partitions.json", legacy_split=legacy)
        name = split["holdoutFiles"][0]
        duplicate = "mc99999999999999999999.txt"
        (split_root / "holdout" / duplicate).write_text((split_root / "holdout" / name).read_text().replace("\n", " \n"))
        split["holdoutFiles"].append(duplicate)
        split["holdoutTeams"] += 1
        split["fileHashes"]["holdout"][duplicate] = sha256_file(split_root / "holdout" / duplicate)
        split["freshHoldoutFiles"] = [duplicate]
        atomic_json(split_root / "split_manifest.json", split)
        before = {str(p): sha256_file(p) for p in split_root.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(RuntimeError, "duplicado por contenido"):
            load_holdout_corpus(split_root / "holdout", split_root / "split_manifest.json", expected_count=3, battle_format=FMT)
        corpus, view = evaluation_corpus(self.root, FMT)
        self.assertEqual(len(corpus.teams), 2)
        self.assertEqual(len(view["removedDuplicates"]), 1)
        self.assertEqual(view["freshHoldoutTeams"], 0)
        self.assertEqual(view["fileHashes"]["holdout"],
                         {n: sha256_file(Path(view["holdoutDir"]) / n) for n in view["holdoutFiles"]})
        self.assertEqual(before, {str(p): sha256_file(p) for p in split_root.rglob("*") if p.is_file()})
        # On a second attempt, ordering and canonical IDs are identical.
        self.assertEqual(view, evaluation_corpus(self.root, FMT)[1])
        (split_root / "holdout" / duplicate).write_text("changed after freeze")
        with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
            evaluation_corpus(self.root, FMT)

    def recovery_fixture(self):
        run = self.root / "Refresh" / "runs" / "recoverable"
        run.mkdir(parents=True)
        initial, candidate = run / "initial.zip", run / "candidate.zip"
        initial.write_bytes(b"initial fixture")
        candidate.write_bytes(b"candidate fixture")
        config = {"root": str(self.root), "mode": "LIGHT", "device": "cuda", "profile": {"steps": 100},
                  "codeSha": "original-code", "runtimeVersions": {"python": "fixture"},
                  "champion": {"checkpoint": str(initial), "sha256": sha256_file(initial)}}
        atomic_json(run / "config.json", config)
        split_manifest = run / "split" / "split_manifest.json"
        atomic_json(split_manifest, {"fixture": "frozen split"})
        for stage in pipeline.STAGES[:-1]:
            payload = {"state": "completed"}
            if stage == "split":
                payload = pipeline.artifact_result(split_manifest, state="completed")
            if stage == "rl":
                payload = pipeline.artifact_result(candidate, state="completed", steps=100,
                                                    checkpoint=str(candidate), sha256=sha256_file(candidate))
            atomic_json(run / "phases" / (stage + ".json"), payload)
        return run, config, candidate

    def test_recovery_validates_only_evaluation_inputs_and_preserves_training_payloads(self):
        run, original, candidate = self.recovery_fixture()
        # Simulate the expensive trajectory payload that triggered the Colab delay.
        trajectory = run / "human" / "trajs" / "00000000.pkl"
        trajectory.parent.mkdir(parents=True)
        trajectory.write_bytes(b"training-only payload")
        manifest = run / "human" / "trajs_manifest.json"
        atomic_json(manifest, {"fixture": True})
        atomic_json(run / "phases" / "trajectories.json",
                    pipeline.artifact_result(manifest, state="completed", files=[trajectory]))
        # Its recorded hash is now stale. Evaluation recovery must not re-read it.
        trajectory.write_bytes(b"changed but irrelevant to frozen evaluation")
        before = {str(p): sha256_file(p) for p in run.rglob("*") if p.is_file()}
        recovered_run, config = pipeline.recovery_config(
            self.root, run.name, code_sha="fixed-evaluation",
            versions=original["runtimeVersions"], direct=True)
        self.assertEqual(recovered_run, run)
        self.assertEqual(config["codeSha"], "fixed-evaluation")
        recovery = config["evaluationRecovery"]
        self.assertEqual(recovery["trainingCodeSha"], "original-code")
        self.assertEqual(recovery["scope"], ["prepare", "evaluate"])
        self.assertEqual(recovery["validation"]["mode"], "evaluation-inputs-only")
        self.assertIn("trajectories", recovery["validation"]["skippedTrainingPayloads"])
        self.assertNotIn("original_champion_checkpoint", recovery["validation"]["verified"])
        self.assertEqual(before, {str(p): sha256_file(p) for p in run.rglob("*") if p.is_file()})
        candidate.write_bytes(b"tampered")
        with self.assertRaises(RuntimeError):
            pipeline.recovery_config(self.root, run.name, code_sha="fixed",
                                     versions=original["runtimeVersions"], direct=True)

    def test_recovery_rejects_changed_split_manifest_without_scanning_training_payloads(self):
        run, original, _ = self.recovery_fixture()
        (run / "split" / "split_manifest.json").write_text('{"changed": true}')
        with self.assertRaisesRegex(RuntimeError, "manifiesto congelado"):
            pipeline.recovery_config(self.root, run.name, code_sha="fixed",
                                     versions=original["runtimeVersions"], direct=True)

    def test_recovery_rejects_incomplete_training_or_changed_runtime(self):
        run, original, _ = self.recovery_fixture()
        with self.assertRaisesRegex(RuntimeError, "runtime cambió"):
            pipeline.recovery_config(self.root, run.name, code_sha="fixed", versions={"python": "different"})
        phase = data.read_json(run / "phases" / "rl.json")
        phase["steps"] = 99
        atomic_json(run / "phases" / "rl.json", phase)
        with self.assertRaisesRegex(RuntimeError, "entrenamiento no está completo"):
            pipeline.recovery_config(self.root, run.name, code_sha="fixed", versions=original["runtimeVersions"])

    def test_recovery_runs_only_prepare_and_evaluate_and_records_failure(self):
        run, original, _ = self.recovery_fixture()
        atomic_json(self.root / "Refresh" / "active_run.json", {"runId": run.name})
        before = {str(p): sha256_file(p) for p in run.rglob("*") if p.is_file()}
        for fail in (False, True):
            calls = []
            def child(stage, selected_run, status, durations, *, worker_config):
                calls.append(stage)
                self.assertEqual(selected_run, run)
                self.assertEqual(data.read_json(worker_config)["codeSha"], "fixed-eval")
                if fail and stage == "evaluate":
                    raise RuntimeError("evaluation interrupted")
            with patch.object(pipeline, "git_sha", return_value="fixed-eval"), \
                 patch.object(pipeline, "runtime_versions", return_value=original["runtimeVersions"]), \
                 patch.object(pipeline, "run_child", side_effect=child), \
                 patch.object(pipeline, "write_report") as report:
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "evaluation interrupted"):
                        pipeline.recover_evaluation(self.root)
                    report.assert_not_called()
                else:
                    pipeline.recover_evaluation(self.root)
                    report.assert_called_once()
            self.assertEqual(calls, ["prepare", "evaluate"])
            self.assertEqual(data.read_json(run / "status.json")["state"], "failed" if fail else "completed")
            self.assertEqual(before, {p: sha256_file(Path(p)) for p in before})

    def test_human_filter_excludes_both_sides_and_duplicate_logs(self):
        registry = self.root / "partitions.json"
        state = {"seed": 260913, "assignments": {}}
        for tag, side in (("a", "train"), ("b", "train"), ("c", "holdout")):
            data.assign_signature(state, roster(tag), side)
        atomic_json(registry, state)
        logs = {"ok": [1, log(roster("a"), roster("b"))],
                "same": [2, log(roster("a"), roster("b"))],
                "held-opponent": [3, log(roster("a"), roster("c"))],
                "held-player": [4, log(roster("c"), roster("a"))],
                "low": [5, log(roster("a"), roster("b"), 1000)]}
        atomic_json(self.root / "cache" / "battle_logs" / f"logs_{FMT}.json", logs)
        result = data.filter_training_logs(cache=self.root / "cache", output=self.root / "human" / "battle_logs", registry=registry)
        self.assertEqual(result["eligibleLogs"], 1)
        self.assertEqual(result["counts"]["holdout_matchup"], 2)
        self.assertEqual(result["counts"]["duplicate_log"], 1)
        self.assertEqual(result["counts"]["winner_rating"], 1)

    def test_refresh_checks_new_head_even_when_history_was_exhausted(self):
        cache = self.root / "cache"
        for fmt in (FMT, BO3):
            atomic_json(cache / "battle_logs" / f"logs_{fmt}.json", {fmt + "-1": [100, "historical"]})
            atomic_json(cache / "scrape_audit" / f"audit_{fmt}.json", {"seen": {}, "historyExhausted": True})
        queries = []
        def getter(url):
            if "search.json?" in url:
                q = parse_qs(urlparse(url).query)
                queries.append(q)
                fmt = q["format"][0]
                return [{"id": fmt + "-2", "uploadtime": 200}, {"id": fmt + "-1", "uploadtime": 100}]
            return {"uploadtime": 200, "log": log(roster("a"), roster("b"))}
        result = data.refresh_replays(cache=cache, legacy_data=self.root / "legacy", workers=2, max_pages=2,
                                     getter=getter, scraper=SimpleNamespace(can_distinguish_team_members=lambda *a: True))
        self.assertEqual(result["addedLogs"], 2)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(x["before"] == ["2000000001"] for x in queries))
        self.assertTrue(all(x["stopReason"] == "new_head_complete" for x in result["formats"].values()))

    def test_replay_page_budget_does_not_claim_exhaustion(self):
        def getter(url):
            if "search.json?" in url:
                fmt = parse_qs(urlparse(url).query)["format"][0]
                return [{"id": fmt + "-2", "uploadtime": 200}]
            return {"uploadtime": 200, "log": log(roster("a"), roster("b"))}
        result = data.refresh_replays(cache=self.root / "cache", legacy_data=self.root / "legacy", workers=1,
                                     max_pages=1, getter=getter, scraper=SimpleNamespace(can_distinguish_team_members=lambda *a: True))
        self.assertTrue(all(x["stopReason"] == "page_budget" for x in result["formats"].values()))
        audit = data.read_json(self.root / "cache" / "scrape_audit" / f"audit_{FMT}.json")
        self.assertFalse(audit.get("historyExhausted", False))

    def test_resume_fixes_code_and_config_but_allows_resource_telemetry_changes(self):
        config = {"codeSha": "abc", "workers": {"availableMemoryGiB": 8}, "seed": 1}
        first = pipeline.select_run(self.root, config)
        changed = copy.deepcopy(config)
        changed["workers"]["availableMemoryGiB"] = 16
        self.assertEqual(pipeline.select_run(self.root, changed), first)
        with self.assertRaisesRegex(RuntimeError, "configuración"):
            pipeline.select_run(self.root, {**config, "seed": 2})
        atomic_json(first / "status.json", {"state": "completed"})
        second = pipeline.select_run(self.root, config)
        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            pipeline.select_run(self.root, config, run_id="../escape")

    def test_modified_phase_inputs_are_rejected(self):
        manifest = self.root / "manifest.json"
        payload = self.root / "team.txt"
        atomic_json(manifest, {"fixture": True})
        payload.write_text("original")
        result = pipeline.artifact_result(manifest, files=[payload])
        pipeline.check_artifact(result)
        payload.write_text("modified")
        with self.assertRaisesRegex(RuntimeError, "modificados"):
            pipeline.check_artifact(result)

    def test_champion_is_selected_by_registry_and_sha_not_latest_zip(self):
        champion = self.root / "chosen.zip"
        champion.write_bytes(b"chosen fixture")
        (self.root / "newer.zip").write_bytes(b"newer fixture")
        info = {"id": "known", "format": FMT, "checkpoint": str(champion), "sha256": sha256_file(champion)}
        atomic_json(self.root / "Refresh" / "champion.json", info)
        self.assertEqual(pipeline.ensure_champion(self.root), info)
        champion.write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            pipeline.ensure_champion(self.root)
        (self.root / "Refresh" / "champion.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "productivo canónico"):
            pipeline.ensure_champion(self.root)

    def test_production_reference_is_exactly_the_selected_champion(self):
        champion_file = self.root / "champion.zip"
        champion_file.write_bytes(b"canonical production")
        historical_file = self.root / "historical.zip"
        historical_file.write_bytes(b"old light")
        champion = {"id": "current-production", "checkpoint": str(champion_file),
                    "sha256": sha256_file(champion_file), "format": FMT}
        atomic_json(self.root / "Refresh" / "champion.json", champion)
        # A stale legacy production registry must never override the canonical champion.
        atomic_json(self.root / "Refresh" / "production.json",
                    {"id": "old-production", "checkpoint": str(historical_file),
                     "sha256": sha256_file(historical_file), "format": FMT})
        reference = pipeline.production_reference(self.root)
        self.assertEqual(reference["id"], champion["id"])
        self.assertEqual(reference["sha256"], champion["sha256"])
        self.assertEqual(reference["checkpoint"], champion["checkpoint"])
        self.assertEqual(reference["source"], "champion-registry")
        self.assertEqual(reference["role"], "production")
        self.assertFalse(reference["liveDeploymentVerified"])
        champion_file.write_bytes(b"changed bytes")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            pipeline.production_reference(self.root)

    def test_direct_recovery_always_uses_current_canonical_production(self):
        run, original, _ = self.recovery_fixture()
        current = self.root / "current-production.zip"
        current.write_bytes(b"current production")
        champion = {"id": "current", "checkpoint": str(current),
                    "sha256": sha256_file(current), "format": FMT}
        atomic_json(self.root / "Refresh" / "champion.json", champion)
        captured = {}
        def child(stage, selected_run, status, durations, *, worker_config, status_root):
            if stage == "evaluate":
                captured.update(data.read_json(worker_config)["production"])
        with patch.object(pipeline, "git_sha", return_value="direct-code"), \
             patch.object(pipeline, "runtime_versions", return_value=original["runtimeVersions"]), \
             patch.object(pipeline, "run_child", side_effect=child), \
             patch.object(pipeline, "write_report"):
            pipeline.recover_evaluation(self.root, run.name, direct=True, battles=22)
        self.assertEqual(captured["sha256"], champion["sha256"])
        self.assertEqual(captured["checkpoint"], champion["checkpoint"])
        self.assertEqual(captured["source"], "champion-registry")

    def test_direct_recovery_preserves_original_reports_status_and_training(self):
        run, original, _ = self.recovery_fixture()
        atomic_json(run / "status.json", {"state": "completed"})
        atomic_json(run / "report.json", {"legacy": "keep"})
        atomic_json(run / "phases" / "evaluate.json", {"legacy": "keep"})
        before = {p: sha256_file(p) for p in run.rglob("*") if p.is_file()}
        calls = []
        def child(stage, selected_run, status, durations, *, worker_config, status_root):
            calls.append(stage)
            self.assertEqual(status_root, run / "direct_evaluation")
            config = data.read_json(worker_config)
            self.assertEqual(config["evaluationProtocol"], "direct-v1")
            self.assertEqual(config["battles"], 22)
            self.assertEqual(config["evaluationRecovery"]["trainingCodeSha"], "original-code")
            with patch.object(pipeline, "perform_stage", return_value={"directStage": stage}):
                pipeline.main(["--worker-stage", stage, "--worker-run", str(run),
                               "--worker-config", str(worker_config)])
            self.assertEqual(data.read_json(status_root / "phases" / (stage + ".json")), {"directStage": stage})
        with patch.object(pipeline, "git_sha", return_value="direct-code"), \
             patch.object(pipeline, "runtime_versions", return_value=original["runtimeVersions"]), \
             patch.object(pipeline, "production_reference", return_value={"sha256": "production"}), \
             patch.object(pipeline, "run_child", side_effect=child), \
             patch.object(pipeline, "write_report") as report:
            pipeline.recover_evaluation(self.root, run.name, direct=True, battles=22)
            self.assertEqual(report.call_args.kwargs["report_root"], run / "direct_evaluation")
        self.assertEqual(calls, ["prepare", "evaluate"])
        self.assertEqual(before, {p: sha256_file(p) for p in before})
        self.assertEqual(data.read_json(run / "direct_evaluation" / "status.json")["state"], "completed")

    def test_direct_matchups_use_distinct_policies_mirror_sides_and_resume(self):
        from battle_lab import mc_refresh_eval as evaluation
        from battle_lab import mc_training as training
        from battle_lab.team_corpus import TeamPairing
        run = self.root / "Refresh" / "runs" / "fixture"
        run.mkdir(parents=True)
        specs = {}
        for name in ("candidate", "production", "base"):
            path = run / (name + ".zip")
            path.write_bytes(name.encode())
            specs[name] = {"checkpoint": str(path), "sha256": sha256_file(path)}
        teams = [SimpleNamespace(id=name, team_text=name) for name in ("team-A", "team-B")]
        split = {"fileHashes": {"holdout": {"a": "hash-a", "b": "hash-b"}}, "sourceManifestSha256": "frozen",
                 "holdoutTeams": 2, "sourceHoldoutTeams": 2, "removedDuplicates": []}
        played, instances, loads, baseline_requests = [], [], [], []
        class Player:
            def __init__(self, *, account_configuration, team, policy=None, save_replays=None, **kwargs):
                self.username = account_configuration.username
                self.policy, self.team, self.replay_dir = policy, team, save_replays
                self.battles, self.stopped = {}, False
                self.ps_client = SimpleNamespace(stop_listening=self.stop)
                instances.append(self)
            async def stop(self):
                self.stopped = True
            def update_team(self, team):
                self.team = team
            def reset_battles(self):
                self.battles.clear()
            async def battle_against(self, other, n_battles):
                played.append((self.policy, other.policy, self.team, other.team))
                tag = "fixture-" + str(len(played))
                # Alpha always wins: candidate must get one win and one loss per pair.
                self.battles[tag] = SimpleNamespace(won=True, lost=False, finished=True, battle_tag=tag, turn=5)
                other.battles[tag] = SimpleNamespace(won=False, lost=True, finished=True, battle_tag=tag, turn=5)
                for player in (self, other):
                    if player.replay_dir:
                        (Path(player.replay_dir) / f"{player.username} - {tag}.html").write_text("replay")
        def loader(**kwargs):
            name = kwargs["checkpoint"].stem
            loads.append(name)
            return SimpleNamespace(policy=name, player_class=Player, metadata={"fixture": name})
        modules = {"poke_env": SimpleNamespace(AccountConfiguration=lambda name, _: SimpleNamespace(username=name),
                                               ServerConfiguration=lambda *args: args),
                   "poke_env.player": SimpleNamespace(SimpleHeuristicsPlayer=Player)}
        params = dict(vgc_root=self.root, showdown=self.root, run=run, production=specs["production"],
                      candidate=specs["candidate"], battles=2, seed=1, port=8000, device="cpu",
                      battle_format=FMT, code_sha="code", showdown_sha="showdown", runtime_versions={"poke-env": "pin"})
        def baseline_loader(path):
            baseline_requests.append(Path(path))
            return Path(specs["base"]["checkpoint"])
        with patch.dict(sys.modules, modules), \
             patch.object(training, "download_baseline", side_effect=baseline_loader), \
             patch.object(training, "VGC_BENCH_CHECKPOINT_SHA256", specs["base"]["sha256"]), \
             patch.object(evaluation, "evaluation_corpus", return_value=(SimpleNamespace(teams=teams), split)), \
             patch.object(evaluation, "alias_mc_runtime_catalogs", return_value={}), \
             patch.object(evaluation, "validate_team"), \
             patch.object(evaluation, "build_pairing_schedule", return_value=[TeamPairing(*teams)]), \
             patch.object(evaluation, "running_showdown", return_value=nullcontext()), \
             patch.object(evaluation.battle, "load_model_runtime", side_effect=loader):
            result = evaluation.evaluate_direct(**params)
            self.assertEqual(loads, ["candidate", "production", "base"])
            self.assertEqual(baseline_requests[0],
                             self.root.parent / "benchmark-cache" / "vgc-bench-ma-mb-100.zip")
            self.assertFalse(str(baseline_requests[0]).startswith(str(run)))
            self.assertEqual([p[:2] for p in played], [("candidate", "production"), ("production", "candidate"),
                              ("candidate", "base"), ("base", "candidate"), ("candidate", None), (None, "candidate")])
            self.assertTrue(all(p[2:] == ("team-A", "team-B") for p in played))
            self.assertTrue(all(p.stopped for p in instances))
            self.assertEqual(result["battles"], 6)
            self.assertEqual(set(result["comparison"]["opponents"]), set(evaluation.DIRECT_OPPONENTS))
            for record in result["comparison"]["opponents"].values():
                self.assertEqual((record["wins"], record["losses"], record["scorePercent"]), (1, 1, 50))
            for item in data.read_json(Path(result["comparisonFile"]))["items"]:
                self.assertTrue(Path(item["replayFile"]).is_file())
            self.assertEqual(evaluation.evaluate_direct(**params)["comparison"], result["comparison"])
            self.assertEqual(len(played), 6, "completed matches must be reused")
            different = evaluation.evaluate_direct(**{**params, "code_sha": "new-code"})
            self.assertNotEqual(different["comparisonFile"], result["comparisonFile"])
            self.assertEqual(len(played), 12, "another contract cannot reuse old results")
            chunk_path = next(Path(result["comparisonFile"]).parent.glob("chunks/production/*.json"))
            chunk = data.read_json(chunk_path)
            chunk["items"].pop()
            atomic_json(chunk_path, chunk)
            with self.assertRaisesRegex(RuntimeError, "incompleto"):
                evaluation.evaluate_direct(**params)
            Path(specs["production"]["checkpoint"]).write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "pesos cambiaron"):
                evaluation.evaluate_direct(**params)

    def test_promotion_requires_completed_observed_improvement(self):
        run = self.root / "Refresh" / "runs" / "fixture"
        atomic_json(run / "report.json", {"state": "completed", "verdict": "SIN_MEJORA"})
        with self.assertRaisesRegex(RuntimeError, "benchmark completo"):
            pipeline.promote(self.root, "fixture")

    def test_direct_report_and_selection_use_new_evaluation_and_preserve_legacy(self):
        run, original, candidate = self.recovery_fixture()
        champion = {**original["champion"], "id": "production", "format": FMT}
        atomic_json(self.root / "Refresh" / "champion.json", champion)
        atomic_json(run / "report.json", {"legacy": True, "verdict": "SIN_MEJORA"})
        atomic_json(self.root / "Refresh" / "latest_result.json", {"runId": "legacy"})
        report_dir = run / "direct_evaluation"
        comparison_file = run / "evaluation" / "direct-v1" / "fixture" / "comparison.json"
        records = {key: {"games": 4, "wins": 3, "losses": 1, "ties": 0,
                          "scorePercent": 75, "replays": str(run / "replays" / key)}
                   for key in ("production", "base", "simple-heuristics")}
        comparison = {"protocol": "direct-v1", "verdict": "pass", "opponents": records}
        atomic_json(comparison_file, comparison)
        comparison_file.with_suffix(".csv").write_text("opponent,wins,losses,ties,candidate_score_pct\nproduction,3,1,0,75\n")
        atomic_json(report_dir / "phases" / "evaluate.json", pipeline.artifact_result(
            comparison_file, comparisonFile=str(comparison_file), comparison=comparison,
            battles=12, holdoutView={"holdoutTeams": 2}))
        for key, value in {"teams": {"usableTeams": 5}, "replays": {"totalLogs": 20, "addedLogs": 1, "formats": {}},
                           "split": {"eligibleLogs": 10, "trainTeams": 3, "holdoutTeams": 2,
                                     "freshHoldoutTeams": 0, "filter": {"counts": {}}},
                           "trajectories": {"trajectories": 10, "transitions": 20, "bcEligible": False}}.items():
            atomic_json(run / "phases" / (key + ".json"), value)
        config = {**original, "champion": champion, "production": champion, "showdownSha": "pin"}
        frozen = [run / "config.json", run / "report.json", self.root / "Refresh" / "latest_result.json"]
        before = {p: sha256_file(p) for p in frozen}
        result = pipeline.write_report(run, config, {"state": "completed"}, report_root=report_dir)
        self.assertEqual(result["verdict"], "MEJORA_OBSERVADA")
        self.assertEqual(result["phases"]["evaluate"]["comparison"], comparison)
        self.assertIn("Candidato vs production", (report_dir / "report.txt").read_text())
        self.assertNotIn("Score global", (report_dir / "report.txt").read_text())
        self.assertEqual(before, {p: sha256_file(p) for p in frozen})
        self.assertEqual(pipeline.ensure_champion(self.root), champion)
        selected = pipeline.promote(self.root, run.name, direct=True)
        self.assertEqual(selected["sha256"], sha256_file(candidate))
        self.assertEqual(data.read_json(run / "promotion.json")["benchmarkReport"], str(report_dir / "report.json"))
        with self.assertRaisesRegex(RuntimeError, "modelo productivo cambió"):
            pipeline.promote(self.root, run.name, direct=True)

    def test_complete_report_and_explicit_promotion_use_actual_comparison(self):
        from battle_lab.mc_holdout_benchmark import compare_model_reports, BASELINE_SPECS
        run = self.root / "Refresh" / "runs" / "fixture"
        run.mkdir(parents=True)
        old, new = run / "old.zip", run / "new.zip"
        old.write_bytes(b"old fixture")
        new.write_bytes(b"candidate fixture")
        champion = {"id": "old", "checkpoint": str(old), "sha256": sha256_file(old), "format": FMT}
        atomic_json(self.root / "Refresh" / "champion.json", champion)
        def model(wins):
            return {"opponents": {s.id: {"scorePercent": wins*25, "wins": wins, "losses": 4-wins, "ties": 0} for s in BASELINE_SPECS},
                    "overall": {"scorePercent": wins*25},
                    "items": [{"benchmark": {"baselineId": s.id, "scheduleIndex": i},
                               "winnerAgent": "vgcBench" if i < wins else "baseline"}
                              for s in BASELINE_SPECS for i in range(4)]}
        comparison = compare_model_reports(model(2), model(3))
        self.assertEqual(comparison["verdict"], "pass")
        self.assertEqual(compare_model_reports(model(3), model(2))["verdict"], "fail")
        comparison_path = run / "comparison.json"
        atomic_json(comparison_path, comparison)
        phases = {
            "teams": {"usableTeams": 181},
            "replays": {"totalLogs": 10, "addedLogs": 2, "formats": {}},
            "split": {"trainTeams": 145, "holdoutTeams": 36, "freshHoldoutTeams": 0,
                      "eligibleLogs": 5, "filter": {"counts": {"eligibleLogs": 5}}},
            "trajectories": {"trajectories": 5, "transitions": 50, "bcEligible": False},
            "rl": pipeline.artifact_result(new, checkpoint=str(new), sha256=sha256_file(new)),
            "evaluate": pipeline.artifact_result(comparison_path, comparison=comparison, battles=24,
                                                  freshHoldout={"champion": {"games": 0, "teamsTested": 0, "scorePercent": None}}),
        }
        for name, value in phases.items():
            atomic_json(run / "phases" / (name + ".json"), value)
        config = {"root": str(self.root), "mode": "LIGHT", "champion": champion,
                  "codeSha": "fixture-code", "showdownSha": "fixture-showdown"}
        status = {"state": "completed", "phaseAttempts": [
            {"stage": "rl", "startedAt": "2026-09-16T00:00:00+00:00", "finishedAt": "2026-09-16T00:01:00+00:00",
             "elapsedSeconds": 60, "state": "completed", "measurementComplete": True}]}
        report = pipeline.write_report(run, config, status)
        self.assertEqual(report["verdict"], "MEJORA_OBSERVADA")
        self.assertEqual(report["timing"]["observedTrainingSeconds"], 60)
        self.assertIn("Aprendizaje BC + PPO registrado: 1m 00s", (run / "report.txt").read_text())
        self.assertEqual(pipeline.ensure_champion(self.root), champion)
        rows = list(csv.DictReader((run / "comparison.csv").read_text().splitlines()))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(float(row["delta_pp"]) == 25 for row in rows))
        self.assertTrue((run / "report.txt").read_text().strip())
        selected = pipeline.promote(self.root, "fixture")
        self.assertEqual(selected["sha256"], sha256_file(new))
        self.assertEqual(selected["parentSha256"], champion["sha256"])
        with self.assertRaisesRegex(RuntimeError, "champion cambió"):
            pipeline.promote(self.root, "fixture")


if __name__ == "__main__":
    unittest.main()
