#!/usr/bin/env python3
"""Independently replay and audit the frozen pinned-context study offline."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from selection_support import (PrivacyScanError, holdout_case_pairs,
                               scan_selection_body, sha256, verify_ci_failure_context,
                               verify_zip_extraction)


ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = ROOT / "pinned-context-design"
BASE_CI_RUN = ROOT / "audit" / "ci-run-36673382253"
DEFAULT_RUN = ROOT / "audit" / "pinned-compact-context-2026-09-30"
DEFAULT_OUTPUT = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "pinned-context-independent-replay"

# These values describe the native experiment binary. Keep the source and binary
# pins together so they can be replaced as one unit if the frozen source changes.
EXPECTED_SOURCE_REVISION = "bb5c1ec2f81cbfb17ac6fb2f7a9e1d7b67168e7f"
EXPECTED_BINARY_SHA256 = "47b9f3bd1b365d18771ba36b0a2b472b139fdb6a08b404697e188478dce38c6e"
EXPECTED_GO_VERSION = "go1.27.1"
EXPECTED_LAYA_VERSION = "0.3.21"
EXPECTED_MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
MODELS = ("english", "multilingual")
TREATMENTS = ("legacy_no_feedback", "compact_no_feedback", "compact_external_feedback")
INTENTS = ("clamp", "absolute", "piecewise", "compound-precedence")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.write_bytes(raw)
    return raw


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"artifact path escapes its root: {relative}") from exc
    if not path.is_file():
        raise RuntimeError(f"missing saved artifact: {path}")
    return path


def canonical_training(cases: list[dict]) -> bytes:
    normalized = [{"input": case["input"], "expected": case["expected"]} for case in cases]
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()


def case_rows(suite: dict) -> list[dict]:
    inputs, expected = suite.get("inputs", []), suite.get("expected", [])
    require(isinstance(inputs, list) and isinstance(expected, list) and len(inputs) == len(expected),
            "oracle input and expected vectors have different lengths")
    return [{"input": inp, "expected": exp} for inp, exp in zip(inputs, expected)]


def verify_source_ci_and_feedback(manifest: dict, design: dict, run_dir: Path) -> dict[str, dict]:
    """Rebuild the source-CI failure binding and compare every saved feedback byte."""
    archive_path = BASE_CI_RUN / "github-artifact.zip"
    download = read_json(BASE_CI_RUN / "download-record.json")
    archive_bytes = archive_path.read_bytes()
    require(sha256(archive_bytes) == download.get("github_reported_archive_sha256"),
            "source CI archive differs from its download receipt")
    require(download.get("workflow_conclusion") == "success" and download.get("model_calls") == 0
            and download.get("all_plans_training_only") is True,
            "source CI artifact is not the successful model-free training-only run")
    extraction = verify_zip_extraction(archive_path, BASE_CI_RUN / "files")
    require(extraction["archive_entries"] > 0, "source CI archive has no bound files")

    source_path = run_dir / "source-provenance.json"
    source = read_json(source_path)
    require(source.get("schema") == "gooo/pinned-context-source-provenance/v1"
            and source.get("ci_archive_sha256") == sha256(archive_bytes)
            and source.get("ci_workflow_run_id") == download.get("workflow_run_id")
            and source.get("ci_workflow_commit") == download.get("workflow_commit"),
            "saved source provenance is not tied to the original CI artifact")
    feedback_by_intent = {}
    manifest_items = {item["id"]: item for item in manifest["intents"]}
    require(set(manifest_items) == set(INTENTS), "manifest intent set differs from the pinned experiment")
    for intent_id, item in manifest_items.items():
        case_dir = BASE_CI_RUN / "files" / intent_id
        evidence = read_json(case_dir / "evidence.json")
        report = read_json(case_dir / "body-codegen-report.json")
        emitted = (case_dir / "emitted.go").read_bytes()
        fixture = (ROOT / item["fixture"]).read_bytes()
        plan_bytes = (ROOT / item["plan"]).read_bytes()
        plan = json.loads(plan_bytes)
        cases = plan["test_cases"]
        expected_binding = {
            "compiler_revision": manifest["compiler_revision"],
            "fixture_sha256": sha256(fixture),
            "plan_sha256": sha256(plan_bytes),
            "activity": item["activity"],
            "activity_id": report["activity_id"],
            "training_suite_sha256": sha256((json.dumps(cases, ensure_ascii=False, indent=2) + "\n").encode()),
            "candidate_id": "identity",
            "candidate_expression": "input",
            "compiler_generated_digest": report["generated_digest"],
            "generated_source_sha256": sha256(emitted),
        }
        require(evidence.get("binding_sha256") == sha256(
            (json.dumps(evidence["binding"], ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()),
            f"{intent_id}: source CI evidence binding digest mismatch")
        require(report.get("compiler_source_sha") == manifest["compiler_revision"]
                and report.get("body_search", {}).get("selected_candidate_id") == "identity"
                and report.get("generated_digest") == "sha256:" + sha256(emitted),
                f"{intent_id}: source CI compiler receipt mismatch")
        ci_feedback_suffix = verify_ci_failure_context(evidence, expected_binding)
        rows = evidence.get("training_observations")
        require(isinstance(rows, list) and rows and all(row.get("passed") is False for row in rows)
                and len(rows) == evidence.get("training_case_count") == evidence.get("training_mismatch_count"),
                f"{intent_id}: source CI does not contain a complete set of actual training failures")
        for case, observed in zip(cases, rows):
            require((case["input"], case["expected"]) == (observed.get("input"), observed.get("expected")),
                    f"{intent_id}: source CI observation differs from the frozen training case")
        saved = read_json(run_dir / "feedback" / f"{intent_id}.json")
        expected_feedback = {
            "source_digest": "sha256:" + sha256(fixture),
            "training_suite_sha256": "sha256:" + sha256(canonical_training(cases)),
            "candidate_id": "identity",
            "observations": [{key: row[key] for key in ("input", "expected", "actual", "passed")} for row in rows],
        }
        require(saved == expected_feedback, f"{intent_id}: native feedback differs from exact source-CI training evidence")
        row = source.get("intents", {}).get(intent_id, {})
        for key, value in {
            "intent_id": intent_id,
            "activity": item["activity"],
            "fixture_sha256": sha256(fixture),
            "original_fixture_source_digest": "sha256:" + sha256(fixture),
            "plan_sha256": sha256(plan_bytes),
            "training_suite_sha256_typed": "sha256:" + sha256(canonical_training(cases)),
            "candidate_id": "identity",
            "training_case_count": len(cases),
            "training_mismatch_count": len(rows),
        }.items():
            require(row.get(key) == value, f"{intent_id}: source provenance field {key} is not reconstructed from source CI")
        require(len(ci_feedback_suffix) <= 900, f"{intent_id}: bounded source CI context exceeded its bound")
        feedback_by_intent[intent_id] = saved
    return feedback_by_intent


def verify_design(run_dir: Path) -> tuple[dict, dict, dict, dict]:
    design_path = DESIGN_DIR / "study-design.json"
    design_bytes = design_path.read_bytes()
    design_sha = sha256(design_bytes)
    expected_sha = (DESIGN_DIR / "study-design.sha256").read_text(encoding="ascii").split()[0]
    require(design_sha == expected_sha, "frozen study design SHA-256 mismatch")
    require((run_dir / "study-design.json").read_bytes() == design_bytes
            and (run_dir / "study-design.sha256").read_bytes() == (DESIGN_DIR / "study-design.sha256").read_bytes(),
            "captured run does not carry the exact frozen study design")
    design = json.loads(design_bytes)
    require(design.get("schema") == "gooo/pinned-compact-context-study-design/v1"
            and design.get("status") == "frozen_before_laya_calls"
            and design.get("study_id") == "pinned-compact-context-2026-09-30",
            "frozen study identity or status mismatch")
    compiler = design.get("compiler", {})
    require(compiler.get("source_revision") == EXPECTED_SOURCE_REVISION
            and compiler.get("binary_sha256") == EXPECTED_BINARY_SHA256
            and compiler.get("clean_vcs_modified") is False,
            "frozen native compiler pin mismatch")
    laya = design.get("laya", {})
    require(laya.get("package_version") == EXPECTED_LAYA_VERSION
            and laya.get("model_revision") == EXPECTED_MODEL_REVISION
            and laya.get("device") == "cpu" and laya.get("threads") == 4
            and laya.get("offline_only") is True,
            "frozen Laya package, checkpoint, or runtime pin mismatch")
    cache_binding_rel = design.get("local_model_cache_binding")
    require(isinstance(cache_binding_rel, str), "frozen design has no local model-cache binding")
    cache_binding = read_json(contained_file(DESIGN_DIR, cache_binding_rel))
    require(cache_binding.get("schema") == "gooo/pinned-context-local-model-cache-binding/v1"
            and cache_binding.get("model_repository") == "convaiinnovations/laya"
            and cache_binding.get("model_revision") == EXPECTED_MODEL_REVISION
            and cache_binding.get("laya_version") == EXPECTED_LAYA_VERSION
            and cache_binding.get("claim_scope") ==
            "Local cache content binding only; does not establish remote model authenticity.",
            "local model-cache binding does not name the pinned Laya checkpoint/runtime")
    cache_files = cache_binding.get("cached_snapshot_files", [])
    runtime_files = cache_binding.get("laya_runtime_source_files", [])
    require(cache_files and runtime_files
            and all(isinstance(item.get("sha256"), str) and len(item["sha256"]) == 64
                    and all(char in "0123456789abcdef" for char in item["sha256"])
                    and isinstance(item.get("bytes"), int) and item["bytes"] > 0
                    for item in cache_files + runtime_files)
            and all(not Path(item.get("path", "")).is_absolute() and ".." not in Path(item.get("path", "")).parts
                    for item in cache_files)
            and sum(item["bytes"] for item in cache_files) == cache_binding.get("cached_snapshot_total_bytes"),
            "local model-cache file inventory has malformed path/hash/size entries")
    counts = design.get("design_counts", {})
    require(counts.get("measured_calls") == 72 and counts.get("warmup_calls") == 2
            and counts.get("measured_per_factorial_cell") == 3
            and counts.get("candidate_choice_attempts_per_invocation") == 1,
            "frozen study denominators differ from the 72 + 2 design")
    plan_rows = design.get("plans", [])
    require(len(plan_rows) == 74, "frozen plan set must contain all 72 measured calls and 2 warmups")
    ids = [row.get("invocation_id") for row in plan_rows]
    require(None not in ids and len(set(ids)) == 74, "frozen study has missing or duplicate invocation IDs")
    measured = [row for row in plan_rows if row.get("phase") == "measured"]
    warmups = [row for row in plan_rows if row.get("phase") == "warmup"]
    require(len(measured) == 72 and len(warmups) == 2, "frozen measured/warmup phase denominators mismatch")
    factors = Counter((row.get("intent_id"), row.get("treatment"), row.get("provider_model"), row.get("replicate"))
                      for row in measured)
    expected_cells = {(intent, treatment, model, replicate)
                      for intent in INTENTS for treatment in TREATMENTS for model in MODELS for replicate in (1, 2, 3)}
    require(set(factors) == expected_cells and all(count == 1 for count in factors.values()),
            "frozen plan set is not the complete 4×3×2×3 design")
    require(all(row.get("intent_id") == "clamp" and row.get("treatment") == "compact_no_feedback"
                and row.get("replicate") == 0 and row.get("provider_model") in MODELS for row in warmups),
            "warmup rows do not match the two declared compact/no-feedback model warmups")
    for row in plan_rows:
        require(row.get("provider_model") in MODELS and row.get("model_revision") == EXPECTED_MODEL_REVISION,
                f"{row.get('invocation_id')}: provider model or checkpoint pin mismatch")
        expected_profile = "" if row["treatment"] == "legacy_no_feedback" else "compact"
        expected_feedback = row["treatment"] == "compact_external_feedback"
        require(row.get("prompt_profile", "") == expected_profile
                and row.get("external_training_feedback_present") is expected_feedback
                and row.get("base_intent_unchanged") is True and row.get("max_attempts") == 1,
                f"{row['invocation_id']}: frozen prompt/context arm does not match its treatment")
        plan_path = contained_file(DESIGN_DIR, row["plan_path"])
        fixture_path = ROOT / row["fixture"]
        plan_raw, fixture_raw = plan_path.read_bytes(), fixture_path.read_bytes()
        require(sha256(plan_raw) == row.get("plan_sha256")
                and sha256(fixture_raw) == row.get("fixture_sha256"),
                f"{row['invocation_id']}: frozen plan or fixture hash mismatch")
        plan = json.loads(plan_raw)
        require(plan.get("provider_model") == row["provider_model"]
                and plan.get("prompt_profile", "") == expected_profile
                and ("external_training_feedback" in plan) is expected_feedback
                and plan.get("max_attempts") == 1 and "holdout_test_cases" not in plan,
                f"{row['invocation_id']}: plan bytes do not implement the declared factors")
        require(len(plan.get("test_cases", [])) == row.get("training_case_count"),
                f"{row['invocation_id']}: plan training denominator mismatch")
        preflight_hash = row.get("protocol_preflight_request_sha256", "")
        require(len(preflight_hash) == 64 and all(char in "0123456789abcdef" for char in preflight_hash),
                f"{row['invocation_id']}: missing or malformed protocol preflight request binding")
    provenance = read_json(run_dir / "source-provenance.json")
    bridge = read_json(run_dir / "native-identity-bridge.json")
    require((run_dir / "source-provenance.json").read_bytes() ==
            (DESIGN_DIR / design["source_ci_provenance"]).read_bytes(),
            "captured run source provenance differs from the frozen preparation artifact")
    require((run_dir / "native-identity-bridge.json").read_bytes() ==
            (DESIGN_DIR / design["native_identity_bridge"]).read_bytes(),
            "captured run native bridge differs from the frozen preparation artifact")
    require((run_dir / cache_binding_rel).read_bytes() == contained_file(DESIGN_DIR, cache_binding_rel).read_bytes(),
            "captured run local model-cache binding differs from the frozen preparation artifact")
    require(bridge.get("compiler_revision") == EXPECTED_SOURCE_REVISION
            and bridge.get("binary_sha256") == EXPECTED_BINARY_SHA256
            and bridge.get("source_ci_archive_sha256") == provenance.get("ci_archive_sha256"),
            "native identity bridge does not bind the source and binary pins")
    bridges = bridge.get("intent_bridges", {})
    require(set(bridges) == set(INTENTS), "native identity bridge omits a known intent")
    for intent_id, bridge_row in bridges.items():
        source_row = provenance.get("intents", {}).get(intent_id, {})
        require(bridge_row.get("source_matches_actual_ci_artifact_byte_for_byte") is True
                and bridge_row.get("selected_candidate_id") == "identity"
                and bridge_row.get("attempted_candidates") == 1
                and bridge_row.get("generated_source_sha256") == source_row.get("generated_source_sha256")
                and bridge_row.get("training_suite_sha256_typed") == source_row.get("training_suite_sha256_typed"),
                f"{intent_id}: native compiler bridge does not reproduce original CI source bytes")
    return design, provenance, bridge, {row["invocation_id"]: row for row in plan_rows}


def validate_preflight(run_dir: Path, design: dict, rows_by_id: dict[str, dict],
                       feedback_by_intent: dict[str, dict], output_dir: Path) -> dict:
    preflight_rel = design.get("protocol_preflight", {}).get("path")
    require(isinstance(preflight_rel, str), "frozen design has no MOCK protocol-preflight path")
    summary_path = contained_file(run_dir, preflight_rel)
    attempt_dir = summary_path.parent
    summary = read_json(summary_path)
    require(summary.get("schema") == "gooo/pinned-context-protocol-preflight/v1"
            and summary.get("status") == "PASS_CACHED_TOKENIZERS_NO_MODEL_INFERENCE"
            and summary.get("mock_choice_posts") == 24 and summary.get("actual_laya_calls") == 0
            and summary.get("provider_calls") == 0 and summary.get("mock_health_checks") == 24
            and summary.get("unique_intent_arm_model_templates") == 24
            and summary.get("model_revisions") == {model: EXPECTED_MODEL_REVISION for model in MODELS},
            "protocol preflight does not bind 24 local MOCK templates with zero model calls")
    require(design["protocol_preflight"].get("mock_choice_posts") == 24
            and design["protocol_preflight"].get("actual_laya_calls") == 0
            and design["protocol_preflight"].get("mock_health_checks") == 24
            and design["protocol_preflight"].get("unique_intent_arm_model_templates") == 24,
            "design summary has incorrect MOCK preflight counts")
    mock_events = summary.get("mock_events", [])
    mock_snapshot = read_json(attempt_dir / "mock-events.json")
    require(mock_snapshot.get("schema") == "gooo/pinned-context-protocol-capture-mock/v1"
            and mock_snapshot.get("counted_as_laya_calls") == 0,
            "preflight capture is not identified as a local MOCK")
    # The snapshot is written before token-budget rows are added to summary events;
    # compare event identity and raw exchange bindings, not that derived annotation.
    snapshot_events = mock_snapshot.get("events", [])
    require(len(mock_events) == len(snapshot_events), "preflight event index and mock snapshot counts differ")
    template_events = []
    typed_inputs = []
    event_keys = set()
    for event in mock_events:
        require(event.get("counted_as_laya_call") is False and event.get("response_status") == 200,
                "preflight event is not a successful local mock exchange")
        request_raw = contained_file(attempt_dir, event["request_file"]).read_bytes()
        response_raw = contained_file(attempt_dir, event["response_file"]).read_bytes()
        require(sha256(request_raw) == event.get("request_sha256")
                and sha256(response_raw) == event.get("response_sha256"),
                "preflight raw exchange bytes differ from event hashes")
        if event.get("kind") == "protocol_capture_mock":
            require(event.get("method") == "POST" and event.get("path") == "/v1/systemone",
                    "preflight choice event has unexpected endpoint")
            template_events.append(event)
            key = (event.get("invocation_id"), event.get("provider_model"))
            require(key not in event_keys, "duplicate preflight template event")
            event_keys.add(key)
            request = json.loads(request_raw)
            response = json.loads(response_raw)
            row = rows_by_id.get(event.get("invocation_id"))
            require(row is not None and row.get("phase") == "measured"
                    and event.get("provider_model") == row.get("provider_model")
                    and request.get("model") == row.get("provider_model")
                    and response.get("routing", {}).get("model") == row.get("provider_model")
                    and response.get("routing", {}).get("repo") == "protocol-capture-mock",
                    "MOCK wire model does not match its representative frozen plan")
            state_wire = request.get("state", {}).get("request")
            require(isinstance(state_wire, str), "MOCK request lacks nested body-codegen state")
            state = json.loads(state_wire)
            plan = json.loads(contained_file(DESIGN_DIR, row["plan_path"]).read_bytes())
            verify_model_state(state, row, plan, feedback_by_intent[row["intent_id"]])
            expected_criteria = {candidate["id"]: "Try this exact expression: " + candidate["expression"]
                                 for candidate in state["remaining_candidates"]}
            question_map = request.get("questions", {})
            require(len(question_map) == 1 and next(iter(question_map.values())).get("type") == "choice"
                    and next(iter(question_map.values())).get("criteria") == expected_criteria,
                    "MOCK Laya question options differ from typed candidate order")
            typed_inputs.append(typed_input_from_wire(event["invocation_id"], request, state))
        else:
            require(event.get("kind") == "health_check" and event.get("method") == "GET"
                    and event.get("path") == "/health" and request_raw == b"",
                    "preflight includes a non-choice or unexpected provider call")
            health = json.loads(response_raw)
            require(health.get("status") == "ok"
                    and all(health.get("revisions", {}).get(model) == EXPECTED_MODEL_REVISION for model in MODELS),
                    "preflight health response does not expose the two pinned model revisions")
    require(len(template_events) == 24 and len(mock_events) == 48,
            "preflight must contain 24 MOCK choice POSTs and their 24 health GETs")
    unique_templates = {(rows_by_id[event["invocation_id"]]["intent_id"],
                         rows_by_id[event["invocation_id"]]["treatment"], event["provider_model"])
                        for event in template_events}
    require(len(unique_templates) == 24, "preflight did not cover every unique intent/arm/model template")
    expected_templates = {(row["intent_id"], row["treatment"], row["provider_model"])
                          for row in rows_by_id.values() if row["phase"] == "measured"}
    require(unique_templates == expected_templates, "preflight templates differ from the measured factorial cells")
    event_by_template = {
        (rows_by_id[event["invocation_id"]]["intent_id"],
         rows_by_id[event["invocation_id"]]["treatment"], event["provider_model"]): event
        for event in template_events
    }
    for row in rows_by_id.values():
        if row["phase"] == "measured":
            event = event_by_template[(row["intent_id"], row["treatment"], row["provider_model"])]
            require(event.get("request_sha256") == row.get("protocol_preflight_request_sha256"),
                    f"{row['invocation_id']}: frozen plan preflight request hash mismatch")

    exchange_records = summary.get("exchange_records", [])
    require(len(exchange_records) == 24, "MOCK preflight omitted one or more compiler request receipts")
    record_by_template = {}
    for record in exchange_records:
        key = (record.get("intent_id"), record.get("treatment"), record.get("provider_model"))
        require(key in expected_templates and key not in record_by_template,
                "MOCK compiler receipt has an unexpected or duplicate factorial template")
        event = event_by_template[key]
        row = rows_by_id[event["invocation_id"]]
        require(record.get("representative_invocation_id") == event.get("invocation_id")
                and record.get("plan_sha256") == row.get("plan_sha256")
                and record.get("compiler_exit_code") == 0
                and record.get("requested_provider_model") == record.get("provider_model")
                and record.get("event", {}).get("request_sha256") == event.get("request_sha256"),
                "MOCK compiler receipt does not bind its representative plan and request")
        stdout = contained_file(attempt_dir, record["compiler_stdout_file"]).read_bytes()
        stderr = contained_file(attempt_dir, record["compiler_stderr_file"]).read_bytes()
        require(sha256(stdout) == record.get("compiler_stdout_sha256")
                and sha256(stderr) == record.get("compiler_stderr_sha256"),
                "MOCK compiler stdout/stderr differs from the saved receipt hashes")
        payload = json.loads(stdout)
        attempts = payload.get("report", {}).get("body_search", {}).get("attempts", [])
        laya = [attempt.get("decision", {}) for attempt in attempts if attempt.get("decision", {}).get("mode") == "laya"]
        require(len(laya) == 1 and laya[0].get("requested_provider_model") == row["provider_model"]
                and laya[0].get("model_revision") == EXPECTED_MODEL_REVISION,
                "MOCK compiler output lacks a model/revision-bound Laya receipt")
        require(record.get("receipt_request_sha256") == laya[0].get("request_sha256")
                and record.get("reconstructed_typed_request_sha256") == laya[0].get("request_sha256"),
                "MOCK compiler typed-request digest summary differs from its receipt")
        record_by_template[key] = record
    go_bin = shutil.which("go")
    require(go_bin is not None, "Go executable is unavailable for typed-request reconstruction")
    go_env = os.environ.copy()
    go_env.update({"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off",
                   "GOOO_LAYA_URL": "", "GOOO_LAYA_API_KEY": "", "HF_HUB_OFFLINE": "1",
                   "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    version = subprocess.run([go_bin, "version"], env=go_env, capture_output=True, text=True,
                             timeout=30, check=True).stdout.strip()
    require(EXPECTED_GO_VERSION in version, f"typed request reconstruction requires {EXPECTED_GO_VERSION}; got {version}")
    reconstructed = typed_request_hashes(go_bin, output_dir, go_env, typed_inputs,
                                         helper_name="mock-preflight-typed-request-hasher")
    for record in exchange_records:
        key = (record["intent_id"], record["treatment"], record["provider_model"])
        inv_id = record["representative_invocation_id"]
        actual = reconstructed[inv_id]
        event = record["event"]
        require(record.get("reconstructed_typed_request_sha256") == actual
                and record.get("receipt_request_sha256") == actual,
                f"{inv_id}: independently reconstructed MOCK typed-request hash differs from receipt")
        require(event.get("token_budget", {}).get("request_sha256") == event.get("request_sha256"),
                f"{inv_id}: token-budget preflight is not bound to the raw MOCK request")
    token_rows = summary.get("token_budget_rows", [])
    require(len(token_rows) == 24 and all(
        isinstance(row.get("token_count_exact_sequence"), int)
        and isinstance(row.get("model_max_len"), int)
        and row["token_count_exact_sequence"] <= row["model_max_len"]
        and row.get("state_truncated") is False for row in token_rows),
        "cached tokenizer preflight is incomplete, over budget, or truncated")
    require({row.get("request_sha256") for row in token_rows} ==
            {event.get("request_sha256") for event in template_events}
            and all(row.get("tokenizer_revision") == EXPECTED_MODEL_REVISION for row in token_rows),
            "tokenizer result rows do not bind all raw request hashes and the pinned revision")
    require(summary.get("tokenizer_output_sha256") == sha256((attempt_dir / "token-budget.json").read_bytes())
            and summary.get("tokenizer_input_sha256") == sha256((attempt_dir / "tokenizer-input.json").read_bytes()),
            "offline tokenizer preflight file hashes do not match its summary")
    return {"mock_choice_posts": len(template_events), "health_gets": len(mock_events) - len(template_events),
            "actual_laya_calls": 0, "unique_templates": len(unique_templates),
            "token_budget_rows": len(token_rows), "mock_compiler_receipts": len(exchange_records),
            "typed_request_hashes_verified": len(reconstructed), "provider_calls": 0}


def verify_model_state(state: dict, row: dict, plan: dict, feedback: dict | None) -> None:
    require(state.get("schema") == "gooo/body-codegen-ir-search-state/v1"
            and state.get("stage") == "choose_before_candidate_evaluation"
            and state.get("intent") == plan.get("intent")
            and state.get("activity") == row.get("activity")
            and state.get("training_test_count") == len(plan.get("test_cases", [])),
            f"{row['invocation_id']}: serialized chooser state does not match its plan")
    require("provider_model" not in state,
            f"{row['invocation_id']}: provider_model leaked into model state instead of the outer route field")
    profile = row.get("prompt_profile", "")
    if profile == "compact":
        require("training_suite_sha256" not in state,
                f"{row['invocation_id']}: compact state exposes the omitted training-suite digest")
    else:
        expected_suite = "sha256:" + sha256(canonical_training(plan["test_cases"]))
        require(state.get("training_suite_sha256") == expected_suite,
                f"{row['invocation_id']}: legacy state training-suite digest mismatch")
    require(state.get("remaining_candidates") == plan.get("candidates"),
            f"{row['invocation_id']}: remaining candidates differ from the frozen candidate order")
    if row.get("treatment") == "compact_external_feedback":
        require(isinstance(feedback, dict), f"{row['invocation_id']}: no source-bound feedback available")
        failed = [obs for obs in feedback["observations"] if obs.get("passed") is False]
        expected_prompt = {
            "candidate_id": feedback["candidate_id"],
            "failed_cases": [{key: item[key] for key in ("input", "expected", "actual")} for item in failed[:8]],
            "failed_cases_total": len(failed),
            "failed_cases_truncated": len(failed) > 8,
        }
        require(state.get("external_training_feedback") == expected_prompt,
                f"{row['invocation_id']}: model-visible external feedback differs from source-CI failures")
        require(all(item.get("passed") is False for item in failed),
                f"{row['invocation_id']}: feedback actuals were not confirmed as failed source observations")
    else:
        require("external_training_feedback" not in state,
                f"{row['invocation_id']}: feedback appeared in a no-feedback arm")


def typed_request_hashes(go_bin: str, output_dir: Path, env: dict[str, str], requests: list[dict],
                         helper_name: str = "typed-request-hasher") -> dict[str, str]:
    helper_dir = output_dir / helper_name
    helper_dir.mkdir(parents=True, exist_ok=False)
    (helper_dir / "go.mod").write_text("module pinned-context-request-hasher\n\ngo 1.27.1\n", encoding="utf-8")
    (helper_dir / "main.go").write_text(r'''package main
import (
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "os"
)
type option struct { ID string `json:"id"`; Description string `json:"description"` }
type question struct { ID string `json:"id"`; Instructions string `json:"instructions"`; Options []option `json:"options"` }
type request struct { Schema string `json:"schema"`; State string `json:"state"`; Question question `json:"question"`; Fallback string `json:"fallback"`; ProviderModel string `json:"provider_model,omitempty"` }
type item struct { InvocationID string `json:"invocation_id"`; State string `json:"state"`; QuestionID string `json:"question_id"`; Instructions string `json:"instructions"`; Options []option `json:"options"`; Fallback string `json:"fallback"`; ProviderModel string `json:"provider_model"` }
type result struct { InvocationID string `json:"invocation_id"`; SHA256 string `json:"sha256"` }
func main() {
    var items []item
    if err := json.NewDecoder(os.Stdin).Decode(&items); err != nil { panic(err) }
    out := make([]result, 0, len(items))
    for _, in := range items {
        wire := request{Schema:"gooo/typed-decision-request/v1", State:in.State,
            Question:question{ID:in.QuestionID, Instructions:in.Instructions, Options:in.Options},
            Fallback:in.Fallback, ProviderModel:in.ProviderModel}
        encoded, err := json.Marshal(wire); if err != nil { panic(err) }
        digest := sha256.Sum256(encoded)
        out = append(out, result{InvocationID:in.InvocationID, SHA256:"sha256:"+hex.EncodeToString(digest[:])})
    }
    if err := json.NewEncoder(os.Stdout).Encode(out); err != nil { panic(err) }
}
''', encoding="utf-8")
    proc = subprocess.run([go_bin, "run", "."], cwd=helper_dir, env=env,
                          input=json.dumps(requests, ensure_ascii=False).encode(),
                          capture_output=True, timeout=90, check=False)
    require(proc.returncode == 0,
            "independent Go typed-request hash reconstruction failed: " + proc.stderr.decode("utf-8", errors="replace"))
    parsed = json.loads(proc.stdout)
    out = {row["invocation_id"]: row["sha256"] for row in parsed}
    require(len(out) == len(requests) and set(out) == {row["invocation_id"] for row in requests},
            "Go typed-request hasher omitted or duplicated an invocation")
    return out


def typed_input_from_wire(invocation_id: str, outer: dict, state: dict) -> dict:
    questions = outer.get("questions")
    require(isinstance(questions, dict) and len(questions) == 1,
            f"{invocation_id}: cannot reconstruct exactly one typed chooser question")
    question_id, question = next(iter(questions.items()))
    candidates = state.get("remaining_candidates")
    require(isinstance(candidates, list) and candidates,
            f"{invocation_id}: cannot reconstruct typed options without remaining candidates")
    return {"invocation_id": invocation_id, "state": outer["state"]["request"],
            "question_id": question_id, "instructions": question["instructions"],
            "options": [{"id": item["id"],
                         "description": "Try this exact expression: " + item["expression"]}
                        for item in candidates],
            "fallback": candidates[0]["id"], "provider_model": outer.get("model", "")}


def probe_source(activity: str, suites: dict[str, list[dict]]) -> str:
    cases = []
    int64_min, int64_max = -(1 << 63), (1 << 63) - 1
    for suite_name, rows in suites.items():
        for index, case in enumerate(rows):
            value, expected = int(case["input"]), int(case["expected"])
            require(int64_min <= value <= int64_max and int64_min <= expected <= int64_max,
                    "finite oracle value is outside int64")
            cases.append(f'{{Suite:{json.dumps(suite_name)}, Index:{index}, Input:{value}, Expected:{expected}}}')
    return f'''package bodycodegen
import ("encoding/json"; "testing")
type pinnedCase struct {{ Suite string; Index int; Input int64; Expected int64 }}
type pinnedObservation struct {{ Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"`; Passed bool `json:"passed"` }}
func TestPinnedFiniteOracle(t *testing.T) {{
 cases := []pinnedCase{{{', '.join(cases)}}}
 for _, c := range cases {{ o := pinnedObservation{{Suite:c.Suite, Index:c.Index, Input:c.Input, Expected:c.Expected, Actual:{activity}(c.Input)}}; o.Passed = o.Actual == o.Expected; b, err := json.Marshal(o); if err != nil {{ t.Fatal(err) }}; t.Logf("PINNED_CASE_RESULT:%s", b); if !o.Passed {{ t.Errorf("PINNED_CASE_MISMATCH:%s", b) }} }}
}}
'''


def independent_go_replay(go_bin: str, output_dir: Path, invocation_id: str, source: bytes,
                           activity: str, suites: dict[str, list[dict]], env: dict[str, str]) -> dict:
    work = output_dir / "go-replays" / invocation_id
    work.mkdir(parents=True, exist_ok=False)
    (work / "emitted.go").write_bytes(source)
    (work / "probe_test.go").write_text(probe_source(activity, suites), encoding="utf-8")
    (work / "go.mod").write_text("module pinned-context-source-replay\n\ngo 1.27.1\n", encoding="utf-8")
    proc = subprocess.run([go_bin, "test", "-count=1", "-v", "./..."], cwd=work, env=env,
                          capture_output=True, timeout=120, check=False)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    observations = []
    for line in stdout.splitlines():
        marker = "PINNED_CASE_RESULT:"
        if marker in line:
            observations.append(json.loads(line.split(marker, 1)[1]))
    expected = {(name, i): case for name, cases in suites.items() for i, case in enumerate(cases)}
    by_key = {}
    for row in observations:
        key = (row.get("suite"), row.get("index"))
        require(key in expected and key not in by_key, f"{invocation_id}: unexpected or duplicate compiled case {key}")
        case = expected[key]
        require((row.get("input"), row.get("expected"), row.get("passed")) ==
                (case["input"], case["expected"], row.get("actual") == case["expected"]),
                f"{invocation_id}: compiled observation fields are inconsistent")
        by_key[key] = row
    if len(by_key) != len(expected):
        return {"status": "compile_or_observation_failure", "exit_code": proc.returncode,
                "observed_cases": len(by_key), "expected_cases": len(expected),
                "stdout": stdout, "stderr": stderr,
                "stdout_sha256": sha256(proc.stdout), "stderr_sha256": sha256(proc.stderr)}
    result = {}
    for suite_name, cases in suites.items():
        rows = []
        for index, case in enumerate(cases):
            observed = by_key[(suite_name, index)]
            rows.append({"input": case["input"], "expected": case["expected"],
                         "actual": observed["actual"], "passed": observed["passed"]})
        passed = sum(row["passed"] for row in rows)
        result[suite_name] = {"passed": passed, "total": len(rows),
                              "accuracy_percent": 100.0 * passed / len(rows) if rows else None,
                              "observations": rows}
    expected_exit = 0 if all(part["passed"] == part["total"] for part in result.values()) else 1
    combined = (proc.stdout + proc.stderr).lower()
    status = "pass" if proc.returncode == expected_exit and b"build failed" not in combined and b"setup failed" not in combined else "test_harness_failure"
    return {"status": status, "exit_code": proc.returncode, "expected_exit_code": expected_exit,
            "training": result["training"], "holdout": result["holdout"],
            "observed_cases": len(by_key), "expected_cases": len(expected),
            "stdout": stdout, "stderr": stderr,
            "stdout_sha256": sha256(proc.stdout), "stderr_sha256": sha256(proc.stderr)}


def score_partition(oracle: dict, suite_name: str, observations: list[dict]) -> dict:
    suite = oracle[suite_name]
    expected = suite["expected"]
    candidate_outputs = oracle["candidate_outputs"]
    vectors = {key: value[suite_name] for key, value in candidate_outputs.items()}
    require(all(len(vector) == len(expected) for vector in vectors.values()),
            f"{oracle['intent_id']}/{suite_name}: candidate vector length mismatch")
    differing = [i for i in range(len(expected)) if len({vector[i] for vector in vectors.values()}) > 1]
    invariant = [i for i in range(len(expected)) if i not in differing]
    def score(indexes: list[int]) -> dict:
        passed = sum(observations[i]["actual"] == expected[i] for i in indexes)
        return {"passed": passed, "total": len(indexes)}
    return {"all_cases": score(list(range(len(expected))),),
            "candidate_discriminating_cases": score(differing),
            "candidate_invariant_cases": score(invariant),
            "candidate_discriminating_indexes": differing,
            "candidate_invariant_indexes": invariant}


def verify_invocation_index(run_dir: Path, design: dict, plans: dict[str, dict],
                            records: dict[str, dict], events: list[dict]) -> dict[str, dict]:
    index = read_json(run_dir / "invocation-index.json")
    rows = index.get("invocations", [])
    require(index.get("schema") == "gooo/pinned-context-invocation-index/v1"
            and index.get("study_id") == design["study_id"]
            and index.get("planned_invocations") == 74 and len(rows) == 74
            and {row.get("invocation_id") for row in rows} == set(plans),
            "invocation index does not list every frozen call")
    events_by_id: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        events_by_id[event.get("invocation_id")].append(event)
    by_id = {}
    for row in rows:
        inv_id = row["invocation_id"]
        plan = plans[inv_id]
        record = records.get(inv_id, {})
        inv_dir = run_dir / "invocations" / inv_id
        require(row.get("sequence") == plan["sequence"] and row.get("phase") == plan["phase"]
                and row.get("intent_id") == plan["intent_id"] and row.get("treatment") == plan["treatment"]
                and row.get("provider_model") == plan["provider_model"]
                and row.get("model_revision") == EXPECTED_MODEL_REVISION,
                f"{inv_id}: invocation index identity differs from the frozen plan")
        for index_key, file_key, sha_key, expected_sha in (
            ("plan", "path", "sha256", plan["plan_sha256"]),
            ("fixture", "path", "sha256", plan["fixture_sha256"]),
            ("cli", "stdout_path", "stdout_sha256", record.get("stdout_sha256")),
            ("cli", "stderr_path", "stderr_sha256", record.get("stderr_sha256")),
        ):
            entry = row[index_key]
            relative = entry[file_key]
            raw = contained_file(run_dir, relative).read_bytes()
            require(entry.get(sha_key) == expected_sha and sha256(raw) == expected_sha,
                    f"{inv_id}: invocation index {index_key} path/hash mismatch")
        refs = row.get("provider_exchange_refs", [])
        actual = sorted([event for event in events_by_id.get(inv_id, []) if event.get("kind") == "laya_choice"],
                        key=lambda event: event["seq"])
        require(len(refs) == len(actual), f"{inv_id}: invocation index provider exchange count mismatch")
        for ref, event in zip(refs, actual):
            require(ref.get("seq") == event.get("seq")
                    and ref.get("request_file") == event.get("request_file")
                    and ref.get("response_file") == event.get("response_file")
                    and ref.get("request_sha256") == event.get("request_sha256")
                    and ref.get("response_sha256") == event.get("response_sha256"),
                    f"{inv_id}: provider exchange reference differs from raw proxy event")
        health_refs = row.get("health_event_refs", [])
        actual_health = sorted([event for event in events_by_id.get(inv_id, [])
                                if event.get("kind") == "health_check"],
                               key=lambda event: event["seq"])
        require(len(health_refs) == len(actual_health)
                and all(ref.get("seq") == event.get("seq")
                        and ref.get("request_file") == event.get("request_file")
                        and ref.get("response_file") == event.get("response_file")
                        and ref.get("request_sha256") == event.get("request_sha256")
                        and ref.get("response_sha256") == event.get("response_sha256")
                        for ref, event in zip(health_refs, actual_health)),
                f"{inv_id}: invocation index health references differ from raw proxy events")
        cli = row["cli"]
        resource_path = contained_file(run_dir, cli["resource_path"])
        require(sha256(resource_path.read_bytes()) == cli.get("resource_sha256"),
                f"{inv_id}: invocation index resource sample path/hash mismatch")
        report_ref = row.get("compiler_report", {})
        report_rel = report_ref.get("path")
        stdout_path = contained_file(run_dir, row["cli"]["stdout_path"])
        if report_rel:
            raw = contained_file(run_dir, report_rel).read_bytes()
            require(sha256(raw) == report_ref.get("sha256"), f"{inv_id}: compiler report file hash mismatch")
            decoded = json.loads(raw)
            stdout_payload = json.loads(stdout_path.read_bytes())
            require(decoded == stdout_payload.get("report"),
                    f"{inv_id}: compiler report file differs from raw CLI stdout")
        receipt_rel = report_ref.get("body_search_receipt_path")
        if receipt_rel:
            raw = contained_file(run_dir, receipt_rel).read_bytes()
            require(sha256(raw) == report_ref.get("body_search_receipt_sha256"),
                    f"{inv_id}: body-search receipt file hash mismatch")
            stdout_payload = json.loads(stdout_path.read_bytes())
            require(json.loads(raw) == stdout_payload.get("report", {}).get("body_search"),
                    f"{inv_id}: body-search receipt differs from raw CLI stdout")
        validation_ref = row.get("validation_receipt", {})
        if validation_ref.get("path"):
            validation_path = contained_file(run_dir, validation_ref["path"])
            require(sha256(validation_path.read_bytes()) == validation_ref.get("sha256"),
                    f"{inv_id}: saved validation receipt hash mismatch")
        by_id[inv_id] = row
    return by_id


def verify_derived_correction(run_dir: Path, report: dict, report_bytes: bytes, metadata: dict,
                              preexecution: dict, design: dict, invocation_results: list[dict],
                              report_invocations: list[dict], events: list[dict], typed_hash_count: int,
                              feedback_count: int, reply_route_count: int) -> dict:
    """Validate the immutable runner report and its explicitly whitelisted derived copy."""
    correction_dir = run_dir / "derived-correction"
    receipt_path = correction_dir / "correction-receipt.json"
    corrected_path = correction_dir / "report.json"
    corrected_md_path = correction_dir / "report.md"
    require(receipt_path.is_file() and corrected_path.is_file() and corrected_md_path.is_file(),
            "derived correction receipt/report/markdown is incomplete")
    receipt = read_json(receipt_path)
    corrected_bytes = corrected_path.read_bytes()
    corrected = json.loads(corrected_bytes)
    original_sha = sha256(report_bytes)
    corrected_sha = sha256(corrected_bytes)
    design_sha = sha256((run_dir / "study-design.json").read_bytes())
    preexecution_sha = sha256((run_dir / "preexecution.json").read_bytes())
    source_provenance_sha = sha256((run_dir / "source-provenance.json").read_bytes())
    checkpoint = preexecution.get("study_code_provenance", {}).get("public_frozen_checkpoint_revision")
    study_provenance = receipt.get("study_provenance", {})
    require(receipt.get("schema") == "gooo/pinned-context-derived-summary-correction/v1"
            and receipt.get("status") == "DERIVED_SUMMARY_CORRECTED_FROM_CAPTURED_EVIDENCE"
            and receipt.get("run_id") == run_dir.name,
            "derived correction receipt identity/status mismatch")
    require(receipt.get("original_report") == {
                "path": "../report.json", "sha256": original_sha,
                "decision": report.get("decision"),
                "feedback_summary": report.get("capture", {}).get("measured_requests_matching_plan_feedback")}
            and metadata.get("report_sha256") == original_sha,
            "derived correction receipt or run metadata does not bind the immutable original report")
    require(study_provenance == {
                "design_sha256": design_sha,
                "preexecution_path": "../preexecution.json",
                "preexecution_sha256": preexecution_sha,
                "preparation_script_sha256": preexecution.get("study_code_provenance", {}).get("preparation_script_sha256"),
                "public_frozen_checkpoint_revision": checkpoint,
                "runner_script_sha256": preexecution.get("study_code_provenance", {}).get("runner_script_sha256"),
                "source_provenance_sha256": source_provenance_sha,
            }, "derived correction receipt does not bind the frozen design/source/runtime provenance")
    expected_capture_hashes = {
        "capture_status": sha256((run_dir / "capture-status.json").read_bytes()),
        "cli_invocation_records": sha256((run_dir / "cli-invocation-records.json").read_bytes()),
        "invocation_index": sha256((run_dir / "invocation-index.json").read_bytes()),
        "proxy_events": sha256((run_dir / "proxy-events.json").read_bytes()),
        "run_metadata": sha256((run_dir / "run-metadata.json").read_bytes()),
    }
    require(receipt.get("capture_manifest_sha256") == expected_capture_hashes,
            "derived correction receipt does not bind current raw capture manifests")
    report_rows = {row.get("invocation_id"): row for row in report_invocations}
    saved_audits = report.get("request_route_and_feedback_audits", [])
    saved_audits_by_id = {row.get("invocation_id"): row for row in saved_audits}
    own_by_id = {row.get("invocation_id"): row for row in invocation_results}
    raw_posts = [event for event in events if event.get("kind") == "laya_choice"]
    measured_own = [row for row in invocation_results if row.get("phase") == "measured"]
    warmup_own = [row for row in invocation_results if row.get("phase") == "warmup"]
    reply_routes = sum(row.get("provider_reply_route_verified") is True for row in invocation_results)
    context_profiles = sum(row.get("provider_context_verified") is True for row in measured_own)
    saved_context_measured = sum(row.get("provider_context_pin_match") is True
                                 for row in report.get("invocation_results", [])
                                 if row.get("phase") == "measured")
    saved_context_warmups = sum(row.get("provider_context_pin_match") is True
                                for row in report.get("invocation_results", [])
                                if row.get("phase") == "warmup")
    request_audit_context_measured = sum(row.get("provider_context_pin_match") is True
                                         for row in saved_audits
                                         if own_by_id.get(row.get("invocation_id"), {}).get("phase") == "measured")
    expected_raw_checks = {
        "captured_response_routes_match_model_pin": reply_routes,
        "measured_calls": len(measured_own),
        "measured_model_state_profiles_and_hashes_match": context_profiles,
        "original_request_audit_context_true_measured": request_audit_context_measured,
        "per_invocation_context_summaries_true_measured": saved_context_measured,
        "per_invocation_context_summaries_true_warmup": saved_context_warmups,
        "planned_invocations": len(report_rows),
        "raw_choice_posts": len(raw_posts),
        "typed_request_receipt_hashes_match": typed_hash_count,
    }
    require(receipt.get("raw_evidence_checks") == expected_raw_checks
            and expected_raw_checks == {
                "captured_response_routes_match_model_pin": 74,
                "measured_calls": 72,
                "measured_model_state_profiles_and_hashes_match": 72,
                "original_request_audit_context_true_measured": 0,
                "per_invocation_context_summaries_true_measured": 72,
                "per_invocation_context_summaries_true_warmup": 2,
                "planned_invocations": 74,
                "raw_choice_posts": 74,
                "typed_request_receipt_hashes_match": 74,
            }, "derived correction's raw evidence counts differ from independent verification")
    require(len(invocation_results) == 74 and len(measured_own) == 72 and len(warmup_own) == 2
            and feedback_count == 72 and reply_route_count == 74 and typed_hash_count == 74
            and all(row.get("provider_context_verified") is True
                    and row.get("request_provider_model_verified") is True
                    and row.get("provider_reply_route_verified") is True
                    and row.get("typed_request_hash_verified") is True
                    and row.get("provider_receipt_verified") is True
                    and row.get("captured_choice_posts") == 1
                    and row.get("captured_reply_matches_compiler_choice") is True
                    and row.get("compiled_replay", {}).get("status") == "pass"
                    and not row.get("issues") for row in invocation_results)
            and all(row.get("decision") == "CAPTURED_AND_COMPILED" and row.get("cli_exit_code") == 0
                    and row.get("choice_post_count") == 1 for row in report_invocations)
            and metadata.get("health_after_validated") is True,
            "independent raw routing/state/hash/compiled-source checks do not justify correcting the runner decision")

    whitelist = ["capture.measured_requests_matching_plan_feedback.passed", "decision"]
    require(receipt.get("modified_field_whitelist") == whitelist
            and receipt.get("immutability") == {
                "model_calls_repeated": False,
                "original_report_bytes_preserved": True,
                "original_report_markdown_preserved": True,
                "raw_requests_responses_and_replies_unchanged": True,
                "scores_timings_resource_samples_and_candidate_choices_unchanged": True,
            }, "derived correction receipt has an incorrect whitelist or immutability claim")
    corrected_decision = "CAPTURED_AND_COMPILED"
    expected_corrected = copy.deepcopy(report)
    expected_corrected["capture"]["measured_requests_matching_plan_feedback"]["passed"] = feedback_count
    expected_corrected["decision"] = corrected_decision
    expected_diff = [
        {"path": whitelist[0], "old": 0, "new": feedback_count},
        {"path": whitelist[1], "old": "PARTIAL_CAPTURE_OR_VALIDATION", "new": corrected_decision},
    ]
    require(receipt.get("diff") == expected_diff
            and report.get("decision") == "PARTIAL_CAPTURE_OR_VALIDATION"
            and report.get("capture", {}).get("measured_requests_matching_plan_feedback") == {"passed": 0, "total": 72}
            and corrected == expected_corrected,
            "derived report changed fields outside the two allowed paths or does not use independently verified values")
    require(receipt.get("derived_report") == {
                "path": "report.json", "sha256": corrected_sha, "decision": corrected_decision,
                "markdown_path": "report.md", "markdown_sha256": sha256(corrected_md_path.read_bytes()),
            }, "derived correction receipt does not bind its report and markdown bytes")
    require(receipt.get("corrector_script_sha256") == sha256((ROOT / "scripts" / "correct_pinned_context_summary.py").read_bytes()),
            "derived correction receipt does not bind the correction script")
    return {"status": "VERIFIED_TWO_FIELD_DERIVATION", "original_report_sha256": original_sha,
            "derived_report_sha256": corrected_sha, "modified_fields": whitelist,
            "raw_model_state_matches": feedback_count, "raw_reply_routes_matched": reply_routes,
            "typed_request_receipts_matched": typed_hash_count}


def validate_run(run_dir: Path, output_dir: Path, required_go: str | None) -> dict:
    run_dir, output_dir = run_dir.resolve(), output_dir.resolve()
    require(not output_dir.exists(), f"refusing to overwrite replay output: {output_dir}")
    output_dir.mkdir(parents=True)
    design, provenance, bridge, plans = verify_design(run_dir)
    manifest = read_json(ROOT / "manifest.json")
    feedback = verify_source_ci_and_feedback(manifest, design, run_dir)
    preflight = validate_preflight(run_dir, design, plans, feedback, output_dir)

    preexecution = read_json(run_dir / "preexecution.json")
    metadata = read_json(run_dir / "run-metadata.json")
    capture_status = read_json(run_dir / "capture-status.json")
    preexecution_raw = (run_dir / "preexecution.json").read_bytes()
    cache_binding = read_json(contained_file(run_dir, design["local_model_cache_binding"]))
    study_code = preexecution.get("study_code_provenance", {})
    require(preexecution.get("design_sha256") == sha256((run_dir / "study-design.json").read_bytes())
            and preexecution.get("holdout_vectors_loaded") is False
            and preexecution.get("laya_version") == EXPECTED_LAYA_VERSION,
            "preexecution receipt does not bind the frozen plan and offline runtime")
    require(metadata.get("preexecution_sha256") == sha256(preexecution_raw),
            "run metadata does not bind the exact preexecution receipt bytes")
    require(study_code.get("runner_script_sha256") == sha256((ROOT / "scripts" / "run_pinned_context_study.py").read_bytes())
            and study_code.get("preparation_script_sha256") == sha256((ROOT / "scripts" / "prepare_pinned_context_study.py").read_bytes())
            and isinstance(study_code.get("public_frozen_checkpoint_revision"), str)
            and len(study_code["public_frozen_checkpoint_revision"]) == 40
            and all(char in "0123456789abcdef" for char in study_code["public_frozen_checkpoint_revision"]),
            "preexecution receipt does not bind the raw-capture runner, preparation script, and public checkpoint")
    model_cache_receipt = preexecution.get("local_model_cache_binding", {})
    require(model_cache_receipt.get("schema") == cache_binding.get("schema")
            and model_cache_receipt.get("model_revision") == EXPECTED_MODEL_REVISION
            and model_cache_receipt.get("verified_cached_files") == len(cache_binding.get("cached_snapshot_files", []))
            and model_cache_receipt.get("verified_cached_bytes") == cache_binding.get("cached_snapshot_total_bytes")
            and model_cache_receipt.get("claim_scope") == cache_binding.get("claim_scope"),
            "preexecution receipt does not bind the frozen local model-cache inventory")
    compiler = preexecution.get("compiler", {})
    require(compiler.get("sha256") == EXPECTED_BINARY_SHA256
            and compiler.get("source_revision") == EXPECTED_SOURCE_REVISION
            and compiler.get("vcs_modified") == "false",
            "native preexecution receipt differs from the pinned clean binary")
    require(metadata.get("run_id") == run_dir.name
            and metadata.get("status") in ("CAPTURED", "CAPTURED_AND_COMPILED",
                                            "all_raw_calls_captured_before_runtime_oracle_validation",
                                            "PARTIAL_CAPTURE_OR_VALIDATION")
            and metadata.get("model_revision") == EXPECTED_MODEL_REVISION
            and metadata.get("laya_version") == EXPECTED_LAYA_VERSION
            and "offline" in metadata.get("provider_policy", "").lower()
            and metadata.get("planned_warmups") == 2
            and metadata.get("planned_measured_invocations") == 72,
            "run metadata does not retain the pinned offline study denominators")
    planned_hashes = preexecution.get("invocation_plan_hashes", [])
    require(len(planned_hashes) == 74 and {item.get("invocation_id") for item in planned_hashes} == set(plans),
            "preexecution receipt does not bind all 74 frozen invocations")
    for item in planned_hashes:
        row = plans[item["invocation_id"]]
        require(item.get("plan_sha256") == row["plan_sha256"]
                and item.get("fixture_sha256") == row["fixture_sha256"]
                and item.get("provider_model") == row["provider_model"]
                and item.get("treatment") == row["treatment"],
                f"{item['invocation_id']}: preexecution plan hash binding mismatch")
    report_bytes = (run_dir / "report.json").read_bytes()
    report = json.loads(report_bytes)
    require(metadata.get("report_sha256") == sha256(report_bytes), "captured study report SHA-256 mismatch")
    require(report.get("design_sha256") == sha256((run_dir / "study-design.json").read_bytes())
            and report.get("study_id") == design["study_id"] and report.get("run_id") == run_dir.name,
            "captured report does not bind the frozen design/run")
    reported_capture = report.get("capture", {})
    require(reported_capture.get("planned_warmup_calls") == 2
            and reported_capture.get("planned_measured_calls") == 72
            and reported_capture.get("completed_cli_invocations") == 74
            and reported_capture.get("captured_choice_posts_all_phases") == 74
            and reported_capture.get("captured_choice_posts_measured") == 72,
            "captured report does not preserve the planned and actual 72 + 2 denominators")
    require(capture_status.get("runtime_oracle_validation_completed") is True
            and capture_status.get("all_raw_provider_exchange_files_saved") is True,
            "capture status does not confirm durable complete raw exchanges before scoring")
    report_invocations = report.get("invocation_results", [])
    require(len(report_invocations) == 74
            and {row.get("invocation_id") for row in report_invocations} == set(plans),
            "study report omitted a planned invocation result")

    oracle_index = read_json(run_dir / "oracles" / "index.json")
    require(oracle_index.get("schema") == "gooo/pinned-context-reused-oracle-index/v1"
            and oracle_index.get("loaded_after_all_choice_posts_saved") is True
            and oracle_index.get("oracle_count") == 4,
            "reused finite oracle index is incomplete or was opened before captures")
    manifest_items = {item["id"]: item for item in manifest["intents"]}
    oracles = {}
    for item in oracle_index["oracles"]:
        intent_id = item["intent_id"]
        require(intent_id in manifest_items, f"unexpected finite oracle {intent_id}")
        raw = contained_file(run_dir, item["file"]).read_bytes()
        require(sha256(raw) == item.get("sha256") == manifest_items[intent_id]["independent_oracle_sha256"]
                and len(raw) == item.get("bytes"), f"{intent_id}: finite oracle hash/size pin mismatch")
        oracle = json.loads(raw)
        require(oracle.get("schema") == "gooo/ir-search-finite-oracle/v1"
                and oracle.get("intent_id") == intent_id, f"{intent_id}: oracle identity mismatch")
        oracles[intent_id] = oracle
    require(set(oracles) == set(INTENTS), "run omitted one or more pinned finite oracles")

    records = read_json(run_dir / "cli-invocation-records.json")
    require({record.get("invocation_id") for record in records} == set(plans),
            "CLI invocation receipt set does not preserve all 74 planned calls")
    records_by_id = {record["invocation_id"]: record for record in records}
    event_index = read_json(run_dir / "proxy-events.json")
    events = event_index.get("events", [])
    event_log = [json.loads(line) for line in (run_dir / "proxy" / "events.jsonl").read_text().splitlines() if line.strip()]
    require(events == event_log and event_index.get("choice_post_count") == 74,
            "raw capture event index/log mismatch or not all 74 choice POSTs were saved")
    choice_events = [event for event in events if event.get("kind") == "laya_choice"]
    health_events = [event for event in events if event.get("kind") == "health_check"]
    require(len(choice_events) == 74 and all(event.get("method") == "POST" and event.get("path") == "/v1/systemone"
                                             for event in choice_events),
            "capture does not contain 74 expected Laya choice POSTs")
    require(all(event.get("method") == "GET" and event.get("path") == "/health"
                for event in health_events), "capture contains an unexpected non-choice provider event")
    health_by_id: dict[str, list[dict]] = defaultdict(list)
    for event in health_events:
        health_by_id[event.get("invocation_id")].append(event)
    require(len(health_events) == 74 and set(health_by_id) == set(plans)
            and all(len(health_by_id[inv_id]) == 1 and health_by_id[inv_id][0].get("status") == 200
                    for inv_id in plans),
            "capture does not contain exactly one successful resolver health check per planned invocation")
    index_by_id = verify_invocation_index(run_dir, design, plans, records_by_id, events)
    events_by_id: dict[str, list[dict]] = defaultdict(list)
    privacy_results = []
    parsed_by_id: dict[str, dict] = {}
    request_hash_inputs = []
    request_hash_receipts = {}
    issues: list[str] = []
    legacy_derived_issues: list[str] = []
    invocation_results = []
    independent_replays = []
    group_accumulators: dict[tuple[str, str], dict[str, Any]] = {}
    for treatment in TREATMENTS:
        for model in MODELS:
            group_accumulators[(treatment, model)] = {
                "planned": 12, "captured_posts": 0, "cli_successes": 0,
                "request_route_passed": 0, "reply_route_passed": 0, "receipt_route_passed": 0,
                "typed_digest_passed": 0, "feedback_passed": 0,
                "compiled": 0, "validated": 0, "training": Counter(), "holdout": Counter(),
                "discriminating": Counter(),
            }
            accumulator = group_accumulators[(treatment, model)]
            for suite_name in ("training", "holdout"):
                for intent_id in INTENTS:
                    oracle = oracles[intent_id]
                    expected = oracle[suite_name]["expected"]
                    candidate_vectors = {key: value[suite_name]
                                         for key, value in oracle["candidate_outputs"].items()}
                    discriminating = sum(
                        len({vector[index] for vector in candidate_vectors.values()}) > 1
                        for index in range(len(expected)))
                    accumulator[suite_name]["all_cases_planned"] += 3 * len(expected)
                    accumulator[suite_name]["candidate_discriminating_cases_planned"] += 3 * discriminating
                    accumulator[suite_name]["candidate_invariant_cases_planned"] += 3 * (len(expected) - discriminating)
    for event in events:
        event_id = event.get("invocation_id")
        if event_id not in plans:
            issues.append(f"event {event.get('seq')}: unknown invocation {event_id}")
            continue
        events_by_id[event_id].append(event)
        try:
            request_raw = contained_file(run_dir, event["request_file"]).read_bytes()
            response_raw = contained_file(run_dir, event["response_file"]).read_bytes()
            require(sha256(request_raw) == event.get("request_sha256")
                    and sha256(response_raw) == event.get("response_sha256"),
                    f"event {event.get('seq')}: raw bytes differ from event hashes")
            event["_request_raw"] = request_raw
            event["_response_raw"] = response_raw
        except Exception as exc:
            issues.append(f"event {event.get('seq')}: {exc}")
    # Preserve event order in `events_by_id`; raw bytes are attached only in memory.

    env = os.environ.copy()
    env.update({"GOOO_LAYA_URL": "", "GOOO_LAYA_API_KEY": "", "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off"})
    go_version = subprocess.run(["go", "version"], cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=30, check=True).stdout.strip()
    require(required_go is None or required_go in go_version, f"expected Go {required_go}, found {go_version}")
    require(EXPECTED_GO_VERSION in go_version, f"replay requires {EXPECTED_GO_VERSION}, found {go_version}")
    go_bin = shutil.which("go")
    require(go_bin is not None, "Go executable is unavailable")

    for row in design["plans"]:
        inv_id = row["invocation_id"]
        inv_dir = run_dir / "invocations" / inv_id
        plan = json.loads(contained_file(inv_dir, "plan.search-plan.json").read_bytes())
        fixture_raw = contained_file(inv_dir, "fixture.gooo.fixture").read_bytes()
        record = records_by_id[inv_id]
        stdout_raw = contained_file(inv_dir, "stdout.raw").read_bytes()
        stderr_raw = contained_file(inv_dir, "stderr.raw").read_bytes()
        row_result: dict[str, Any] = {"sequence": row["sequence"], "invocation_id": inv_id,
                                     "phase": row["phase"], "intent_id": row["intent_id"],
                                     "treatment": row["treatment"], "provider_model": row["provider_model"],
                                     "replicate": row["replicate"], "cli_exit_code": record.get("exit_code"),
                                     "planned": True, "captured_choice_posts": 0,
                                     "typed_request_hash_verified": False, "request_provider_model_verified": False,
                                     "provider_reply_route_verified": False, "provider_route_verified": False,
                                     "provider_context_verified": False, "provider_receipt_verified": False,
                                     "compiled_replay": None,
                                     "issues": []}
        if record.get("exit_code") != 0:
            cli_issue = f"{inv_id}: CLI process exited with {record.get('exit_code')}"
            row_result["issues"].append(cli_issue)
            issues.append(cli_issue)
        group = group_accumulators.get((row["treatment"], row["provider_model"])) if row["phase"] == "measured" else None
        try:
            require(record.get("sequence") == row["sequence"] and record.get("phase") == row["phase"]
                    and record.get("intent_id") == row["intent_id"] and record.get("treatment") == row["treatment"]
                    and record.get("provider_model") == row["provider_model"]
                    and record.get("plan_sha256") == row["plan_sha256"]
                    and record.get("fixture_sha256") == row["fixture_sha256"],
                    f"{inv_id}: invocation receipt differs from frozen plan")
            require(sha256(stdout_raw) == record.get("stdout_sha256")
                    and sha256(stderr_raw) == record.get("stderr_sha256")
                    and sha256((inv_dir / "plan.search-plan.json").read_bytes()) == row["plan_sha256"]
                    and sha256(fixture_raw) == row["fixture_sha256"]
                    and plan == json.loads(contained_file(DESIGN_DIR, row["plan_path"]).read_bytes()),
                    f"{inv_id}: raw CLI bytes, copied plan, or fixture differ from frozen inputs")
            require("holdout_test_cases" not in plan and plan.get("test_cases")
                    == json.loads(contained_file(inv_dir, "training-cases.json").read_bytes()),
                    f"{inv_id}: copied training cases differ or contain a holdout field")
            if record.get("exit_code") == 0:
                if group:
                    group["cli_successes"] += 1
            row_events = [event for event in events_by_id[inv_id] if event.get("kind") == "laya_choice"]
            row_result["captured_choice_posts"] = len(row_events)
            if group:
                group["captured_posts"] += len(row_events)
            require(len(row_events) == 1, f"{inv_id}: expected exactly one captured choice POST, found {len(row_events)}")
            event = row_events[0]
            request_raw, response_raw = event.get("_request_raw"), event.get("_response_raw")
            require(isinstance(request_raw, bytes) and isinstance(response_raw, bytes), f"{inv_id}: raw exchange unavailable")
            oracle = oracles[row["intent_id"]]
            try:
                scan = scan_selection_body(request_raw, holdout_case_pairs(oracle))
            except PrivacyScanError as exc:
                raise RuntimeError(f"recursive privacy scan failed: {exc}") from exc
            privacy_results.append({"invocation_id": inv_id, "sequence": event["seq"], **scan})
            outer = json.loads(request_raw)
            state_raw = outer.get("state", {}).get("request")
            require(isinstance(state_raw, str), f"{inv_id}: raw wire is missing state.request string")
            state = json.loads(state_raw)
            require(outer.get("model") == row["provider_model"], f"{inv_id}: outer model route pin mismatch")
            row_result["request_provider_model_verified"] = True
            if group:
                group["request_route_passed"] += 1
            verify_model_state(state, row, plan, feedback[row["intent_id"]])
            row_result["provider_context_verified"] = True
            if group:
                group["feedback_passed"] += 1
            questions = outer.get("questions")
            require(isinstance(questions, dict) and len(questions) == 1 and "body_ir_search" in questions,
                    f"{inv_id}: typed question set differs from body-codegen protocol")
            qid, question = next(iter(questions.items()))
            expected_criteria = {candidate["id"]: "Try this exact expression: " + candidate["expression"]
                                 for candidate in state["remaining_candidates"]}
            require(question.get("type") == "choice" and question.get("criteria") == expected_criteria,
                    f"{inv_id}: Laya question criteria differ from the captured candidate options")
            options = [{"id": candidate["id"],
                        "description": "Try this exact expression: " + candidate["expression"]}
                       for candidate in state["remaining_candidates"]]
            typed = {"invocation_id": inv_id, "state": state_raw, "question_id": qid,
                     "instructions": question["instructions"], "options": options,
                     "fallback": state["remaining_candidates"][0]["id"],
                     "provider_model": row["provider_model"]}
            request_hash_inputs.append(typed)
            response = json.loads(response_raw)
            choice = response.get("answers", {}).get("body_ir_search", {}).get("choice")
            require(choice in {candidate["id"] for candidate in plan["candidates"]},
                    f"{inv_id}: provider reply did not choose a declared candidate")
            require(event.get("selected_candidate_id") == choice,
                    f"{inv_id}: raw response and capture event choice differ")
            route_model = response.get("routing", {}).get("model")
            row_result["provider_reply_model"] = route_model
            require(route_model == row["provider_model"], f"{inv_id}: provider reply routing.model differs from requested model")
            row_result["provider_reply_route_verified"] = True
            row_result["provider_route_verified"] = row_result["request_provider_model_verified"]
            if group:
                group["reply_route_passed"] += 1
            report_payload = None
            try:
                report_payload = json.loads(stdout_raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            if report_payload is not None:
                body = report_payload.get("report", {}).get("body_search", {})
                attempts = body.get("attempts", [])
                decisions = [attempt.get("decision", {}) for attempt in attempts]
                laya_decisions = [decision for decision in decisions if decision.get("mode") == "laya"]
                row_result["provider_reply_choice"] = choice
                row_result["compiler_candidate_id"] = body.get("selected_candidate_id")
                row_result["captured_reply_matches_compiler_choice"] = body.get("selected_candidate_id") == choice
                if len(laya_decisions) == 1:
                    decision = laya_decisions[0]
                    row_result["receipt_request_sha256"] = decision.get("request_sha256")
                    row_result["receipt_requested_provider_model"] = decision.get("requested_provider_model")
                    row_result["receipt_model_revision"] = decision.get("model_revision")
                    row_result["provider_receipt_verified"] = (
                        decision.get("requested_provider_model") == row["provider_model"]
                        and decision.get("model_revision") == row["model_revision"]
                        and decision.get("selected") == choice)
                    request_hash_receipts[inv_id] = decision.get("request_sha256")
                    if group and row_result["provider_receipt_verified"]:
                        group["receipt_route_passed"] += 1
                    require(row_result["provider_receipt_verified"],
                            f"{inv_id}: Laya receipt does not bind requested model, revision, and captured choice")
                else:
                    require(False, f"{inv_id}: expected exactly one Laya decision receipt, found {len(laya_decisions)}")
                if body.get("selected_candidate_id") != choice:
                    require(False, f"{inv_id}: compiled candidate differs from captured Laya choice")
                source_text = report_payload.get("source")
                if isinstance(source_text, str) and source_text:
                    source = source_text.encode()
                    row_result["source_sha256"] = sha256(source)
                    generated_digest = report_payload.get("report", {}).get("generated_digest")
                    require(generated_digest == "sha256:" + sha256(source),
                            f"{inv_id}: emitted source does not match compiler generated digest")
                    suites = {name: case_rows(oracle[name]) for name in ("training", "holdout")}
                    replay = independent_go_replay(go_bin, output_dir, inv_id, source, row["activity"], suites, env)
                    row_result["compiled_replay"] = replay
                    independent_replays.append({"invocation_id": inv_id, "phase": row["phase"],
                                                 "treatment": row["treatment"], "provider_model": row["provider_model"],
                                                 "replay": replay})
                    if replay.get("status") in ("pass", "test_harness_failure") and "training" in replay:
                        body_score = score_partition(oracle, "training", replay["training"]["observations"])
                        holdout_score = score_partition(oracle, "holdout", replay["holdout"]["observations"])
                        row_result["finite_scores"] = {"training": body_score, "holdout": holdout_score}
                        if group:
                            group["compiled"] += 1
                            for name, part in (("training", body_score), ("holdout", holdout_score)):
                                for field, score in part.items():
                                    if isinstance(score, dict):
                                        group[name][field + "_passed"] += score["passed"]
                                        group[name][field + "_total"] += score["total"]
                            for suite_name in ("training", "holdout"):
                                diff = row_result["finite_scores"][suite_name]["candidate_discriminating_cases"]
                                group["discriminating"][suite_name + "_passed"] += diff["passed"]
                                group["discriminating"][suite_name + "_total"] += diff["total"]
                        expected_vector = oracle["candidate_outputs"].get(choice, {})
                        for suite_name in ("training", "holdout"):
                            actuals = [item["actual"] for item in replay[suite_name]["observations"]]
                            require(actuals == expected_vector.get(suite_name),
                                    f"{inv_id}: independently compiled output differs from declared candidate oracle")
                        body_report = report_payload.get("report", {}).get("body_search", {})
                        row_result["compiler_report_decision"] = report_payload.get("report", {}).get("decision")
                        row_result["compiler_training_score"] = {
                            "passed": body_report.get("training_passed"), "total": body_report.get("training_total")}
                        require(body_report.get("training_passed") == replay["training"]["passed"]
                                and body_report.get("training_total") == replay["training"]["total"],
                                f"{inv_id}: independent Go training score differs from Gooo receipt")
                        if group and replay.get("status") == "pass":
                            group["validated"] += 1
                else:
                    row_result["compile_skip_reason"] = "CLI output had no emitted source"
            else:
                row_result["compile_skip_reason"] = "CLI stdout was not valid JSON"
            parsed_by_id[inv_id] = {"outer": outer, "state": state, "response": response,
                                    "choice": choice, "plan": plan}
        except Exception as exc:
            issue = str(exc)
            row_result["issues"].append(issue)
            issues.append(f"{inv_id}: {issue}")

        # Keep the digest denominator aligned with captured POSTs even when a
        # separate route, state, or receipt check failed above.
        if inv_id not in {item["invocation_id"] for item in request_hash_inputs}:
            try:
                raw_event = next(event for event in events_by_id[inv_id] if event.get("kind") == "laya_choice")
                outer = json.loads(raw_event["_request_raw"])
                state_raw = outer["state"]["request"]
                state = json.loads(state_raw)
                qwire = outer["questions"]
                require(len(qwire) == 1, f"{inv_id}: cannot reconstruct typed question options")
                qid, question = next(iter(qwire.items()))
                remaining = state["remaining_candidates"]
                options = [{"id": candidate["id"],
                            "description": "Try this exact expression: " + candidate["expression"]}
                           for candidate in remaining]
                request_hash_inputs.append({"invocation_id": inv_id, "state": state_raw,
                                            "question_id": qid, "instructions": question["instructions"],
                                            "options": options, "fallback": remaining[0]["id"],
                                            "provider_model": outer.get("model", "")})
                try:
                    payload = json.loads(stdout_raw)
                    decisions = [attempt.get("decision", {}) for attempt in
                                 payload.get("report", {}).get("body_search", {}).get("attempts", [])]
                    laya = [decision for decision in decisions if decision.get("mode") == "laya"]
                    if len(laya) == 1:
                        request_hash_receipts[inv_id] = laya[0].get("request_sha256")
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    pass
            except Exception as digest_exc:
                message = f"{inv_id}: cannot reconstruct typed decision request: {digest_exc}"
                row_result["issues"].append(message)
                issues.append(message)

        # A route or receipt failure must not suppress independent execution of
        # source that the compiler emitted. Keep a separate compile result row.
        if row_result["compiled_replay"] is None:
            try:
                payload = json.loads(stdout_raw)
                source_text = payload.get("source") if isinstance(payload, dict) else None
                if isinstance(source_text, str) and source_text:
                    oracle = oracles[row["intent_id"]]
                    suites = {name: case_rows(oracle[name]) for name in ("training", "holdout")}
                    replay = independent_go_replay(go_bin, output_dir, inv_id, source_text.encode(),
                                                    row["activity"], suites, env)
                    row_result["compiled_replay"] = replay
                    independent_replays.append({"invocation_id": inv_id, "phase": row["phase"],
                                                 "treatment": row["treatment"], "provider_model": row["provider_model"],
                                                 "replay": replay})
                    if replay.get("status") in ("pass", "test_harness_failure") and "training" in replay:
                        training_score = score_partition(oracle, "training", replay["training"]["observations"])
                        holdout_score = score_partition(oracle, "holdout", replay["holdout"]["observations"])
                        row_result["finite_scores"] = {"training": training_score, "holdout": holdout_score}
                        if group:
                            group["compiled"] += 1
                            for suite_name, part in (("training", training_score), ("holdout", holdout_score)):
                                for field, score in part.items():
                                    if isinstance(score, dict):
                                        group[suite_name][field + "_passed"] += score["passed"]
                                        group[suite_name][field + "_total"] += score["total"]
                        payload = None
                        try:
                            payload = json.loads(stdout_raw)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                        body = payload.get("report", {}).get("body_search", {}) if isinstance(payload, dict) else {}
                        candidate_id = body.get("selected_candidate_id")
                        vectors = oracle.get("candidate_outputs", {}).get(candidate_id, {})
                        matches_vector = all(
                            [case["actual"] for case in replay[suite_name]["observations"]] == vectors.get(suite_name)
                            for suite_name in ("training", "holdout"))
                        matches_training_receipt = (body.get("training_passed") == replay["training"]["passed"]
                                                    and body.get("training_total") == replay["training"]["total"])
                        if group and replay.get("status") == "pass" and matches_vector and matches_training_receipt:
                            group["validated"] += 1
                else:
                    row_result["compile_skip_reason"] = row_result.get("compile_skip_reason", "no emitted Go source")
            except Exception as replay_exc:
                row_result["compiled_replay"] = {"status": "independent_replay_failure", "error": str(replay_exc)}
                row_result["issues"].append(f"independent Go replay failed: {replay_exc}")
                issues.append(f"{inv_id}: independent Go replay failed: {replay_exc}")
        invocation_results.append(row_result)

    # Reconstruct the exact typed Request JSON with Go's encoder, including the
    # trailing optional provider_model field used by the source implementation.
    hash_errors = []
    if request_hash_inputs:
        hashes = typed_request_hashes(go_bin, output_dir, env, request_hash_inputs)
        by_result = {item["invocation_id"]: item for item in invocation_results}
        for request in request_hash_inputs:
            inv_id = request["invocation_id"]
            actual, receipt = hashes[inv_id], request_hash_receipts.get(inv_id)
            result = by_result[inv_id]
            result["reconstructed_typed_request_sha256"] = actual
            result["typed_request_hash_verified"] = bool(receipt == actual)
            if result["typed_request_hash_verified"]:
                group = group_accumulators[(result["treatment"], result["provider_model"])] if result["phase"] == "measured" else None
                if group:
                    group["typed_digest_passed"] += 1
            else:
                msg = f"{inv_id}: typed decision request hash differs from returned decision receipt"
                result["issues"].append(msg)
                hash_errors.append(msg)
        issues.extend(hash_errors)

    # Compute finite-case oracle discrimination denominators independently from
    # the emitted source's observations; every planned invocation remains listed.
    group_results = []
    for treatment in TREATMENTS:
        for model in MODELS:
            values = group_accumulators[(treatment, model)]
            group_results.append({
                "treatment": treatment, "provider_model": model,
                "planned_invocations": values["planned"], "captured_choice_posts": values["captured_posts"],
                "cli_successes": values["cli_successes"],
                "request_provider_model_verified": {"passed": values["request_route_passed"], "total": values["planned"]},
                "provider_reply_route_verified": {"passed": values["reply_route_passed"], "total": values["planned"]},
                "provider_route_verified": {"passed": min(values["request_route_passed"], values["reply_route_passed"]),
                                            "total": values["planned"]},
                "provider_receipt_verified": {"passed": values["receipt_route_passed"], "total": values["planned"]},
                "typed_request_digest_verified": {"passed": values["typed_digest_passed"], "total": values["planned"]},
                "source_feedback_verified": {"passed": values["feedback_passed"], "total": values["planned"]},
                "independent_compiled_invocations": values["compiled"],
                "independently_validated_invocations": values["validated"],
                "compiled_finite_scores": {
                    suite: {
                        scope: {
                            "passed": values[suite][scope + "_passed"],
                            "observed_total": values[suite][scope + "_total"],
                            "planned_total": values[suite][scope + "_planned"],
                            "unknown_total": values[suite][scope + "_planned"] - values[suite][scope + "_total"],
                        }
                        for scope in ("all_cases", "candidate_discriminating_cases", "candidate_invariant_cases")
                    } for suite in ("training", "holdout")
                },
            })

    # Recompute the runner report's high-level denominators and finite score
    # aggregates from the raw captures and independent compiler executions.
    report_result_by_id = {item["invocation_id"]: item for item in report_invocations}
    for independent in invocation_results:
        saved = report_result_by_id.get(independent["invocation_id"], {})
        row_identity_ok = (
            saved.get("sequence") == independent["sequence"]
            and saved.get("phase") == independent["phase"]
            and saved.get("intent_id") == independent["intent_id"]
            and saved.get("treatment") == independent["treatment"]
            and saved.get("provider_model_pin") == independent["provider_model"]
            and saved.get("cli_exit_code") == independent["cli_exit_code"]
            and saved.get("choice_post_count") == independent["captured_choice_posts"]
        )
        if not row_identity_ok:
            issues.append(f"{independent['invocation_id']}: runner report invocation identity/count differs from independent audit")
        if "provider_route_pin_match" in saved and saved.get("provider_route_pin_match") != independent["request_provider_model_verified"]:
            issues.append(f"{independent['invocation_id']}: runner request-route summary differs from raw outer.model")
        if "provider_context_pin_match" in saved and saved.get("provider_context_pin_match") != independent["provider_context_verified"]:
            issues.append(f"{independent['invocation_id']}: runner feedback summary differs from raw serialized state")
        if "captured_reply_matches_compiler_choice" in saved and saved.get("captured_reply_matches_compiler_choice") != independent.get("captured_reply_matches_compiler_choice", False):
            issues.append(f"{independent['invocation_id']}: runner choice agreement differs from raw reply/source")
        independent_scores = independent.get("finite_scores", {})
        saved_validation = saved.get("compiled_validation", {})
        for suite_name in ("training", "holdout"):
            expected_score = independent_scores.get(suite_name, {}).get("all_cases")
            saved_score = saved_validation.get("finite_scores", {}).get(suite_name, {}).get("all_cases")
            if expected_score is not None and saved_score is not None and expected_score != saved_score:
                issues.append(f"{independent['invocation_id']}: runner {suite_name} score differs from independent compile")
    saved_route = report.get("capture", {}).get("measured_requests_matching_plan_provider_model", {})
    saved_feedback = report.get("capture", {}).get("measured_requests_matching_plan_feedback", {})
    independent_measured = [item for item in invocation_results if item["phase"] == "measured"]
    request_route_count = sum(bool(item["request_provider_model_verified"]) for item in independent_measured)
    feedback_count = sum(bool(item["provider_context_verified"]) for item in independent_measured)
    if saved_route != {"passed": request_route_count, "total": 72}:
        issues.append("runner measured request-route denominator/count differs from raw outer.model audit")
    if saved_feedback != {"passed": feedback_count, "total": 72}:
        legacy_derived_issues.append(
            "original runner report measured feedback summary disagrees with raw model-state audit: "
            f"reported {saved_feedback.get('passed')}/{saved_feedback.get('total')}, "
            f"raw verified {feedback_count}/72; runner status was {metadata.get('status')}"
        )
    saved_groups = {(item.get("context_treatment"), item.get("provider_model")): item
                    for item in report.get("results_by_context_and_model", [])}
    if set(saved_groups) != {(treatment, model) for treatment in TREATMENTS for model in MODELS}:
        issues.append("runner report does not have all six treatment/model aggregates")
    for own in group_results:
        saved = saved_groups.get((own["treatment"], own["provider_model"]), {})
        checks = {
            "planned_intent_replicate_invocations": own["planned_invocations"],
            "captured_laya_choice_posts": own["captured_choice_posts"],
            "compiler_cli_successes": own["cli_successes"],
            "provider_model_receipt_pin_agreement": own["provider_receipt_verified"],
            "independent_go_validations": own["independently_validated_invocations"],
        }
        for key, expected in checks.items():
            actual = saved.get(key)
            if key == "provider_model_receipt_pin_agreement":
                matches = actual == expected
            elif key == "independent_go_validations":
                matches = actual == expected
            else:
                matches = actual == expected
            if not matches:
                issues.append(f"{own['treatment']}/{own['provider_model']}: runner group {key} differs from independent denominator")
        runner_scores = saved.get("compiled_finite_scores", {})
        for suite_name in ("training", "holdout"):
            for scope in ("all_cases", "candidate_discriminating_cases", "candidate_invariant_cases"):
                actual = runner_scores.get(suite_name, {}).get(scope)
                expected = own["compiled_finite_scores"][suite_name][scope]
                if actual != expected:
                    issues.append(f"{own['treatment']}/{own['provider_model']}: runner {suite_name}/{scope} aggregate differs from independent compile")
    typed_hash_count = sum(row.get("typed_request_hash_verified") is True for row in invocation_results)
    reply_route_count = sum(row.get("provider_reply_route_verified") is True for row in invocation_results)
    correction_validation = verify_derived_correction(
        run_dir, report, report_bytes, metadata, preexecution, design, invocation_results,
        report_invocations, events, typed_hash_count, feedback_count, reply_route_count,
    )
    warmup_rows = [row for row in invocation_results if row["phase"] == "warmup"]
    report_out = {
        "schema": "gooo/pinned-context-independent-replay-report/v1",
        "run_id": run_dir.name, "study_id": design["study_id"],
        "source_pin": {"revision": EXPECTED_SOURCE_REVISION, "binary_sha256": EXPECTED_BINARY_SHA256,
                       "go_version": go_version},
        "denominators": {"planned_invocations": len(plans), "planned_measured": 72,
                         "planned_warmups": 2, "captured_choice_posts": len(choice_events),
                         "captured_measured_choice_posts": sum(
                             plans[event["invocation_id"]]["phase"] == "measured" for event in choice_events),
                         "captured_warmup_choice_posts": sum(
                             plans[event["invocation_id"]]["phase"] == "warmup" for event in choice_events),
                         "mock_preflight_choice_posts": preflight["mock_choice_posts"],
                         "mock_preflight_actual_laya_calls": preflight["actual_laya_calls"],
                         "independent_compiled_invocations": sum(row["compiled_replay"] is not None for row in invocation_results),
                         "typed_request_hash_checks": len(request_hash_inputs),
                         "warmup_route_verified": sum(bool(row["provider_route_verified"]) for row in warmup_rows),
                         "warmup_receipt_verified": sum(bool(row["provider_receipt_verified"]) for row in warmup_rows),
                         "warmup_typed_request_hash_verified": sum(bool(row["typed_request_hash_verified"]) for row in warmup_rows),
                         "warmup_independent_compiles": sum(row["compiled_replay"] is not None for row in warmup_rows)},
        "preflight": preflight,
        "group_results": group_results,
        "invocation_results": invocation_results,
        "privacy_scan_count": len(privacy_results), "privacy_results": privacy_results,
        "issues": issues,
        "legacy_derived_issues": legacy_derived_issues,
        "derived_correction_validation": correction_validation,
        "decision": "PASS" if not issues and len(invocation_results) == 74
                    and len(choice_events) == 74 and all(row["compiled_replay"].get("status") == "pass"
                                                         for row in invocation_results)
                    and correction_validation.get("status") == "VERIFIED_TWO_FIELD_DERIVATION"
                    else "FAIL",
        "limitations": ["The holdout suite is a reused finite benchmark, not a fresh generalization sample.",
                        "Independent Go execution confirms emitted behavior on declared finite vectors only.",
                        "Source-CI actual values remain observed failures supplied as advisory feedback; they are not promoted to verified passing behavior."],
    }
    write_json(output_dir / "independent-replay-report.json", report_out)
    lines = ["# Independent pinned-context replay", "",
             f"Decision: **{report_out['decision']}**. The frozen plan contains 72 measured calls and 2 separate warmups.", "",
             "| Treatment | Model | Planned | POSTs | Route | Receipt | Typed digest | Feedback | Go builds | Training score | Reused holdout score |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for group in group_results:
        training = group["compiled_finite_scores"]["training"]["all_cases"]
        holdout = group["compiled_finite_scores"]["holdout"]["all_cases"]
        lines.append(f"| {group['treatment']} | {group['provider_model']} | {group['planned_invocations']} | {group['captured_choice_posts']} | {group['provider_route_verified']['passed']}/{group['provider_route_verified']['total']} | {group['provider_receipt_verified']['passed']}/{group['provider_receipt_verified']['total']} | {group['typed_request_digest_verified']['passed']}/{group['typed_request_digest_verified']['total']} | {group['source_feedback_verified']['passed']}/{group['source_feedback_verified']['total']} | {group['independent_compiled_invocations']}/12 | {training['passed']}/{training['planned_total']} (observed {training['observed_total']}, unknown {training['unknown_total']}) | {holdout['passed']}/{holdout['planned_total']} (observed {holdout['observed_total']}, unknown {holdout['unknown_total']}) |")
    lines.extend(["", f"MOCK preflight: {preflight['mock_choice_posts']} choice POST templates, {preflight['actual_laya_calls']} actual Laya calls.",
                  f"Privacy-scanned raw provider requests: {len(privacy_results)}.", "",
                  "## Invocation failures", ""])
    failures = [row for row in invocation_results if row["issues"] or row["compiled_replay"] and row["compiled_replay"].get("status") != "pass"]
    lines.extend([f"- `{row['invocation_id']}`: " + "; ".join(row["issues"] or [row["compiled_replay"]["status"]]) for row in failures] or ["- None"])
    lines.extend(["", "## Scope", "", *["- " + item for item in report_out["limitations"]], ""])
    (output_dir / "independent-replay-report.md").write_text("\n".join(lines), encoding="utf-8")
    return report_out


def validate_design_only(design_dir: Path, output_dir: Path) -> dict:
    """Validate the frozen, source-bound protocol design before study capture exists."""
    design_dir, output_dir = design_dir.resolve(), output_dir.resolve()
    require(design_dir == DESIGN_DIR.resolve(),
            "design-only mode must read the repository's pinned-context-design directory")
    require(not output_dir.exists(), f"refusing to overwrite design validation output: {output_dir}")
    output_dir.mkdir(parents=True)
    design, provenance, bridge, plans = verify_design(design_dir)
    manifest = read_json(ROOT / "manifest.json")
    verify_source_ci_and_feedback(manifest, design, design_dir)
    preflight = validate_preflight(design_dir, design, plans,
                                   {intent_id: read_json(design_dir / "feedback" / f"{intent_id}.json")
                                    for intent_id in INTENTS}, output_dir)
    capture_dir = ROOT / "audit" / "pinned-compact-context-2026-09-30"
    capture_status_path = capture_dir / "capture-status.json"
    capture_artifact_present = capture_dir.exists()
    reported_capture_status = read_json(capture_status_path) if capture_status_path.is_file() else None
    if not capture_artifact_present:
        capture_status_label = "NOT_CAPTURED"
    elif reported_capture_status is None:
        capture_status_label = "CAPTURE_ARTIFACT_PRESENT_NOT_VALIDATED"
    else:
        capture_status_label = "CAPTURE_ARTIFACT_PRESENT_NOT_VALIDATED"
    result = {
        "schema": "gooo/pinned-context-independent-replay-report/v1",
        "mode": "design_only",
        "study_id": design["study_id"],
        "design_sha256": sha256((design_dir / "study-design.json").read_bytes()),
        "decision": "DESIGN_SOURCE_BOUND_VALIDATED_NO_MODEL_CALLS",
        "capture_status": capture_status_label,
        "source_pin": {"revision": EXPECTED_SOURCE_REVISION, "binary_sha256": EXPECTED_BINARY_SHA256},
        "planned": {"measured_calls": 72, "warmups": 2, "factorial_cells": 24},
        "preflight": preflight,
        "capture": {"study_calls_started": False if not capture_artifact_present else None,
                    "captured_choice_posts_reported_unverified": (reported_capture_status or {}).get("captured_laya_choice_posts"),
                    "actual_study_model_calls": None,
                    "captured_measured_posts": None, "captured_warmup_posts": None},
        "source_ci_provenance_verified": True,
        "native_identity_bridge_verified": True,
        "feedback_payloads_source_bound": len(INTENTS),
        "limitations": ["This CI run validates the frozen design, source-CI bindings, native identity bridge, and local MOCK protocol preflight only.",
                        ("No measured or warmup capture artifact is present." if not capture_artifact_present else
                         "A study capture artifact is present but was not validated in design-only mode; this job makes no claim about its model calls or results.")],
    }
    write_json(output_dir / "independent-replay-report.json", result)
    (output_dir / "independent-replay-report.md").write_text(
        "# Pinned-context design validation\n\n"
        "Decision: **DESIGN_SOURCE_BOUND_VALIDATED_NO_MODEL_CALLS**.\n\n"
        "The frozen source-bound design validates for 72 planned measured calls and 2 planned warmups. "
        "Its 24 local MOCK protocol templates and cached-tokenizer records are bound and report zero actual Laya calls. "
        + ("No measured capture artifact is present.\n" if not capture_artifact_present else
           "A separate study capture artifact exists but is not audited by this design-only report.\n"),
        encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-go-version", default=EXPECTED_GO_VERSION)
    parser.add_argument("--design-only", action="store_true",
                        help="validate the frozen source-bound design and local MOCK preflight without a study capture")
    args = parser.parse_args()
    try:
        report = (validate_design_only(args.run_dir, args.output) if args.design_only
                  else validate_run(args.run_dir, args.output, args.require_go_version))
    except Exception as exc:
        parser.exit(2, f"pinned-context replay validation failed before report completion: {exc}\n")
    if args.design_only:
        print(report["decision"] + ": no study capture or actual model inference was performed")
        return
    print(f"{report['decision']}: {report['denominators']['captured_measured_choice_posts']}/72 measured POSTs; "
          f"{report['denominators']['independent_compiled_invocations']}/74 independently compiled.")
    if report["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
