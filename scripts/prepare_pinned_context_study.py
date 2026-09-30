#!/usr/bin/env python3
"""Freeze a source-bound, randomized native-feedback Laya study without Laya calls."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import inspect
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import tempfile
import time
from typing import Any

from selection_support import sha256, verify_zip_extraction


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "pinned-context-design"
CI_RUN = ROOT / "audit" / "ci-run-36673382253"
SEED = 20260930
MODELS = ("english", "multilingual")
TREATMENTS = ("legacy_no_feedback", "compact_no_feedback", "compact_external_feedback")
REPLICATES = (1, 2, 3)
MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
LAYA_VERSION = "0.3.21"
WARMUP_INTENT = "clamp"
DEFAULT_VENV = Path("/tmp/meta-ontology-go-laya-venv-20260930")
DEFAULT_MODEL_SNAPSHOT = Path.home() / ".cache/huggingface/hub/models--convaiinnovations--laya/snapshots" / MODEL_REVISION


def write_json(path: Path, value: Any) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def streaming_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def inspect_laya_runtime(laya_venv: Path, model_snapshot: Path) -> dict:
    python = laya_venv.resolve() / "bin" / "python"
    if not python.is_file():
        raise RuntimeError(f"Laya Python environment is unavailable: {laya_venv}")
    probe = r'''import importlib.metadata as md, inspect, json, pathlib
import laya.common as common
from laya.common import build_sequence, encode_text, serialize_state
import transformers, tokenizers
print(json.dumps({
  "laya_version": md.version("laya"),
  "transformers_version": transformers.__version__,
  "tokenizers_version": tokenizers.__version__,
  "source_files": sorted({str(pathlib.Path(inspect.getsourcefile(fn)).resolve()) for fn in
                           (build_sequence, encode_text, serialize_state)} | {str(pathlib.Path(common.__file__).resolve())}),
}))'''
    result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError(f"cannot inspect the pinned Laya/tokenizer runtime: {result.stderr[-1000:]}")
    runtime = json.loads(result.stdout)
    if runtime["laya_version"] != LAYA_VERSION:
        raise RuntimeError(f"Laya package version differs from the study pin: {runtime['laya_version']!r}")
    snapshot_input = model_snapshot.expanduser()
    if not snapshot_input.exists():
        raise RuntimeError(f"cached Laya model snapshot is unavailable: {snapshot_input}")
    snapshot = snapshot_input.resolve()
    if snapshot_input.name != MODEL_REVISION or snapshot.name != MODEL_REVISION:
        raise RuntimeError(f"cached model snapshot must resolve to pinned revision {MODEL_REVISION}: {snapshot}")
    source_files = []
    for filename in runtime["source_files"]:
        path = Path(filename)
        digest, size = streaming_sha256(path)
        source_files.append({"path": str(path), "sha256": digest, "bytes": size})
    model_files = []
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        digest, size = streaming_sha256(path)
        model_files.append({"path": str(path.relative_to(snapshot)), "sha256": digest, "bytes": size,
                            "is_symlink": path.is_symlink()})
    if not model_files:
        raise RuntimeError("pinned model snapshot contains no cached files")
    return {"schema": "gooo/pinned-context-local-model-cache-binding/v1",
            "claim_scope": "Local cache content binding only; does not establish remote model authenticity.",
            "model_repository": "convaiinnovations/laya", "model_revision": MODEL_REVISION,
            "snapshot_path": str(snapshot), "laya_version": runtime["laya_version"],
            "transformers_version": runtime["transformers_version"],
            "tokenizers_version": runtime["tokenizers_version"],
            "laya_runtime_source_files": source_files,
            "cached_snapshot_files": model_files,
            "cached_snapshot_total_bytes": sum(item["bytes"] for item in model_files)}


def canonical_cases(cases: list[dict]) -> bytes:
    """The exact compact typed-case encoding consumed by Gooo's search receipt."""
    return json.dumps(
        [{"input": case["input"], "expected": case["expected"]} for case in cases],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()


def canonical_go_json(value: Any) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (encoded.replace(b"&", b"\\u0026").replace(b"<", b"\\u003c").replace(b">", b"\\u003e")
            .replace("\u2028".encode(), b"\\u2028").replace("\u2029".encode(), b"\\u2029"))


def typed_request_digest(outer: dict) -> str:
    state_wire = outer["state"]["request"]
    state = json.loads(state_wire)
    if len(outer["questions"]) != 1 or not state.get("remaining_candidates"):
        raise RuntimeError("mock request cannot reconstruct its single typed decision question")
    question_id, question_wire = next(iter(outer["questions"].items()))
    candidates = state["remaining_candidates"]
    request = {
        "schema": "gooo/typed-decision-request/v1",
        "state": state_wire,
        "question": {"id": question_id, "instructions": question_wire["instructions"],
                     "options": [{"id": item["id"],
                                  "description": "Try this exact expression: " + item["expression"]}
                                 for item in candidates]},
        "fallback": candidates[0]["id"],
        "provider_model": outer["model"],
    }
    return "sha256:" + sha256(canonical_go_json(request))


def go_build_metadata(binary: Path) -> tuple[str, str | None]:
    result = subprocess.run(["go", "version", "-m", str(binary)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"cannot inspect compiler build metadata: {result.stderr.strip()}")
    revision = modified = None
    for line in result.stdout.splitlines():
        if match := re.search(r"\bvcs\.revision=(\S+)", line):
            revision = match.group(1)
        if match := re.search(r"\bvcs\.modified=(\S+)", line):
            modified = match.group(1)
    return revision or "", modified


def bind_ci_artifact(manifest: dict, item: dict) -> tuple[dict, dict, bytes]:
    record = json.loads((CI_RUN / "download-record.json").read_text(encoding="utf-8"))
    archive_path = CI_RUN / record["downloaded_archive"]
    archive = archive_path.read_bytes()
    if sha256(archive) != record["github_reported_archive_sha256"]:
        raise RuntimeError("downloaded GitHub archive hash differs from its receipt")
    extraction = verify_zip_extraction(archive_path, CI_RUN / record["extracted_directory"])
    if record.get("workflow_conclusion") != "success" or record.get("compiler_revision") != manifest["compiler_revision"]:
        raise RuntimeError("source CI artifact does not bind the manifest compiler revision")
    if record.get("all_plans_training_only") is not True or record.get("model_calls") != 0:
        raise RuntimeError("source CI artifact is not the declared model-free, training-only workflow")

    case_dir = CI_RUN / record["extracted_directory"] / item["id"]
    fixture = (ROOT / item["fixture"]).read_bytes()
    plan_bytes = (ROOT / item["plan"]).read_bytes()
    plan = json.loads(plan_bytes)
    evidence = json.loads((case_dir / "evidence.json").read_text(encoding="utf-8"))
    report = json.loads((case_dir / "body-codegen-report.json").read_text(encoding="utf-8"))
    emitted = (case_dir / "emitted.go").read_bytes()
    binding = evidence.get("binding", {})
    binding_bytes = (json.dumps(binding, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if sha256(binding_bytes) != evidence.get("binding_sha256"):
        raise RuntimeError(f"{item['id']}: CI evidence binding hash mismatch")
    checks = {
        "intent_id": item["id"],
        "compiler_revision": manifest["compiler_revision"],
        "fixture_sha256": sha256(fixture),
        "plan_sha256": sha256(plan_bytes),
        "activity": item["activity"],
        "activity_id": report.get("activity_id"),
        "candidate_id": "identity",
        "candidate_expression": "input",
        "generated_source_sha256": sha256(emitted),
    }
    if any(binding.get(key) != value for key, value in checks.items()):
        raise RuntimeError(f"{item['id']}: CI artifact binding does not match current fixture, plan, or candidate")
    if report.get("compiler_source_sha") != manifest["compiler_revision"]:
        raise RuntimeError(f"{item['id']}: report compiler pin differs from manifest")
    if report.get("body_search", {}).get("selected_candidate_id") != "identity":
        raise RuntimeError(f"{item['id']}: source CI report did not select identity")
    if report.get("generated_digest") != "sha256:" + sha256(emitted):
        raise RuntimeError(f"{item['id']}: source CI report generated digest differs from emitted bytes")
    if evidence.get("result") != "EXPECTED_TRAINING_MISMATCHES_REPRODUCED":
        raise RuntimeError(f"{item['id']}: source CI report is not an expected training mismatch")
    cases = plan.get("test_cases", [])
    observations = evidence.get("training_observations")
    if (len(cases) != evidence.get("training_case_count")
            or len(observations or []) != len(cases)
            or evidence.get("training_mismatch_count") != len(cases)
            or any(row.get("passed") is not False for row in observations)):
        raise RuntimeError(f"{item['id']}: CI observation denominator is incomplete or includes a passing case")
    for source_case, observation in zip(cases, observations):
        if any(source_case[key] != observation.get(key) for key in ("input", "expected")):
            raise RuntimeError(f"{item['id']}: CI observation order or training input differs from the saved plan")
    report_observations = report.get("body_search", {}).get("training_case_results", [])
    if [{key: row.get(key) for key in ("input", "expected", "actual", "passed")} for row in report_observations] != [
            {key: row.get(key) for key in ("input", "expected", "actual", "passed")} for row in observations]:
        raise RuntimeError(f"{item['id']}: compiler receipt observations differ from source CI Go mismatches")
    digest = "sha256:" + sha256(canonical_cases(cases))
    return {
        "archive_record": record,
        "extraction_binding": extraction,
        "evidence": evidence,
        "body_codegen_report": report,
        "emitted_go": emitted,
        "fixture": fixture,
        "plan": plan,
        "plan_bytes": plan_bytes,
        "training_suite_sha256_typed": digest,
    }, {
        "intent_id": item["id"],
        "activity": item["activity"],
        "fixture_sha256": sha256(fixture),
        "original_fixture_source_digest": "sha256:" + sha256(fixture),
        "plan_sha256": sha256(plan_bytes),
        "generated_source_sha256": sha256(emitted),
        "training_suite_sha256_typed": digest,
        "candidate_id": "identity",
        "training_case_count": len(cases),
        "training_mismatch_count": len(observations),
    }, emitted


def run_identity_bridge(binary: Path, activity: str, fixture: bytes, plan: dict, expected_go: bytes,
                        scratch: Path, intent_id: str) -> dict:
    """Require the clean native build to reproduce the source-bound CI identity output."""
    bridge_plan = copy.deepcopy(plan)
    bridge_plan.pop("external_training_feedback", None)
    bridge_plan.pop("provider_model", None)
    plan_path = scratch / f"{intent_id}.search-plan.json"
    fixture_path = scratch / f"{intent_id}.gooo.fixture"
    plan_path.write_bytes((json.dumps(bridge_plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    fixture_path.write_bytes(fixture)
    command = [str(binary), "body-codegen", "--json", "--fill-search", str(plan_path), "--activity", activity, str(fixture_path)]
    env = os.environ.copy()
    env.pop("GOOO_LAYA_URL", None)
    env.pop("GOOO_LAYA_API_KEY", None)
    env.pop("GOOO_PROVIDER_MODEL", None)
    result = subprocess.run(command, cwd=scratch, env=env, capture_output=True, check=False, timeout=90)
    if result.returncode:
        raise RuntimeError(f"{intent_id}: no-provider identity bridge command failed: {result.stderr.decode(errors='replace')[-500:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{intent_id}: bridge command did not emit one JSON payload") from exc
    source = payload.get("source")
    report = payload.get("report", {})
    body = report.get("body_search", {})
    if not isinstance(source, str) or source.encode() != expected_go:
        raise RuntimeError(f"{intent_id}: new native identity Go bytes do not exactly match the source CI artifact")
    if body.get("selected_candidate_id") != "identity" or body.get("attempted_candidates") != 1:
        raise RuntimeError(f"{intent_id}: bridge did not preserve the single identity candidate")
    if body.get("training_suite_sha256") != "sha256:" + sha256(canonical_cases(bridge_plan["test_cases"])):
        raise RuntimeError(f"{intent_id}: compiler typed-case receipt does not match canonical training cases")
    return {
        "decision": report.get("decision"),
        "selected_candidate_id": body.get("selected_candidate_id"),
        "attempted_candidates": body.get("attempted_candidates"),
        "generated_source_sha256": sha256(source.encode()),
        "source_matches_actual_ci_artifact_byte_for_byte": True,
        "training_suite_sha256_typed": body.get("training_suite_sha256"),
        "cli_stdout_sha256": sha256(result.stdout),
        "cli_stderr_sha256": sha256(result.stderr),
    }


class ProtocolCaptureMock:
    """A local MOCK responder used only to capture deterministic outgoing wire bodies."""
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.events: list[dict] = []
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                response = {"status": "ok", "loaded": list(MODELS),
                            "revisions": {model: MODEL_REVISION for model in MODELS}, "device": "cpu"}
                mock._write_exchange(self, "GET", b"", response, "health_check")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw)
                    model = body.get("model")
                    state = body.get("state", {}).get("request", "")
                    state = json.loads(state) if isinstance(state, str) else state
                    identity = next(candidate["id"] for candidate in state["remaining_candidates"]
                                    if candidate["id"] == "identity")
                except Exception as exc:
                    response = {"error": f"protocol capture mock could not decode typed request: {exc}"}
                    mock._write_exchange(self, "POST", raw, response, "protocol_capture_error", model=None)
                    return
                response = {
                    "model": "laya-rl-agent",
                    "answers": {"body_ir_search": {"type": "choice", "choice": identity,
                                                     "probabilities": {identity: 1.0}}},
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "routing": {"model": model, "repo": "protocol-capture-mock",
                                "reason": "local protocol capture only; no model inference",
                                "detection": {"script": "unknown", "language": "und",
                                              "is_english": None, "language_undecided": True}},
                }
                mock._write_exchange(self, "POST", raw, response, "protocol_capture_mock", model=model)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/systemone"

    def _write_exchange(self, handler, method: str, request: bytes, response: dict, kind: str, model=None) -> None:
        response_bytes = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        with self.lock:
            seq = len(self.events) + 1
            invocation_id = getattr(self, "current_invocation", None)
            event_dir = self.out_dir / "exchanges" / f"{seq:03d}-{invocation_id or 'health'}"
            event_dir.mkdir(parents=True, exist_ok=False)
            request_file = "request.raw"
            response_file = "response.raw"
            event_dir.joinpath(request_file).write_bytes(request)
            event_dir.joinpath(response_file).write_bytes(response_bytes)
            event = {"sequence": seq, "kind": kind, "invocation_id": invocation_id,
                     "method": method, "path": handler.path, "provider_model": model,
                     "request_file": str((event_dir / request_file).relative_to(self.out_dir)),
                     "response_file": str((event_dir / response_file).relative_to(self.out_dir)),
                     "request_sha256": sha256(request), "response_sha256": sha256(response_bytes),
                     "response_status": 200 if kind != "protocol_capture_error" else 400,
                     "counted_as_laya_call": False}
            self.events.append(event)
        handler.send_response(event["response_status"])
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(response_bytes)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(response_bytes)
        handler.close_connection = True

    def set_invocation(self, invocation_id: str | None) -> None:
        with self.lock:
            self.current_invocation = invocation_id

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def protocol_mock_preflight(binary: Path, rows: list[dict], design_dir: Path, laya_venv: Path,
                            model_snapshot: Path) -> dict:
    measured = [row for row in rows if row["phase"] == "measured"]
    representatives: dict[tuple, dict] = {}
    for row in measured:
        key = (row["intent_id"], row["treatment"], row["provider_model"])
        representatives.setdefault(key, row)
    if len(representatives) != 24:
        raise RuntimeError(f"expected 24 unique intent/arm/model wire templates, got {len(representatives)}")
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    attempt_dir = design_dir / "protocol-preflight" / f"attempt-{timestamp}"
    (attempt_dir / "plans").mkdir(parents=True)
    (attempt_dir / "exchanges").mkdir()
    mock = ProtocolCaptureMock(attempt_dir)
    env = os.environ.copy()
    env.pop("GOOO_LAYA_API_KEY", None)
    env["GOOO_LAYA_URL"] = mock.url
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    records = []
    try:
        for index, ((intent_id, treatment, model), row) in enumerate(sorted(representatives.items()), start=1):
            plan = json.loads((design_dir / row["plan_path"]).read_bytes())
            scratch = attempt_dir / "plans" / row["invocation_id"]
            scratch.mkdir()
            plan_path = scratch / "plan.search-plan.json"
            fixture_path = scratch / "fixture.gooo.fixture"
            plan_bytes = (json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            plan_path.write_bytes(plan_bytes)
            fixture_bytes = (ROOT / row["fixture"]).read_bytes()
            fixture_path.write_bytes(fixture_bytes)
            mock.set_invocation(row["invocation_id"])
            command = [str(binary), "body-codegen", "--json", "--fill-search", str(plan_path),
                       "--activity", row["activity"], str(fixture_path)]
            result = subprocess.run(command, cwd=scratch, env=env, capture_output=True, check=False, timeout=90)
            mock.set_invocation(None)
            if result.returncode:
                raise RuntimeError(f"{row['invocation_id']}: local protocol mock capture failed: {result.stderr.decode(errors='replace')[-800:]}")
            choice_event = next((event for event in reversed(mock.events)
                                 if event.get("invocation_id") == row["invocation_id"]
                                 and event.get("method") == "POST"), None)
            if choice_event is None or choice_event.get("kind") != "protocol_capture_mock":
                raise RuntimeError(f"{row['invocation_id']}: typed compiler request did not reach the local protocol mock")
            event = choice_event
            request_path = attempt_dir / event["request_file"]
            request = json.loads(request_path.read_bytes())
            if request.get("model") != model:
                raise RuntimeError(f"{row['invocation_id']}: serialized model route differs from the explicit plan pin")
            response_payload = json.loads(result.stdout)
            decision = response_payload.get("report", {}).get("body_search", {}).get("attempts", [{}])[0].get("decision", {})
            requested_model = decision.get("requested_provider_model")
            if requested_model != model or decision.get("model_revision") != MODEL_REVISION:
                raise RuntimeError(f"{row['invocation_id']}: Gooo mock receipt does not bind requested model and revision")
            reconstructed_request_sha = typed_request_digest(request)
            if decision.get("request_sha256") != reconstructed_request_sha:
                raise RuntimeError(f"{row['invocation_id']}: Gooo typed request hash differs from the exact captured MOCK wire")
            routing = json.loads((attempt_dir / event["response_file"]).read_bytes()).get("routing", {})
            if routing.get("model") != model:
                raise RuntimeError(f"{row['invocation_id']}: mock Laya routing result differs from the requested model")
            compiler_dir = attempt_dir / "mock-compiler"
            compiler_dir.mkdir(exist_ok=True)
            stdout_path = compiler_dir / f"{row['invocation_id']}.stdout.raw"
            stderr_path = compiler_dir / f"{row['invocation_id']}.stderr.raw"
            stdout_path.write_bytes(result.stdout)
            stderr_path.write_bytes(result.stderr)
            records.append({"intent_id": intent_id, "treatment": treatment, "provider_model": model,
                            "representative_invocation_id": row["invocation_id"], "event": event,
                            "compiler_exit_code": result.returncode, "compiler_stdout_sha256": sha256(result.stdout),
                            "compiler_stderr_sha256": sha256(result.stderr),
                            "compiler_stdout_file": str(stdout_path.relative_to(attempt_dir)),
                            "compiler_stderr_file": str(stderr_path.relative_to(attempt_dir)),
                            "requested_provider_model": requested_model,
                            "receipt_request_sha256": decision.get("request_sha256"),
                            "reconstructed_typed_request_sha256": reconstructed_request_sha,
                            "plan_sha256": row["plan_sha256"]})
    finally:
        mock.stop()
    write_json(attempt_dir / "mock-events.json", {"schema": "gooo/pinned-context-protocol-capture-mock/v1",
                                                    "counted_as_laya_calls": 0, "events": mock.events})
    mock_posts = [event for event in mock.events if event["kind"] == "protocol_capture_mock"]
    mock_health = [event for event in mock.events if event["kind"] == "health_check"]
    if len(mock_posts) != 24 or len(mock_health) != 24 or any(event["method"] != "POST" for event in mock_posts):
        raise RuntimeError("local preflight must capture exactly 24 MOCK POSTs and 24 resolver health checks")
    captured_requests = []
    for event in mock.events:
        if event["kind"] == "protocol_capture_mock":
            request_bytes = (attempt_dir / event["request_file"]).read_bytes()
            if sha256(request_bytes) != event["request_sha256"]:
                raise RuntimeError("local protocol preflight request file hash mismatch")
            captured_requests.append({"provider_model": event["provider_model"],
                                      "request_sha256": event["request_sha256"],
                                      "request": json.loads(request_bytes)})
    tokenizer_input = attempt_dir / "tokenizer-input.json"
    tokenizer_output = attempt_dir / "token-budget.json"
    tokenizer_input.write_text(json.dumps(captured_requests, ensure_ascii=False), encoding="utf-8")
    python = laya_venv.resolve() / "bin" / "python"
    # Use a standalone exact implementation below. The import of Laya's sequence
    # builder is intentional: this is token accounting only, not model inference.
    tokenizer_code = r'''import json, os, sys
from pathlib import Path
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
from transformers import AutoTokenizer
from laya.common import build_sequence, encode_text, serialize_state
input_path, output_path, model_root = [Path(x) for x in sys.argv[1:4]]
revision = sys.argv[4]
requests = json.loads(input_path.read_text(encoding="utf-8"))
results = []
cache = {}
for entry in requests:
    model = entry["provider_model"]
    subfolder = "multilingual" if model == "multilingual" else ""
    model_dir = model_root / subfolder if subfolder else model_root
    config = json.loads((model_dir / "rl_agent_config.json").read_text(encoding="utf-8"))
    tokenizer_dir = model_dir / "tokenizer"
    if model not in cache:
        cache[model] = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True)
    tok = cache[model]
    wire = entry["request"]
    if wire.get("model") != model:
        raise RuntimeError("captured protocol model differs from requested model")
    if wire.get("max_len") is not None or wire.get("head_max_len") is not None:
        max_len = int(wire.get("max_len", config.get("max_len", 512)))
        head_max_len = int(wire.get("head_max_len", config.get("head_max_len", 192)))
    else:
        max_len = int(config.get("max_len", 512))
        head_max_len = int(config.get("head_max_len", 192))
    state = wire["state"]
    if not isinstance(state, (str, dict, list)):
        raise RuntimeError("unexpected Laya state type in captured wire")
    qid, qdef = next(iter(wire["questions"].items()))
    crit = qdef.get("criteria")
    if qdef["type"] == "choice" and isinstance(crit, list):
        crit = {choice: None for choice in crit}
    elif qdef["type"] == "noul" and isinstance(crit, dict):
        crit = {str(key).lower(): value for key, value in crit.items()}
    ins = qdef["instructions"]
    if not isinstance(ins, str):
        ins = json.dumps(ins, ensure_ascii=False)
    q = {"t": qdef["type"], "ins": ins, "crit": crit}
    if "labels" in qdef:
        q["labels"] = qdef["labels"]
    tokenized_state = encode_text(tok, serialize_state(state).replace(tok.mask_token, " "),
                                 add_special_tokens=False)["input_ids"]
    seq, markers, stats = build_sequence(tok, state, q, max_len=max_len,
                                         head_max_len=head_max_len,
                                         truncate_left=isinstance(state, list),
                                         state_ids=tokenized_state, return_stats=True)
    empty_seq, _ = build_sequence(tok, state, q, max_len=max_len,
                                  head_max_len=head_max_len,
                                  truncate_left=isinstance(state, list),
                                  state_ids=[], return_stats=False)
    state_room = max(0, max_len - (len(empty_seq) - 1) - 1)
    results.append({"provider_model": model, "request_sha256": entry["request_sha256"],
                    "tokenizer_revision": revision, "token_count_exact_sequence": len(seq),
                    "model_max_len": max_len, "question_head_max_len": head_max_len,
                    "state_tokens_before_model_truncation": len(tokenized_state),
                    "state_token_room": state_room,
                    "state_truncated": len(tokenized_state) > state_room,
                    "head_option_stats": stats, "question_count": 1})
output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
'''
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    token_result = subprocess.run([str(python), "-c", tokenizer_code, str(tokenizer_input), str(tokenizer_output),
                                   str(model_snapshot), MODEL_REVISION], cwd=attempt_dir, env=env,
                                  capture_output=True, text=True, check=False, timeout=300)
    if token_result.returncode:
        write_json(attempt_dir / "tokenizer-error.json", {"returncode": token_result.returncode,
                                                           "stdout": token_result.stdout,
                                                           "stderr": token_result.stderr})
        raise RuntimeError("cached Laya token-budget preflight failed; no Laya calls were made")
    token_rows = read_json(tokenizer_output)
    if len(token_rows) != 24:
        raise RuntimeError("cached tokenizer did not score all 24 exact protocol templates")
    over = [row for row in token_rows if row["token_count_exact_sequence"] > row["model_max_len"] or row["state_truncated"]]
    if over:
        write_json(attempt_dir / "preflight-failure.json", {"reason": "token budget exceeded or state truncated",
                                                             "over_budget_or_truncated": over,
                                                             "mock_laya_choice_posts": len(records),
                                                             "actual_laya_choice_posts": 0})
        raise RuntimeError("cached tokenizers found a captured prompt that would exceed its pinned model budget; no Laya calls were made")
    by_sha = {row["request_sha256"]: row for row in token_rows}
    for event in mock.events:
        if event["kind"] == "protocol_capture_mock":
            event["token_budget"] = by_sha[event["request_sha256"]]
    summary = {
        "schema": "gooo/pinned-context-protocol-preflight/v1",
        "status": "PASS_CACHED_TOKENIZERS_NO_MODEL_INFERENCE",
        "mock_choice_posts": len(mock_posts), "mock_health_checks": len(mock_health), "actual_laya_calls": 0,
        "provider_calls": 0,
        "unique_intent_arm_model_templates": 24,
        "model_revisions": {model: MODEL_REVISION for model in MODELS},
        "model_max_len_by_request_model": {model: max(row["model_max_len"] for row in token_rows if row["provider_model"] == model)
                                           for model in MODELS},
        "token_budget_rows": token_rows,
        "exchange_records": records,
        "mock_events": mock.events,
        "local_mock_capture_directory": str(attempt_dir),
        "tokenizer_input_sha256": sha256(tokenizer_input.read_bytes()),
        "tokenizer_output_sha256": sha256(tokenizer_output.read_bytes()),
    }
    write_json(attempt_dir / "preflight-summary.json", summary)
    return summary


def build(args: argparse.Namespace) -> dict:
    if (DESIGN / "study-design.json").exists():
        raise RuntimeError(f"refusing to overwrite an already frozen study: {DESIGN / 'study-design.json'}")
    binary = args.binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"pinned compiler binary is unavailable or non-executable: {binary}")
    actual_binary_sha = sha256(binary.read_bytes())
    if actual_binary_sha != args.binary_sha256:
        raise RuntimeError("compiler binary hash differs from the supplied clean pin")
    revision, modified = go_build_metadata(binary)
    if revision != args.compiler_revision or modified != "false":
        raise RuntimeError(f"compiler build is not the clean source pin: revision={revision!r}, modified={modified!r}")

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if len(manifest["intents"]) != 4:
        raise RuntimeError("the pinned study is defined for exactly four existing known intents")
    intents: dict[str, dict] = {}
    provenance: dict[str, dict] = {}
    bridge: dict[str, dict] = {}
    archive_record = None
    with tempfile.TemporaryDirectory(prefix="pinned-context-bridge-") as temporary:
        scratch = Path(temporary)
        for item in manifest["intents"]:
            source, binding, emitted = bind_ci_artifact(manifest, item)
            archive_record = source["archive_record"]
            base = source["plan"]
            if ("holdout_test_cases" in base or base.get("max_attempts") != 1
                    or not base.get("candidates") or base["candidates"][0] != {"id": "identity", "expression": "input"}):
                raise RuntimeError(f"{item['id']}: existing plan is not the declared identity-first one-attempt training plan")
            bridge[item["id"]] = run_identity_bridge(binary, item["activity"], source["fixture"], base, emitted,
                                                      scratch, item["id"])
            binding["native_identity_bridge"] = bridge[item["id"]]
            provenance[item["id"]] = binding
            feedback = {
                "source_digest": binding["original_fixture_source_digest"],
                "training_suite_sha256": binding["training_suite_sha256_typed"],
                "candidate_id": "identity",
                "observations": [
                    {key: row[key] for key in ("input", "expected", "actual", "passed")}
                    for row in source["evidence"]["training_observations"]
                ],
            }
            intents[item["id"]] = {"manifest": item, "fixture": source["fixture"], "base_plan": base,
                                   "base_plan_sha256": sha256(source["plan_bytes"]), "feedback": feedback}

    # Construct all plans in deterministic semantic order, then randomize only the
    # measured-call schedule. Warmups are predeclared and excluded from n=3 cells.
    prepared: list[dict] = []
    for intent_id, model in (
        (WARMUP_INTENT, "english"),
        (WARMUP_INTENT, "multilingual"),
    ):
        source = intents[intent_id]
        plan = copy.deepcopy(source["base_plan"])
        plan["provider_model"] = model
        plan["prompt_profile"] = "compact"
        prepared.append({"intent_id": intent_id, "activity": source["manifest"]["activity"],
                         "fixture": source["manifest"]["fixture"], "treatment": "compact_no_feedback",
                         "provider_model": model, "phase": "warmup", "replicate": 0,
                         "plan": plan})
    measured: list[dict] = []
    for intent_id, source in intents.items():
        for treatment in TREATMENTS:
            for model in MODELS:
                for replicate in REPLICATES:
                    plan = copy.deepcopy(source["base_plan"])
                    plan["provider_model"] = model
                    if treatment.startswith("compact_"):
                        plan["prompt_profile"] = "compact"
                    if treatment == "compact_external_feedback":
                        plan["external_training_feedback"] = copy.deepcopy(source["feedback"])
                    measured.append({"intent_id": intent_id, "activity": source["manifest"]["activity"],
                                     "fixture": source["manifest"]["fixture"], "treatment": treatment,
                                     "provider_model": model, "phase": "measured", "replicate": replicate,
                                     "plan": plan})
    import random
    random.Random(args.order_seed).shuffle(measured)
    prepared.extend(measured)

    if len(measured) != 72 or len(prepared) != 74:
        raise RuntimeError("study must contain 72 measured calls and two separately counted warmups")
    if (DESIGN / "study-design.json").exists():
        raise RuntimeError(f"refusing to overwrite a frozen study: {DESIGN / 'study-design.json'}")
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    attempt_root = DESIGN / "preparation-attempts" / timestamp
    attempt_root.mkdir(parents=True)
    prepared_rows = []
    for sequence, row in enumerate(prepared, start=1):
        phase = row["phase"]
        sequence_id = (f"warmup-{row['provider_model']}" if phase == "warmup" else
                       f"{sequence:02d}-{row['intent_id']}-{row['treatment']}-{row['provider_model']}-r{row['replicate']}")
        relative = f"plans/{sequence_id}.search-plan.json"
        attempt_plan = attempt_root / relative
        plan_bytes = write_json(attempt_plan, row["plan"])
        fixture_bytes = intents[row["intent_id"]]["fixture"]
        prepared_rows.append({
            "sequence": sequence, "invocation_id": sequence_id, "phase": phase,
            "intent_id": row["intent_id"], "activity": row["activity"], "fixture": row["fixture"],
            "fixture_sha256": sha256(fixture_bytes), "treatment": row["treatment"], "arm": row["treatment"],
            "provider_model": row["provider_model"], "model_revision": MODEL_REVISION,
            "replicate": row["replicate"], "plan_path": relative, "plan_sha256": sha256(plan_bytes),
            "preparation_plan_path": str(attempt_plan.relative_to(DESIGN)),
            "plan_intent_sha256": sha256(row["plan"]["intent"].encode()),
            "base_intent_unchanged": row["plan"]["intent"] == intents[row["intent_id"]]["base_plan"]["intent"],
            "external_training_feedback_present": "external_training_feedback" in row["plan"],
            "prompt_profile": row["plan"].get("prompt_profile", ""),
            "candidate_count": len(row["plan"]["candidates"]),
            "training_case_count": len(row["plan"]["test_cases"]),
            "max_attempts": row["plan"]["max_attempts"],
        })
    model_cache_binding = inspect_laya_runtime(args.laya_venv, args.model_snapshot)
    preflight = protocol_mock_preflight(binary, prepared_rows, attempt_root, args.laya_venv,
                                        args.model_snapshot)
    if preflight.get("status") != "PASS_CACHED_TOKENIZERS_NO_MODEL_INFERENCE" or preflight["actual_laya_calls"] != 0:
        raise RuntimeError("local protocol/token preflight did not pass cleanly")

    # The mock captures the exact wire templates before the immutable study document
    # is frozen. They are separate from the later 74 real offline-Laya calls.
    import shutil
    preflight_attempt = Path(preflight["local_mock_capture_directory"])
    final_preflight_rel = f"protocol-preflight/attempt-{timestamp}"
    final_preflight_dir = DESIGN / final_preflight_rel
    final_preflight_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(preflight_attempt, final_preflight_dir)
    token_by_sha = {row["request_sha256"]: row for row in preflight["token_budget_rows"]}
    exchanges_by_key = {(row["intent_id"], row["treatment"], row["provider_model"]): row
                        for row in preflight["exchange_records"]}
    plan_rows = []
    for row in prepared_rows:
        source_path = DESIGN / row["preparation_plan_path"]
        final_path = DESIGN / row["plan_path"]
        plan_bytes = source_path.read_bytes()
        if sha256(plan_bytes) != row["plan_sha256"]:
            raise RuntimeError(f"{row['invocation_id']}: preflight plan changed before final freeze")
        write_json(final_path, json.loads(plan_bytes))
        template_key = (row["intent_id"], row["treatment"], row["provider_model"])
        exchange = exchanges_by_key[template_key]
        token_row = token_by_sha[exchange["event"]["request_sha256"]]
        mock_request = json.loads((preflight_attempt / exchange["event"]["request_file"]).read_bytes())
        serialized_state = mock_request.get("state", {}).get("request")
        if not isinstance(serialized_state, str):
            raise RuntimeError(f"{row['invocation_id']}: mock request has no serialized model state")
        decoded_state = json.loads(serialized_state)
        questions = mock_request.get("questions", {})
        if len(questions) != 1:
            raise RuntimeError(f"{row['invocation_id']}: mock request does not contain exactly one chooser question")
        question_id, question = next(iter(questions.items()))
        candidate_order = [item["id"] for item in decoded_state.get("remaining_candidates", [])]
        row_state_profile = {
            "provider_model": row["provider_model"],
            "prompt_profile": row["prompt_profile"],
            "training_suite_sha256_present": "training_suite_sha256" in decoded_state,
            "training_suite_sha256": decoded_state.get("training_suite_sha256"),
            "external_training_feedback_present": "external_training_feedback" in decoded_state,
            "state_request_sha256": sha256(serialized_state.encode("utf-8")),
            "decoded_state": decoded_state,
            "chooser_question": {
                "id": question_id,
                "instructions": question.get("instructions"),
                "option_order": candidate_order,
            },
        }
        row = {key: value for key, value in row.items() if key != "preparation_plan_path"}
        row.update({
            "plan_path": str(final_path.relative_to(DESIGN)),
            "protocol_preflight_request_sha256": exchange["event"]["request_sha256"],
            "expected_input_token_count": token_row["token_count_exact_sequence"],
            "expected_input_token_budget": token_row["model_max_len"],
            "expected_model_state_profile": row_state_profile,
            "expected_question_sha256": sha256(json.dumps(
                questions, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        })
        plan_rows.append(row)
    bridge_record = {
        "schema": "gooo/pinned-context-native-identity-bridge/v1",
        "compiler_revision": args.compiler_revision,
        "binary_sha256": actual_binary_sha,
        "source_ci_archive_sha256": archive_record["github_reported_archive_sha256"],
        "source_ci_workflow_run_id": archive_record["workflow_run_id"],
        "intent_bridges": bridge,
    }
    write_json(DESIGN / "native-identity-bridge.json", bridge_record)
    write_json(DESIGN / "source-provenance.json", {
        "schema": "gooo/pinned-context-source-provenance/v1",
        "ci_archive_sha256": archive_record["github_reported_archive_sha256"],
        "ci_workflow_run_id": archive_record["workflow_run_id"],
        "ci_workflow_commit": archive_record["workflow_commit"],
        "intents": provenance,
    })
    for intent_id, source in intents.items():
        feedback = source["feedback"]
        write_json(DESIGN / "feedback" / f"{intent_id}.json", feedback)
    write_json(DESIGN / "local-model-cache-binding.json", model_cache_binding)

    measured_rows = [row for row in plan_rows if row["phase"] == "measured"]
    warmup_count = sum(row["phase"] == "warmup" for row in plan_rows)
    if (len(measured_rows) != 72 or warmup_count != 2
            or len({(row["intent_id"], row["treatment"], row["provider_model"], row["replicate"])
                    for row in measured_rows}) != 72):
        raise RuntimeError("frozen design failed its 72 measured invocations, factorial cells, or two warmups check")
    if any(not row["base_intent_unchanged"] for row in plan_rows):
        raise RuntimeError("study plan changed an original intent")
    if sum(row["external_training_feedback_present"] for row in measured_rows) != 24:
        raise RuntimeError("native compact context is not present in exactly its 24 measured arm runs")
    study = {
        "schema": "gooo/pinned-compact-context-study-design/v1",
        "study_id": "pinned-compact-context-2026-09-30",
        "status": "frozen_before_laya_calls",
        "prepared_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "compiler": {"source_revision": args.compiler_revision, "binary_sha256": actual_binary_sha,
                     "clean_vcs_modified": False, "binary_path_at_preparation": str(binary)},
        "laya": {"package_version": LAYA_VERSION, "model_revision": MODEL_REVISION,
                 "device": "cpu", "threads": 4, "offline_only": True},
        "factors": {"known_intents": list(intents), "context_treatments": list(TREATMENTS),
                    "provider_models": list(MODELS), "replicates_per_cell": len(REPLICATES)},
        "design_counts": {"warmup_calls": warmup_count, "measured_calls": len(measured_rows),
                          "measured_per_factorial_cell": len(REPLICATES),
                          "candidate_choice_attempts_per_invocation": 1},
        "order_seed": args.order_seed,
        "ordering": "two fixed compact/no-feedback model warmups first, then 72 randomized measured calls",
        "provider_policy": "local loopback capture proxy only; offline cached models; API key unset; no model downloads",
        "native_context_definition": "The compact_external_feedback arm supplies structured source_digest (raw original fixture bytes), Gooo typed training_suite_sha256, candidate_id identity, and actual source-CI mismatch observations. The compact state sent to Laya contains only candidate ID, at most eight failed input/expected/actual triples, total/failed/prompted counts, and truncation status; its source and suite hashes stay local.",
        "prompt_profile_definition": "legacy_no_feedback omits prompt_profile and external context; compact_no_feedback sets prompt_profile=compact without external context; compact_external_feedback sets prompt_profile=compact with source-bound context. Compact arms share the same concise chooser instructions.",
        "protocol_preflight": {"path": f"{final_preflight_rel}/preflight-summary.json",
                               "mock_choice_posts": preflight["mock_choice_posts"],
                               "mock_health_checks": preflight["mock_health_checks"],
                               "actual_laya_calls": preflight["actual_laya_calls"],
                               "unique_intent_arm_model_templates": preflight["unique_intent_arm_model_templates"],
                               "tokenizer_output_sha256": preflight["tokenizer_output_sha256"]},
        "local_model_cache_binding": "local-model-cache-binding.json",
        "source_ci_provenance": "source-provenance.json",
        "native_identity_bridge": "native-identity-bridge.json",
        "plans": plan_rows,
        "limitations": [
            "Three repeats per finite intent-prompt-profile-model cell do not support a general population or speed claim.",
            "The source CI observations are training mismatches for the declared identity candidate; they are not full-domain correctness evidence.",
            "The four existing known intents and their candidate lists define this study's scope.",
            "Finite oracle outputs are reused benchmark vectors and are not a fresh unseen generalization set.",
            "CPU and RSS measurements are sampled at the process level and do not measure model-forward-only cost or host CPU increase.",
        ],
    }
    study["preparation_attempt"] = str(attempt_root.relative_to(DESIGN))
    design_bytes = write_json(DESIGN / "study-design.json", study)
    (DESIGN / "study-design.sha256").write_text(sha256(design_bytes) + "  study-design.json\n", encoding="ascii")
    return study


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--compiler-revision", required=True)
    parser.add_argument("--binary-sha256", required=True)
    parser.add_argument("--laya-venv", type=Path, default=DEFAULT_VENV)
    parser.add_argument("--model-snapshot", type=Path, default=DEFAULT_MODEL_SNAPSHOT)
    parser.add_argument("--order-seed", type=int, default=SEED)
    args = parser.parse_args()
    study = build(args)
    print(f"Froze {study['design_counts']['measured_calls']} measured plans and {study['design_counts']['warmup_calls']} warmups; no Laya calls made.")
    print(f"Design SHA-256: {sha256((DESIGN / 'study-design.json').read_bytes())}")


if __name__ == "__main__":
    main()
