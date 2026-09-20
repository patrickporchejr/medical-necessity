#!/usr/bin/env bash
# Synthea invocation + seed -> FHIR R4 bundles in data/synthea/
#
# Builds a fixed-seed cohort of adults with an active rheumatoid arthritis
# diagnosis (SNOMED 69896004). RA is rare (~0.3% of the population) and too rare
# for Synthea's keep-module mechanism, so we generate batches of patients with
# sequential seeds, keep the RA patients from each batch, and discard the rest.
# Seeds and reference date are pinned, so the cohort (and the eval ground truth
# derived from it) is reproducible.
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

# Copy RA patient bundles from $1 into $2; print how many were copied.
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

echo "Bundles: $(ls synthea/fhir/*.json | wc -l | tr -d ' ') in data/synthea/fhir/"
