#!/usr/bin/env python3
"""Exercise the saved-selection validator against temporary evidence mutations."""
from __future__ import annotations

import hashlib
import argparse
import datetime as dt
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Callable


REPO = Path(__file__).resolve().parents[1]
RUN_REL = Path("audit/laya-selection-2026-09-30")
SCRIPT_PATH = REPO / "scripts"
DEFAULT_OUTPUT = (Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
                  / "selection-replay-validation" / "mutations")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
                             for row in rows), encoding="utf-8")


def set_report_and_manifest_digests(root: Path, run: Path) -> None:
    report_path = run / "report.json"
    report_sha = digest(report_path.read_bytes())
    metadata_path = run / "run-metadata.json"
    metadata = read_json(metadata_path)
    metadata["report_sha256"] = report_sha
    write_json(metadata_path, metadata)
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["selection_design"]["report_sha256"] = report_sha
    write_json(manifest_path, manifest)


def alter_report(root: Path, run: Path, mutate: Callable[[dict], None]) -> None:
    path = run / "report.json"
    report = read_json(path)
    mutate(report)
    write_json(path, report)
    set_report_and_manifest_digests(root, run)


def build_copy(work: Path, label: str) -> tuple[Path, Path]:
    root = work / label / "repo"
    root.mkdir(parents=True)
    for relative in ("selection-design", "fixtures", "plans", "audit/ci-run-36673382253"):
        shutil.copytree(REPO / relative, root / relative)
    shutil.copy2(REPO / "manifest.json", root / "manifest.json")
    run = root / RUN_REL
    shutil.copytree(REPO / RUN_REL, run)
    return root, run


def design_row(root: Path, invocation_id: str) -> dict:
    design = read_json(root / "selection-design/study-design.json")
    return next(row for row in design["invocations"] if row["invocation_id"] == invocation_id)


def mutate_typed_receipt(root: Path, run: Path) -> None:
    invocation_id = "01-piecewise-rejected_stale_source_context"
    invocation = run / "invocations" / invocation_id
    stdout_path = invocation / "stdout.raw"
    raw = stdout_path.read_bytes()
    payload = json.loads(raw)
    decision = payload["report"]["body_search"]["attempts"][0]["decision"]
    old = decision["request_sha256"]
    replacement = "sha256:" + ("0" * 64 if old != "sha256:" + ("0" * 64) else "1" * 64)
    changed = raw.replace(old.encode(), replacement.encode(), 1)
    if changed == raw:
        raise RuntimeError("typed request digest token was not found in saved stdout")
    stdout_path.write_bytes(changed)
    record_path = run / "cli-invocation-records.json"
    records = read_json(record_path)
    target_record = next(row for row in records if row["invocation_id"] == invocation_id)
    target_record["stdout_sha256"] = digest(changed)
    write_json(record_path, records)
    write_json(invocation / "invocation.json", target_record)

    def edit_report(report: dict) -> None:
        row = next(row for row in report["intent_treatment_results"]
                   if row["sequence"] == design_row(root, invocation_id)["sequence"])
        row["typed_decision_request_sha256"] = replacement

    alter_report(root, run, edit_report)


def mutate_source_copy(root: Path, run: Path) -> None:
    source = run / "postselection-validation/01-piecewise-rejected_stale_source_context/emitted.go"
    source.write_bytes(source.read_bytes() + b"\n// evidence mutation\n")


def mutate_finite_score(root: Path, run: Path) -> None:
    def edit(report: dict) -> None:
        row = next(row for row in report["intent_treatment_results"] if row["sequence"] == 1)
        score = row["holdout_score_external_go"]
        if score["passed"] <= 0:
            raise RuntimeError("selected score has no count to decrement")
        score["passed"] -= 1
    alter_report(root, run, edit)


def mutate_stale_gate_label(root: Path, run: Path) -> None:
    def edit(report: dict) -> None:
        row = next(row for row in report["intent_treatment_results"] if row["sequence"] == 1)
        row["context_gate"] = "accepted_exact_source_bound_ci_context"
    alter_report(root, run, edit)


def mutate_missing_request(root: Path, run: Path) -> None:
    snapshot = read_json(run / "proxy-events.json")
    first_choice = next(row for row in snapshot["events"] if row["kind"] == "laya_choice")
    (run / first_choice["request_file"]).unlink()


