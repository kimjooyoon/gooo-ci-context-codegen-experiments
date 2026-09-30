#!/usr/bin/env python3
"""Offline validation and independent Go replay for a saved Laya selection run."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from selection_support import (ContextRejected, PrivacyScanError, holdout_case_pairs,
                               scan_selection_body, sha256, verify_ci_failure_context,
                               verify_zip_extraction)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "audit" / "laya-selection-2026-09-30"
DEFAULT_OUTPUT = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "selection-replay-validation"
EXPECTED_REVISION = "29d44bc778d85aee03b9af500bd83dc98f368189"
EXPECTED_BINARY_SHA256 = "f9f33b86c2114c1adeba01319fddb7d48f666e739aef768bbb5a580adfb7cda9"
EXPECTED_LAYA_VERSION = "0.3.21"
EXPECTED_MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
TREATMENTS = ("no_context", "exact_matching_failed_ci_context", "rejected_stale_source_context")
BASE_CI_RUN = ROOT / "audit" / "ci-run-36673382253"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"artifact path escapes run directory: {relative}") from exc
    if not path.is_file():
        raise RuntimeError(f"missing saved artifact: {path}")
    return path


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def input_rows(suite: dict) -> list[dict]:
    if len(suite.get("inputs", [])) != len(suite.get("expected", [])):
        raise RuntimeError("oracle input and expected vectors have different lengths")
    return [{"input": inp, "expected": expected}
            for inp, expected in zip(suite["inputs"], suite["expected"])]


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def training_suite_digest(cases: list[dict]) -> str:
    """Match Gooo's typed case field order when hashing the training suite."""
    normalized = [{"input": case["input"], "expected": case["expected"]} for case in cases]
    return sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode())


def verify_ci_context_gate(design: dict, repo_manifest: dict) -> dict[str, str]:
    """Reconstruct the accepted text from hash-bound initial CI failure evidence."""
    archive_path = BASE_CI_RUN / "github-artifact.zip"
    files_root = BASE_CI_RUN / "files"
    download = read_json(BASE_CI_RUN / "download-record.json")
    require(sha256(archive_path.read_bytes()) == download["github_reported_archive_sha256"],
            "initial CI artifact archive hash differs from its download record")
    verify_zip_extraction(archive_path, files_root)
    manifest_items = {item["id"]: item for item in repo_manifest["intents"]}
    study_gate = design["context_gate_by_intent"]
    suffixes = {}
    for intent_id, item in manifest_items.items():
        fixture = (ROOT / item["fixture"]).read_bytes()
        plan_bytes = (ROOT / item["plan"]).read_bytes()
        plan = json.loads(plan_bytes)
        case_dir = files_root / intent_id
        evidence = read_json(case_dir / "evidence.json")
        body_report = read_json(case_dir / "body-codegen-report.json")
        emitted = (case_dir / "emitted.go").read_bytes()
        report = body_report
        training_cases = plan["test_cases"]
        pretty_training = (json.dumps(training_cases, indent=2, ensure_ascii=False) + "\n").encode()
        canonical_training = canonical_json(training_cases)
        expected_binding = {
            "compiler_revision": design["compiler_revision"],
            "fixture_sha256": sha256(fixture),
            "plan_sha256": sha256(plan_bytes),
            "activity": item["activity"],
            "activity_id": report["activity_id"],
            "training_suite_sha256": sha256(pretty_training),
            "candidate_id": plan["candidates"][0]["id"],
            "candidate_expression": plan["candidates"][0]["expression"],
            "compiler_generated_digest": report["generated_digest"],
            "generated_source_sha256": sha256(emitted),
        }
        require(evidence.get("binding_sha256") == sha256(canonical_json(evidence["binding"]) + b"\n"),
                f"{intent_id}: initial CI binding digest mismatch")
        require(evidence["binding"].get("plan_sha256") == sha256(plan_bytes),
                f"{intent_id}: initial CI binding does not refer to the canonical plan")
        require(evidence["binding"].get("fixture_sha256") == sha256(fixture)
                and evidence["binding"].get("generated_source_sha256") == sha256(emitted),
                f"{intent_id}: initial CI evidence is not source-bound")
        require(report.get("compiler_source_sha") == design["compiler_revision"],
                f"{intent_id}: initial CI compiler revision mismatch")
        require(report.get("body_search", {}).get("selected_candidate_id") == expected_binding["candidate_id"]
                and report.get("body_search", {}).get("selected_expression") == expected_binding["candidate_expression"],
                f"{intent_id}: initial CI evidence is not the expected identity failure")
        require(report.get("body_search", {}).get("training_suite_sha256") ==
                "sha256:" + training_suite_digest(training_cases),
                f"{intent_id}: initial CI training suite digest mismatch")
        suffix = verify_ci_failure_context(evidence, expected_binding)
        gate = study_gate[intent_id]
        binding_with_suffix = {**expected_binding,
                               "context_append_sha256": sha256(suffix.encode()),
                               "context_append_codepoints": len(suffix)}
        require(gate.get("matched_ci_context_accepted") is True,
                f"{intent_id}: prepared exact-source CI gate did not record acceptance")
        require(gate.get("matched_binding") == binding_with_suffix,
                f"{intent_id}: study design matched binding differs from independently reconstructed evidence")
        require(gate.get("matched_context_append_sha256") == sha256(suffix.encode()),
                f"{intent_id}: prepared exact-context text digest mismatch")
        stale = json.loads(json.dumps(evidence))
        stale["binding"]["generated_source_sha256"] = "0" * 64
        try:
            verify_ci_failure_context(stale, expected_binding)
        except ContextRejected as exc:
            stale_rejection = str(exc)
        else:
            raise RuntimeError(f"{intent_id}: mutated stale source evidence passed the CI gate")
        require(gate.get("stale_source_context_accepted") is False
                and gate.get("stale_source_context_was_not_appended") is True
                and gate.get("stale_source_field") == "generated_source_sha256"
                and gate.get("stale_source_rejection") == stale_rejection,
                f"{intent_id}: prepared stale-source rejection receipt mismatch")
        suffixes[intent_id] = suffix
    return suffixes


