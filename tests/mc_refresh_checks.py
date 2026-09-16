"""Offline behavioral checks for the periodic M-C pipeline; no GPU or network."""
import copy
import csv
import json
import tempfile
import unittest
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
        for stage in pipeline.STAGES[:-1]:
            payload = {"state": "completed"}
            if stage == "rl":
                payload = pipeline.artifact_result(candidate, state="completed", steps=100,
                                                    checkpoint=str(candidate), sha256=sha256_file(candidate))
            atomic_json(run / "phases" / (stage + ".json"), payload)
        return run, config, candidate

    def test_recovery_validates_and_preserves_original_training_contract(self):
        run, original, candidate = self.recovery_fixture()
        before = {str(p): sha256_file(p) for p in run.rglob("*") if p.is_file()}
        recovered_run, config = pipeline.recovery_config(self.root, run.name, code_sha="fixed-evaluation",
                                                         versions=original["runtimeVersions"])
        self.assertEqual(recovered_run, run)
        self.assertEqual(config["codeSha"], "fixed-evaluation")
        self.assertEqual(config["evaluationRecovery"]["trainingCodeSha"], "original-code")
        self.assertEqual(config["evaluationRecovery"]["scope"], ["prepare", "evaluate"])
        self.assertEqual(before, {str(p): sha256_file(p) for p in run.rglob("*") if p.is_file()})
        candidate.write_bytes(b"tampered")
        with self.assertRaises(RuntimeError):
            pipeline.recovery_config(self.root, run.name, code_sha="fixed", versions=original["runtimeVersions"])

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

    def test_promotion_requires_completed_observed_improvement(self):
        run = self.root / "Refresh" / "runs" / "fixture"
        atomic_json(run / "report.json", {"state": "completed", "verdict": "SIN_MEJORA"})
        with self.assertRaisesRegex(RuntimeError, "benchmark completo"):
            pipeline.promote(self.root, "fixture")

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
        report = pipeline.write_report(run, config, {"state": "completed"})
        self.assertEqual(report["verdict"], "MEJORA_OBSERVADA")
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
