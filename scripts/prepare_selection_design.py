#!/usr/bin/env python3
"""Prepare randomized training-only Laya treatment plans without contacting Laya."""
from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

from selection_support import ContextRejected, sha256, verify_ci_failure_context, verify_zip_extraction


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "selection-design"
CI_RUN = ROOT / "audit" / "ci-run-36673382253"
SEED = 20260930
COMPILER_REVISION = "29d44bc778d85aee03b9af500bd83dc98f368189"
Laya = {"package_version": "0.3.21", "model": "english", "model_revision": "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851", "device": "cpu", "threads": 4}


def write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def check_ci_artifact(intent_id: str, fixture: bytes, plan_bytes: bytes, plan: dict, activity: str) -> tuple[dict, dict]:
    record = json.loads((CI_RUN / "download-record.json").read_text())
    archive = (CI_RUN / "github-artifact.zip").read_bytes()
    if sha256(archive) != record["github_reported_archive_sha256"]:
        raise RuntimeError("downloaded GitHub artifact archive hash mismatch")
    verify_zip_extraction(CI_RUN / "github-artifact.zip", CI_RUN / "files")
    if record["workflow_conclusion"] != "success" or record["compiler_revision"] != COMPILER_REVISION:
        raise RuntimeError("initial CI context does not come from the pinned successful workflow")
    case_dir = CI_RUN / "files" / intent_id
    evidence = json.loads((case_dir / "evidence.json").read_text())
    body_report = json.loads((case_dir / "body-codegen-report.json").read_text())
    emitted = (case_dir / "emitted.go").read_bytes()
    report = body_report
    cases = plan["test_cases"]
    training_bytes = (json.dumps(cases, indent=2, ensure_ascii=False) + "\n").encode()
    canonical_training = json.dumps(cases, separators=(",", ":"), ensure_ascii=False).encode()
    expected = {
        "compiler_revision": COMPILER_REVISION,
        "fixture_sha256": sha256(fixture),
        "plan_sha256": sha256(plan_bytes),
        "activity": activity,
        "activity_id": report["activity_id"],
        "training_suite_sha256": sha256(training_bytes),
        "candidate_id": plan["candidates"][0]["id"],
        "candidate_expression": plan["candidates"][0]["expression"],
        "compiler_generated_digest": report["generated_digest"],
        "generated_source_sha256": sha256(emitted),
    }
    binding_bytes = (json.dumps(evidence["binding"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    if sha256(binding_bytes) != evidence["binding_sha256"]:
        raise RuntimeError(f"{intent_id}: initial CI evidence binding hash mismatch")
    if sha256((case_dir / "plan.search-plan.json").read_bytes()) != sha256(plan_bytes):
        raise RuntimeError(f"{intent_id}: current plan differs from the initial CI plan")
    if evidence["binding"].get("plan_sha256") != sha256(plan_bytes):
        raise RuntimeError(f"{intent_id}: initial CI receipt does not bind the exact current plan")
    if sha256((case_dir / "go-test.stdout.raw").read_bytes()) != evidence["go_test_stdout_sha256"]:
        raise RuntimeError(f"{intent_id}: raw external test output hash mismatch")
    if evidence["binding"]["fixture_sha256"] != sha256(fixture):
        raise RuntimeError(f"{intent_id}: fixture differs from the source-bound CI failure")
    if evidence["binding"]["generated_source_sha256"] != sha256(emitted):
        raise RuntimeError(f"{intent_id}: emitted source differs from the source-bound CI failure")
    if report["compiler_source_sha"] != COMPILER_REVISION:
        raise RuntimeError(f"{intent_id}: compiler report revision mismatch")
    if report["body_search"]["selected_candidate_id"] != "identity" or report["body_search"]["selected_expression"] != "input":
        raise RuntimeError(f"{intent_id}: CI evidence is not the declared failed identity candidate")
    if report["body_search"]["training_suite_sha256"] != "sha256:" + sha256(canonical_training):
        raise RuntimeError(f"{intent_id}: Gooo training-suite receipt differs from the current training cases")
    if sha256((case_dir / "gooo.stdout.raw").read_bytes()) != evidence["cli_stdout_sha256"]:
        raise RuntimeError(f"{intent_id}: raw Gooo source output hash mismatch")
    suffix = verify_ci_failure_context(evidence, expected)
    return evidence, {**expected, "context_append_sha256": sha256(suffix.encode()), "context_append_codepoints": len(suffix)}


def main() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    planned = []
    gate_records = {}
    base_intents = {}
    for item in manifest["intents"]:
        plan = json.loads((ROOT / item["plan"]).read_text())
        fixture = (ROOT / item["fixture"]).read_bytes()
        if "holdout_test_cases" in plan:
            raise RuntimeError(f"{item['id']}: base plan must contain only training cases")
        if plan["max_attempts"] != 1 or plan["candidates"][0]["id"] != "identity":
            raise RuntimeError(f"{item['id']}: expected bounded declared-order candidate baseline")
        activity = item["activity"]
        base_intents[item["id"]] = plan["intent"]
        evidence, expected_binding = check_ci_artifact(item["id"], fixture, (ROOT / item["plan"]).read_bytes(), plan, activity)
        exact_suffix = verify_ci_failure_context(evidence, expected_binding)
        stale_evidence = copy.deepcopy(evidence)
        stale_evidence["binding"]["generated_source_sha256"] = "0" * 64
        try:
            verify_ci_failure_context(stale_evidence, expected_binding)
        except ContextRejected as exc:
            stale_rejection = str(exc)
        else:
            raise RuntimeError("stale source context unexpectedly passed its provenance gate")
        gate_records[item["id"]] = {
            "matched_ci_context_accepted": True,
            "matched_binding": expected_binding,
            "matched_context_append_sha256": sha256(exact_suffix.encode()),
            "stale_source_context_accepted": False,
            "stale_source_field": "generated_source_sha256",
            "stale_source_rejection": stale_rejection,
            "stale_source_context_was_not_appended": True,
        }
        for treatment in ("no_context", "exact_matching_failed_ci_context", "rejected_stale_source_context"):
            candidate_plan = copy.deepcopy(plan)
            if treatment == "exact_matching_failed_ci_context":
                candidate_plan["intent"] += exact_suffix
            if len(candidate_plan["intent"]) > 2000:
                raise RuntimeError(f"{item['id']}/{treatment}: intent exceeds compiler limit")
            planned.append({
                "intent_id": item["id"],
                "activity": activity,
                "fixture": item["fixture"],
                "treatment": treatment,
                "plan": candidate_plan,
            })

    random.Random(SEED).shuffle(planned)
    OUT.mkdir(parents=True, exist_ok=True)
    design_rows = []
    for ordinal, row in enumerate(planned, start=1):
        invocation_id = f"{ordinal:02d}-{row['intent_id']}-{row['treatment']}"
        plan_bytes = write_json(OUT / "plans" / f"{invocation_id}.search-plan.json", row["plan"])
        base_intent = base_intents[row["intent_id"]]
        entry = {
            "sequence": ordinal,
            "invocation_id": invocation_id,
            "intent_id": row["intent_id"],
            "activity": row["activity"],
            "fixture": row["fixture"],
            "fixture_sha256": sha256((ROOT / row["fixture"]).read_bytes()),
            "treatment": row["treatment"],
            "plan_path": f"plans/{invocation_id}.search-plan.json",
            "plan_sha256": sha256(plan_bytes),
            "plan_intent_sha256": sha256(row["plan"]["intent"].encode()),
            "intent_is_base": row["plan"]["intent"] == base_intent,
            "candidate_count": len(row["plan"]["candidates"]),
            "training_case_count": len(row["plan"]["test_cases"]),
            "max_attempts": row["plan"]["max_attempts"],
            "context_gate": gate_records[row["intent_id"]]["stale_source_rejection"] if row["treatment"] == "rejected_stale_source_context" else ("accepted_exact_source_bound_ci_context" if row["treatment"] == "exact_matching_failed_ci_context" else "no_context"),
        }
        design_rows.append(entry)

    study = {
        "schema": "gooo/ir-search-ci-context-selection-design/v1",
        "study_id": "ir-search-ci-context-laya-2026-09-30",
        "status": "prepared_not_executed",
        "compiler_revision": COMPILER_REVISION,
        "binary_sha256": manifest["compiler_binary_sha256_local_reference"],
        "runtime": Laya,
        "provider_policy": "offline loopback-only Laya service; no credentials or model downloads",
        "treatment_order_seed": SEED,
        "randomized_order": True,
        "replicates_per_intent_treatment": 1,
        "warmup_invocations": 0,
        "choice_attempts_per_invocation": 1,
        "training_case_count_total": sum(row["training_case_count"] for row in design_rows),
        "invocation_count": len(design_rows),
        "context_gate_by_intent": gate_records,
        "invocations": design_rows,
        "limitations": [
            "One randomized run, n=1 per intent-treatment cell; no statistical or general latency claim.",
            "The exact-context treatment appends a bounded plain-text summary to IRBodySearchPlan.intent because the compiler has no structured external-CI-context field.",
            "The stale-source context is rejected before plan construction; the invocation uses the original intent, so stale text does not reach Laya.",
            "All selection plans contain training cases only. Holdout cases will be opened only after all candidate selections finish.",
            "No model-forward-only latency or host CPU increase will be inferred from process-level timing samples."
        ]
    }
    design_bytes = write_json(OUT / "study-design.json", study)
    (OUT / "study-design.sha256").write_text(sha256(design_bytes) + "  study-design.json\n")
    print(f"prepared {len(design_rows)} plans; design SHA-256 {sha256(design_bytes)}; no Laya calls made")


if __name__ == "__main__":
    main()
