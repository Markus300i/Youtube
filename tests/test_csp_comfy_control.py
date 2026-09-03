from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from csp_studio.comfy_control import interrupt_comfyui


class _Handler(BaseHTTPRequestHandler):
    calls: list[tuple[str, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).calls.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib contract
        return


class ComfyControlTests(unittest.TestCase):
    def test_interrupt_posts_to_comfy_interrupt_endpoint(self) -> None:
        _Handler.calls = []
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            result = interrupt_comfyui(base_url=f"http://{host}:{port}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(result["requested"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(_Handler.calls, [("/interrupt", b"{}")])


if __name__ == "__main__":
    unittest.main()