def mutate_response_choice(root: Path, run: Path) -> None:
    snapshot_path = run / "proxy-events.json"
    snapshot = read_json(snapshot_path)
    event = next(row for row in snapshot["events"] if row["kind"] == "laya_choice")
    response_path = run / event["response_file"]
    response_raw = response_path.read_bytes()
    response = json.loads(response_raw)
    current = response["answers"]["body_ir_search"]["choice"]
    response["answers"]["body_ir_search"]["choice"] = "identity" if current != "identity" else "zero"
    changed = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
    response_path.write_bytes(changed)
    event["response_sha256"] = digest(changed)
    write_json(snapshot_path, snapshot)
    write_jsonl(run / "proxy/events.jsonl", snapshot["events"])


def run_case(module, root: Path, run: Path, label: str) -> dict:
    module.ROOT = root
    module.BASE_CI_RUN = root / "audit/ci-run-36673382253"
    output = root / ("_mutation-validation-" + label)
    try:
        result = module.validate(run, output, REQUIRED_GO_VERSION)
        validation_report = output / "validation-report.json"
        return {"label": label, "status": "PASS", "error": None,
                "independent_go_replays": result.get("independent_go_replays"),
                "typed_request_hashes_recomputed_with_go_json": result.get("typed_request_hashes_recomputed_with_go_json"),
                "model_calls": result.get("actual_model_calls_by_validator"),
                "gooo_cli_calls": result.get("gooo_cli_calls_by_validator"),
                "go_version": result.get("go_version"),
                "validation_report_sha256": digest(validation_report.read_bytes())}
    except Exception as exc:
        error = str(exc)
        for path in (WORK_ROOT, WORK_ROOT.resolve()):
            error = error.replace(str(path), "<temporary-mutation-work>")
        return {"label": label, "status": "FAIL", "error": error,
                "error_type": type(exc).__name__}
    finally:
        if output.exists():
            shutil.rmtree(output)


REQUIRED_GO_VERSION: str | None = None
WORK_ROOT: Path


