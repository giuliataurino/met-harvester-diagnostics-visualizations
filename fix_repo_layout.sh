#!/bin/bash
# Run from inside ~/met-harvester-diagnostics. Renames the package so Python can import it,
# removes the stray root-level copies, replaces the empty test.py placeholders, writes the new README.
set -e
cd "$(git rev-parse --show-toplevel)"
[ -d met-harvester ] && git mv met-harvester met_harvest
for f in cli.py client.py serve.py met_harvest/test.py tests/test.py; do [ -f "$f" ] && git rm -qf "$f"; done
for d in data/met/ids data/met/objects; do mkdir -p "$d"; [ -f "$d/test.py" ] && git mv "$d/test.py" "$d/.gitkeep" || touch "$d/.gitkeep"; done
cp "$(dirname "$0")/README.md" README.md
cp "$(dirname "$0")/.gitignore" .gitignore
git add -A
python -m pytest tests -q
echo "layout fixed; review with 'git status' then: git commit -m 'Fix package layout and README' && git push"
