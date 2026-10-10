#!/usr/bin/env python3
"""Serve the local factory board; loopback only."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from home import state_root  # noqa: E402

BOARD = Path(__file__).resolve().parents[1] / 'dashboard.html'
ROOT = None  # the project's state directory, set in main

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in {'/', '/dashboard.html'}:
            body = BOARD.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        elif self.path == '/api/jobs':
            jobs = []
            for path in sorted((ROOT/'jobs').glob('*/job.json')):
                try:
                    jobs.append(json.loads(path.read_text()))
                except (OSError, json.JSONDecodeError):
                    jobs.append({'id':path.parent.name,'stage':'blocked','blocker':'Unreadable job state'})
            body = json.dumps(jobs).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            super().do_GET()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--root', type=Path, help='factory state directory (default: <project>/.factory)')
    args = parser.parse_args()
    ROOT = (args.root or state_root()).resolve()
    server = ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(ROOT)))
    print(f'Factory board: http://127.0.0.1:{args.port}/dashboard.html',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