def main() -> int:
    global REQUIRED_GO_VERSION, WORK_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help="new directory for mutation report artifacts")
    parser.add_argument("--require-go-version", default=None,
                        help="require this Go version token for every control/replay")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing mutation results: {output_dir}")
    if not (REPO / RUN_REL / "report.json").is_file():
        raise SystemExit("saved primary Laya run is not available")
    output_dir.mkdir(parents=True, exist_ok=False)
    WORK_ROOT = output_dir / ".work"
    WORK_ROOT.mkdir()
    REQUIRED_GO_VERSION = args.require_go_version
    sys.path.insert(0, str(SCRIPT_PATH))
    module = importlib.import_module("validate_selection_run")

    offline_env = {
        "GOOO_LAYA_URL": "", "GOOO_LAYA_API_KEY": "", "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off",
    }
    prior_env = {key: os.environ.get(key) for key in offline_env}
    os.environ.update(offline_env)

    mutations = [
        ("typed_receipt_sha_changed", mutate_typed_receipt,
         "typed Laya request SHA-256 receipts differ from independent Go JSON reconstruction",
         "recomputed typed request hash"),
        ("emitted_go_copy_changed", mutate_source_copy,
         "saved postselection Go/oracle vectors differ from raw source and pinned cases",
         "saved postselection source binding"),
        ("finite_score_relabeled", mutate_finite_score,
         "report row does not match raw choice and independent finite scores",
         "derived report row consistency (finite score)"),
        ("stale_gate_relabeled_accepted", mutate_stale_gate_label,
         "report row does not match raw choice and independent finite scores",
         "derived report row consistency (context gate)"),
        ("request_artifact_missing", mutate_missing_request,
         "missing saved artifact",
         "raw proxy artifact presence"),
        ("response_choice_mismatch", mutate_response_choice,
         "raw choice response/event does not name a declared candidate",
         "provider response/choice binding"),
    ]

    results = []
    control_validation_sha256 = None
    try:
        root, run = build_copy(WORK_ROOT, "unchanged-control")
        control = run_case(module, root, run, "unchanged-control")
        results.append({**control, "expected": "PASS", "stage": "unchanged control"})
        control_validation_sha256 = control.get("validation_report_sha256")

        for label, mutation, expected_error, stage in mutations:
            root, run = build_copy(WORK_ROOT, label)
            try:
                mutation(root, run)
                actual = run_case(module, root, run, label)
            except Exception as exc:
                actual = {"label": label, "status": "HARNESS_ERROR", "error": str(exc),
                          "error_type": type(exc).__name__}
            results.append({**actual, "expected": "FAIL", "stage": stage,
                            "expected_error_fragment": expected_error,
                            "rejected_as_expected": actual["status"] == "FAIL"
                            and expected_error in (actual.get("error") or ""),
                            "actual_rejection_stage": stage if actual["status"] == "FAIL"
                            and expected_error in (actual.get("error") or "") else "unexpected"})
    finally:
        shutil.rmtree(WORK_ROOT, ignore_errors=True)
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    control_ok = (results[0]["status"] == "PASS"
                  and results[0].get("independent_go_replays") == 12
                  and results[0].get("typed_request_hashes_recomputed_with_go_json") == 12
                  and results[0].get("model_calls") == 0
                  and results[0].get("gooo_cli_calls") == 0
                  and (REQUIRED_GO_VERSION is None or REQUIRED_GO_VERSION in results[0].get("go_version", "")))
    failures_ok = all(row.get("rejected_as_expected") is True for row in results[1:])
    report_bytes = (REPO / RUN_REL / "report.json").read_bytes()
    run_metadata = read_json(REPO / RUN_REL / "run-metadata.json")
    frozen_design = (REPO / "selection-design/study-design.json").read_bytes()
    frozen_design_sha = digest(frozen_design)
    manifest_bytes = (REPO / "manifest.json").read_bytes()
    study_manifest = json.loads(manifest_bytes)
    if digest(report_bytes) != run_metadata.get("report_sha256"):
        raise RuntimeError("saved report SHA-256 differs from run metadata")
    if study_manifest.get("selection_design", {}).get("report_sha256") != run_metadata.get("report_sha256"):
        raise RuntimeError("saved report SHA-256 differs from repository manifest")
    main_validation_path = output_dir.parent / "validation-report.json"
    prior_validation_sha = digest(main_validation_path.read_bytes()) if main_validation_path.is_file() else None
    prior_validation_run_id = read_json(main_validation_path).get("run_id") if main_validation_path.is_file() else None
    if prior_validation_run_id is not None and prior_validation_run_id != run_metadata.get("run_id"):
        raise RuntimeError("preceding validator report refers to a different saved run")
    summary = {
        "schema": "gooo/selection-replay-negative-mutations/v1",
        "run_id": RUN_REL.name,
        "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "provenance": {
            "mutation_harness_sha256": digest(Path(__file__).resolve().read_bytes()),
            "validator_script_sha256": digest((SCRIPT_PATH / "validate_selection_run.py").read_bytes()),
            "saved_run_report_sha256": run_metadata["report_sha256"],
            "run_metadata_report_sha256": run_metadata["report_sha256"],
            "frozen_design_sha256": frozen_design_sha,
            "manifest_sha256": digest(manifest_bytes),
            "unchanged_control_validation_report_sha256": control_validation_sha256,
            "prior_replay_report_sha256": prior_validation_sha,
        },
        "control_passed": control_ok,
        "negative_cases_total": len(mutations),
        "negative_cases_rejected_at_expected_checks": sum(bool(row.get("rejected_as_expected")) for row in results[1:]),
        "model_calls": 0,
        "gooo_cli_calls": 0,
        "network_or_model_downloads": 0,
        "temporary_copies_cleaned": True,
        "results": results,
    }
    json_output = output_dir / "mutation-report.json"
    markdown_output = output_dir / "mutation-report.md"
    json_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    lines = ["# Saved selection validator: negative mutation checks", "",
             "The unchanged control passed all twelve saved-source replays. Each altered copy was rejected by the stated validation check.", "",
             "| Temporary treatment | Observed check | Outcome | Validator message |",
             "|---|---|---|---|"]
    for row in results:
        stage = row.get("actual_rejection_stage", row["stage"])
        outcome = "rejected as expected" if row.get("rejected_as_expected") else row["status"]
        lines.append(f"| {row['label']} | {stage} | {outcome} | {row.get('error') or 'control passed'} |")
    lines.extend(["", "No Laya or model calls, Gooo CLI calls, network access, or model downloads were used. Temporary evidence copies and replay output directories were removed after each case.", ""])
    lines.extend(["", "## Provenance", "",
                  f"- Saved study report SHA-256: `{summary['provenance']['saved_run_report_sha256']}`",
                  f"- Validator script SHA-256: `{summary['provenance']['validator_script_sha256']}`",
                  f"- Unchanged-control validation report SHA-256: `{control_validation_sha256}`",
                  ""])
    markdown_output.write_text("\n".join(lines), encoding="utf-8")
    print(json_output)
    print(f"control={'PASS' if control_ok else 'FAIL'}; expected negative checks={summary['negative_cases_rejected_at_expected_checks']}/{len(mutations)}")
    return 0 if control_ok and failures_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
