#!/usr/bin/env bash
# Synthea invocation + seed -> FHIR R4 bundles in data/synthea/
#
# Builds a fixed-seed cohort of adults with a rheumatoid arthritis diagnosis
# (SNOMED 69896004), active or resolved: a resolved diagnosis is kept on purpose,
# because "the chart does not show active RA" is one of the situations the eval
# scores. RA is rare (~0.3% of the population) and too rare for Synthea's
# keep-module mechanism, so we generate batches of patients with sequential
# seeds, keep the RA patients from each batch, and discard the rest. The last
# batch can overshoot TARGET, so the cohort is then trimmed to exactly TARGET
# (sorted by patient id, first TARGET kept) and its fingerprint printed: the
# sha256 of the sorted, newline-joined patient ids, the same value the eval
# summaries record as cohort.sha256. Seeds and reference date are pinned, so the
# cohort (and the eval ground truth derived from it) is reproducible.
# Runs Synthea in Docker, so no local JDK is needed (Synthea 4.x requires 17+).
set -euo pipefail

cd "$(dirname "$0")"

SYNTHEA_VERSION="v4.0.0"
TARGET="${TARGET:-30}"            # RA patients to keep
BATCH_SIZE="${BATCH_SIZE:-500}"
SEED="${SEED:-42}"                # first batch seed; batch N uses SEED+N
MAX_BATCHES="${MAX_BATCHES:-40}"
REFERENCE_DATE="${REFERENCE_DATE:-20260101}"
AGE_RANGE="${AGE_RANGE:-40-80}"

JAR=".cache/synthea-${SYNTHEA_VERSION}.jar"
RAW=".cache/raw"
mkdir -p .cache synthea

if [[ ! -f "$JAR" ]]; then
  echo "Downloading Synthea ${SYNTHEA_VERSION}..."
  curl -fL -o "$JAR" \
    "https://github.com/synthetichealth/synthea/releases/download/${SYNTHEA_VERSION}/synthea-with-dependencies.jar"
fi

# Clear previous output but keep the .gitkeep placeholder.
find synthea -mindepth 1 ! -name .gitkeep -delete
mkdir -p synthea/fhir

# Copy RA patient bundles (any clinical status, see above) from $1 into $2;
# print how many were copied.
filter_ra() {
  python3 - "$1" "$2" <<'PY'
import json, shutil, sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
RA = "69896004"
kept = 0
for f in sorted(src.glob("*.json")):
    if f.name.startswith(("hospitalInformation", "practitionerInformation")):
        continue
    bundle = json.loads(f.read_text())
    if any(
        e["resource"]["resourceType"] == "Condition"
        and any(c.get("code") == RA for c in e["resource"]["code"].get("coding", []))
        for e in bundle["entry"]
    ):
        shutil.copy(f, dst / f.name)
        kept += 1
print(kept)
PY
}

kept=0
for ((batch = 0; batch < MAX_BATCHES && kept < TARGET; batch++)); do
  rm -rf "$RAW"
  docker run --rm --user "$(id -u):$(id -g)" \
    -v "$PWD:/data" -w /data \
    eclipse-temurin:17-jre \
    java -jar "/data/${JAR}" \
      -p "$BATCH_SIZE" -s "$((SEED + batch))" -cs "$((SEED + batch))" \
      -r "$REFERENCE_DATE" -a "$AGE_RANGE" \
      --exporter.baseDirectory="/data/${RAW}" \
      --exporter.fhir.export=true \
      --exporter.fhir.use_us_core_ig=true \
      --exporter.ccda.export=false \
      --exporter.csv.export=false >/dev/null
  kept=$((kept + $(filter_ra "$RAW/fhir" synthea/fhir)))
  echo "batch $((batch + 1)): $kept/$TARGET RA patients"
done
rm -rf "$RAW"

if ((kept < TARGET)); then
  echo "Only $kept/$TARGET RA patients after $MAX_BATCHES batches; raise MAX_BATCHES" >&2
  exit 1
fi

# Keep exactly $2 bundles in $1: sorted by patient id, the first $2. Print the
# cohort fingerprint (sha256 of the sorted, newline-joined patient ids).
trim_cohort() {
  python3 - "$1" "$2" <<'PY'
import hashlib, json, sys
from pathlib import Path

fhir, target = Path(sys.argv[1]), int(sys.argv[2])
by_id = {}
for f in fhir.glob("*.json"):
    bundle = json.loads(f.read_text())
    [patient] = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient"]
    by_id[patient["id"]] = f
ids = sorted(by_id)
for pid in ids[target:]:
    by_id[pid].unlink()
kept = ids[:target]
print(hashlib.sha256("".join(f"{p}\n" for p in kept).encode()).hexdigest())
PY
}

fingerprint="$(trim_cohort synthea/fhir "$TARGET")"
echo "Bundles: $(ls synthea/fhir/*.json | wc -l | tr -d ' ') in data/synthea/fhir/"
echo "Cohort fingerprint (sha256 of sorted patient ids): $fingerprint"