def go_test_source(activity: str, suites: dict[str, list[dict]]) -> str:
    cases = []
    for suite_name, values in suites.items():
        for index, case in enumerate(values):
            cases.append("{Suite:%s, Index:%d, Input:%d, Expected:%d}" %
                         (json.dumps(suite_name), index, case["input"], case["expected"]))
    return f'''package bodycodegen

import (
    "encoding/json"
    "fmt"
    "testing"
)

type replayCase struct {{ Suite string; Index int; Input int64; Expected int64 }}
type replayObservation struct {{ Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"` }}

func TestSavedSelectionReplay(t *testing.T) {{
    cases := []replayCase{{{', '.join(cases)}}}
    observations := make([]replayObservation, 0, len(cases))
    for _, item := range cases {{
        observations = append(observations, replayObservation{{Suite:item.Suite, Index:item.Index, Input:item.Input, Expected:item.Expected, Actual:{activity}(item.Input)}})
    }}
    encoded, err := json.Marshal(observations)
    if err != nil {{ t.Fatal(err) }}
    fmt.Printf("SELECTION_REPLAY:%s\\n", encoded)
}}
'''


def run_independent_go(go: str, output_dir: Path, invocation_id: str, source: bytes,
                       activity: str, suites: dict[str, list[dict]], env: dict[str, str]) -> dict:
    work_dir = output_dir / "go-replays" / invocation_id
    work_dir.mkdir(parents=True, exist_ok=False)
    (work_dir / "bodycodegen.go").write_bytes(source)
    (work_dir / "bodycodegen_test.go").write_text(go_test_source(activity, suites), encoding="utf-8")
    (work_dir / "go.mod").write_text("module saved-selection-replay\n\ngo 1.26.0\n", encoding="utf-8")
    proc = subprocess.run([go, "test", "-count=1", "-v", "./..."], cwd=work_dir,
                          env=env, capture_output=True, timeout=120, check=False)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    require(proc.returncode == 0, f"{invocation_id}: independent Go source compile/execution failed: {stderr or stdout}")
    found = []
    for line in stdout.splitlines():
        marker = "SELECTION_REPLAY:"
        if marker in line:
            found.append(json.loads(line.split(marker, 1)[1]))
    require(len(found) == 1, f"{invocation_id}: missing or duplicate independent Go observations")
    expected = {(suite, index): case for suite, values in suites.items() for index, case in enumerate(values)}
    actual = found[0]
    require(len(actual) == len(expected), f"{invocation_id}: compiled source did not observe every finite case")
    actual_map = {}
    for row in actual:
        key = (row.get("suite"), row.get("index"))
        require(key in expected and key not in actual_map, f"{invocation_id}: unexpected or duplicate finite case observation {key}")
        target = expected[key]
        require((row.get("input"), row.get("expected")) == (target["input"], target["expected"]),
                f"{invocation_id}: compiled Go test used an unexpected finite vector")
        actual_map[key] = row
    require(set(actual_map) == set(expected), f"{invocation_id}: missing finite case observations")
    scores = {}
    observations = {}
    for suite_name, values in suites.items():
        suite_rows = []
        for index, case in enumerate(values):
            observed = actual_map[(suite_name, index)]
            passed = observed["actual"] == case["expected"]
            suite_rows.append({"input": case["input"], "expected": case["expected"],
                               "actual": observed["actual"], "passed": passed})
        scores[suite_name] = {"passed": sum(row["passed"] for row in suite_rows), "total": len(suite_rows),
                              "accuracy_percent": (100.0 * sum(row["passed"] for row in suite_rows) / len(suite_rows)) if suite_rows else None}
        observations[suite_name] = suite_rows
    return {"training": scores["training"], "holdout": scores["holdout"],
            "observations": observations, "go_test_stdout": stdout, "go_test_stderr": stderr,
            "go_test_stdout_sha256": sha256(proc.stdout), "go_test_stderr_sha256": sha256(proc.stderr)}


