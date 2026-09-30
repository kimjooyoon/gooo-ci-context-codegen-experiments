"""Model-free regressions for capture ordering and bounded pending requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from run_pinned_context_study import CaptureProxy


class MockBackend:
    def __init__(self, blocked=False):
        self.accepted = threading.Event()
        self.two_accepted = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.headers = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                backend.headers.append(dict(self.headers))
                if len(backend.headers) >= 2:
                    backend.two_accepted.set()
                backend.accepted.set()
                backend.release.wait(2)
                self.reply({"answers": {"body_ir_search": {"choice": "mock"}}})

            def do_GET(self):
                self.reply({"loaded": ["english", "multilingual"], "device": "cpu"})

            def reply(self, value):
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class CaptureProxyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gooo-proxy-regression-")
        self.root = Path(self.temporary.name)
        (self.root / "proxy").mkdir()
        self.backend = MockBackend(blocked=True)
        self.proxy = CaptureProxy(self.backend.server.server_port, self.root)

    def tearDown(self):
        self.backend.release.set()
        self.proxy.wait_for_pending(3)
        self.proxy.stop()
        self.backend.close()
        self.temporary.cleanup()

    def connection(self, timeout=2):
        return http.client.HTTPConnection("127.0.0.1", self.proxy.server.server_port, timeout=timeout)

    def test_cancelled_client_keeps_pending_until_backend_response_saved(self):
        self.proxy.set_invocation("first")
        first = self.connection(timeout=0.03)
        first.request("POST", "/v1/systemone", body=b'{"mock":1}')
        self.assertTrue(self.backend.accepted.wait(1))
        with self.assertRaises((socket.timeout, TimeoutError)):
            first.getresponse()
        first.close()
        pending = self.proxy.wait_for_pending(0.03)
        self.assertFalse(pending["settled"])
        self.assertEqual(pending["pending_sequences"], [1])
        self.backend.release.set()
        self.assertTrue(self.proxy.wait_for_pending(2)["settled"])
        self.assertEqual(self.proxy.events[0]["invocation_id"], "first")
        self.assertTrue((self.root / self.proxy.events[0]["response_file"]).is_file())
        self.proxy.set_invocation("second")
        second = self.connection()
        second.request("POST", "/v1/systemone", body=b'{"mock":2}')
        self.assertEqual(second.getresponse().status, 200)
        second.close()
        self.assertTrue(self.proxy.wait_for_pending(2)["settled"])
        events = sorted(self.proxy.events, key=lambda event: event["seq"])
        self.assertEqual([event["seq"] for event in events], [1, 2])
        self.assertGreaterEqual(events[1]["started_unix_ns"], events[0]["completed_unix_ns"])
        self.assertEqual([event["invocation_id"] for event in events], ["first", "second"])

    def test_overlapping_accepts_have_unique_sequences_and_files(self):
        self.proxy.set_invocation("mock-overlap")
        first, second = self.connection(), self.connection()
        first.request("POST", "/v1/systemone", body=b'{"mock":1}')
        self.assertTrue(self.backend.accepted.wait(1))
        second.request("POST", "/v1/systemone", body=b'{"mock":2}')
        self.assertTrue(self.backend.two_accepted.wait(1))
        self.backend.release.set()
        self.assertEqual(first.getresponse().status, 200)
        self.assertEqual(second.getresponse().status, 200)
        first.close()
        second.close()
        self.assertTrue(self.proxy.wait_for_pending(2)["settled"])
        events = sorted(self.proxy.events, key=lambda event: event["seq"])
        self.assertEqual([event["seq"] for event in events], [1, 2])
        self.assertEqual(len({event["request_file"] for event in events}), 2)
        self.assertEqual({(self.root / event["request_file"]).read_bytes() for event in events},
                         {b'{"mock":1}', b'{"mock":2}'})

    def test_health_is_separate_and_credentials_are_not_forwarded(self):
        self.backend.release.set()
        self.proxy.set_invocation("mock-health")
        connection = self.connection()
        connection.request("GET", "/health")
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        connection = self.connection()
        connection.request("POST", "/v1/systemone", body=b"{}",
                           headers={"Authorization": "synthetic-test-value", "X-API-Key": "synthetic-test-value"})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        self.assertTrue(self.proxy.wait_for_pending(2)["settled"])
        self.assertEqual(sorted(event["kind"] for event in self.proxy.events), ["health_check", "laya_choice"])
        self.assertFalse({key.lower() for key in self.backend.headers[-1]} & {"authorization", "x-api-key"})


if __name__ == "__main__":
    unittest.main()
