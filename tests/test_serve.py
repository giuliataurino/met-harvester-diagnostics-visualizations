"""The live page's API against the fake Met API."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from met_harvest.client import MetClient
from met_harvest.serve import App, make_handler
from test_pipeline import FakeSession


def _req(port, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                                      method="POST" if data is not None else "GET"))
    return json.loads(r.read()) if path.startswith("/api") else r.read().decode()


def test_serve_harvest_updates_state(tmp_path):
    sess = FakeSession()
    client = MetClient(tmp_path / "met", rate=1000, session=sess)
    app = App(client)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = _req(port, "/")
        assert "<title>Met metadata by department</title>" in page and "/api/state" in page
        s = _req(port, "/api/state")
        assert s["survey"]["records"] == 0 and s["api"].startswith("https://collectionapi.metmuseum.org")
        assert len(s["fields"]) == 58 and [d["id"] for d in s["known_departments"]] == [6, 13]
        assert _req(port, "/api/harvest/start", {"departments": [13], "limit": None, "workers": 2})["ok"]
        for _ in range(100):
            s = _req(port, "/api/state")
            if not s["running"] and not s["indexing"] and s["progress"]["departments"]:
                break
            time.sleep(0.05)
        d13 = s["progress"]["departments"][0]
        assert d13["done"] and d13["fetched"] == 2 and d13["missing"] == 1 and d13["invalid"] == 1
        depts = s["survey"]["departments"]
        assert set(depts) == {"Asian Art", "Greek and Roman Art"}       # object 4 claims Asian Art
        gr = depts["Greek and Roman Art"]
        assert gr["records"] == 2 and gr["fill"]["culture"] == 1.0 and gr["kinds"]["dynasty"] == {"empty_string": 2}
        assert gr["aat"]["culture"] == {"filled": 2, "resolved": 2}  # "Greek, Attic" -> greek; "Japan" -> japanese (aat.py substring match)
        assert s["survey"]["department_mismatch"][0]["n"] == 1
        # a second start refuses while running only; after completion it re-runs from cache (no new fetches)
        n = len(sess.calls)
        assert _req(port, "/api/harvest/start", {"departments": [13], "limit": None, "workers": 1})["ok"]
        for _ in range(100):
            s = _req(port, "/api/state")
            if not s["running"] and not s["indexing"]:
                break
            time.sleep(0.05)
        assert s["survey"]["records"] == 3 and s["progress"]["departments"][0]["cached"] == 4  # 3 records + 1 .missing marker
        assert all("/objects/" not in c[0] for c in sess.calls[n:])
    finally:
        httpd.shutdown()
