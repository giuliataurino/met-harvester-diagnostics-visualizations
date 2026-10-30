"""Field map: every observed JSON path joined with the documented schema (fields.py), the
staging predicates it feeds (staging.py) and the AAT lookup table it is matched against.

Outputs
    field_map.json   machine-readable rows
    field_map.csv    same rows, for a spreadsheet
    REPORT.md        human report: status counts, undocumented keys, per-department fill
                     matrix, AAT resolution rates, staging triple yield (if sampled)

Status of a path:
    documented      in the API docs and seen in the data
    undocumented    seen in the data, not in the docs (schema drift: report upstream)
    never_seen      documented but absent from every harvested record
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

from rdflib import Graph

from . import aat as aat_mappings
from .fields import BY_NAME, DOCUMENTED, DOCUMENTED_PATHS, DYNAMIC_KEY_PARENTS
from .models import MetObject
from .staging import STG, met_object_to_staging

AAT_FIELDS = {"culture": "cultures", "objectName": "object_types",
              "classification": "classifications", "medium": "materials"}


def _spec_for(path: str):
    top = path.split("[")[0].split(".")[0]
    return BY_NAME.get(top)


def _is_documented(path: str) -> bool:
    if path in DOCUMENTED_PATHS:
        return True
    if path.endswith("[]") and path[:-2] in DOCUMENTED_PATHS:   # items of a documented array
        return True
    for parent in DYNAMIC_KEY_PARENTS:
        if path == f"{parent}.*":
            return True
    return False


def build_rows(survey: dict) -> list[dict]:
    n = max(survey["records"], 1)
    paths = survey["paths"]
    rows = []
    seen = set()
    for path, st in paths.items():
        spec = _spec_for(path)
        seen.add(path)
        observed_types = ",".join(f"{t}:{c}" for t, c in sorted(st["types"].items(), key=lambda x: -x[1]))
        filled_rate = st["filled"] / n if "[]" not in path else None
        rows.append({
            "path": path,
            "status": "documented" if _is_documented(path) else "undocumented",
            "group": spec.group if spec else "",
            "doc_type": spec.doc_type if spec and path == spec.name else "",
            "observed_types": observed_types,
            "occurrences": st["occurrences"],
            "filled": st["filled"],
            "fill_rate": round(filled_rate, 4) if filled_rate is not None else "",
            "nulls": st["nulls"], "empty_strings": st["empty_strings"], "empty_lists": st["empty_lists"],
            "distinct": st["distinct"] if not st["high_cardinality"] else f">{st['distinct']}",
            "num_range": f"{st['num_min']}..{st['num_max']}" if "num_min" in st else "",
            "len_range": f"{st['len_min']}..{st['len_max']} (mean {st['len_mean']})" if "len_min" in st else "",
            "top_values": "; ".join(f"{v!s} ({c})" for v, c in st["top_values"][:5]),
            "examples": "; ".join(str(e)[:60] for e in st["examples"]),
            "staging": ", ".join(spec.staging) if spec else "",
            "aat_category": spec.aat or "" if spec else "",
            "note": spec.note if spec and path == spec.name else "",
        })
    for spec in DOCUMENTED:
        if spec.name not in seen:
            rows.append({"path": spec.name, "status": "never_seen", "group": spec.group, "doc_type": spec.doc_type,
                         "observed_types": "", "occurrences": 0, "filled": 0, "fill_rate": 0.0, "nulls": 0,
                         "empty_strings": 0, "empty_lists": 0, "distinct": 0, "num_range": "", "len_range": "",
                         "top_values": "", "examples": "", "staging": ", ".join(spec.staging),
                         "aat_category": spec.aat or "", "note": spec.note})
    order = {"documented": 0, "undocumented": 1, "never_seen": 2}
    doc_order = {f.name: i for i, f in enumerate(DOCUMENTED)}
    rows.sort(key=lambda r: (order[r["status"]], doc_order.get(r["path"].split("[")[0].split(".")[0], 999), r["path"]))
    return rows


# --------------------------------------------------------------------------- AAT resolution
def aat_resolution(survey: dict) -> dict:
    """How much of each AAT-mapped field's volume resolves through aat.py's tables. Uses the
    surveyed value counts, so it is exact only where the path is not high-cardinality; otherwise
    it covers the top `max_distinct` values and says so."""
    out = {}
    for fld, category in AAT_FIELDS.items():
        st = survey.get("aat_values", {}).get(fld)
        if not st:
            continue
        resolved = unresolved = 0
        misses: Counter = Counter()
        for value, count in st["values"]:
            term = str(value).split(",")[0].strip() if fld == "culture" else str(value)
            if aat_mappings.get_aat_uri(term, category=category):
                resolved += count
            else:
                unresolved += count
                misses[value] += count
        total = resolved + unresolved
        out[fld] = {"category": category, "resolved": resolved, "unresolved": unresolved,
                    "resolved_share": round(resolved / total, 4) if total else None,
                    "partial": st["high_cardinality"], "top_unresolved": misses.most_common(15)}
    return out


# --------------------------------------------------------------------------- staging yield
def staging_sample(records: Iterable[dict], sample: int = 500, seed: int = 0) -> dict:
    """Run the staging mapping on a sample and report triples per predicate, plus fields that were
    filled but produced no triple for that record (mapping gaps, AAT misses, dropped values)."""
    recs = list(records)
    random.Random(seed).shuffle(recs)
    recs = recs[:sample]
    pred_count: Counter = Counter()
    filled_no_triple: Counter = Counter()
    rejected = 0
    for raw in recs:
        try:
            obj = MetObject.model_validate(raw)
        except Exception:  # noqa: BLE001
            rejected += 1
            continue
        g = Graph()
        met_object_to_staging(g, obj)
        preds = Counter(str(p).replace(str(STG), "stg:") for _, p, _ in g)
        pred_count.update(preds)
        for spec in DOCUMENTED:
            v = raw.get(spec.name)
            if v in (None, "", [], {}) or not spec.staging:
                continue
            if not any(p in preds for p in spec.staging):
                filled_no_triple[spec.name] += 1
    return {"sampled": len(recs), "rejected_by_model": rejected,
            "triples_per_predicate": dict(pred_count.most_common()),
            "filled_but_no_triple": dict(filled_no_triple.most_common())}


# --------------------------------------------------------------------------- outputs
def write_outputs(survey: dict, out_dir: Path, staging: Optional[dict] = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows(survey)
    aat = aat_resolution(survey)
    (out_dir / "field_map.json").write_text(
        json.dumps({"records": survey["records"], "rows": rows, "aat": aat, "staging": staging},
                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    with (out_dir / "field_map.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out_dir / "REPORT.md").write_text(render_report(survey, rows, aat, staging), encoding="utf-8")
    return [out_dir / "REPORT.md", out_dir / "field_map.csv", out_dir / "field_map.json"]


def render_report(survey: dict, rows: list[dict], aat: dict, staging: Optional[dict]) -> str:
    n = survey["records"]
    by_status = Counter(r["status"] for r in rows)
    L = []
    L.append("# Met Collection API — metadata survey and field map\n")
    L.append(f"Records surveyed: **{n:,}** across **{len(survey['departments'])}** departments "
             f"(by the record's own `department` value).\n")
    L.append(f"Paths: {by_status['documented']} documented and seen, "
             f"**{by_status['undocumented']} undocumented**, {by_status['never_seen']} documented but never seen.\n")

    img = survey.get("images", {})
    if img:
        L.append(f"Images: {img.get('has_primary', 0):,} with a primary image, "
                 f"{img.get('public_domain', 0):,} public domain, "
                 f"{img.get('public_domain_with_image', 0):,} public domain with image.\n")
    dup = survey.get("duplicate_accession_numbers", {})
    if dup.get("distinct"):
        L.append(f"Accession numbers shared by more than one object: {dup['distinct']:,} numbers, "
                 f"{dup['objects']:,} objects (the docs warn accessionNumber is not unique).\n")
    mm = survey.get("department_mismatch") or []
    if mm:
        L.append("\n## Listing vs record department\n\nObjects returned by `/objects?departmentIds=X` whose record says another department:\n")
        L.append("| listed in | record says | n |\n|---|---|---:|")
        for m in mm[:30]:
            L.append(f"| {m['listed_in']} | {m['record_says']} | {m['n']:,} |")
        L.append("")

    und = [r for r in rows if r["status"] == "undocumented"]
    L.append("\n## Undocumented paths\n")
    if und:
        L.append("| path | observed types | occurrences | examples |\n|---|---|---:|---|")
        for r in und:
            L.append(f"| `{r['path']}` | {r['observed_types']} | {r['occurrences']:,} | {r['examples']} |")
    else:
        L.append("None: every key seen is in the API documentation.")
    ns = [r for r in rows if r["status"] == "never_seen"]
    if ns:
        L.append("\n## Documented but never seen\n")
        L.append(", ".join(f"`{r['path']}`" for r in ns))

    L.append("\n## Field map (top-level fields)\n")
    L.append("| field | group | doc type | observed types | fill | distinct | staging | AAT | top values |")
    L.append("|---|---|---|---|---:|---:|---|---|---|")
    for r in rows:
        if "[]" in r["path"] or "." in r["path"]:
            continue
        fill = f"{r['fill_rate']:.1%}" if isinstance(r["fill_rate"], float) else "—"
        L.append(f"| `{r['path']}` | {r['group']} | {r['doc_type']} | {r['observed_types']} | {fill} | "
                 f"{r['distinct']} | {r['staging']} | {r['aat_category']} | {r['top_values'][:80]} |")

    L.append("\n## Nested paths\n")
    L.append("| path | observed types | occurrences | filled | distinct | examples |\n|---|---|---:|---:|---:|---|")
    for r in rows:
        if "[]" in r["path"] or "." in r["path"]:
            L.append(f"| `{r['path']}` | {r['observed_types']} | {r['occurrences']:,} | {r['filled']:,} | "
                     f"{r['distinct']} | {r['examples'][:80]} |")

    if aat:
        L.append("\n## AAT resolution (aat.py tables)\n")
        L.append("| field | category | resolved | unresolved | share | top unresolved terms |\n|---|---|---:|---:|---:|---|")
        for fld, a in aat.items():
            share = f"{a['resolved_share']:.1%}" if a["resolved_share"] is not None else "—"
            share += " (top values only)" if a["partial"] else ""
            tops = "; ".join(f"{v} ({c})" for v, c in a["top_unresolved"][:8])
            L.append(f"| `{fld}` | {a['category']} | {a['resolved']:,} | {a['unresolved']:,} | {share} | {tops} |")

    if staging:
        L.append(f"\n## Staging yield (sample of {staging['sampled']:,} records, "
                 f"{staging['rejected_by_model']} rejected by MetObject)\n")
        L.append("Fields filled in the raw record that produced none of their expected triples:\n")
        L.append("| field | records |\n|---|---:|")
        for f, c in staging["filled_but_no_triple"].items():
            L.append(f"| `{f}` | {c:,} |")
        L.append("\nTriples per predicate:\n")
        L.append("| predicate | triples |\n|---|---:|")
        for p, c in staging["triples_per_predicate"].items():
            L.append(f"| {p} | {c:,} |")

    # per-department fill matrix, fields in documented order, worst-covered first within group is noise; keep doc order
    depts = survey["departments"]
    fields = [f.name for f in DOCUMENTED if f.name in survey["paths"]]
    L.append("\n## Fill rate by department\n")
    L.append("Share of a department's records with a non-empty value. Rows are fields, columns departments.\n")
    short = {d: _short(d) for d in depts}
    L.append("| field | " + " | ".join(short[d] for d in depts) + " |")
    L.append("|---|" + "---:|" * len(depts))
    L.append("| records | " + " | ".join(f"{depts[d]['records']:,}" for d in depts) + " |")
    for f in fields:
        L.append(f"| `{f}` | " + " | ".join(_pct(depts[d]["fill"].get(f, 0)) for d in depts) + " |")
    L.append("\nDepartment key: " + "; ".join(f"{short[d]} = {d}" for d in depts))

    cent = survey.get("object_begin_century") or {}
    if cent:
        L.append("\n## objectBeginDate by century\n")
        L.append("| century start | objects |\n|---:|---:|")
        for c, k in cent.items():
            L.append(f"| {c} | {k:,} |")
    return "\n".join(L) + "\n"


def _pct(x: float) -> str:
    return "·" if not x else f"{x*100:.0f}"


def _short(name: str) -> str:
    stop = {"the", "and", "of", "art", "arts"}
    words = [w for w in name.replace(",", "").split() if w.lower() not in stop]
    return "".join(w[0] for w in words)[:4].upper() or name[:4]