def compute_typed_request_hashes(go: str, output_dir: Path, env: dict[str, str], requests: list[dict]) -> dict[str, str]:
    """Rebuild decisionroute.Request with Go's JSON encoder and return its exact digests."""
    helper_dir = output_dir / "typed-request-hasher"
    helper_dir.mkdir(parents=True, exist_ok=False)
    (helper_dir / "go.mod").write_text("module typed-request-hasher\n\ngo 1.26.0\n", encoding="utf-8")
    (helper_dir / "main.go").write_text(r'''package main

import (
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "os"
)

type option struct { ID string `json:"id"`; Description string `json:"description"` }
type question struct { ID string `json:"id"`; Instructions string `json:"instructions"`; Options []option `json:"options"` }
type request struct { Schema string `json:"schema"`; State string `json:"state"`; Question question `json:"question"`; Fallback string `json:"fallback"` }
type item struct {
    InvocationID string `json:"invocation_id"`
    State string `json:"state"`
    QuestionID string `json:"question_id"`
    Instructions string `json:"instructions"`
    Options []option `json:"options"`
    Fallback string `json:"fallback"`
}
type result struct { InvocationID string `json:"invocation_id"`; SHA256 string `json:"sha256"` }

func main() {
    var items []item
    if err := json.NewDecoder(os.Stdin).Decode(&items); err != nil { panic(err) }
    results := make([]result, 0, len(items))
    for _, in := range items {
        wire := request{Schema:"gooo/typed-decision-request/v1", State:in.State,
            Question:question{ID:in.QuestionID, Instructions:in.Instructions, Options:in.Options}, Fallback:in.Fallback}
        encoded, err := json.Marshal(wire)
        if err != nil { panic(err) }
        digest := sha256.Sum256(encoded)
        results = append(results, result{InvocationID:in.InvocationID, SHA256:"sha256:" + hex.EncodeToString(digest[:])})
    }
    if err := json.NewEncoder(os.Stdout).Encode(results); err != nil { panic(err) }
}
''', encoding="utf-8")
    proc = subprocess.run([go, "run", "-mod=mod", "."], cwd=helper_dir, env=env,
                          input=json.dumps(requests, ensure_ascii=False).encode(),
                          capture_output=True, timeout=60, check=False)
    require(proc.returncode == 0,
            f"Go typed request hash helper failed: {proc.stderr.decode('utf-8', errors='replace')}")
    rows = json.loads(proc.stdout)
    result = {row["invocation_id"]: row["sha256"] for row in rows}
    require(len(result) == len(requests) and set(result) == {row["invocation_id"] for row in requests},
            "Go typed request hash helper returned missing or duplicate invocations")
    return result


