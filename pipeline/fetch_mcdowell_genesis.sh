#!/usr/bin/env bash
# Genesis graft (Phase B §1/§9): build the 1957-58 deep-history prologue from
# Jonathan McDowell's element archive and weld it onto the Space-Track rebuild.
#
# Per §10 decision 3: McDowell sources 1957-10-04 -> 1958 ONLY (one clean
# provenance boundary); at 1959 the authoritative source is Space-Track. We keep
# only <=1958 elsets and carry them forward to the Space-Track floor
# (1959-01-11), so the welded chain has no gap. The Space-Track segment is left
# byte-for-byte unchanged.
#
# Source: planet4589.org/space/elements/00000/ — 3-line extended TLE (the "3 "
# provenance line is ignored by tle_normalize). Redistribution-unrestricted.
#
# Usage:  ./fetch_mcdowell_genesis.sh <work-dir-with-build_history.py-and-spacetrack-manifest>
set -euo pipefail
WORK="${1:?usage: fetch_mcdowell_genesis.sh <workdir>}"
cd "$WORK"
ST_MANIFEST="${ST_MANIFEST:-out/daily_manifest.txt}"   # the Space-Track rebuild manifest

mkdir -p mcdowell/raw mcdowell/corpus mcdowell/out
echo "fetching McDowell 00000/ (NORAD 1-99)..."
for n in $(seq -w 1 99); do
  curl -fsS --max-time 30 "https://planet4589.org/space/elements/00000/S000$n" \
       -o "mcdowell/raw/S000$n" 2>/dev/null || rm -f "mcdowell/raw/S000$n"
done
echo "fetched $(ls mcdowell/raw | wc -l | tr -d ' ') files"

cat mcdowell/raw/* > mcdowell/all.tle
( cd mcdowell && rm -f corpus/mcdowell.zip && zip -q corpus/mcdowell.zip all.tle )

# Genesis segment: <=1958 elsets, carried forward to the day before the
# Space-Track floor (1959-01-11).
python3 build_history.py --corpus mcdowell/corpus --year-max 1958 \
  --final-day 1959-01-10 --out-dir mcdowell/out --jobs 1

# Weld: genesis days then the (unchanged) Space-Track days.
cat mcdowell/out/daily_manifest.txt "$ST_MANIFEST" > out/full_manifest.txt
echo "welded full chain -> out/full_manifest.txt"
head -1 out/full_manifest.txt
tail -1 out/full_manifest.txt
echo "days: $(wc -l < out/full_manifest.txt)"
