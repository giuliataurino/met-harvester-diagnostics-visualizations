"""Local page: start/stop a harvest and watch four views of metadata fill by department update as
records arrive.

    python -m met_harvest serve [--port 8766]

On start the cache is indexed into a Survey; every record the harvester fetches is added to it as
it lands, and when a harvest finishes the survey is rebuilt from disk so re-fetches never double
count. The page polls /api/state while a harvest runs.

Routes
    GET  /                   the page (index.html next to this file)
    GET  /api/state          harvest progress + survey snapshot + field groups
    POST /api/harvest/start  {"departments": [13, 10] | null, "limit": 500 | null, "workers": 4}
    POST /api/harvest/stop
    POST /api/reindex
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .client import API, MetClient
from .fields import DOCUMENTED
from .harvest import Harvester
from .survey import Survey

HERE = Path(__file__).parent


class LiveSurvey:
    """Survey guarded by a lock, with a cached snapshot so polling is cheap."""

    def __init__(self, client: MetClient):
        self.client = client
        self.lock = threading.Lock()
        self.survey = Survey()
        self.indexed = 0
        self.indexing = False
        self._snapshot: dict | None = None
        self._dirty = True

    def reindex(self) -> None:
        self.indexing = True
        sv = Survey()
        sv.load_listings(self.client.cache_dir)
        n = sv.add_all(self.client.iter_cached())
        with self.lock:
            self.survey, self.indexed, self._dirty = sv, n, True
        self.indexing = False

    def add(self, rec: dict) -> None:
        with self.lock:
            self.survey.add(rec)
            self.indexed += 1
            self._dirty = True

    def snapshot(self) -> dict:
        with self.lock:
            if self._dirty or self._snapshot is None:
                res = self.survey.result()
                self._snapshot = {"records": res["records"], "departments": res["departments"],
                                  "department_mismatch": res["department_mismatch"][:20],
                                  "images": res["images"]}
                self._dirty = False
            return self._snapshot


class App:
    def __init__(self, client: MetClient):
        self.client = client
        self.live = LiveSurvey(client)
        self.harvester: Harvester | None = None
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        threading.Thread(target=self.live.reindex, daemon=True).start()

    # ------------------------------------------------------------- harvest control
    def start(self, departments, limit, workers) -> tuple[bool, str]:
        if self.thread and self.thread.is_alive():
            return False, "a harvest is already running"
        self.error = None
        self.harvester = Harvester(self.client, workers=workers or 4, on_record=self.live.add)
        self.harvester.state.departments.clear()      # progress shown is for this run only

        def run():
            try:
                self.harvester.run(only=departments or None, limit_per_dept=limit)
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self.live.reindex()                   # exact counts, no double counting

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return True, "started"

    def stop(self) -> None:
        if self.harvester:
            self.harvester.stop.set()

    def state(self) -> dict:
        running = bool(self.thread and self.thread.is_alive())
        progress = self.harvester.summary() if self.harvester else {"departments": [], "totals": {}}
        return {
            "running": running, "indexing": self.live.indexing, "error": self.error,
            "requests_made": self.client.requests_made, "api": API,
            "progress": progress,
            "survey": self.live.snapshot(),
            "fields": [{"name": f.name, "group": f.group, "aat": f.aat} for f in DOCUMENTED],
            "known_departments": [{"id": d.id, "name": d.name}
                                  for d in self._departments()],
            "t": time.time(),
        }

    def _departments(self):
        try:
            return self.client.departments(refresh=False) if (self.client.cache_dir / "departments.json").exists() \
                else self.client.departments(refresh=True)
        except Exception:  # noqa: BLE001 - offline: the page still works on the cache
            return []


def make_handler(app: App):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE), **kw)

        def log_message(self, *a):  # quiet
            pass

        def send_json(self, data, status=200):
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.path = "/index.html"
            if self.path == "/api/state":
                return self.send_json(app.state())
            return super().do_GET()

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if self.path == "/api/harvest/start":
                ok, msg = app.start(body.get("departments"), body.get("limit"), body.get("workers"))
                return self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
            if self.path == "/api/harvest/stop":
                app.stop()
                return self.send_json({"ok": True})
            if self.path == "/api/reindex":
                threading.Thread(target=app.live.reindex, daemon=True).start()
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, 404)

    return Handler


def serve(client: MetClient, port: int = 8766) -> None:
    app = App(client)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    print(f"serving http://localhost:{port}  cache: {client.cache_dir}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        app.stop()
        print("\nstopped", file=sys.stderr)
