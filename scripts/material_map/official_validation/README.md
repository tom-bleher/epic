# Paired B0 material validation

Run inside the official `~/eic/eic-shell`, with the current local geometry
installation sourced. Build with its bundled ACTS:

```sh
cmake -S scripts/material_map/official_validation -B build/official-validation
cmake --build build/official-validation -j$(nproc)
```

Use a fresh output directory; DD4hep creates calibration links there. Example
for a map trained on recorded ROOT entries `[0, 4000000)`:

```sh
python scripts/material_map/official_validation/analyze.py run \
  --xml "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" \
  --map /absolute/path/material-map.cbor \
  --truth /absolute/path/geant4_material_tracks.root \
  --output /absolute/path/validation \
  --probe "$PWD/build/official-validation/b0-material-probe" \
  --first-entry 4000000 --max-input-entries 200000 --sample-size 1000
```

The original `material-map.cbor.provenance.json` beside the truth ROOT file
supplies its SHA-256 and top XML hash; `--truth-provenance` selects another
sidecar. The current map's `.provenance.json` supplies its hash and
`training_entry_start`/`training_entry_count`. Explicit training-range CLI
options are available when this metadata is missing; these are recorded as
user assertions. Missing or overlapping training ranges are diagnostic only.
Entry indices refer to ROOT records, not thrown particles or events.

The probe selects rays with `4 < eta < 6` crossing silicon in at least three
B0 stations. It independently intersects sensors, propagates through ACTS,
and traces TGeo. Analysis matches recorded origins and momenta and integrates
finite Geant4 segments fractionally at interval boundaries. ACTS 44 recording
stores the pre-step point in `mat_z`; the endpoint is reconstructed from
direction and length. The writer's centered `mat_sz`/`mat_ez` are not used.

Outputs are `report.json`, `probe.json`, the replay rays/log, and
`assets/b0_geant4_map_closure.pdf`. `--axis-max` fixes common axes across
before/after runs and rejects limits that would clip data. Open the PDF before
accepting a result. `analyze` can repeat analysis using `--probe probe.json`.

Geometry coverage, material screening, and navigation have separate statuses.
Process exit zero means analysis completed, not physics acceptance. The
`--require-material-validation` option exits 4 when geometry, truth crosscheck,
material screening, post-envelope screening, or disjoint holdout fails;
navigation retains a separate status so existing navigation issues are visible.
The 16 planar approaches describe the current eight-layer B0 arrangement;
sensor counts are checked against the layer inventory. Per-measurement gap
material comparisons are retained in each ray record for redistribution checks.
The
material screening thresholds are explicitly reported engineering diagnostics,
not an ACTS standard. Overall validation additionally requires a disjoint
holdout and no missing sensor intersections. Straight geantino replay does
not measure charged-track bias. Original sidecars lack recursive geometry and
plugin hashes; current metadata cannot repair this recording provenance gap.

Generate material and ACTS geometry PDFs with the same inputs:

```sh
python scripts/material_map/official_validation/export_and_plot.py \
  --xml "$DETECTOR_PATH/$DETECTOR_CONFIG.xml" \
  --map /absolute/path/material-map.cbor --output /absolute/path/surface-plots
```

`--material-max` fixes a shared material color scale across before/after maps,
rejecting a limit that clips data. The exported JSON retains actual surface
identifiers, transformations, binning, and material; PDF outputs contain the
16 planar material maps, transformed sensor/approach geometry, and B0 volume
boundary rims. Plots show the selected map hash in the statistics JSON.

Keep generated maps, ROOT files, reports, build products, and plots out of git.
