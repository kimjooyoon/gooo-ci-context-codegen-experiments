#!/usr/bin/env python3
"""Run one randomized, offline Laya choice for each prepared intent-treatment pair."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from selection_support import holdout_case_pairs, scan_selection_body, sha256


ROOT = Path(__file__).resolve().parents[1]
COHORT_SOURCE = Path("/Users/alice/meta-go/research/metaprogramming/gooo-metaprogramming-experiments/cohorts/ir-search-2026-09-30")
DESIGN_DIR = ROOT / "selection-design"
RUN_ID_DEFAULT = "laya-selection-2026-09-30"
EXPECTED_BINARY_SHA = "f9f33b86c2114c1adeba01319fddb7d48f666e739aef768bbb5a580adfb7cda9"
EXPECTED_SOURCE_REV = "29d44bc778d85aee03b9af500bd83dc98f368189"
EXPECTED_LAYA_VERSION = "0.3.21"
EXPECTED_MODEL_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
INT64_MIN, INT64_MAX = -(1 << 63), (1 << 63) - 1


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, value: object) -> bytes:
    raw = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    write_bytes(path, raw)
    return raw


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_binary(binary: Path) -> dict[str, str]:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"Gooo binary is not executable: {binary}")
    actual = sha256(binary.read_bytes())
    if actual != EXPECTED_BINARY_SHA:
        raise RuntimeError(f"Gooo SHA-256 mismatch: expected {EXPECTED_BINARY_SHA}, got {actual}")
    info = subprocess.run(["go", "version", "-m", str(binary)], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        raise RuntimeError(f"cannot inspect pinned Gooo build metadata: {info.stderr.strip()}")
    revision = modified = None
    for line in info.stdout.splitlines():
        match = re.search(r"\bvcs\.revision=(\S+)", line)
        if match:
            revision = match.group(1)
        match = re.search(r"\bvcs\.modified=(\S+)", line)
        if match:
            modified = match.group(1)
    if revision != EXPECTED_SOURCE_REV or modified != "false":
        raise RuntimeError(f"Gooo binary is not the clean pin: revision={revision!r}, modified={modified!r}")
    return {"sha256": actual, "source_revision": revision, "vcs_modified": modified}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch_health(port: int, timeout: float = 3.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
        raw = response.read(65536)
    return raw, json.loads(raw)


def wait_for_health(process, port: int, run_dir: Path, timeout: int = 240) -> dict:
    started = time.monotonic()
    last_error = None
    while time.monotonic() - started < timeout:
        if process.poll() is not None:
            raise RuntimeError(f"owned Laya server exited with status {process.returncode}")
        try:
            raw, health = fetch_health(port)
            write_bytes(run_dir / "laya" / "health-before.json", raw)
            if health.get("device") != "cpu" or health.get("loaded") != ["english"]:
                raise RuntimeError(f"Laya did not load pinned CPU/english model: {health!r}")
            if health.get("revisions", {}).get("english") != EXPECTED_MODEL_REVISION:
                raise RuntimeError("Laya English revision does not match the pinned model")
            return health
        except (OSError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, RuntimeError) and "pinned" in str(exc):
                raise
            time.sleep(1)
    raise RuntimeError(f"timed out waiting for owned offline Laya: {last_error}")


def stop_owned_server(process, pgid: int | None, stdout_handle, stderr_handle) -> None:
    if process is not None and process.poll() is None:
        try:
            if os.getpgid(process.pid) == pgid == process.pid:
                os.killpg(pgid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    if process.poll() is None and os.getpgid(process.pid) == pgid:
                        os.killpg(pgid, signal.SIGKILL)
                        process.wait(timeout=4)
        except (ProcessLookupError, PermissionError):
            pass
    for handle in (stdout_handle, stderr_handle):
        if handle is not None and not handle.closed:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()


def parse_cputime(raw: str) -> float:
    parts = raw.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def sample_pid(pid: int | None) -> dict | None:
    if not pid:
        return None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "cputime=,pcpu=,rss=,pid="], capture_output=True, text=True, check=False)
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return {"pid": pid, "alive": False, "ps_raw": raw}
    parts = raw.split()
    if len(parts) < 4:
        return {"pid": pid, "alive": True, "ps_raw": raw}
    try:
        return {"pid": int(parts[-1]), "alive": True, "cpu_seconds": parse_cputime(parts[0]),
                "pcpu_percent_sample": float(parts[1]), "rss_kb": int(parts[2]), "ps_raw": raw}
    except (ValueError, IndexError):
        return {"pid": pid, "alive": True, "ps_raw": raw}


def process_summary(samples: list[dict], key: str) -> dict:
    available = [sample[key] for sample in samples if sample.get(key) and sample[key].get("alive")]
    if not available:
        return {"sample_count": 0, "cpu_seconds_delta_coarse": None, "rss_peak_kb_sampled": None,
                "pcpu_percent_max_sampled": None}
    cpu_values = [row["cpu_seconds"] for row in available if "cpu_seconds" in row]
    pcpu_values = [row["pcpu_percent_sample"] for row in available if "pcpu_percent_sample" in row]
    rss_values = [row["rss_kb"] for row in available if "rss_kb" in row]
    return {
        "sample_count": len(available),
        "cpu_seconds_delta_coarse": max(0.0, cpu_values[-1] - cpu_values[0]) if len(cpu_values) >= 2 else None,
        "rss_peak_kb_sampled": max(rss_values) if rss_values else None,
        "pcpu_percent_max_sampled": max(pcpu_values) if pcpu_values else None,
        "measurement_note": "ps cumulative CPU is second-granularity on this host; sampled RSS/pcpu are observations, not continuous peaks",
    }


class CaptureProxy:
    def __init__(self, target_port: int, run_dir: Path):
        self.target_host, self.target_port = "127.0.0.1", target_port
        self.run_dir = run_dir
        self.lock = threading.Lock()
        self.current_invocation: str | None = None
        self.seq = 0
        self.events: list[dict] = []
        proxy = self

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                self.forward("GET")

            def do_POST(self):
                self.forward("POST")

            def forward(self, method: str):
                if self.path not in ("/health", "/v1/systemone") or (self.path == "/v1/systemone" and method != "POST"):
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(411)
                    return
                if size < 0 or size > 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                request_body = self.rfile.read(size) if size else b""
                started_ns, started_mono = time.time_ns(), time.monotonic()
                started_utc = now_utc()
                with proxy.lock:
                    proxy.seq += 1
                    seq = proxy.seq
                    invocation_id = proxy.current_invocation
                request_rel = f"proxy/requests/{seq:04d}.request.raw"
                response_rel = f"proxy/responses/{seq:04d}.response.raw"
                request_path, response_path = proxy.run_dir / request_rel, proxy.run_dir / response_rel
                write_bytes(request_path, request_body)
                kind = "health_check" if self.path == "/health" else "laya_choice"
                try:
                    upstream = http.client.HTTPConnection(proxy.target_host, proxy.target_port, timeout=90)
                    headers = {key: value for key, value in self.headers.items()
                               if key.lower() not in ("host", "connection", "content-length", "transfer-encoding",
                                                      "authorization", "x-api-key", "api-key")}
                    headers["Content-Length"] = str(len(request_body))
                    upstream.request(method, self.path, body=request_body, headers=headers)
                    upstream_response = upstream.getresponse()
                    response_body = upstream_response.read(2 * 1024 * 1024 + 1)
                    status = upstream_response.status
                    if len(response_body) > 2 * 1024 * 1024:
                        response_body = response_body[:2 * 1024 * 1024]
                        status = 502
                    response_headers = {key: value for key, value in upstream_response.getheaders()
                                        if key.lower() in ("content-type", "content-encoding", "cache-control")}
                    upstream.close()
                except Exception as exc:
                    response_body = json.dumps({"error": str(exc)}).encode()
                    response_headers, status = {"Content-Type": "application/json"}, 502
                write_bytes(response_path, response_body)
                try:
                    parsed_response = json.loads(response_body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed_response = {}
                answers = parsed_response.get("answers", {}) if isinstance(parsed_response, dict) else {}
                answer = answers.get("body_ir_search", {}) if isinstance(answers, dict) else {}
                event = {
                    "seq": seq, "kind": kind, "invocation_id": invocation_id,
                    "method": method, "path": self.path, "started_utc": started_utc,
                    "started_unix_ns": started_ns, "completed_utc": now_utc(),
                    "completed_unix_ns": time.time_ns(), "duration_ms": (time.monotonic() - started_mono) * 1000,
                    "status": status, "request_file": request_rel, "response_file": response_rel,
                    "request_sha256": sha256(request_body), "response_sha256": sha256(response_body),
                    "selected_candidate_id": answer.get("choice") if isinstance(answer, dict) else None,
                }
                with proxy.lock:
                    proxy.events.append(event)
                    log = proxy.run_dir / "proxy" / "events.jsonl"
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                self.send_response(status)
                for key, value in response_headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(response_body)
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="laya-selection-capture-proxy", daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/systemone"

    def set_invocation(self, invocation_id: str | None) -> None:
        with self.lock:
            self.current_invocation = invocation_id

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def run_cli(binary: Path, row: dict, run_dir: Path, proxy: CaptureProxy,
            server_pid: int, env: dict) -> dict:
    ordinal = row["sequence"]
    invocation_id = row["invocation_id"]
    out_dir = run_dir / "invocations" / invocation_id
    out_dir.mkdir(parents=True, exist_ok=False)
    plan_src = DESIGN_DIR / row["plan_path"]
    fixture_src = ROOT / row["fixture"]
    plan_bytes, fixture_bytes = plan_src.read_bytes(), fixture_src.read_bytes()
    if sha256(plan_bytes) != row["plan_sha256"] or sha256(fixture_bytes) != row["fixture_sha256"]:
        raise RuntimeError(f"{invocation_id}: prepared plan or fixture hash changed")
    plan = json.loads(plan_bytes)
    if "holdout_test_cases" in plan:
        raise RuntimeError(f"{invocation_id}: refusing to send a plan containing a held-out suite")
    write_bytes(out_dir / "plan.search-plan.json", plan_bytes)
    write_bytes(out_dir / "fixture.gooo.fixture", fixture_bytes)
    write_json(out_dir / "training-cases.json", plan["test_cases"])
    command = [str(binary), "body-codegen", "--json", "--fill-search", str(out_dir / "plan.search-plan.json"),
               "--activity", row["activity"], str(out_dir / "fixture.gooo.fixture")]
    cli_env = env.copy()
    cli_env["GOOO_LAYA_URL"] = proxy.url
    cli_env.pop("GOOO_LAYA_API_KEY", None)
    started_utc, started_ns, start_mono = now_utc(), time.time_ns(), time.monotonic()
    proxy.set_invocation(invocation_id)
    stop = threading.Event()
    sample_lock = threading.Lock()
    samples: list[dict] = []

    def take_sample(cli_pid: int | None) -> None:
        sample = {"sampled_utc": now_utc(), "sampled_unix_ns": time.time_ns(),
                  "elapsed_ms": (time.monotonic() - start_mono) * 1000,
                  "cli": sample_pid(cli_pid), "laya_server": sample_pid(server_pid)}
        with sample_lock:
            samples.append(sample)
            path = out_dir / "resource-samples.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    process = None
    sampler = None
    stdout, stderr, exit_code = b"", b"", 124
    process_completed_mono = None
    process_completed_ns = None
    process_completion_kind = "not_started"
    try:
        process = subprocess.Popen(command, cwd=out_dir, env=cli_env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        take_sample(process.pid)

        def sample_loop():
            while not stop.wait(0.12):
                take_sample(process.pid if process else None)

        sampler = threading.Thread(target=sample_loop, name=f"resource-{ordinal:02d}", daemon=True)
        sampler.start()
        try:
            stdout, stderr = process.communicate(timeout=90)
            exit_code = process.returncode
            process_completed_mono = time.monotonic()
            process_completed_ns = time.time_ns()
            process_completion_kind = "communicate_returned"
        except subprocess.TimeoutExpired:
            if process.poll() is None and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if process.poll() is None and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=5)
            exit_code = 124
            stderr += b"\nrunner watchdog: 90 seconds (outside Gooo search budget)\n"
            process_completed_mono = time.monotonic()
            process_completed_ns = time.time_ns()
            process_completion_kind = "runner_watchdog_killed"
    finally:
        if process is not None:
            take_sample(process.pid)
        stop.set()
        if sampler is not None:
            sampler.join(timeout=3)
        proxy.set_invocation(None)
    harness_completed_ns = time.time_ns()
    write_bytes(out_dir / "stdout.raw", stdout)
    write_bytes(out_dir / "stderr.raw", stderr)
    resource = {
        "schema": "gooo/ci-context-selection-resource/v1",
        "cli_active_wall_ms": ((process_completed_mono - start_mono) * 1000) if process_completed_mono is not None else None,
        "process_completion_kind": process_completion_kind,
        "harness_sampling_window_ms": (time.monotonic() - start_mono) * 1000,
        "sampling_interval_ms": 120,
        "cli_process": process_summary(samples, "cli"),
        "owned_laya_server_process": process_summary(samples, "laya_server"),
        "raw_samples": "resource-samples.jsonl",
    }
    write_json(out_dir / "resource.json", resource)
    relevant_events = [event for event in proxy.events if event.get("invocation_id") == invocation_id]
    record = {
        "schema": "gooo/ci-context-selection-invocation/v1",
        "sequence": ordinal, "invocation_id": invocation_id, "intent_id": row["intent_id"],
        "treatment": row["treatment"], "activity": row["activity"],
        "started_utc": started_utc, "started_unix_ns": started_ns,
        "process_completed_unix_ns": process_completed_ns,
        "harness_completed_utc": now_utc(), "harness_completed_unix_ns": harness_completed_ns,
        "exit_code": exit_code, "harness_watchdog_seconds": 90,
        "argv": command, "binary_sha256": EXPECTED_BINARY_SHA,
        "plan_sha256": sha256(plan_bytes), "fixture_sha256": sha256(fixture_bytes),
        "stdout_sha256": sha256(stdout), "stderr_sha256": sha256(stderr),
        "resource_file": "resource.json", "choice_event_sequences": [e["seq"] for e in relevant_events if e["kind"] == "laya_choice"],
        "event_sequences": [e["seq"] for e in relevant_events],
    }
    write_json(out_dir / "invocation.json", record)
    return record


def parse_state(raw: bytes) -> tuple[dict, dict]:
    outer = json.loads(raw)
    state_wrapper = outer.get("state", {})
    encoded = state_wrapper.get("request", "") if isinstance(state_wrapper, dict) else ""
    state = json.loads(encoded) if isinstance(encoded, str) and encoded else {}
    return outer, state


def test_source(activity: str, suites: dict[str, list[dict[str, int]]]) -> str:
    cases = []
    for suite_name, rows in suites.items():
        for index, case in enumerate(rows):
            cases.append(f'{{Suite: {json.dumps(suite_name)}, Index: {index}, Input: {case["input"]}, Expected: {case["expected"]}}}')
    return f'''package bodycodegen

import (
	"encoding/json"
	"testing"
)

type probeCase struct {{ Suite string; Index int; Input int64; Expected int64 }}
type probeObservation struct {{ Suite string `json:"suite"`; Index int `json:"index"`; Input int64 `json:"input"`; Expected int64 `json:"expected"`; Actual int64 `json:"actual"`; Passed bool `json:"passed"` }}

func TestSelectedBodyAgainstPostSelectionSuites(t *testing.T) {{
	cases := []probeCase{{{', '.join(cases)}}}
	for _, testCase := range cases {{
		actual := {activity}(testCase.Input)
		observation := probeObservation{{Suite: testCase.Suite, Index: testCase.Index, Input: testCase.Input, Expected: testCase.Expected, Actual: actual, Passed: actual == testCase.Expected}}
		encoded, err := json.Marshal(observation)
		if err != nil {{ t.Fatal(err) }}
		t.Logf("CASE_RESULT:%s", encoded)
		if !observation.Passed {{ t.Errorf("CASE_MISMATCH:%s", encoded) }}
	}}
}}
'''


def evaluate_saved_source(run_dir: Path, study: dict, cli_records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Open finite holdout vectors only after all model choices and raw requests are saved."""
    rows_by_id = {row["invocation_id"]: row for row in study["invocations"]}
    events = [json.loads(line) for line in (run_dir / "proxy" / "events.jsonl").read_text().splitlines() if line.strip()]
    report_rows = []
    privacy_rows = []
    manifest_intents = {row["id"]: row for row in read_json(ROOT / "manifest.json")["intents"]}
    binary_env = os.environ.copy()
    binary_env.update({"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOWORK": "off"})

    # All choice responses and plans are durable before this first oracle read.
    for item in study["intents"]:
        oracle = read_json(COHORT_SOURCE / "oracles" / f"{item['id']}.oracle.json")
        base_plan = read_json(ROOT / item["plan"])
        if oracle.get("schema") != "gooo/ir-search-finite-oracle/v1" or oracle.get("intent_id") != item["id"]:
            raise RuntimeError(f"{item['id']}: independent finite oracle identity mismatch")
        oracle_bytes = (COHORT_SOURCE / "oracles" / f"{item['id']}.oracle.json").read_bytes()
        if sha256(oracle_bytes) != manifest_intents[item["id"]]["independent_oracle_sha256"]:
            raise RuntimeError(f"{item['id']}: independent finite oracle differs from predeclared digest")
        for suite_name in ("training", "holdout"):
            suite = oracle[suite_name]
            if len(suite["inputs"]) != len(suite["expected"]):
                raise RuntimeError(f"{item['id']}: oracle {suite_name} vectors have different lengths")
        if oracle["training"]["inputs"] != [case["input"] for case in base_plan["test_cases"]] or oracle["training"]["expected"] != [case["expected"] for case in base_plan["test_cases"]]:
            raise RuntimeError(f"{item['id']}: independent training oracle differs from the prepared plan")
        forbidden = holdout_case_pairs(oracle)
        for row in study["invocations"]:
            if row["intent_id"] != item["id"]:
                continue
            inv_dir = run_dir / "invocations" / row["invocation_id"]
            record = read_json(inv_dir / "invocation.json")
            relevant = [event for event in events if event.get("invocation_id") == row["invocation_id"] and event.get("kind") == "laya_choice"]
            privacy_count = 0
            sent_state = None
            captured_response_ids = []
            plan_sent = read_json(inv_dir / "plan.search-plan.json")
            for event in relevant:
                request_path = run_dir / event["request_file"]
                response_path = run_dir / event["response_file"]
                request_bytes, response_bytes = request_path.read_bytes(), response_path.read_bytes()
                if sha256(request_bytes) != event["request_sha256"] or sha256(response_bytes) != event["response_sha256"]:
                    raise RuntimeError(f"{row['invocation_id']}: raw request/response digest mismatch")
                privacy = scan_selection_body(request_bytes, forbidden)
                privacy_count += 1
                response_payload = json.loads(response_bytes)
                response_choice = response_payload.get("answers", {}).get("body_ir_search", {}).get("choice")
                if event.get("selected_candidate_id") != response_choice:
                    raise RuntimeError(f"{row['invocation_id']}: capture event choice differs from raw response bytes")
                captured_response_ids.append(response_choice)
                outer, state = parse_state(request_bytes)
                if state.get("intent") != plan_sent["intent"]:
                    raise RuntimeError(f"{row['invocation_id']}: captured request intent differs from the saved plan")
                if state.get("activity") != row["activity"] or state.get("training_test_count") != len(base_plan["test_cases"]):
                    raise RuntimeError(f"{row['invocation_id']}: captured activity or training count differs from plan")
                canonical_training = json.dumps(plan_sent["test_cases"], separators=(",", ":"), ensure_ascii=False).encode()
                if state.get("training_suite_sha256") != "sha256:" + sha256(canonical_training):
                    raise RuntimeError(f"{row['invocation_id']}: captured training suite digest differs from plan")
                declared = {candidate["id"]: candidate["expression"] for candidate in plan_sent["candidates"]}
                remaining = {candidate["id"]: candidate["expression"] for candidate in state.get("remaining_candidates", [])}
                if remaining != declared:
                    raise RuntimeError(f"{row['invocation_id']}: captured candidate options differ from declared plan")
                criteria = outer.get("questions", {}).get("body_ir_search", {}).get("criteria", {})
                if set(criteria) != set(declared):
                    raise RuntimeError(f"{row['invocation_id']}: Laya choice criteria differ from declared candidate IDs")
                if any("holdout" in key.lower() for key in state):
                    raise RuntimeError(f"{row['invocation_id']}: held-out state field reached selection request")
                sent_state = state
                privacy_rows.append({"invocation_id": row["invocation_id"], "sequence": event["seq"], **privacy})
            if record["exit_code"] != 0:
                report_rows.append({"invocation_id": row["invocation_id"], "decision": "CLI_FAILED", "exit_code": record["exit_code"],
                                    "captured_choice_posts": len(relevant),
                                    "captured_request_sha256": relevant[0].get("request_sha256") if relevant else None,
                                    "request_intent_sha256": sha256(sent_state.get("intent", "").encode()) if sent_state else None})
                continue
            payload = json.loads((inv_dir / "stdout.raw").read_bytes())
            report = payload["report"]
            body = report.get("body_search")
            if report.get("decision") != "PASS" or not isinstance(body, dict) or not payload.get("source"):
                report_rows.append({"invocation_id": row["invocation_id"], "decision": report.get("decision", "NO_REPORT"), "error": report.get("error"),
                                    "captured_choice_posts": len(relevant),
                                    "captured_request_sha256": relevant[0].get("request_sha256") if relevant else None,
                                    "request_intent_sha256": sha256(sent_state.get("intent", "").encode()) if sent_state else None})
                continue

            attempts = body.get("attempts", [])
            attempt = attempts[0] if attempts else {}
            selected_id = body.get("selected_candidate_id")
            selected_expression = body.get("selected_expression")
            declared_expressions = {candidate["id"]: candidate["expression"] for candidate in base_plan["candidates"]}
            if selected_id not in declared_expressions:
                raise RuntimeError(f"{row['invocation_id']}: selected ID is not in the declared candidate set")
            if declared_expressions[selected_id] != selected_expression:
                raise RuntimeError(f"{row['invocation_id']}: selected expression differs from its declared candidate")
            model_rounds = sum(1 for candidate_attempt in attempts if (candidate_attempt.get("decision") or {}).get("mode") == "laya")
            response_selected = relevant[0].get("selected_candidate_id") if relevant else None
            if model_rounds and response_selected != selected_id:
                raise RuntimeError(f"{row['invocation_id']}: captured response and receipt selected different candidates")
            if model_rounds and attempts[0].get("decision", {}).get("request_sha256") != "sha256:" + relevant[0]["request_sha256"]:
                raise RuntimeError(f"{row['invocation_id']}: typed decision receipt does not bind captured choice request")
            if len(relevant) > 1 or (model_rounds and len(relevant) != 1):
                raise RuntimeError(f"{row['invocation_id']}: one-attempt search has an unexpected Laya POST count")
            if not attempts or len(attempts) != 1 or attempts[0].get("candidate_id") != selected_id:
                raise RuntimeError(f"{row['invocation_id']}: selected candidate does not match its sole attempted candidate")
            if report.get("generated_digest") != "sha256:" + sha256(payload["source"].encode()):
                raise RuntimeError(f"{row['invocation_id']}: emitted source digest differs from compiler report")
            if model_rounds and attempts[0].get("decision", {}).get("selected") != selected_id:
                raise RuntimeError(f"{row['invocation_id']}: model decision receipt differs from selected candidate")

            training_cases = base_plan["test_cases"]
            holdout_cases = [{"input": input_value, "expected": expected_value}
                             for input_value, expected_value in zip(oracle["holdout"]["inputs"], oracle["holdout"]["expected"])]
            suites = {"training": training_cases, "holdout": holdout_cases}
            validation_dir = run_dir / "postselection-validation" / row["invocation_id"]
            validation_dir.mkdir(parents=True, exist_ok=False)
            source_bytes = payload["source"].encode()
            write_bytes(validation_dir / "emitted.go", source_bytes)
            harness = test_source(row["activity"], suites).encode()
            write_bytes(validation_dir / "postselection_test.go", harness)
            write_bytes(validation_dir / "go.mod", b"module postselection-probe\n\ngo 1.26.0\n")
            # These vectors and test results are created strictly after the corresponding choice response is durable.
            write_json(validation_dir / "training-cases.json", training_cases)
            write_json(validation_dir / "holdout-cases.json", holdout_cases)
            write_bytes(validation_dir / "independent-oracle.json", oracle_bytes)

            go_started = time.time_ns()
            test = subprocess.run(["go", "test", "-count=1", "-v", "./..."], cwd=validation_dir,
                                  env=binary_env, capture_output=True, check=False)
            go_finished = time.time_ns()
            write_bytes(validation_dir / "go-test.stdout.raw", test.stdout)
            write_bytes(validation_dir / "go-test.stderr.raw", test.stderr)
            actual_rows = []
            mismatch_rows = []
            for line in test.stdout.decode("utf-8", errors="replace").splitlines():
                match = re.search(r"CASE_RESULT:(\{.*\})$", line)
                if match:
                    actual_rows.append(json.loads(match.group(1)))
                mismatch = re.search(r"CASE_MISMATCH:(\{.*\})$", line)
                if mismatch:
                    mismatch_rows.append(json.loads(mismatch.group(1)))
            expected_rows = []
            for suite_name, cases in suites.items():
                for index, case in enumerate(cases):
                    expected_rows.append({"suite": suite_name, "index": index, "input": case["input"],
                                          "expected": case["expected"], "actual": None, "passed": None})
            actual_by_key = {(row.get("suite"), row.get("index")): row for row in actual_rows}
            expected_keys = {(row["suite"], row["index"]) for row in expected_rows}
            if len(actual_by_key) != len(actual_rows) or set(actual_by_key) != expected_keys:
                raise RuntimeError(f"{row['invocation_id']}: compiled Go test did not observe every post-selection case")
            for expected in expected_rows:
                actual = actual_by_key[(expected["suite"], expected["index"])]
                if (actual.get("input"), actual.get("expected")) != (expected["input"], expected["expected"]):
                    raise RuntimeError(f"{row['invocation_id']}: compiled Go observations differ from exact oracle cases")
                if actual.get("passed") != (actual.get("actual") == actual.get("expected")):
                    raise RuntimeError(f"{row['invocation_id']}: compiled Go case pass flag is inconsistent")
            actual_mismatches = [actual_by_key[(value["suite"], value["index"])] for value in mismatch_rows]
            if len(actual_mismatches) != len(mismatch_rows) or {
                (value["suite"], value["index"]) for value in actual_mismatches
            } != {key for key, value in actual_by_key.items() if not value["passed"]}:
                raise RuntimeError(f"{row['invocation_id']}: Go mismatch output differs from finite case results")
            internal_training = body.get("training_case_results", [])
            internal_training_by_input = {item["input"]: item for item in internal_training}
            if set(internal_training_by_input) != {case["input"] for case in training_cases}:
                raise RuntimeError(f"{row['invocation_id']}: Gooo training receipt does not cover exact training inputs")
            if body.get("training_total") != len(training_cases) or body.get("training_passed") != receipt["training_score"]["passed"]:
                raise RuntimeError(f"{row['invocation_id']}: Gooo training score differs from independent plan result")
            for actual in actual_rows:
                if actual["suite"] == "training":
                    internal = internal_training_by_input.get(actual["input"])
                    if not internal or (internal["actual"], internal["expected"], internal["passed"]) != (actual["actual"], actual["expected"], actual["passed"]):
                        raise RuntimeError(f"{row['invocation_id']}: compiled training result differs from Gooo's training receipt")
            if test.returncode not in (0, 1) or b"setup failed" in (test.stdout + test.stderr).lower() or b"build failed" in (test.stdout + test.stderr).lower():
                raise RuntimeError(f"{row['invocation_id']}: Go source compilation/execution failed outside expected case mismatches")
            expected_exit = 1 if mismatch_rows else 0
            if test.returncode != expected_exit:
                raise RuntimeError(f"{row['invocation_id']}: Go test exit does not match observed case outcomes")

            training_actual = [actual for actual in actual_rows if actual["suite"] == "training"]
            holdout_actual = [actual for actual in actual_rows if actual["suite"] == "holdout"]
            score = lambda rows: {"passed": sum(bool(case["passed"]) for case in rows), "total": len(rows)}
            receipt = {
                "schema": "gooo/postselection-independent-body-validation/v1",
                "invocation_id": row["invocation_id"], "intent_id": item["id"],
            "candidate_id": selected_id, "candidate_expression": selected_expression,
                "source_sha256": sha256(source_bytes), "source_digest_reported": report.get("generated_digest"),
                "oracle_sha256": sha256(oracle_bytes), "training_score": score(training_actual),
                "holdout_score": score(holdout_actual), "all_finite_cases_observed": True,
                "go_test_exit_code": test.returncode,
                "go_test_expected_exit_code": expected_exit,
                "go_test_stdout_sha256": sha256(test.stdout), "go_test_stderr_sha256": sha256(test.stderr),
                "timing_unix_ns": {"go_test_started": go_started, "go_test_finished": go_finished},
                "scope": "independent finite int64 input observations after candidate selection; no full-domain proof",
            }
            write_json(validation_dir / "validation-receipt.json", receipt)
            attempt_decision = (attempt.get("decision") or {}).get("mode")
            decision_latency = attempt.get("decision_latency_ms")
            invocation_row = {
                "sequence": row["sequence"], "intent_id": row["intent_id"], "treatment": row["treatment"],
                "candidate_id": selected_id, "candidate_expression": selected_expression,
                "selection_mode": attempt_decision, "model_choice_rounds": model_rounds,
                "captured_choice_posts": len(relevant), "response_selected_candidate_id": response_selected,
                "captured_response_candidate_ids": captured_response_ids,
                "training_accuracy_percent_from_gooo": body.get("training_accuracy_percent"),
                "training_score_external_go": receipt["training_score"],
                "holdout_score_external_go": receipt["holdout_score"],
                "resolver_decision_latency_ms": decision_latency,
                "proxy_choice_latency_ms": relevant[0].get("duration_ms") if relevant else None,
                "first_choice_request_after_cli_start_ms": ((relevant[0].get("started_unix_ns") - record["started_unix_ns"]) / 1e6) if relevant else None,
                "cli_active_wall_ms": read_json(inv_dir / "resource.json")["cli_active_wall_ms"],
                "harness_sampling_window_ms": read_json(inv_dir / "resource.json")["harness_sampling_window_ms"],
                "owned_server_resource_samples": read_json(inv_dir / "resource.json")["owned_laya_server_process"],
                "privacy_scanned_post_count": privacy_count,
                "captured_request_sha256": relevant[0].get("request_sha256") if relevant else None,
                "request_intent_sha256": sha256(sent_state.get("intent", "").encode()) if sent_state else None,
                "context_gate": row["context_gate"],
                "context_was_injected": row["treatment"] == "exact_matching_failed_ci_context",
                "go_validation_receipt": str((validation_dir / "validation-receipt.json").relative_to(run_dir)),
            }
            report_rows.append(invocation_row)

    return report_rows, privacy_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, default=Path("/tmp/gooo-ir-search-20260930"))
    parser.add_argument("--laya-venv", type=Path, default=Path("/tmp/meta-ontology-go-laya-venv-20260930"))
    parser.add_argument("--run-id", default=RUN_ID_DEFAULT)
    args = parser.parse_args()

    study = read_json(DESIGN_DIR / "study-design.json")
    design_bytes = (DESIGN_DIR / "study-design.json").read_bytes()
    if sha256(design_bytes) != (DESIGN_DIR / "study-design.sha256").read_text().split()[0]:
        raise RuntimeError("prepared randomized design hash mismatch")
    if study.get("status") != "prepared_not_executed" or study.get("invocation_count") != 12:
        raise RuntimeError("prepared design is not the expected 12-invocation study")
    if study.get("compiler_revision") != EXPECTED_SOURCE_REV or study.get("binary_sha256") != EXPECTED_BINARY_SHA:
        raise RuntimeError("prepared study compiler pin mismatch")
    binary = args.binary.resolve()
    binary_receipt = verify_binary(binary)
    if binary_receipt["sha256"] != EXPECTED_BINARY_SHA:
        raise RuntimeError("binary verification mismatch")
    venv = args.laya_venv.resolve()
    server_exe, python_exe = venv / "bin" / "laya-serve", venv / "bin" / "python"
    if not server_exe.is_file() or not os.access(server_exe, os.X_OK) or not python_exe.is_file():
        raise RuntimeError(f"pinned installed Laya environment unavailable: {venv}")
    version = subprocess.run([str(python_exe), "-c", "import importlib.metadata as m; print(m.version('laya'))"],
                             capture_output=True, text=True, check=False)
    if version.returncode != 0 or version.stdout.strip() != EXPECTED_LAYA_VERSION:
        raise RuntimeError(f"Laya version mismatch: got {version.stdout.strip()!r}")
    out_dir = ROOT / "audit" / args.run_id
    if out_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing study run: {out_dir}")
    out_dir.mkdir(parents=True)
    (out_dir / "laya").mkdir()
    (out_dir / "proxy").mkdir()
    (out_dir / "invocations").mkdir()
    write_bytes(out_dir / "study-design.json", design_bytes)
    write_bytes(out_dir / "study-design.sha256", (DESIGN_DIR / "study-design.sha256").read_bytes())
    preexecution = {
        "schema": "gooo/ir-search-ci-context-preexecution/v1",
        "study_id": study["study_id"], "status": "plans_frozen_before_laya_start",
        "study_design_sha256": sha256(design_bytes), "binary": binary_receipt,
        "laya_version": version.stdout.strip(), "expected_model_revision": EXPECTED_MODEL_REVISION,
        "runtime": study["runtime"], "offline_flags": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
        "invocation_plan_hashes": [{"invocation_id": row["invocation_id"], "plan_sha256": row["plan_sha256"],
                                    "fixture_sha256": row["fixture_sha256"], "treatment": row["treatment"]}
                                   for row in study["invocations"]],
        "holdout_vectors_loaded": False,
    }
    pre_bytes = write_json(out_dir / "preexecution.json", preexecution)
    meta = {
        "schema": "gooo/ir-search-ci-context-laya-run/v1", "run_id": args.run_id,
        "status": "starting", "started_utc": now_utc(), "preexecution_sha256": sha256(pre_bytes),
        "treatment_order_seed": study["treatment_order_seed"], "randomized_order": True,
        "warmup_invocations": 0, "invocation_count_planned": 12,
        "provider_policy": "offline loopback-only; GOOO_LAYA_API_KEY unset; no model download",
        "laya_venv": str(venv), "laya_version": version.stdout.strip(),
        "expected_model_revision": EXPECTED_MODEL_REVISION, "device": "cpu", "threads": 4,
        "host_logical_cpu_count": os.cpu_count(),
    }
    write_json(out_dir / "run-metadata.json", meta)

    port = free_port()
    service_env = os.environ.copy()
    service_env.update({
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "LAYA_HOST": "127.0.0.1", "LAYA_PORT": str(port), "LAYA_MODELS": "english",
        "LAYA_DEVICE": "cpu", "LAYA_THREADS": "4", "LAYA_PRELOAD": "1",
    })
    service_env.pop("GOOO_LAYA_API_KEY", None)
    server_stdout = (out_dir / "laya" / "stdout.log").open("wb")
    server_stderr = (out_dir / "laya" / "stderr.log").open("wb")
    server = None
    pgid = None
    proxy = None
    cli_records = []
    run_started_mono = time.monotonic()
    try:
        server = subprocess.Popen([str(server_exe)], cwd=out_dir, env=service_env,
                                  stdin=subprocess.DEVNULL, stdout=server_stdout, stderr=server_stderr,
                                  start_new_session=True)
        pgid = server.pid
        write_json(out_dir / "laya" / "owned-process.json", {
            "schema": "gooo/ir-search-owned-process/v1", "pid": server.pid, "process_group_id": pgid,
            "started_utc": now_utc(), "executable": str(server_exe), "owned_by_runner": True,
            "host": "127.0.0.1", "port": port, "device": "cpu", "loaded_model": "english",
        })
        health_before = wait_for_health(server, port, out_dir)
        proxy = CaptureProxy(port, out_dir)
        meta.update({"status": "running", "laya_port": port, "capture_proxy_url": proxy.url,
                     "health_before": {"device": health_before["device"], "loaded": health_before["loaded"],
                                       "revision": health_before["revisions"]["english"]}})
        write_json(out_dir / "run-metadata.json", meta)
        cli_env = os.environ.copy()
        cli_env["HF_HUB_OFFLINE"] = "1"
        cli_env["TRANSFORMERS_OFFLINE"] = "1"
        cli_env["GOOO_LAYA_URL"] = proxy.url
        cli_env.pop("GOOO_LAYA_API_KEY", None)
        for row in study["invocations"]:
            cli_records.append(run_cli(binary, row, out_dir, proxy, server.pid, cli_env))
        try:
            raw, health_after = fetch_health(port)
            write_bytes(out_dir / "laya" / "health-after.json", raw)
            meta["health_after"] = {"device": health_after.get("device"), "loaded": health_after.get("loaded"),
                                    "revision": health_after.get("revisions", {}).get("english")}
        except Exception as exc:
            meta["health_after_error"] = str(exc)
        write_json(out_dir / "cli-invocation-records.json", cli_records)
        write_json(out_dir / "proxy-events.json", {"events": proxy.events, "choice_post_count": sum(e["kind"] == "laya_choice" for e in proxy.events)})
        meta.update({"status": "raw_selection_capture_complete", "completed_utc": now_utc(),
                     "elapsed_ms_including_health_and_sampler": (time.monotonic() - run_started_mono) * 1000,
                     "planned_invocations": 12, "completed_invocations": len(cli_records),
                     "choice_post_count": sum(e["kind"] == "laya_choice" for e in proxy.events)})
        write_json(out_dir / "run-metadata.json", meta)
    except Exception as exc:
        meta.update({"status": "incomplete_raw_capture", "error": str(exc), "completed_utc": now_utc()})
        write_json(out_dir / "run-metadata.json", meta)
        raise
    finally:
        if proxy is not None:
            proxy.stop()
        if server is not None:
            stop_owned_server(server, pgid, server_stdout, server_stderr)
            server_stdout = server_stderr = None
        else:
            stop_owned_server(None, pgid, server_stdout, server_stderr)

    # Selection requests, responses, and plans are already fsynced and the only owned server is now stopped.
    report_rows, privacy_rows = evaluate_saved_source(out_dir, study, cli_records)
    write_json(out_dir / "privacy-audit.json", {
        "schema": "gooo/ir-search-selection-privacy-audit/v1",
        "choice_post_count": len(privacy_rows), "independent_raw_request_scans": len(privacy_rows),
        "heldout_fields_or_exact_case_pairs_found": 0,
        "holdout_vectors_opened_after_all_selection_responses_saved": True,
        "request_results": privacy_rows,
    })
    choice_event_count = sum(event["kind"] == "laya_choice" for event in proxy.events)
    model_round_count = sum(row.get("model_choice_rounds", 0) for row in report_rows)
    errors = [row for row in report_rows if row.get("decision") == "CLI_FAILED"]
    report = {
        "schema": "gooo/ir-search-ci-context-laya-study-report/v1",
        "study_id": study["study_id"], "run_id": args.run_id,
        "decision": "CAPTURED" if len(report_rows) == 12 and not errors and choice_event_count == 12 and model_round_count == 12
                    and all(row.get("training_score_external_go") is not None and row.get("holdout_score_external_go") is not None for row in report_rows)
                    and len(privacy_rows) == 12 else "PARTIAL_CAPTURE",
        "design_sha256": sha256(design_bytes), "compiler_revision": EXPECTED_SOURCE_REV,
        "binary_sha256": EXPECTED_BINARY_SHA, "runtime": study["runtime"],
        "treatment_order_seed": study["treatment_order_seed"], "randomized_order": True,
        "replicates_per_intent_treatment": 1, "warmup_invocations": 0,
        "planned_invocations": 12, "completed_cli_invocations": len(cli_records),
        "captured_laya_choice_posts": choice_event_count, "receipt_attributed_laya_choice_rounds": model_round_count,
        "expected_holdout_fields_or_pairs_in_choice_requests": 0,
        "intent_treatment_results": report_rows,
        "base_request_equivalence": {
            intent: {
                "no_context_request_sha256": next((row.get("captured_request_sha256") for row in report_rows if row.get("intent_id") == intent and row.get("treatment") == "no_context"), None),
                "rejected_stale_request_sha256": next((row.get("captured_request_sha256") for row in report_rows if row.get("intent_id") == intent and row.get("treatment") == "rejected_stale_source_context"), None),
                "byte_identical": (lambda left, right: left is not None and left == right)(
                    next((row.get("captured_request_sha256") for row in report_rows if row.get("intent_id") == intent and row.get("treatment") == "no_context"), None),
                    next((row.get("captured_request_sha256") for row in report_rows if row.get("intent_id") == intent and row.get("treatment") == "rejected_stale_source_context"), None)),
            }
            for intent in sorted({row["intent_id"] for row in study["invocations"]})
        },
        "limitations": [
            "One randomized replicate per intent-treatment cell; no statistical or general speed claim.",
            "The CI summary enters the exact-context treatment as bounded plain text appended to plan.intent; the compiler exposes no structured external-CI authority field.",
            "The stale-source context fails the provenance gate and the real Laya call uses the unchanged base intent.",
            "Training and holdout scores have separate finite denominators. No full-domain correctness, global-best, model-forward-only latency, or host-CPU-increase claim is made.",
            "CPU time is sampled at the process level and host sampling granularity; it is not a direct accelerator or pure-model-forward measurement.",
        ],
    }
    report_bytes = write_json(out_dir / "report.json", report)
    md = [
        "# Laya selection with CI failure context",
        "",
        f"Run `{args.run_id}` used compiler `{EXPECTED_SOURCE_REV}` and Laya `english` revision `{EXPECTED_MODEL_REVISION}` on CPU with four threads, offline.",
        "",
        f"The randomized study captured {choice_event_count} selection POSTs for 12 single-attempt invocations. Each cell has n=1.",
        "",
        "The exact-context arm appended a bounded, provenance-checked training failure summary to `intent`. The stale-source arm was rejected before injection and used the original intent. Holdout cases were opened only after all selection request/response bytes were saved.",
        "",
        "| Intent | Treatment | Choice | Training (finite) | Holdout (post-selection finite) | Resolver ms | Proxy ms | CLI wall ms |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report_rows:
        train, held = row.get("training_score_external_go", {}), row.get("holdout_score_external_go", {})
        md.append(f"| {row.get('intent_id')} | {row.get('treatment')} | {row.get('candidate_id')} | {train.get('passed')}/{train.get('total')} | {held.get('passed')}/{held.get('total')} | {row.get('resolver_decision_latency_ms')} | {row.get('proxy_choice_latency_ms')} | {row.get('cli_active_wall_ms')} |")
    md += ["", "These finite measurements do not establish full-domain correctness or a general latency effect. Per-invocation resource samples, raw exchanges, receipts, and independent compiled Go runs are stored beside this report.", ""]
    write_bytes(out_dir / "report.md", "\n".join(md).encode())
    meta.update({"status": report["decision"], "completed_utc": now_utc(), "report_sha256": sha256(report_bytes),
                 "privacy_scan_count": len(privacy_rows), "model_choice_round_count": model_round_count})
    write_json(out_dir / "run-metadata.json", meta)
    print(out_dir / "report.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Laya selection study stopped with preserved evidence: {exc}", file=sys.stderr)
        raise SystemExit(2)
