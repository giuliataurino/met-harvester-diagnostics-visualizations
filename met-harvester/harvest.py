"""Harvest every department (or a chosen subset) into the object cache.

Per department: fetch the id listing, then fetch each object not yet cached. Progress is
written to <cache>/harvest_state.json after every department and every `checkpoint` objects,
and failures to <cache>/harvest_failures.jsonl, so a killed run resumes by re-running the same
command: cached ids cost no request. An object can belong to one department in the listing but
report another in its `department` field; both are kept (listing membership in <cache>/ids/,
the record's own claim in the record) and the survey reports disagreements.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from pydantic import ValidationError

from .client import Department, MetClient, NotFound
from .models import MetObject


@dataclass
class DeptProgress:
    department_id: int
    name: str
    listed: int = 0
    fetched: int = 0
    cached: int = 0
    missing: int = 0
    failed: int = 0
    invalid: int = 0          # fetched fine, but MetObject rejected it
    seconds: float = 0.0
    done: bool = False


@dataclass
class HarvestState:
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    departments: dict[int, DeptProgress] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        self.updated_at = time.time()
        payload = {"started_at": self.started_at, "updated_at": self.updated_at,
                   "departments": {k: asdict(v) for k, v in self.departments.items()}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "HarvestState":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        st = cls(started_at=raw.get("started_at", time.time()))
        for k, v in raw.get("departments", {}).items():
            st.departments[int(k)] = DeptProgress(**v)
        return st


ProgressFn = Callable[[DeptProgress, int, int], None]   # (dept progress, done, total)


class Harvester:
    def __init__(self, client: MetClient, workers: int = 4, checkpoint: int = 500,
                 validate: bool = True, on_progress: Optional[ProgressFn] = None):
        self.client = client
        self.workers = max(1, workers)
        self.checkpoint = checkpoint
        self.validate = validate
        self.on_progress = on_progress
        self.state_path = client.cache_dir / "harvest_state.json"
        self.failures_path = client.cache_dir / "harvest_failures.jsonl"
        self.state = HarvestState.load(self.state_path)
        self._lock = threading.Lock()
        self.stop = threading.Event()

    # ------------------------------------------------------------------ selection
    def select_departments(self, only: Optional[Iterable[int]] = None) -> list[Department]:
        depts = self.client.departments(refresh=True)
        if only is not None:
            wanted = set(only)
            unknown = wanted - {d.id for d in depts}
            if unknown:
                raise SystemExit(f"unknown department id(s): {sorted(unknown)}; "
                                 f"valid: {[d.id for d in depts]}")
            depts = [d for d in depts if d.id in wanted]
        return depts

    # ------------------------------------------------------------------ run
    def run(self, only: Optional[Iterable[int]] = None, limit_per_dept: Optional[int] = None,
            since: Optional[str] = None, refresh_ids: bool = True, refresh_objects: bool = False,
            redo_done: bool = False) -> HarvestState:
        for dept in self.select_departments(only):
            prog = self.state.departments.get(dept.id)
            if prog and prog.done and not redo_done and not refresh_objects and not since:
                continue
            prog = DeptProgress(dept.id, dept.name)
            self.state.departments[dept.id] = prog
            self._harvest_department(dept, prog, limit_per_dept, since, refresh_ids, refresh_objects)
            self.state.save(self.state_path)
            if self.stop.is_set():
                break
        return self.state

    def _harvest_department(self, dept: Department, prog: DeptProgress, limit: Optional[int],
                            since: Optional[str], refresh_ids: bool, refresh_objects: bool) -> None:
        t0 = time.time()
        ids = self.client.object_ids(dept.id, since=since, refresh=refresh_ids)
        if limit:
            ids = ids[:limit]
        prog.listed = len(ids)
        todo = ids if refresh_objects else [i for i in ids if not self.client.is_cached(i)]
        prog.cached = len(ids) - len(todo)
        done = 0

        def one(oid: int) -> tuple[int, str, str]:
            if self.stop.is_set():
                return oid, "stopped", ""
            try:
                raw = self.client.get_object(oid, refresh=refresh_objects)
            except NotFound:
                return oid, "missing", ""
            except Exception as e:  # noqa: BLE001 - keep harvesting, record the failure
                return oid, "failed", f"{type(e).__name__}: {str(e)[:200]}"
            if self.validate:
                try:
                    MetObject.model_validate(raw)
                except ValidationError as e:
                    return oid, "invalid", str(e)[:300]
            return oid, "fetched", ""

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(one, oid) for oid in todo]
            for fut in as_completed(futures):
                oid, status, msg = fut.result()
                with self._lock:
                    if status == "stopped":
                        continue
                    setattr(prog, status, getattr(prog, status) + 1)
                    if status in ("failed", "invalid"):
                        self._log_failure(dept.id, oid, status, msg)
                    done += 1
                    if self.on_progress:
                        self.on_progress(prog, prog.cached + done, len(ids))
                    if done % self.checkpoint == 0:
                        prog.seconds = time.time() - t0
                        self.state.save(self.state_path)
        prog.seconds = time.time() - t0
        prog.done = not self.stop.is_set()

    def _log_failure(self, dept_id: int, oid: int, status: str, msg: str) -> None:
        with self.failures_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"departmentId": dept_id, "objectID": oid, "status": status,
                                 "error": msg, "t": time.time()}) + "\n")

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict:
        rows = [asdict(p) for p in self.state.departments.values()]
        tot = {k: sum(r[k] for r in rows) for k in ("listed", "fetched", "cached", "missing", "failed", "invalid")}
        return {"departments": rows, "totals": tot,
                "complete": all(r["done"] for r in rows) if rows else False}
