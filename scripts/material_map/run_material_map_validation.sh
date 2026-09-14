#!/bin/bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Official-shell material mapping and paired B0 validation.
# Original material-map validation workflow: Shujie Li, 03.2024.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${DETECTOR_PATH:?Source the intended local thisepic.sh inside the official eic-shell}"
DETECTOR_CONFIG="${DETECTOR_CONFIG:-epic_ip6_extended}"
nevents=1000
nparticles=5000
sample_size=1000
workdir="$PWD"
mapping_args=()
while (($#)); do
  case "$1" in
    --nevents) nevents="$2"; shift 2 ;;
    --nparticles) nparticles="$2"; shift 2 ;;
    --sample-size) sample_size="$2"; shift 2 ;;
    --workdir) workdir="$2"; shift 2 ;;
    --truth|--binning-map) mapping_args+=("$1" "$(realpath "$2")"); shift 2 ;;
    --training-entries) mapping_args+=("$1" "$2"); shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--nevents 1000] [--nparticles 5000] [--sample-size 1000] [--workdir CLEAN_DIRECTORY]"
      echo "Reuse verified truth: --truth FILE [--binning-map FILE] [--training-entries N]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$workdir"
workdir="$(cd "$workdir" && pwd)"
if [[ -e "$workdir/material-map.cbor" || -e "$workdir/validation/report.json" ]]; then
  echo "Use a clean work directory; refusing to overwrite material-map/validation outputs." >&2
  exit 2
fi
cmake -S "$script_dir/bounded_mapping" -B "$workdir/mapper-build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$workdir/mapper-build" --parallel "$(nproc)"
cmake -S "$script_dir/official_validation" -B "$workdir/probe-build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$workdir/probe-build" --parallel "$(nproc)"
cd "$workdir"
python "$script_dir/official_acts_material_map.py" \
  --xml "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" --output "$workdir/material-map.cbor" \
  --events "$nevents" --particles "$nparticles" \
  --mapper "$workdir/mapper-build/bounded-material-map" "${mapping_args[@]}"
truth="$(python -c 'import json; print(json.load(open("material-map.cbor.provenance.json"))["truth"])')"
first_entry="$(python -c 'import json; p=json.load(open("material-map.cbor.provenance.json")); print(p["training_entry_start"]+p["training_entry_count"])')"
python "$script_dir/official_validation/export_and_plot.py" \
  --xml "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" --map "$workdir/material-map.cbor" \
  --output "$workdir/plots"
python "$script_dir/official_validation/analyze.py" run \
  --xml "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" --map "$workdir/material-map.cbor" \
  --truth "$truth" --first-entry "$first_entry" --sample-size "$sample_size" \
  --probe "$workdir/probe-build/b0-material-probe" --output "$workdir/validation" \
  --require-material-validation
