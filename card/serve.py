#!/usr/bin/env python3
"""Serve the card the way its production hosts do.

A strictly sandboxed NFT iframe (``sandbox="allow-scripts"`` without
``allow-same-origin``) runs with an opaque origin, so even fetches back to the
serving host are cross-origin — module imports and data fetches only work when
the server sends CORS headers. Arweave/IPFS gateways send
``Access-Control-Allow-Origin: *``; plain ``python3 -m http.server`` does not.
This server adds the header (plus no-store, so iteration never fights the
cache), making local sandbox testing honest to production hosting.

Usage:  python3 serve.py [port]    (default 8755)
"""

import http.server
import sys


class CorsHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8755
    server = http.server.ThreadingHTTPServer(("", port), CorsHandler)
    print(f"serving card with CORS on http://localhost:{port}/")
    server.serve_forever()
