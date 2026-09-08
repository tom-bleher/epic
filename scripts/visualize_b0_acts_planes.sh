#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# Run inside the ACTS 47.7 eic-shell after sourcing the intended epic install:
# scripts/visualize_b0_acts_planes.sh /path/to/output-directory
set -euo pipefail
: "${DETECTOR_PATH:?Source the intended epic installation first}"
: "${DETECTOR_CONFIG:?Select epic_ip6_extended first}"
if [[ $# != 1 ]]; then
  echo "Usage: $0 output-directory" >&2
  exit 2
fi
if [[ "$DETECTOR_CONFIG" != epic_ip6_extended ]]; then
  echo "This B0 visualization requires DETECTOR_CONFIG=epic_ip6_extended" >&2
  exit 2
fi
scripts_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$1/assets" "$1/build-source"
output_dir=$(cd -- "$1" && pwd)
cat > "$output_dir/build-source/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.21)
project(B0PlaneExport LANGUAGES CXX)
find_package(DD4hep REQUIRED COMPONENTS DDCore DDRec)
find_package(Acts 47.7 REQUIRED COMPONENTS Core PluginDD4hep PluginRoot)
add_executable(export_b0_acts_planes "${EPIC_SCRIPTS_DIR}/export_b0_acts_planes.cpp")
target_compile_features(export_b0_acts_planes PRIVATE cxx_std_20)
target_link_libraries(export_b0_acts_planes PRIVATE DD4hep::DDCore Acts::Core Acts::PluginDD4hep Acts::PluginRoot)
file(WRITE "${CMAKE_BINARY_DIR}/acts-version.txt" "${Acts_VERSION}")
CMAKE
cmake -S "$output_dir/build-source" -B "$output_dir/build" -DEPIC_SCRIPTS_DIR="$scripts_dir"
cmake --build "$output_dir/build" --parallel
# DD4hep's resource plugin creates relative calibration/field-map links.
# Give each export a fresh directory so it cannot replace caller-owned files.
resource_dir=$(mktemp -d "$output_dir/geometry-resources.XXXXXX")
(
  cd "$resource_dir"
  export DETECTOR_CACHE="${DETECTOR_CACHE:-$DETECTOR_PATH}"
  "$output_dir/build/export_b0_acts_planes" "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" "$output_dir/surfaces.json"
)
b0_xml="$DETECTOR_PATH/compact/far_forward/B0_tracker.xml"
xml_sha=$(sha256sum "$b0_xml")
xml_sha=${xml_sha%% *}
loaded_library=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["geometry_library"])' "$output_dir/surfaces.json")
library_sha=$(sha256sum "$loaded_library")
library_sha=${library_sha%% *}
python3 "$scripts_dir/plot_b0_acts_planes.py" "$output_dir/surfaces.json" \
  "$output_dir/assets/b0_acts_planes.pdf" \
  --geometry-label "Installed B0 XML ${xml_sha:0:12}; loaded libepic ${library_sha:0:12}" \
  --acts-version "$(cat "$output_dir/build/acts-version.txt")"
{
  echo "DETECTOR_PATH=$DETECTOR_PATH"
  echo "DETECTOR_CONFIG=$DETECTOR_CONFIG"
  echo "Source checkout HEAD (not a claim about the installed geometry):"
  git -C "$scripts_dir" rev-parse HEAD
  git -C "$scripts_dir" status --short
  sha256sum "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" "$b0_xml" "$loaded_library" "$output_dir/surfaces.json"
} > "$output_dir/provenance.txt"
