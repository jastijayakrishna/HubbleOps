from __future__ import annotations

import ssl
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return None


with tempfile.TemporaryDirectory(prefix="hops-fixture-") as temporary:
    root = Path(temporary)
    certificate = root / "server.crt"
    key = root / "server.key"
    subprocess.run(
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=fixture-service",
            "-addext",
            "subjectAltName=DNS:fixture-service",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server = ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
