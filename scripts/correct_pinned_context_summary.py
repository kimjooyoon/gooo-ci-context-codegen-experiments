#!/usr/bin/env python3
"""Write a transparent derived-summary correction from an immutable capture."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "audit" / "pinned-compact-context-2026-09-30"
DEFAULT_OUTPUT = DEFAULT_RUN / "derived-correction"
EXPECTED_PUBLIC_CHECKPOINT = "9dc4beb54002880da2a919d2b9b65148718500f5"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> tuple[Any, bytes]:
    raw = path.read_bytes()
    return json.loads(raw), raw


def write_json(path: Path, value: Any) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
    return raw


def contained(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"artifact path escapes the run directory: {relative}") from exc
    if not candidate.is_file():
        raise RuntimeError(f"missing capture evidence: {relative}")
    return candidate


def state_request(outer: dict) -> tuple[dict, str]:
    state_container = outer.get("state")
    if not isinstance(state_container, dict) or not isinstance(state_container.get("request"), str):
        raise RuntimeError("captured model request has no serialized state.request string")
    raw_state = state_container["request"]
    state = json.loads(raw_state)
    if not isinstance(state, dict):
        raise RuntimeError("captured serialized model state is not a JSON object")
    return state, raw_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"refusing to overwrite derived correction: {output_dir}")

    design, design_bytes = load_json(run_dir / "study-design.json")
    original_report, original_report_bytes = load_json(run_dir / "report.json")
    metadata, metadata_bytes = load_json(run_dir / "run-metadata.json")
    preexecution, preexecution_bytes = load_json(run_dir / "preexecution.json")
    source_provenance, source_provenance_bytes = load_json(run_dir / "source-provenance.json")
    captured_events, captured_events_bytes = load_json(run_dir / "proxy-events.json")
    index, index_bytes = load_json(run_dir / "invocation-index.json")
    cli_records, cli_records_bytes = load_json(run_dir / "cli-invocation-records.json")
    capture_status, capture_status_bytes = load_json(run_dir / "capture-status.json")

    if design.get("study_id") != original_report.get("study_id"):
        raise RuntimeError("run design and original report have different study IDs")
    if sha256(design_bytes) != original_report.get("design_sha256"):
        raise RuntimeError("original report does not bind the saved study design")
    if metadata.get("preexecution_sha256") != sha256(preexecution_bytes):
        raise RuntimeError("run metadata does not bind the preexecution receipt")
    code_provenance = preexecution.get("study_code_provenance", {})
    runner_path = ROOT / "scripts" / "run_pinned_context_study.py"
    prep_path = ROOT / "scripts" / "prepare_pinned_context_study.py"
    if code_provenance.get("runner_script_sha256") != sha256(runner_path.read_bytes()):
        raise RuntimeError("current runner bytes differ from the preexecution script pin")
    if code_provenance.get("preparation_script_sha256") != sha256(prep_path.read_bytes()):
        raise RuntimeError("current preparation bytes differ from the preexecution script pin")
    if code_provenance.get("public_frozen_checkpoint_revision") != EXPECTED_PUBLIC_CHECKPOINT:
        raise RuntimeError("preexecution does not bind the public frozen checkpoint")

    plans = design.get("plans", [])
    if len(plans) != 74 or sum(row.get("phase") == "measured" for row in plans) != 72 \
            or sum(row.get("phase") == "warmup" for row in plans) != 2:
        raise RuntimeError("saved plan is not the frozen 72 measured + 2 warmup design")
    if (capture_status.get("planned_invocations") != 74
            or capture_status.get("completed_invocation_records") != 74
            or capture_status.get("captured_laya_choice_posts") != 74
            or capture_status.get("unstarted_invocation_ids") != []):
        raise RuntimeError("capture status does not show all 74 planned exchanges completed")
    if metadata.get("health_after_validated") is not True:
        raise RuntimeError("pinned health-after check did not pass")
    if original_report.get("decision") != "PARTIAL_CAPTURE_OR_VALIDATION":
        raise RuntimeError("expected the original report's bookkeeping-only partial decision")
    old_feedback_summary = original_report.get("capture", {}).get("measured_requests_matching_plan_feedback")
    if old_feedback_summary != {"passed": 0, "total": 72}:
        raise RuntimeError("original report does not contain the known zero-of-72 bookkeeping summary")
    if original_report.get("capture", {}).get("failed_invocations") != 0 \
            or original_report.get("capture", {}).get("unknown_or_unvalidated_invocations") != 0:
        raise RuntimeError("original capture contains failed or unvalidated invocation rows")
    if original_report.get("capture", {}).get("measured_requests_matching_plan_provider_model") != {
            "passed": 72, "total": 72}:
        raise RuntimeError("provider model pin aggregate is not 72/72")

    events = captured_events.get("events", [])
    choice_events = [event for event in events if event.get("kind") == "laya_choice"]
    if len(choice_events) != 74 or captured_events.get("choice_post_count") != 74:
        raise RuntimeError("raw provider event log does not contain exactly 74 choice posts")
    events_by_id: dict[str, list[dict]] = {}
    for event in choice_events:
        events_by_id.setdefault(event.get("invocation_id", ""), []).append(event)
    if set(events_by_id) != {row["invocation_id"] for row in plans} \
            or any(len(items) != 1 for items in events_by_id.values()):
        raise RuntimeError("raw provider event log is missing, duplicating, or adding an invocation")

    result_rows = {row["invocation_id"]: row for row in original_report.get("invocation_results", [])}
    audit_rows = {row["invocation_id"]: row
                  for row in original_report.get("request_route_and_feedback_audits", [])}
    cli_by_id = {row["invocation_id"]: row for row in cli_records}
    index_by_id = {row["invocation_id"]: row for row in index.get("invocations", [])}
    if not all(len(mapping) == 74 for mapping in (result_rows, audit_rows, cli_by_id, index_by_id)):
        raise RuntimeError("one of the saved invocation result/audit/index files lacks 74 unique rows")

    checks = []
    response_route_matches = 0
    typed_receipt_matches = 0
    for plan in plans:
        invocation_id = plan["invocation_id"]
        event = events_by_id[invocation_id][0]
        request_path = contained(run_dir, event["request_file"])
        response_path = contained(run_dir, event["response_file"])
        request_bytes = request_path.read_bytes()
        response_bytes = response_path.read_bytes()
        if sha256(request_bytes) != event.get("request_sha256"):
            raise RuntimeError(f"{invocation_id}: raw POST bytes fail the event SHA-256")
        if sha256(response_bytes) != event.get("response_sha256"):
            raise RuntimeError(f"{invocation_id}: raw reply bytes fail the event SHA-256")
        outer = json.loads(request_bytes)
        state, serialized_state = state_request(outer)
        expected_profile = plan.get("expected_model_state_profile", {})
        expected_state = expected_profile.get("decoded_state")
        state_match = state == expected_state
        expected_state_sha = expected_profile.get("state_request_sha256", "")
        if expected_state_sha.startswith("sha256:"):
            expected_state_sha = expected_state_sha.removeprefix("sha256:")
        state_sha_match = sha256(serialized_state.encode("utf-8")) == expected_state_sha
        model_match = outer.get("model") == plan.get("provider_model")
        if not (state_match and state_sha_match and model_match):
            raise RuntimeError(f"{invocation_id}: raw model state/model differs from frozen plan "
                               f"(state={state_match}, state_sha={state_sha_match}, model={model_match}, "
                               f"actual_sha=sha256:{sha256(serialized_state.encode('utf-8'))}, "
                               f"expected_sha={expected_state_sha!r})")

        response = json.loads(response_bytes)
        reply_route_match = response.get("routing", {}).get("model") == plan.get("provider_model")
        response_route_matches += int(reply_route_match)
        if not reply_route_match:
            raise RuntimeError(f"{invocation_id}: captured response routing model differs from plan")
        expected_choice = response.get("answers", {}).get("body_ir_search", {}).get("choice")
        if event.get("selected_candidate_id") != expected_choice:
            raise RuntimeError(f"{invocation_id}: captured choice differs from the raw response")

        result = result_rows[invocation_id]
        audit = audit_rows[invocation_id]
        if result.get("provider_context_pin_match") is not True:
            raise RuntimeError(f"{invocation_id}: original per-invocation context verification is not true")
        if result.get("decision") != "CAPTURED_AND_COMPILED":
            raise RuntimeError(f"{invocation_id}: original row is not fully compiled and validated")
        receipt_hashes = audit.get("reconstructed_typed_request_sha256s", [])
        if (not audit.get("typed_request_hash_matches_receipt")
                or not audit.get("provider_model_typed_request_match")
                or not receipt_hashes
                or any(value != audit.get("receipt_request_sha256") for value in receipt_hashes)):
            raise RuntimeError(f"{invocation_id}: reconstructed typed request does not match its receipt")
        typed_receipt_matches += 1
        if audit.get("provider_receipt_route_pin_match") is not True:
            raise RuntimeError(f"{invocation_id}: compiler receipt route pin did not pass")
        if audit.get("protocol_preflight_request_match") is not True:
            raise RuntimeError(f"{invocation_id}: raw POST differs from its frozen MOCK template")
        if not cli_by_id[invocation_id].get("attempted", True) \
                or index_by_id[invocation_id].get("decision") != "CAPTURED_AND_COMPILED":
            raise RuntimeError(f"{invocation_id}: invocation index or CLI record is incomplete")

        checks.append({
            "invocation_id": invocation_id, "phase": plan["phase"],
            "treatment": plan["treatment"], "provider_model": plan["provider_model"],
            "request_sha256": event["request_sha256"], "response_sha256": event["response_sha256"],
            "model_pin_match": model_match, "decoded_state_profile_match": state_match,
            "serialized_state_sha256_match": state_sha_match,
            "per_invocation_context_summary_true": True,
        })

    measured = [row for row in checks if row["phase"] == "measured"]
    feedback_passed = sum(row["decoded_state_profile_match"] and row["serialized_state_sha256_match"]
                          for row in measured)
    if len(measured) != 72 or feedback_passed != 72 or typed_receipt_matches != 74 \
            or response_route_matches != 74:
        raise RuntimeError("raw evidence does not support a complete 72/72 context correction")
    if sum(row.get("provider_context_pin_match") is True
           for row in original_report["invocation_results"] if row.get("phase") == "measured") != 72:
        raise RuntimeError("saved per-invocation summaries do not independently show 72/72")

    corrected = copy.deepcopy(original_report)
    corrected["capture"]["measured_requests_matching_plan_feedback"]["passed"] = feedback_passed
    corrected["decision"] = "CAPTURED_AND_COMPILED"
    allowed_modified_fields = [
        "capture.measured_requests_matching_plan_feedback.passed",
        "decision",
    ]
    if (set(corrected) != set(original_report)
            or corrected["capture"]["measured_requests_matching_plan_feedback"]["total"] != 72):
        raise RuntimeError("derived summary changed an unapproved report field")
    changed_fields = []
    if corrected["capture"]["measured_requests_matching_plan_feedback"]["passed"] != old_feedback_summary["passed"]:
        changed_fields.append({"path": allowed_modified_fields[0],
                               "old": old_feedback_summary["passed"], "new": feedback_passed})
    if corrected["decision"] != original_report["decision"]:
        changed_fields.append({"path": "decision", "old": original_report["decision"],
                               "new": corrected["decision"]})
    if [row["path"] for row in changed_fields] != allowed_modified_fields:
        raise RuntimeError("derived report diff does not match the explicit correction whitelist")

    # The corrected report is derived alongside the original; it never overwrites it.
    output_dir.mkdir(parents=True)
    corrected_bytes = write_json(output_dir / "report.json", corrected)
    md_lines = [
        "# Pinned compact training context study — derived summary correction", "",
        "This report is a derived correction to the original capture summary. The original `report.json` and `report.md` remain unchanged.",
        "The correction fixes a bookkeeping omission: per-invocation context checks and raw model-state/profile hashes pass, while the original request-audit aggregate did not copy those verified results into its summary count.", "",
        f"Original capture report SHA-256: `{sha256(original_report_bytes)}`.",
        f"Derived report decision: **{corrected['decision']}**. Design SHA-256: `{corrected['design_sha256']}`.", "",
        "## Measured results", "",
        "All 72 measured invocations and both warmups have one captured provider POST, a matching pinned response route, a typed request digest matching its receipt, and a model state matching the frozen profile. Each compiled output passed independent Go validation against the reused finite oracle.", "",
        "| Context | Model | Go validations | Training all | Training discriminating | Reused holdout all | Reused holdout discriminating | Receipt route |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    def score_text(value: dict) -> str:
        return f"{value['passed']}/{value['planned_total']} (observed {value['observed_total']}, unknown {value['unknown_total']})"

    for group in corrected.get("results_by_context_and_model", []):
        score = group["compiled_finite_scores"]
        route = group["provider_model_receipt_pin_agreement"]
        md_lines.append(
            f"| {group['context_treatment']} | {group['provider_model']} | "
            f"{group['independent_go_validations']}/12 | "
            f"{score_text(score['training']['all_cases'])} | "
            f"{score_text(score['training']['candidate_discriminating_cases'])} | "
            f"{score_text(score['holdout']['all_cases'])} | "
            f"{score_text(score['holdout']['candidate_discriminating_cases'])} | "
            f"{route['passed']}/{route['total']} |"
        )
    md_lines.extend([
        "", "## Timing and resources", "",
        "Warmup timing is saved separately and excluded from measured-cell latency lists. Per-invocation CLI active wall time, harness sampling window, and process-local CPU/RSS samples remain exactly as recorded in the original report.", "",
        corrected.get("timing_scope", ""), "", corrected.get("resource_scope", ""), "",
        "## Correction audit", "",
        f"Raw request/model-state/profile checks passed for {feedback_passed}/72 measured calls; response routes passed for {response_route_matches}/74 calls; reconstructed typed request digests matched receipts for {typed_receipt_matches}/74 calls.",
        f"Original feedback aggregate was {old_feedback_summary['passed']}/{old_feedback_summary['total']}; original per-invocation context rows were true for 72/72 measured calls.",
        "Only `capture.measured_requests_matching_plan_feedback.passed` and top-level `decision` were changed in this derived report. No selected candidate, compiled score, timing, resource sample, raw request, raw response, receipt, or source evidence was changed.", "",
        "## Scope", "", *[f"- {limitation}" for limitation in corrected.get("limitations", [])], "",
    ])
    markdown_bytes = ("\n".join(md_lines)).encode()
    (output_dir / "report.md").write_bytes(markdown_bytes)

    original_report_hash = sha256(original_report_bytes)
    preexecution_hash = sha256(preexecution_bytes)
    receipt = {
        "schema": "gooo/pinned-context-derived-summary-correction/v1",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_id": original_report.get("run_id"),
        "status": "DERIVED_SUMMARY_CORRECTED_FROM_CAPTURED_EVIDENCE",
        "original_report": {"path": "../report.json", "sha256": original_report_hash,
                            "decision": original_report["decision"],
                            "feedback_summary": old_feedback_summary},
        "derived_report": {"path": "report.json", "sha256": sha256(corrected_bytes),
                           "markdown_path": "report.md", "markdown_sha256": sha256(markdown_bytes),
                           "decision": corrected["decision"]},
        "study_provenance": {
            "design_sha256": sha256(design_bytes),
            "preexecution_path": "../preexecution.json", "preexecution_sha256": preexecution_hash,
            "runner_script_sha256": code_provenance["runner_script_sha256"],
            "preparation_script_sha256": code_provenance["preparation_script_sha256"],
            "public_frozen_checkpoint_revision": code_provenance["public_frozen_checkpoint_revision"],
            "source_provenance_sha256": sha256(source_provenance_bytes),
        },
        "capture_manifest_sha256": {
            "run_metadata": sha256(metadata_bytes), "capture_status": sha256(capture_status_bytes),
            "proxy_events": sha256(captured_events_bytes), "invocation_index": sha256(index_bytes),
            "cli_invocation_records": sha256(cli_records_bytes),
        },
        "raw_evidence_checks": {
            "planned_invocations": 74, "raw_choice_posts": len(choice_events),
            "measured_calls": len(measured), "measured_model_state_profiles_and_hashes_match": feedback_passed,
            "captured_response_routes_match_model_pin": response_route_matches,
            "typed_request_receipt_hashes_match": typed_receipt_matches,
            "per_invocation_context_summaries_true_measured": 72,
            "per_invocation_context_summaries_true_warmup": 2,
            "original_request_audit_context_true_measured": sum(
                audit_rows[row["invocation_id"]].get("provider_context_pin_match") is True for row in measured),
        },
        "modified_field_whitelist": allowed_modified_fields,
        "diff": changed_fields,
        "immutability": {
            "original_report_bytes_preserved": True,
            "original_report_markdown_preserved": True,
            "raw_requests_responses_and_replies_unchanged": True,
            "scores_timings_resource_samples_and_candidate_choices_unchanged": True,
            "model_calls_repeated": False,
        },
        "correction_note": "The original request audit left provider_context_pin_match at its default false even after validating every raw serialized model state. Invocation result rows correctly recorded the state/profile checks. This derived copy aggregates the verified raw evidence into the intended 72/72 field.",
        "corrector_script_sha256": sha256(Path(__file__).read_bytes()),
    }
    write_json(output_dir / "correction-receipt.json", receipt)
    print(f"Wrote derived correction {output_dir}; original report remains unchanged ({original_report_hash}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"summary correction refused: {exc}")
