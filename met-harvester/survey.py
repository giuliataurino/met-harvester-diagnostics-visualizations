"""Survey every cached record: which JSON paths occur, with what types, how often filled,
what values they take, and how that varies by department. Memory stays bounded for the full
~480k-object collection: value counting stops per path once distinct values pass `max_distinct`
(the path is then flagged high-cardinality and only presence/type/length keep accumulating).

Path notation: top-level key `culture`; array items `additionalImages[]`; keys of objects in
arrays `tags[].term`; keys of a free-form map `measurements[].elementMeasurements.<Height>`
are collapsed to `measurements[].elementMeasurements.*` with the key names counted as values.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import aat as aat_mappings
from .fields import DYNAMIC_KEY_PARENTS

AAT_FIELDS = (("culture", "cultures"), ("objectName", "object_types"),
              ("classification", "classifications"), ("medium", "materials"))

EMPTY_STRING = "<empty string>"


@dataclass
class PathStats:
    occurrences: int = 0            # key present at all (including null / "")
    filled: int = 0                 # present with a non-empty value
    nulls: int = 0
    empty_strings: int = 0
    empty_lists: int = 0
    types: Counter = field(default_factory=Counter)
    values: Counter = field(default_factory=Counter)
    high_cardinality: bool = False
    distinct_seen: int = 0
    num_min: Optional[float] = None
    num_max: Optional[float] = None
    len_min: Optional[int] = None
    len_max: Optional[int] = None
    len_sum: int = 0
    list_len_max: int = 0
    examples: list = field(default_factory=list)
    example_ids: list = field(default_factory=list)

    def to_json(self, top_values: int) -> dict:
        d = {
            "occurrences": self.occurrences, "filled": self.filled, "nulls": self.nulls,
            "empty_strings": self.empty_strings, "empty_lists": self.empty_lists,
            "types": dict(self.types), "distinct": self.distinct_seen,
            "high_cardinality": self.high_cardinality,
            "top_values": self.values.most_common(top_values),
            "examples": self.examples, "example_ids": self.example_ids,
        }
        if self.num_min is not None:
            d["num_min"], d["num_max"] = self.num_min, self.num_max
        if self.len_min is not None:
            d["len_min"], d["len_max"] = self.len_min, self.len_max
            d["len_mean"] = round(self.len_sum / max(self.filled, 1), 1)
        if self.list_len_max:
            d["list_len_max"] = self.list_len_max
        return d


class Survey:
    def __init__(self, max_distinct: int = 5000, examples: int = 3, top_values: int = 25):
        self.max_distinct = max_distinct
        self.n_examples = examples
        self.top_values = top_values
        self.records = 0
        self.paths: dict[str, PathStats] = defaultdict(PathStats)
        self.by_dept_records: Counter = Counter()
        self.by_dept_filled: dict[str, Counter] = defaultdict(Counter)   # dept -> path -> filled
        # dept -> path -> {filled, empty_string, null, empty_list} for top-level fields (small multiples)
        self.by_dept_kind: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        # dept -> field -> {filled, resolved} for the AAT-mapped fields
        self.by_dept_aat: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
        self._aat_cache: dict[tuple[str, str], bool] = {}
        self.listing_dept: dict[int, int] = {}       # objectID -> departmentId from ids/ manifests
        self.dept_names: dict[int, str] = {}
        self.dept_mismatch: Counter = Counter()      # (listing dept, record dept) -> n
        self.duplicate_accessions: Counter = Counter()
        self.objects_per_year: Counter = Counter()   # objectBeginDate century buckets
        self.image_stats = Counter()

    # ----------------------------------------------------------------- setup
    def load_listings(self, cache_dir: Path) -> None:
        deps = cache_dir / "departments.json"
        if deps.exists():
            for d in json.loads(deps.read_text(encoding="utf-8")).get("departments", []):
                self.dept_names[d["departmentId"]] = d["displayName"]
        for p in (cache_dir / "ids").glob("*.json"):
            if not p.stem.isdigit():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            for oid in data.get("objectIDs") or []:
                self.listing_dept[oid] = int(p.stem)

    # ----------------------------------------------------------------- ingest
    def add(self, rec: dict) -> None:
        self.records += 1
        oid = rec.get("objectID")
        dept = rec.get("department") or "<no department>"
        self.by_dept_records[dept] += 1
        self._walk("", rec, oid, dept)

        ld = self.listing_dept.get(oid)
        if ld is not None and self.dept_names.get(ld) and self.dept_names[ld] != rec.get("department"):
            self.dept_mismatch[(self.dept_names[ld], rec.get("department"))] += 1
        acc = rec.get("accessionNumber")
        if acc:
            self.duplicate_accessions[acc] += 1
        y = rec.get("objectBeginDate")
        if isinstance(y, int):
            self.objects_per_year[(y // 100) * 100] += 1
        for fld, category in AAT_FIELDS:
            v = rec.get(fld)
            if isinstance(v, str) and v.strip():
                term = v.split(",")[0].strip() if fld == "culture" else v.strip()
                key = (category, term.lower())
                hit = self._aat_cache.get(key)
                if hit is None:
                    hit = self._aat_cache[key] = bool(aat_mappings.get_aat_uri(term, category=category))
                c = self.by_dept_aat[dept][fld]
                c["filled"] += 1
                if hit:
                    c["resolved"] += 1
        self.image_stats["has_primary" if rec.get("primaryImage") else "no_primary"] += 1
        if rec.get("isPublicDomain"):
            self.image_stats["public_domain"] += 1
        if rec.get("isPublicDomain") and rec.get("primaryImage"):
            self.image_stats["public_domain_with_image"] += 1

    def add_all(self, records: Iterable[dict]) -> int:
        for r in records:
            self.add(r)
        return self.records

    def _walk(self, prefix: str, value: Any, oid: Any, dept: str) -> None:
        if isinstance(value, dict):
            dynamic = prefix in DYNAMIC_KEY_PARENTS
            for k, v in value.items():
                if dynamic:
                    # count the key as a value of `<prefix>.*`, then walk the value under it
                    self._note(f"{prefix}.*", k, oid, dept, as_key=True)
                    self._note(f"{prefix}.*", v, oid, dept)
                else:
                    path = f"{prefix}.{k}" if prefix else k
                    self._note(path, v, oid, dept)
                    if isinstance(v, (dict, list)):
                        self._walk(path, v, oid, dept)
        elif isinstance(value, list):
            for item in value:
                p = f"{prefix}[]"
                self._note(p, item, oid, dept)
                if isinstance(item, (dict, list)):
                    self._walk(p, item, oid, dept)

    def _note(self, path: str, v: Any, oid: Any, dept: str, as_key: bool = False) -> None:
        st = self.paths[path]
        st.occurrences += 1
        if as_key:
            st.types["key"] += 1
            st.filled += 1
            self._count_value(st, v)
            return
        t = _type_name(v)
        st.types[t] += 1
        top = "[]" not in path and "." not in path
        if v is None:
            st.nulls += 1
            if top:
                self.by_dept_kind[dept][path]["null"] += 1
            return
        if isinstance(v, str):
            if not v.strip():
                st.empty_strings += 1
                if top:
                    self.by_dept_kind[dept][path]["empty_string"] += 1
                return
            n = len(v)
            st.len_min = n if st.len_min is None else min(st.len_min, n)
            st.len_max = n if st.len_max is None else max(st.len_max, n)
            st.len_sum += n
        elif isinstance(v, list):
            if not v:
                st.empty_lists += 1
                if top:
                    self.by_dept_kind[dept][path]["empty_list"] += 1
                return
            st.list_len_max = max(st.list_len_max, len(v))
        elif isinstance(v, bool):
            pass
        elif isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            st.num_min = v if st.num_min is None else min(st.num_min, v)
            st.num_max = v if st.num_max is None else max(st.num_max, v)
        elif isinstance(v, dict) and not v:
            return
        st.filled += 1
        if top:                                   # per-department fill for top-level fields only
            self.by_dept_filled[dept][path] += 1
            self.by_dept_kind[dept][path]["filled"] += 1
        if isinstance(v, (str, int, float, bool)):
            self._count_value(st, v)
        if len(st.examples) < self.n_examples and not isinstance(v, (dict, list)):
            st.examples.append(v)
            st.example_ids.append(oid)

    def _count_value(self, st: PathStats, v: Any) -> None:
        if st.high_cardinality:
            return
        key = v if isinstance(v, (str, int, bool)) else repr(v)
        if isinstance(key, str) and len(key) > 200:
            key = key[:200] + "…"
        if key not in st.values and len(st.values) >= self.max_distinct:
            st.high_cardinality = True
            st.distinct_seen = len(st.values)
            return
        st.values[key] += 1
        st.distinct_seen = len(st.values)

    # ----------------------------------------------------------------- output
    def result(self) -> dict:
        depts = sorted(self.by_dept_records)
        top_paths = [p for p in self.paths if "[]" not in p]
        per_dept = {
            d: {"records": self.by_dept_records[d],
                "fill": {p: round(self.by_dept_filled[d][p] / self.by_dept_records[d], 4)
                         for p in top_paths},
                "kinds": {p: dict(self.by_dept_kind[d][p]) for p in top_paths},
                "aat": {f: dict(c) for f, c in self.by_dept_aat[d].items()}}
            for d in depts
        }
        dup = {k: n for k, n in self.duplicate_accessions.items() if n > 1}
        return {
            "records": self.records,
            "paths": {p: s.to_json(self.top_values) for p, s in sorted(self.paths.items())},
            "departments": per_dept,
            "department_mismatch": [{"listed_in": a, "record_says": b, "n": n}
                                    for (a, b), n in self.dept_mismatch.most_common()],
            "duplicate_accession_numbers": {"distinct": len(dup), "objects": sum(dup.values()),
                                            "top": Counter(dup).most_common(20)},
            "object_begin_century": dict(sorted(self.objects_per_year.items())),
            "images": dict(self.image_stats),
            # full value counters for the AAT-mapped fields (bounded by max_distinct), so the
            # mapping step can score resolution over all values rather than the top 25
            "aat_values": {f: {"values": self.paths[f].values.most_common(),
                               "high_cardinality": self.paths[f].high_cardinality}
                           for f in ("culture", "objectName", "classification", "medium") if f in self.paths},
        }

    def save(self, path: Path) -> dict:
        res = self.result()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return res


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__
