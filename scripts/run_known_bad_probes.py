#!/usr/bin/env python3
"""Generate and independently probe bounded, intentionally failing Gooo bodies."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "manifest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    write_bytes(path, data)
    return data


def parse_markers(output: bytes, marker: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in output.decode("utf-8", errors="replace").splitlines():
        match = re.search(rf"{re.escape(marker)}:(\{{.*\}})$", line)
        if match:
            rows.append(json.loads(match.group(1)))
    return rows


def test_source(activity: str, cases: list[dict[str, int]]) -> str:
    literal_cases = ", ".join(
        f"{{Input: {case['input']}, Expected: {case['expected']}}}" for case in cases
    )
    return f'''package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct {{ Input int64 `json:"input"`; Expected int64 `json:"expected"` }}
type probeObservation struct {{
	Index int `json:"index"`
	Input int64 `json:"input"`
	Expected int64 `json:"expected"`
	Actual int64 `json:"actual"`
	Passed bool `json:"passed"`
}}

func TestKnownBadTrainingProbe(t *testing.T) {{
	cases := []probeCase{{{literal_cases}}}
	for index, testCase := range cases {{
		actual := {activity}(testCase.Input)
		observation := probeObservation{{Index: index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}}
		encoded, err := json.Marshal(observation)
		if err != nil {{ t.Fatal(err) }}
		t.Logf("PROBE_ACTUAL:%s", encoded)
		if !observation.Passed {{ t.Errorf("PROBE_MISMATCH:%s", encoded) }}
	}}
}}
'''


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gooo-bin", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compiler-revision", required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST_PATH.read_text())
    expected_revision = manifest["compiler_revision"]
    if args.compiler_revision != expected_revision:
        raise SystemExit(f"compiler revision mismatch: {args.compiler_revision} != {expected_revision}")
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"refusing to overwrite nonempty output directory: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)

    binary = args.gooo_bin.resolve(strict=True)
    binary_sha = sha256(binary.read_bytes())
    env = os.environ.copy()
    # Explicitly disable any accidentally inherited provider configuration.
    env["GOOO_LAYA_URL"] = ""
    env["GOOO_LAYA_API_KEY"] = ""
    env["GOTOOLCHAIN"] = "local"
    env["GOPROXY"] = "off"
    env["GOSUMDB"] = "off"
    env["GOWORK"] = "off"

    run_started = time.time_ns()
    run_info: dict[str, object] = {
        "schema": "gooo/ci-context-probe-run/v1",
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "compiler_repository": manifest["compiler_repository"],
        "compiler_revision": args.compiler_revision,
        "compiler_binary_sha256": binary_sha,
        "go_version": subprocess.run(["go", "version"], check=True, capture_output=True, text=True).stdout.strip(),
        "provider_endpoint_configured": False,
        "provider_api_key_configured": False,
        "model_called": False,
        "training_only": True,
        "intent_count": len(manifest["intents"]),
        "expected_probe_mismatch_total": sum(item["expected_mismatch_count"] for item in manifest["intents"]),
    }
    write_json(args.out / "manifest.json", manifest)
    intent_evidence: list[dict[str, object]] = []

    for item in manifest["intents"]:
        intent_id = item["id"]
        case_dir = args.out / intent_id
        case_dir.mkdir(parents=True)
        plan_path = ROOT / item["plan"]
        fixture_path = ROOT / item["fixture"]
        plan_bytes = plan_path.read_bytes()
        fixture_bytes = fixture_path.read_bytes()
        plan = json.loads(plan_bytes)

        if "holdout_test_cases" in plan:
            raise SystemExit(f"{intent_id}: plan must contain training cases only")
        if plan.get("max_attempts") != 1 or not plan.get("candidates") or plan["candidates"][0]["id"] != "identity":
            raise SystemExit(f"{intent_id}: expected first-declared identity candidate with one attempt")
        if len(plan["test_cases"]) != item["training_case_count"]:
            raise SystemExit(f"{intent_id}: training case count differs from manifest")
        training_inputs = [case["input"] for case in plan["test_cases"]]
        if training_inputs != item["expected_identity_actuals"]:
            raise SystemExit(f"{intent_id}: known identity outputs differ from the training input list")
        if len(plan["test_cases"]) != item["expected_mismatch_count"]:
            raise SystemExit(f"{intent_id}: declared mismatch denominator is inconsistent")

        write_bytes(case_dir / "fixture.gooo.fixture", fixture_bytes)
        write_bytes(case_dir / "plan.search-plan.json", plan_bytes)
        training_bytes = (json.dumps(plan["test_cases"], indent=2, ensure_ascii=False) + "\n").encode()
        write_bytes(case_dir / "training-cases.json", training_bytes)

        cli_started = time.time_ns()
        cli = subprocess.run(
            [str(binary), "body-codegen", "--json", "--fill-search", str(plan_path),
             "--activity", item["activity"], str(fixture_path)],
            cwd=ROOT, env=env, capture_output=True, check=False,
        )
        cli_finished = time.time_ns()
        write_bytes(case_dir / "gooo.stdout.raw", cli.stdout)
        write_bytes(case_dir / "gooo.stderr.raw", cli.stderr)
        if cli.returncode != 0:
            raise SystemExit(f"{intent_id}: Gooo body-codegen exited {cli.returncode}; raw output saved")
        payload = json.loads(cli.stdout)
        report = payload["report"]
        body = report["body_search"]
        emitted = payload["source"].encode()
        write_bytes(case_dir / "emitted.go", emitted)
        write_json(case_dir / "body-codegen-report.json", report)

        expected_candidate = plan["candidates"][0]
        if report.get("decision") != "PASS" or report.get("compiler_source_sha") != expected_revision:
            raise SystemExit(f"{intent_id}: Gooo report decision or source revision mismatch")
        if report.get("repository_writes") != 0 or not report.get("typecheck_passed"):
            raise SystemExit(f"{intent_id}: generated body did not remain read-only/typechecked")
        if body.get("selected_candidate_id") != "identity" or body.get("selected_expression") != expected_candidate["expression"]:
            raise SystemExit(f"{intent_id}: deterministic candidate selection differs from the declared first candidate")
        if body.get("attempted_candidates") != 1 or body.get("evaluated_candidates") != 1:
            raise SystemExit(f"{intent_id}: bounded one-attempt protocol was not observed")
        if body.get("candidate_count") != len(plan["candidates"]) or body.get("untested_candidates") != len(plan["candidates"]) - 1:
            raise SystemExit(f"{intent_id}: untried candidate count differs from the bounded search plan")
        if body.get("provider_operations", 0) != 0 or body.get("training_total") != len(plan["test_cases"]):
            raise SystemExit(f"{intent_id}: provider-free or training-only receipt invariant failed")
        if body.get("holdout_total") != 0 or body.get("holdout_case_results"):
            raise SystemExit(f"{intent_id}: a nonempty extra case set appeared in the Gooo receipt")
        if body.get("training_passed") != 0 or body.get("training_accuracy_percent") != 0:
            raise SystemExit(f"{intent_id}: the declared known-bad probe unexpectedly passed training")
        if body.get("original_source_digest") != f"sha256:{sha256(fixture_bytes)}":
            raise SystemExit(f"{intent_id}: compiler fixture digest differs from the checked-in fixture")
        if report.get("generated_digest") != f"sha256:{sha256(emitted)}":
            raise SystemExit(f"{intent_id}: generated source digest differs from emitted bytes")

        attempt = body["attempts"][0]
        if attempt.get("selection_method") != "deterministic_fallback" or attempt.get("typecheck_passed") is not True:
            raise SystemExit(f"{intent_id}: expected provider-free deterministic selection and a typed candidate")
        decision = attempt.get("decision") or {}
        if decision.get("mode") != "deterministic_fallback" or decision.get("provider") != "deterministic":
            raise SystemExit(f"{intent_id}: chooser receipt is not the no-provider deterministic fallback")
        canonical_training = json.dumps(plan["test_cases"], separators=(",", ":"), ensure_ascii=False).encode()
        if body.get("training_suite_sha256") != f"sha256:{sha256(canonical_training)}":
            raise SystemExit(f"{intent_id}: compiler training-suite digest differs from the declared cases")
        if [r.get("actual") for r in body.get("training_case_results", [])] != item["expected_identity_actuals"]:
            raise SystemExit(f"{intent_id}: Gooo training score does not match the declared identity behavior")

        temp_dir = case_dir / "go-test-package"
        temp_dir.mkdir()
        write_bytes(temp_dir / "emitted.go", emitted)
        (temp_dir / "go.mod").write_text("module ci-context-probe\n\ngo 1.26.0\n")
        generated_test = test_source(item["activity"], plan["test_cases"]).encode()
        write_bytes(temp_dir / "probe_test.go", generated_test)
        # The worktree itself is retained in the artifact for exact test provenance.
        write_bytes(case_dir / "probe_test.go", generated_test)
        write_bytes(case_dir / "go.mod", (temp_dir / "go.mod").read_bytes())

        go_started = time.time_ns()
        go_test = subprocess.run(
            ["go", "test", "-count=1", "-v", "./..."],
            cwd=temp_dir, env=env, capture_output=True, check=False,
        )
        go_finished = time.time_ns()
        write_bytes(case_dir / "go-test.stdout.raw", go_test.stdout)
        write_bytes(case_dir / "go-test.stderr.raw", go_test.stderr)
        observations = parse_markers(go_test.stdout, "PROBE_ACTUAL")
        mismatches = parse_markers(go_test.stdout, "PROBE_MISMATCH")
        expected_observations = [
            {"index": index, "input": case["input"], "expected": case["expected"],
             "actual": actual, "passed": actual == case["expected"]}
            for index, (case, actual) in enumerate(zip(plan["test_cases"], item["expected_identity_actuals"]))
        ]
        expected_mismatches = [observation for observation in expected_observations if not observation["passed"]]
        normalized_observations = sorted(observations, key=lambda row: int(row["index"]))
        normalized_mismatches = sorted(mismatches, key=lambda row: int(row["index"]))
        if go_test.returncode != manifest["probe_policy"]["expected_test_exit_code"]:
            raise SystemExit(f"{intent_id}: Go test returned {go_test.returncode}; expected intentional test exit 1")
        if normalized_observations != expected_observations or normalized_mismatches != expected_mismatches:
            raise SystemExit(f"{intent_id}: raw Go test observations/mismatches differ from the declared training probe")
        if len(normalized_mismatches) != item["expected_mismatch_count"]:
            raise SystemExit(f"{intent_id}: expected mismatch count differs from the manifest")
        combined_test_output = go_test.stdout + go_test.stderr
        if b"build failed" in combined_test_output.lower() or b"undefined:" in combined_test_output.lower():
            raise SystemExit(f"{intent_id}: Go emitted a build failure rather than an expected test mismatch")
        if b"--- FAIL: TestKnownBadTrainingProbe" not in combined_test_output or b"setup failed" in combined_test_output.lower():
            raise SystemExit(f"{intent_id}: the intentional training test did not produce its expected failure marker")
        if [r.get("actual") for r in body["training_case_results"]] != [r["actual"] for r in normalized_observations]:
            raise SystemExit(f"{intent_id}: independent Go behavior differs from Gooo training evaluator")

        binding = {
            "compiler_revision": expected_revision,
            "compiler_binary_sha256": binary_sha,
            "intent_id": intent_id,
            "activity": item["activity"],
            "activity_id": report["activity_id"],
            "fixture_sha256": sha256(fixture_bytes),
            "plan_sha256": sha256(plan_bytes),
            "training_suite_sha256": sha256(training_bytes),
            "candidate_id": expected_candidate["id"],
            "candidate_expression": expected_candidate["expression"],
            "compiler_source_digest": report["source_digest"],
            "compiler_program_digest": report["program_digest"],
            "compiler_generated_digest": report["generated_digest"],
            "generated_source_sha256": sha256(emitted),
            "test_harness_sha256": sha256(generated_test),
            "test_output_sha256": sha256(go_test.stdout),
        }
        binding_sha = sha256((json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n").encode())
        evidence = {
            "schema": "gooo/ci-context-failure-evidence/v1",
            "result": "EXPECTED_TRAINING_MISMATCHES_REPRODUCED",
            "probe_exit_code": go_test.returncode,
            "expected_probe_exit_code": manifest["probe_policy"]["expected_test_exit_code"],
            "training_case_count": len(plan["test_cases"]),
            "training_pass_count": sum(1 for row in normalized_observations if row["passed"]),
            "training_mismatch_count": len(normalized_mismatches),
            "training_observations": normalized_observations,
            "raw_mismatch_records": normalized_mismatches,
            "binding": binding,
            "binding_sha256": binding_sha,
            "cli_stdout_sha256": sha256(cli.stdout),
            "cli_stderr_sha256": sha256(cli.stderr),
            "go_test_stdout_sha256": sha256(go_test.stdout),
            "go_test_stderr_sha256": sha256(go_test.stderr),
            "timing_unix_ns": {
                "body_codegen_started": cli_started,
                "body_codegen_finished": cli_finished,
                "go_test_started": go_started,
                "go_test_finished": go_finished,
            },
            "note": "External Go test evidence is finite to this saved training suite; it is not a full-domain correctness claim.",
        }
        write_json(case_dir / "evidence.json", evidence)
        intent_evidence.append({"intent_id": intent_id, "evidence_path": f"{intent_id}/evidence.json",
                               "binding_sha256": binding_sha, "training_case_count": len(plan["test_cases"]),
                               "mismatch_count": len(normalized_mismatches)})

    run_info["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    run_info["elapsed_ns"] = time.time_ns() - run_started
    run_info["intents"] = intent_evidence
    run_info["status"] = "PASS_EXPECTED_FAILURE_EVIDENCE"
    write_json(args.out / "run.json", run_info)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (AssertionError, KeyError, json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"probe evidence generation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