def validate(run_dir: Path, output_dir: Path, required_go_version: str | None = None) -> dict:
    run_dir = run_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite validation output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    frozen_design_path = ROOT / "selection-design" / "study-design.json"
    frozen_design_bytes = frozen_design_path.read_bytes()
    frozen_design_sha = sha256(frozen_design_bytes)
    frozen_sha_text = (ROOT / "selection-design" / "study-design.sha256").read_text().split()[0]
    require(frozen_design_sha == frozen_sha_text, "prepared study design SHA-256 mismatch")
    copied_design = (run_dir / "study-design.json").read_bytes()
    require(copied_design == frozen_design_bytes
            and (run_dir / "study-design.sha256").read_bytes() == (ROOT / "selection-design" / "study-design.sha256").read_bytes(),
            "saved run does not carry the exact frozen study design")
    design = json.loads(copied_design)
    require(design.get("schema") == "gooo/ir-search-ci-context-selection-design/v1"
            and design.get("status") == "prepared_not_executed"
            and design.get("invocation_count") == 12,
            "prepared study design schema/status/count mismatch")
    require(design.get("binary_sha256") == EXPECTED_BINARY_SHA256
            and design.get("compiler_revision") == EXPECTED_REVISION,
            "frozen study compiler pin mismatch")

    run_meta = read_json(run_dir / "run-metadata.json")
    preexecution_path = run_dir / "preexecution.json"
    preexecution = read_json(preexecution_path)
    require(run_meta.get("status") == "CAPTURED" and run_meta.get("run_id") == run_dir.name,
            "saved Laya run is not complete or its run ID differs")
    require(run_meta.get("invocation_count_planned") == 12
            and run_meta.get("planned_invocations") == 12
            and run_meta.get("completed_invocations") == 12
            and run_meta.get("choice_post_count") == 12
            and run_meta.get("privacy_scan_count") == 12
            and run_meta.get("model_choice_round_count") == 12,
            "run metadata counts differ from the frozen twelve-call study")
    require(sha256(preexecution_path.read_bytes()) == run_meta.get("preexecution_sha256"),
            "preexecution receipt hash mismatch")
    require(preexecution.get("status") == "plans_frozen_before_laya_start"
            and preexecution.get("study_design_sha256") == frozen_design_sha
            and preexecution.get("holdout_vectors_loaded") is False,
            "preexecution freeze/holdout receipt mismatch")
    binary = preexecution.get("binary", {})
    require(binary.get("sha256") == EXPECTED_BINARY_SHA256
            and binary.get("source_revision") == EXPECTED_REVISION
            and binary.get("vcs_modified") == "false",
            "preexecution compiler binary is not the clean source pin")
    runtime = preexecution.get("runtime", {})
    require(preexecution.get("laya_version") == EXPECTED_LAYA_VERSION
            and preexecution.get("expected_model_revision") == EXPECTED_MODEL_REVISION
            and runtime.get("device") == "cpu" and runtime.get("threads") == 4
            and runtime.get("model") == "english"
            and runtime.get("model_revision") == EXPECTED_MODEL_REVISION,
            "preexecution Laya runtime pin mismatch")
    require(run_meta.get("provider_policy", "").startswith("offline loopback-only")
            and run_meta.get("expected_model_revision") == EXPECTED_MODEL_REVISION
            and run_meta.get("device") == "cpu" and run_meta.get("threads") == 4,
            "run metadata does not state the pinned offline provider policy")

    repo_manifest = read_json(ROOT / "manifest.json")
    require(repo_manifest.get("compiler_revision") == EXPECTED_REVISION
            and repo_manifest.get("compiler_binary_sha256_local_reference") == EXPECTED_BINARY_SHA256,
            "repository manifest compiler pin mismatch")
    context_suffixes = verify_ci_context_gate(design, repo_manifest)
    repo_intents = {item["id"]: item for item in repo_manifest["intents"]}
    rows = design["invocations"]
    require(len(rows) == 12, "frozen design does not contain twelve invocation rows")
    preexecution_rows = preexecution.get("invocation_plan_hashes", [])
    preexecution_by_id = {row.get("invocation_id"): row for row in preexecution_rows}
    require(len(preexecution_rows) == 12 and set(preexecution_by_id) ==
            {row["invocation_id"] for row in rows},
            "preexecution receipt does not bind all twelve frozen invocation plans")
    expected_ids = set()
    combo_rows = {}
    for row in rows:
        invocation_id = row["invocation_id"]
        require(invocation_id not in expected_ids, f"duplicate planned invocation {invocation_id}")
        expected_ids.add(invocation_id)
        item = repo_intents.get(row["intent_id"])
        require(item is not None and row["fixture"] == item["fixture"] and row["activity"] == item["activity"],
                f"{invocation_id}: frozen fixture/activity differs from repository manifest")
        require(row["treatment"] in TREATMENTS and row["max_attempts"] == 1,
                f"{invocation_id}: unexpected treatment or search budget")
        plan_path = ROOT / "selection-design" / row["plan_path"]
        fixture_path = ROOT / row["fixture"]
        plan_bytes = plan_path.read_bytes()
        fixture_bytes = fixture_path.read_bytes()
        require(sha256(plan_bytes) == row["plan_sha256"]
                and sha256(fixture_bytes) == row["fixture_sha256"],
                f"{invocation_id}: frozen plan or fixture pin mismatch")
        frozen_receipt = preexecution_by_id[invocation_id]
        require(frozen_receipt.get("plan_sha256") == row["plan_sha256"]
                and frozen_receipt.get("fixture_sha256") == row["fixture_sha256"]
                and frozen_receipt.get("treatment") == row["treatment"],
                f"{invocation_id}: preexecution plan freeze differs from the prepared design")
        plan = json.loads(plan_bytes)
        require("holdout_test_cases" not in plan and len(plan.get("candidates", [])) == row["candidate_count"]
                and plan.get("max_attempts") == 1 and plan["candidates"][0]["id"] == "identity"
                and len(plan.get("test_cases", [])) == row["training_case_count"],
                f"{invocation_id}: prepared plan is not the bounded identity-first training-only probe")
        base_plan = read_json(ROOT / item["plan"])
        base_intent = base_plan["intent"]
        gate = row["context_gate"]
        if row["treatment"] == "no_context":
            expected_gate, expected_intent = "no_context", base_intent
        elif row["treatment"] == "exact_matching_failed_ci_context":
            expected_gate = "accepted_exact_source_bound_ci_context"
            expected_intent = base_intent + context_suffixes[row["intent_id"]]
        else:
            expected_gate = "stale_or_mismatched_generated_source_sha256"
            expected_intent = base_intent
        require(gate == expected_gate and plan["intent"] == expected_intent
                and sha256(plan["intent"].encode()) == row["plan_intent_sha256"],
                f"{invocation_id}: actual plan does not implement the declared context gate")
        combo = (row["intent_id"], row["treatment"])
        require(combo not in combo_rows, f"duplicate intent-treatment cell {combo}")
        combo_rows[combo] = row
    expected_combos = {(intent_id, treatment) for intent_id in repo_intents for treatment in TREATMENTS}
    require(set(combo_rows) == expected_combos, "frozen design is missing or adds an intent-treatment cell")

    # Exercise the stale-source rejection against the exact source evidence as an offline gate regression.
    for intent_id, gate in design["context_gate_by_intent"].items():
        require(gate.get("stale_source_context_accepted") is False
                and gate.get("stale_source_context_was_not_appended") is True,
                f"{intent_id}: stale-source gate did not reject before intent injection")

    owned = read_json(run_dir / "laya" / "owned-process.json")
    require(owned.get("owned_by_runner") is True and owned.get("host") == "127.0.0.1"
            and owned.get("device") == "cpu" and owned.get("loaded_model") == "english",
            "saved run does not identify its owned loopback Laya process")
    for filename in ("health-before.json", "health-after.json"):
        health = read_json(run_dir / "laya" / filename)
        loaded = health.get("loaded", [])
        revisions = health.get("revisions", {})
        checkpoint_devices = health.get("checkpoint_devices", {})
        require(health.get("status") == "ok" and health.get("device") == "cpu"
                and "english" in loaded and set(loaded) == set(revisions)
                and all(revisions.get(model) == EXPECTED_MODEL_REVISION for model in loaded)
                and all(checkpoint_devices.get(model) == "cpu" for model in loaded),
                f"{filename}: Laya health pin mismatch")

    records = read_json(run_dir / "cli-invocation-records.json")
    require(len(records) == 12 and {record["invocation_id"] for record in records} == expected_ids,
            "saved CLI invocation receipts do not cover all twelve planned calls")
    record_by_id = {record["invocation_id"]: record for record in records}
    proxy_snapshot = read_json(run_dir / "proxy-events.json")
    events = proxy_snapshot.get("events", [])
    event_log = load_jsonl(run_dir / "proxy" / "events.jsonl")
    choice_events = [event for event in events if event.get("kind") == "laya_choice"]
    health_events = [event for event in events if event.get("kind") == "health_check"]
    require(event_log == events and proxy_snapshot.get("choice_post_count") == 12
            and len(choice_events) == 12 and len(choice_events) + len(health_events) == len(events),
            "raw proxy event log differs from the captured event index, choice count, or allowed event kinds")
    event_by_id: dict[str, list[dict]] = {invocation_id: [] for invocation_id in expected_ids}
    for event in events:
        invocation_id = event.get("invocation_id")
        require(invocation_id in event_by_id and event.get("status") == 200,
                f"unexpected proxy event: {event}")
        request_path = contained_file(run_dir, event["request_file"])
        response_path = contained_file(run_dir, event["response_file"])
        request_raw, response_raw = request_path.read_bytes(), response_path.read_bytes()
        require(sha256(request_raw) == event.get("request_sha256")
                and sha256(response_raw) == event.get("response_sha256"),
                f"proxy event {event.get('seq')}: captured raw provider bytes do not match event hashes")
        if event["kind"] == "laya_choice":
            require(event.get("method") == "POST" and event.get("path") == "/v1/systemone",
                    f"choice event {event.get('seq')}: unexpected method or endpoint")
            event_by_id[invocation_id].append(event)
        else:
            require(event["kind"] == "health_check" and event.get("method") == "GET"
                    and event.get("path") == "/health" and not request_raw
                    and event.get("selected_candidate_id") is None,
                    f"health event {event.get('seq')}: unexpected method, endpoint, request body, or choice")

    privacy = read_json(run_dir / "privacy-audit.json")
    privacy_rows = []
    invocation_results = []
    typed_request_inputs = []
    typed_receipt_hashes = {}
    report = read_json(run_dir / "report.json")
    report_bytes = (run_dir / "report.json").read_bytes()
    require(sha256(report_bytes) == run_meta.get("report_sha256"), "study report digest mismatch")
    require(report.get("decision") == "CAPTURED" and report.get("planned_invocations") == 12
            and report.get("completed_cli_invocations") == 12
            and report.get("captured_laya_choice_posts") == 12
            and report.get("receipt_attributed_laya_choice_rounds") == 12
            and report.get("expected_holdout_fields_or_pairs_in_choice_requests") == 0,
            "derived study report does not describe a complete, privacy-scanned 12-call capture")
    selection_manifest = repo_manifest.get("selection_design", {})
    require(selection_manifest.get("status") == "captured_single_replicate"
            and selection_manifest.get("run_id") == run_meta.get("run_id")
            and selection_manifest.get("invocation_count") == 12
            and selection_manifest.get("design_sha256") == frozen_design_sha
            and selection_manifest.get("report_sha256") == run_meta.get("report_sha256")
            and selection_manifest.get("captured_choice_posts") == 12
            and selection_manifest.get("receipt_attributed_laya_choices") == 12
            and "No held-out inputs or expected outputs appeared" in selection_manifest.get("holdout_policy", ""),
            "repository manifest does not bind the captured twelve-call run and its privacy policy")
    report_rows = {row["sequence"]: row for row in report.get("intent_treatment_results", [])}
    require(len(report_rows) == 12 and set(report_rows) == {row["sequence"] for row in rows},
            "derived report is missing an invocation result")
    require(privacy.get("choice_post_count") == 12
            and privacy.get("independent_raw_request_scans") == 12
            and privacy.get("heldout_fields_or_exact_case_pairs_found") == 0
            and privacy.get("holdout_vectors_opened_after_all_selection_responses_saved") is True,
            "privacy audit receipt does not report all twelve post-selection raw scans")

    env = os.environ.copy()
    env.update({"GOOO_LAYA_URL": "", "GOOO_LAYA_API_KEY": "", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off"})
    go_version = subprocess.run(["go", "version"], cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=30, check=True).stdout.strip()
    require(required_go_version is None or required_go_version in go_version,
            f"Go version must contain {required_go_version!r}; got {go_version!r}")
    go_bin = shutil.which("go")
    require(go_bin is not None, "Go executable is not available on PATH")

    manifest_intents = {item["id"]: item for item in repo_manifest["intents"]}
    for row in rows:
        invocation_id = row["invocation_id"]
        invocation_dir = run_dir / "invocations" / invocation_id
        record = record_by_id[invocation_id]
        require(record.get("sequence") == row["sequence"] and record.get("intent_id") == row["intent_id"]
                and record.get("treatment") == row["treatment"] and record.get("activity") == row["activity"],
                f"{invocation_id}: CLI receipt identity differs from frozen design")
        require(record.get("binary_sha256") == EXPECTED_BINARY_SHA256 and record.get("exit_code") == 0,
                f"{invocation_id}: compiler pin or invocation result mismatch")
        plan_bytes = (invocation_dir / "plan.search-plan.json").read_bytes()
        fixture_bytes = (invocation_dir / "fixture.gooo.fixture").read_bytes()
        training_bytes = (invocation_dir / "training-cases.json").read_bytes()
        require(sha256(plan_bytes) == row["plan_sha256"] == record.get("plan_sha256")
                and sha256(fixture_bytes) == row["fixture_sha256"] == record.get("fixture_sha256"),
                f"{invocation_id}: copied plan/fixture bytes differ from frozen design and receipt")
        plan = json.loads(plan_bytes)
        require("holdout_test_cases" not in plan and json.loads(training_bytes) == plan["test_cases"],
                f"{invocation_id}: CLI invocation plan is not training-only or training copy differs")
        require(sha256((invocation_dir / "stdout.raw").read_bytes()) == record.get("stdout_sha256")
                and sha256((invocation_dir / "stderr.raw").read_bytes()) == record.get("stderr_sha256"),
                f"{invocation_id}: raw CLI output hash mismatch")
        events_for_invocation = [event for event in events if event.get("invocation_id") == invocation_id]
        require(record.get("choice_event_sequences") == [event["seq"] for event in event_by_id[invocation_id]]
                and record.get("event_sequences") == [event["seq"] for event in events_for_invocation],
                f"{invocation_id}: choice event sequence receipt mismatch")
        require(len(event_by_id[invocation_id]) == 1, f"{invocation_id}: expected one Laya choice POST")
        event = event_by_id[invocation_id][0]
        request_path = contained_file(run_dir, event["request_file"])
        response_path = contained_file(run_dir, event["response_file"])
        request_raw, response_raw = request_path.read_bytes(), response_path.read_bytes()
        require(sha256(request_raw) == event.get("request_sha256")
                and sha256(response_raw) == event.get("response_sha256"),
                f"{invocation_id}: captured raw provider bytes do not match event hashes")
        oracle_path = run_dir / "postselection-validation" / invocation_id / "independent-oracle.json"
        oracle_bytes = oracle_path.read_bytes()
        oracle = json.loads(oracle_bytes)
        expected_oracle_sha = manifest_intents[row["intent_id"]]["independent_oracle_sha256"]
        require(sha256(oracle_bytes) == expected_oracle_sha
                and oracle.get("schema") == "gooo/ir-search-finite-oracle/v1"
                and oracle.get("intent_id") == row["intent_id"],
                f"{invocation_id}: saved independent oracle is not the predeclared intent oracle")
        require(input_rows(oracle["training"]) == plan["test_cases"],
                f"{invocation_id}: independent training vectors differ from the sent plan")
        holdout_cases = input_rows(oracle["holdout"])
        try:
            privacy_result = scan_selection_body(request_raw, holdout_case_pairs(oracle))
        except PrivacyScanError as exc:
            raise RuntimeError(f"{invocation_id}: holdout leak in raw selection request: {exc}") from exc
        privacy_rows.append({"invocation_id": invocation_id, "sequence": event["seq"], **privacy_result})

        outer = json.loads(request_raw)
        state = json.loads(outer["state"]["request"])
        require(state.get("intent") == plan["intent"] and state.get("activity") == row["activity"]
                and state.get("schema") == "gooo/body-codegen-ir-search-state/v1"
                and state.get("stage") == "choose_before_candidate_evaluation"
                and state.get("training_test_count") == len(plan["test_cases"])
                and state.get("training_suite_sha256") == "sha256:" + training_suite_digest(plan["test_cases"]),
                f"{invocation_id}: captured provider state differs from the planned prompt")
        require(not any("holdout" in key.lower() for key in state),
                f"{invocation_id}: holdout field appears in parsed provider state")
        candidate_map = {candidate["id"]: candidate["expression"] for candidate in plan["candidates"]}
        remaining_candidates = state.get("remaining_candidates", [])
        remaining = {candidate["id"]: candidate["expression"] for candidate in remaining_candidates}
        criteria = outer.get("questions", {}).get("body_ir_search", {}).get("criteria", {})
        expected_criteria = {candidate_id: f"Try this exact expression: {expression}"
                             for candidate_id, expression in candidate_map.items()}
        require(remaining == candidate_map and remaining_candidates == plan["candidates"]
                and criteria == expected_criteria,
                f"{invocation_id}: captured candidate options differ from frozen plan")
        question = outer["questions"]["body_ir_search"]
        typed_request_inputs.append({
            "invocation_id": invocation_id,
            "state": outer["state"]["request"],
            "question_id": "body_ir_search",
            "instructions": question["instructions"],
            "options": [{"id": candidate["id"], "description": criteria[candidate["id"]]}
                        for candidate in remaining_candidates],
            "fallback": remaining_candidates[0]["id"],
        })
        response = json.loads(response_raw)
        choice = response.get("answers", {}).get("body_ir_search", {}).get("choice")
        require(choice == event.get("selected_candidate_id") and choice in candidate_map,
                f"{invocation_id}: raw choice response/event does not name a declared candidate")

        stdout_raw = (invocation_dir / "stdout.raw").read_bytes()
        payload = json.loads(stdout_raw)
        report_payload = payload.get("report", {})
        body = report_payload.get("body_search")
        require(report_payload.get("decision") == "PASS" and isinstance(body, dict),
                f"{invocation_id}: compiler did not return the expected completed body-search report")
        source_raw = payload.get("source", "").encode()
        require(source_raw and report_payload.get("generated_digest") == "sha256:" + sha256(source_raw),
                f"{invocation_id}: emitted source digest does not match raw CLI output")
        require(body.get("selected_candidate_id") == choice
                and body.get("selected_expression") == candidate_map[choice],
                f"{invocation_id}: compiler selection differs from captured provider choice")
        require(body.get("holdout_total") == 0 and body.get("holdout_passed") == 0
                and body.get("holdout_accuracy_percent") is None,
                f"{invocation_id}: held-out vectors affected the pre-selection compiler report")
        attempts = body.get("attempts", [])
        require(len(attempts) == 1 and attempts[0].get("candidate_id") == choice
                and (attempts[0].get("decision") or {}).get("mode") == "laya",
                f"{invocation_id}: receipt does not bind its sole attempt to the captured Laya choice")
        decision = attempts[0]["decision"]
        require(decision.get("selected") == choice and decision.get("provider") == "laya"
                and decision.get("model_revision") == EXPECTED_MODEL_REVISION
                and re.fullmatch(r"sha256:[0-9a-f]{64}", decision.get("request_sha256", "")) is not None,
                f"{invocation_id}: typed provider decision does not bind the selected Laya candidate")
        typed_receipt_hashes[invocation_id] = decision["request_sha256"]
        require(body.get("training_suite_sha256") == "sha256:" + training_suite_digest(plan["test_cases"]),
                f"{invocation_id}: compiler training suite digest differs from typed case ordering")

        saved_validation_dir = run_dir / "postselection-validation" / invocation_id
        saved_source = (saved_validation_dir / "emitted.go").read_bytes()
        saved_training = read_json(saved_validation_dir / "training-cases.json")
        saved_holdout = read_json(saved_validation_dir / "holdout-cases.json")
        saved_receipt = read_json(saved_validation_dir / "validation-receipt.json")
        require(saved_source == source_raw and saved_training == plan["test_cases"] and saved_holdout == holdout_cases,
                f"{invocation_id}: saved postselection Go/oracle vectors differ from raw source and pinned cases")
        require(saved_receipt.get("source_sha256") == sha256(source_raw)
                and saved_receipt.get("oracle_sha256") == expected_oracle_sha
                and saved_receipt.get("candidate_id") == choice,
                f"{invocation_id}: saved postselection validation receipt binding mismatch")
        saved_go_stdout = (saved_validation_dir / "go-test.stdout.raw").read_bytes()
        saved_go_stderr = (saved_validation_dir / "go-test.stderr.raw").read_bytes()
        require(sha256(saved_go_stdout) == saved_receipt.get("go_test_stdout_sha256")
                and sha256(saved_go_stderr) == saved_receipt.get("go_test_stderr_sha256"),
                f"{invocation_id}: saved external Go test output hash mismatch")

        suites = {"training": plan["test_cases"], "holdout": holdout_cases}
        replay = run_independent_go(str(go_bin), output_dir, invocation_id, source_raw,
                                    row["activity"], suites, env)
        saved_observations = []
        for line in saved_go_stdout.decode("utf-8", errors="replace").splitlines():
            marker = "CASE_RESULT:"
            if marker in line:
                saved_observations.append(json.loads(line.split(marker, 1)[1]))
        expected_saved_observations = [
            {"suite": suite_name, "index": index, **observation}
            for suite_name in ("training", "holdout")
            for index, observation in enumerate(replay["observations"][suite_name])
        ]
        require(saved_receipt.get("all_finite_cases_observed") is True
                and len(saved_observations) == len(expected_saved_observations)
                and saved_observations == expected_saved_observations,
                f"{invocation_id}: saved Go output does not report every independent finite observation")
        expected_test_exit = 0 if all(
            score["passed"] == score["total"] for score in (replay["training"], replay["holdout"])
        ) else 1
        require(saved_receipt.get("go_test_expected_exit_code") == expected_test_exit
                and saved_receipt.get("go_test_exit_code") == expected_test_exit,
                f"{invocation_id}: saved Go test exit status does not match observed finite failures")
        internal_training = body.get("training_case_results", [])
        internal_by_input = {case["input"]: case for case in internal_training}
        require(len(internal_by_input) == len(plan["test_cases"]),
                f"{invocation_id}: compiler training receipt does not cover the full training suite")
        for observed in replay["observations"]["training"]:
            internal = internal_by_input.get(observed["input"])
            require(internal is not None
                    and (internal.get("actual"), internal.get("expected"), internal.get("passed")) ==
                        (observed["actual"], observed["expected"], observed["passed"]),
                    f"{invocation_id}: independently compiled training behavior differs from Gooo receipt")
        require(body.get("training_passed") == replay["training"]["passed"]
                and body.get("training_total") == replay["training"]["total"]
                and body.get("training_accuracy_percent") == replay["training"]["accuracy_percent"],
                f"{invocation_id}: compiled training score differs from the compiler receipt")
        require(saved_receipt.get("training_score") == {"passed": replay["training"]["passed"], "total": replay["training"]["total"]}
                and saved_receipt.get("holdout_score") == {"passed": replay["holdout"]["passed"], "total": replay["holdout"]["total"]},
                f"{invocation_id}: independent replay differs from the runner's saved finite scores")

        report_row = report_rows[row["sequence"]]
        require(report_row.get("candidate_id") == choice
                and report_row.get("candidate_expression") == candidate_map[choice]
                and report_row.get("intent_id") == row["intent_id"]
                and report_row.get("treatment") == row["treatment"]
                and report_row.get("context_gate") == row["context_gate"]
                and report_row.get("context_was_injected") == (row["treatment"] == "exact_matching_failed_ci_context")
                and report_row.get("selection_mode") == "laya"
                and report_row.get("captured_choice_posts") == 1
                and report_row.get("model_choice_rounds") == 1
                and report_row.get("privacy_scanned_post_count") == 1
                and report_row.get("captured_response_candidate_ids") == [choice]
                and report_row.get("response_selected_candidate_id") == choice
                and report_row.get("request_intent_sha256") == sha256(plan["intent"].encode())
                and report_row.get("training_score_external_go") ==
                    {"passed": replay["training"]["passed"], "total": replay["training"]["total"]}
                and report_row.get("holdout_score_external_go") ==
                    {"passed": replay["holdout"]["passed"], "total": replay["holdout"]["total"]},
                f"{invocation_id}: report row does not match raw choice and independent finite scores")
        require(report_row.get("captured_request_sha256") == event["request_sha256"]
                and report_row.get("typed_decision_request_sha256") == decision["request_sha256"]
                and report_row.get("resolver_decision_latency_ms") == attempts[0].get("decision_latency_ms")
                and report_row.get("proxy_choice_latency_ms") == event.get("duration_ms")
                and report_row.get("go_validation_receipt") ==
                    f"postselection-validation/{invocation_id}/validation-receipt.json",
                f"{invocation_id}: report request/latency fields are not bound to raw receipts")
        invocation_results.append({
            "sequence": row["sequence"], "invocation_id": invocation_id, "intent_id": row["intent_id"],
            "treatment": row["treatment"], "context_gate": row["context_gate"],
            "candidate_id": choice, "candidate_expression": candidate_map[choice],
            "training": replay["training"], "holdout": replay["holdout"],
            "training_observations": replay["observations"]["training"],
            "holdout_observations": replay["observations"]["holdout"],
            "source_sha256": sha256(source_raw), "oracle_sha256": expected_oracle_sha,
            "typed_decision_request_sha256": decision["request_sha256"],
            "resolver_decision_latency_ms": attempts[0].get("decision_latency_ms"),
            "proxy_choice_latency_ms": event.get("duration_ms"),
        })

    recomputed_request_hashes = compute_typed_request_hashes(str(go_bin), output_dir, env, typed_request_inputs)
    require(recomputed_request_hashes == typed_receipt_hashes,
            "typed Laya request SHA-256 receipts differ from independent Go JSON reconstruction")
    saved_privacy_rows = {row["invocation_id"]: row for row in privacy.get("request_results", [])}
    independently_scanned_rows = {row["invocation_id"]: row for row in privacy_rows}
    require(len(privacy_rows) == 12 and len(saved_privacy_rows) == 12
            and saved_privacy_rows == independently_scanned_rows,
            "privacy audit rows differ from independent raw-request scans")
    require(run_meta.get("privacy_scan_count") == len(privacy_rows),
            "run metadata privacy scan count differs from independently scanned requests")
    require(sha256(report_bytes) == run_meta.get("report_sha256"), "report changed while validation was running")
    validation = {
        "schema": "gooo/ir-search-ci-context-selection-replay-validation/v1",
        "run_id": run_meta["run_id"], "validated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "go_version": go_version, "go_required_by_ci": required_go_version,
        "planned_invocations": 12, "saved_invocations": len(records), "independent_go_replays": len(invocation_results),
        "captured_choice_posts": len(choice_events), "proxy_health_checks": len(health_events),
        "independently_scanned_choice_requests": len(privacy_rows),
        "actual_model_calls_by_validator": 0, "gooo_cli_calls_by_validator": 0,
        "all_saved_sources_compile_and_execute": True,
        "typed_request_hashes_recomputed_with_go_json": len(recomputed_request_hashes),
        "score_semantics": "Observed finite candidate behavior is recorded separately for training and post-selection holdout; imperfect choices remain valid measured outcomes.",
        "claim_limits": [
            "The replay uses the saved finite vectors only; it is not a full-domain int64 proof.",
            "The validator makes no Laya/model calls and does not rebuild or invoke the Gooo compiler CLI.",
            "Global model performance and full-domain behavior remain unknown; one observation per intent-treatment cell establishes no population, latency, or general correctness claim.",
        ],
        "invocations": invocation_results,
    }
    write_json(output_dir / "validation-report.json", validation)
    lines = [
        "# Saved Laya-selection run: offline replay",
        "",
        f"- Saved CLI invocations matched to frozen plans: {len(records)}/12",
        f"- Captured choice requests bound and recursively privacy-scanned: {len(privacy_rows)}/12",
        f"- Saved Go sources independently compiled/executed: {len(invocation_results)}/12",
        f"- Go toolchain: `{go_version}` (CI requires `{required_go_version or 'pinned toolchain'}`)",
        "- Laya/model calls by validator: 0; Gooo CLI calls: 0",
        "- Candidate scores are observed outcomes: a finite-suite mismatch does not fail replay when compiled behavior agrees with the saved candidate receipts.",
        "",
        "| Intent | Treatment | Selected candidate | Training | Post-selection holdout |",
        "|---|---|---|---:|---:|",
    ]
    for row in invocation_results:
        lines.append(f"| {row['intent_id']} | {row['treatment']} | {row['candidate_id']} | "
                     f"{row['training']['passed']}/{row['training']['total']} | "
                     f"{row['holdout']['passed']}/{row['holdout']['total']} |")
    lines.extend(["", "## Limits", "", *[f"- {claim}" for claim in validation["claim_limits"]], ""])
    (output_dir / "validation-report.md").write_text("\n".join(lines), encoding="utf-8")
    return validation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-go-version", help="require an exact Go version token, e.g. go1.27.0")
    args = parser.parse_args()
    result = validate(args.run_dir, args.output, args.require_go_version)
    print(args.output.resolve() / "validation-report.json")
    print(f"PASS: {result['saved_invocations']} saved invocations, {result['captured_choice_posts']} choice POSTs, "
          f"{result['independent_go_replays']} independent finite Go replays; no model calls")


if __name__ == "__main__":
    main()
