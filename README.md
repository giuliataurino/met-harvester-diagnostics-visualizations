# Met Harvester Diagnostics

Harvests every object in every curatorial department from The Met Collection Public API
(`https://collectionapi.metmuseum.org/public/collection/v1`), surveys the metadata (every
JSON path actually served, its types, fill rates and values, and how these vary by
department) and maps each path to the documented schema and to the `stg:` staging predicates
used by `paa_graph_kb`. A local web page shows the harvest running and four views of
metadata fill by department that update as records arrive.

It generalises [met-harvester-diagnostics-department13](https://github.com/giuliataurino/met-harvester-diagnostics-department13)
from Greek and Roman Art (department 13) to the whole collection and keeps the same
`<objectID>.json` cache layout, so that tool's UI can be pointed at this cache.

## Requirements

- Python 3.9 or newer (`python3 --version`)
- git

## Install

```bash
cd ~
git clone https://github.com/giuliataurino/met-harvester-diagnostics.git
cd ~/met-harvester-diagnostics
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt            # requests, pydantic, rdflib
pip install -r requirements-dev.txt        # pytest (optional)
python -m pytest tests                     # 8 tests, no network needed
```

Every command below is run from `~/met-harvester-diagnostics` with the venv active. If the
prompt shows `~` instead of `met-harvester-diagnostics`, run
`cd ~/met-harvester-diagnostics && source .venv/bin/activate` first.

## Run on localhost

```bash
python -m met_harvest check        # three live calls to the Met API; prints each URL and status
python -m met_harvest serve        # starts the page
```

Open **http://localhost:8766** in your browser and leave the terminal running. On the page:

1. **Harvest** — tick departments (none ticked means all), optionally set a limit per
   department and the number of workers, click **Start harvest**. A progress row per
   department shows listed / fetched / from cache / missing / failed / invalid. **Stop**
   finishes the in-flight requests; **Start** again resumes, since cached objects cost no
   request. **Re-index cache** rebuilds the statistics from disk.
2. **Fill rate, field × department** — heatmap of the 58 documented fields (grouped) against
   departments, most complete first. Hover a cell for the number.
3. **How a field is unpopulated, per department** — one bar per field per department, split
   into filled / empty string / null / empty list / key absent.
4. **Department completeness** — mean fill, fields never filled, fields always filled.
5. **Filled vs resolved to AAT** — for `culture`, `objectName`, `classification` and `medium`,
   the share of records with a value against the share that resolves to an AAT URI through
   the tables in `met_harvest/aat.py`.

The charts refresh every two seconds while a harvest runs. Use another port with
`python -m met_harvest serve --port 8080`. Ctrl-C stops the server.

Records are written to `data/met/objects/<objectID>.json`; `data/met/` is ignored by git.

## Command line

```bash
python -m met_harvest departments                            # list department ids and names
python -m met_harvest harvest --dept 13 --limit 500          # trial run, one department
python -m met_harvest harvest --workers 4 --rate 20          # everything (~480k objects, ~7 h at 20 req/s)
python -m met_harvest harvest --since 2026-01-01             # only objects updated since a date
python -m met_harvest survey --staging-sample 2000           # read the cache, write data/met/report/survey.json
python -m met_harvest map                                    # field_map.csv / field_map.json / REPORT.md
python -m met_harvest all --dept 13                          # harvest + survey + map in one go
```

Add `--verbose` before the subcommand to log every request URL and status. `--cache` and
`--out` change the cache root (`data/met`) and report directory (`data/met/report`).

### Harvest behaviour

- `/departments` is read on every run; `/objects?departmentIds=N` is read per department and
  saved to `data/met/ids/<N>.json`. `/v1.1/search` is not used for enumeration because it is
  capped at 10,000 results.
- Each object is fetched once and stored as served. A 404 leaves
  `objects/<objectID>.missing` so it is not retried. `--refresh` refetches.
- Workers share one rate limiter (default 20 req/s; the Met asks for at most 80).
  Progress is checkpointed to `data/met/harvest_state.json`; failures and records the
  `MetObject` model rejects go to `data/met/harvest_failures.jsonl` with the reason.
- A department is skipped on rerun once marked done unless `--redo`, `--refresh` or `--since`.

### Survey and field map

`survey.json` holds, for every JSON path seen (`culture`, `tags[].term`,
`measurements[].elementMeasurements.*`, …): occurrences, non-empty fills, nulls, empty strings
and lists, observed types, distinct-value count, top values, numeric and string-length ranges
and example values with object ids; plus per-department fill, listing-vs-record department
disagreements, shared accession numbers and an `objectBeginDate` century histogram.

`field_map.csv` / `.json` has one row per path: status (`documented`, `undocumented`,
`never_seen`), documented vs observed types, fill rate, distinct count, top values, the
staging predicates it feeds and the AAT table it is looked up in. `REPORT.md` renders the
same with the per-department matrix, AAT resolution rates and (with `--staging-sample`)
triples per predicate and fields filled but producing no triple.

The documented schema is `met_harvest/fields.py` (58 fields, from
https://metmuseum.github.io/#object). Anything the API serves that is not there shows up as
`undocumented`, which is how schema drift surfaces.

## Using the department-13 UI on the same cache

The older diagnostics UI can browse the records this tool harvests:

```bash
cd ~/met-harvester-diagnostics-department13/diagnostics
python server.py --cache ~/met-harvester-diagnostics/data/met/objects
```

It runs on http://localhost:8765, so both pages can be open at once.

## Layout

```
met_harvest/
  client.py     API client, cache, rate limiter
  harvest.py    all-department orchestration, resume, failure log
  survey.py     path / type / value / department statistics
  fields.py     documented schema + staging map
  mapping.py    joins survey with fields.py; writes CSV / JSON / REPORT.md
  serve.py, index.html   local page: harvest control + live views
  cli.py        command line
  models.py, staging.py, aat.py   copied from the department-13 tool (originally paa_graph_kb)
tests/          run the whole pipeline and the page's API against a fake Met API
data/met/       cache and reports (ignored by git)
```

## Troubleshooting

- `No module named met_harvest` — you are not in `~/met-harvester-diagnostics`, or the
  package folder is not named `met_harvest`. Python cannot import a folder with a hyphen in
  its name.
- `No module named pytest` / `requests` — the venv is not active: `source .venv/bin/activate`.
- `cannot reach https://collectionapi.metmuseum.org/...` from `check` — no network, or a
  proxy blocking the host. The page still opens and shows whatever is already in the cache.
- `Address already in use` — another server has port 8766; add `--port 8080`.

License: GPL-3.0.
