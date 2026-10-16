"""A loopback Presidio double for offline tests (Step 34): the analyzer finds nothing and the
anonymizer returns the text unchanged, so guardrails behave as they would with clean data.

Tests that are about PII use the real Presidio (stand-in, ``-m integration``); this only stops
the PII guardrail's fail-closed rule from blocking runs that have no personal data in them.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        reply = [] if self.path == "/analyze" else {"text": body.get("text", ""), "items": []}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        return None


@contextmanager
def presidio() -> Iterator[str]:
    """A running fake on 127.0.0.1; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
