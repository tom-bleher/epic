# Material Map for ACTS
The material map must be updated when **any** geometry or material thickness changes inside the tracking volume, including changes to non-sensitive or B0 structures.

## B0 validation and remapping with ACTS 47.7

For `epic_ip6_extended_5x41`, use `validate_b0_material.py` and its companion
`b0_validation/b0-material-probe`. The validator also accepts the base
`epic_ip6_extended` entry point, records its actual optics include, and derives
the B0 layers and sensor footprints from the loaded geometry. Build the probe
inside `eic-shell-acts47`:

```sh
cmake -S scripts/material_map/b0_validation -B build/b0_acts47_validation/bin
cmake --build build/b0_acts47_validation/bin -j 8
```

Run from a directory with the geometry's calibration and field-map resources.
Use an absolute geometry-matched map path; the map supplies binning for remapping.
Start with the small default sample before increasing the recorded training range:

```sh
python scripts/material_map/validate_b0_material.py \
  --truth build/b0_acts47_validation/geant4_material_tracks.root \
  --probe build/b0_acts47_validation/bin/b0-material-probe \
  --map calibrations/materials-map-ip6-extended.cbor \
  --output build/b0_acts47_validation/pilot \
  --remap --training-entries 100000 --sample-size 100
```

Use absolute paths when running outside this checkout. Run long jobs in `tmux`
and check the recorded exit code. The remapper consumes a matched Geant4 scan.
The validator requires the recording sidecar and checks its ROOT checksum, ACTS
version, counts and complete compact include hashes before mapping or certifying
Geant4 truth. Relocating an otherwise identical installed geometry is supported.
The XML check does not cover compiled geometry plugins or external resources;
retain a frozen install manifest and verify its plugin checksums separately.
For a scan of 5,000,000 geantinos, use 4,500,000 recorded entries for training and hold out the
remaining entries: add `--training-entries 4500000 --sample-size 1000
--max-input-entries 500000 --candidate-limit 40000`. The held-out sample
contains rays with `4 < eta < 6` intersecting sensitive sensors in at least three
stations; these are geometrical geantino rays, not reconstructed protons.

For a new scan, use `record_b0_material.py`, a thin wrapper around the native
ACTS 47.7 `Examples/Scripts/Python/material_recording.py`. Build ACTS with
`ACTS_BUILD_PLUGIN_GEANT4=ON` and `ACTS_BUILD_EXAMPLES_GEANT4=ON` in a separate
local prefix if those examples are absent, then source that prefix's
`bin/this_acts.sh` and the intended geometry before recording:

```sh
python scripts/material_map/record_b0_material.py \
  --xml "$DETECTOR_PATH/epic_ip6_extended_5x41.xml" \
  --output build/b0_acts47_validation/geant4_material_tracks.root \
  --events 100 --particles 1000 --eta-range -7 7
```

The wrapper records the native source checksum, geometry include hashes,
random seed, throwing distribution, counts and ROOT checksum. It refuses to
overwrite an existing scan. Use the small scan to test remapping first, then
increase statistics if needed. Training and validation use disjoint recorded
entry ranges. Geant4 recording uses one thread because its global state is not
safe to share across concurrent events.

The remapper clips each physical Geant4 step at the ray's first exit from the
**highest ACTS tracking volume**, places the retained slab at its midpoint,
then uses native ACTS material assignment and empty-bin correction. Geant4's
recorded position is the pre-step point; assigning that point directly can
move a thick support slab to the wrong side of a sensor. This retains upstream
material such as the B0 entrance
window and the material from the final sensor to the tracking-volume exit.
It prevents remote calorimeter/magnet material from being projected backwards
onto B0 surfaces. The supported rays start inside that volume, normally at the IP.
No material bins are capped or normalized by hand. Assignment must conserve the
retained material, including material unassigned because no mapping surface is
intersected. Older ROOT scans omit optional elemental-composition branches;
ACTS 47.7 logs two missing-branch messages but reads and validates the supplied
X0, L0, atomic mass, charge, and mass density.

Validation independently compares direct surface intersections, ACTS navigation,
and current DD4hep/TGeo material for exactly the same rays. Recorded Geant4 steps
cross-check TGeo through B0; their `mat_z` is a **pre-step point**, so historical
`mat_sz`/`mat_ez` midpoint-derived branches are not used. A scan from the 18×275
configuration cannot certify the 5×41 geometry; record the intended configuration.
For an initial audit when a matching scan is unavailable, `--directions-only`
uses another scan solely for its origins and directions and compares the map
with the loaded TGeo geometry. It explicitly omits Geant4 material cross-checks
and cannot be used with `--remap`.

`report.json` records geometry fingerprints, map checksum, training/validation
ranges, per-ray material intervals, navigation failures, and material closure.
The remap sidecar also reports per-surface bin occupancy. Finer bins require
enough training rays; a formally present material map can still contain empty
bins inside the accepted tracking region.
Exit zero requires real mapped material on every intended B0 approach disc,
unique sensitive volume IDs, contained sensor/approach footprints, no missing
sensor intersections, and material closure. The declared engineering tolerances
are mean mapped/truth within 25% (or 0.001 X0 absolute for near-zero intervals)
before the first sensor, between sensors, and from the last sensor to the
tracking-volume exit. No interval may have a ray with mapped material at most
0.000001 X0 while truth exceeds 0.01 X0. Between-sensor and final intervals must
also contain no ray exceeding five times truth by more than 0.1 X0.
These are validation thresholds, not an ACTS standard.

The candidate stays in the work directory as `candidate.cbor`; generation does
not install it. Inspect `assets/b0_material_closure.pdf` and the report before
installing a candidate, and retain `candidate.cbor.json` with the provenance.
The plot script produces PDF only. Omit `--remap` to validate an existing map.
The named IP6 map must come from this bounded mapping and validation workflow;
the legacy `scripts/refresh_local_material_map.sh` writes the generic map and
does not supply this B0 validation. Do not copy its unbounded output over
`materials-map-ip6-extended.cbor`.

## ACTS 47.7 reduced IP6 campaign

In ACTS 47.7, `run_material_map_validation.sh` selects the native B0 workflow
instead of downloading incompatible ACTS 45 scripts. Source the current local
`epic_ip6_extended` installation and an ACTS 47.7 build with Geant4 examples:

```sh
export ACTS_SOURCE_DIR=/path/to/acts-v47.7.0
./run_material_map_validation.sh --nevents 1000 --nparticles 5000 \
  --work-dir /path/to/new-campaign
```

This records eta from -7 to 7, trains on the first 90% of recorded entries, and
compares regenerated/current maps on disjoint B0-accepted rays from the remaining
10%. `--validation-entries` can reserve a later, untouched range for final testing
after choosing binning. `--recording` reuses an existing recording only after its
checksums, compact geometry, counts and angular range pass provenance checks.
Default mapping uses current XML-declared prototypes, including new passive
layers; it does not reuse identifiers from an old material map. An explicit
`--binning-map geometry-map.json` can override binning after exporting the current
geometry. Retain that configuration and compare alternatives on a selection
sample, reserving a separate range for the final test. Navigation layers are
never physical mapping targets.
New recordings also fingerprint the actual loaded ePIC construction library and
reject geometry changes during recording. Older sidecars explicitly retain their
narrower XML-only verification scope.

The configured XML `material-map` constant selects the comparison artifact;
`calibrations/materials-map.cbor` may be a different, full-detector map. Outputs
include `regenerated/candidate.cbor`, bin occupancy, paired material/navigation
reports, PDF closure plots and `campaign.json`. Existing output directories are
never overwritten. Review the evidence before installing or publishing a map.
The position PDF and upstream material moments check where the radiation lengths
are placed: correct integrated material alone cannot validate scattering lever
arms or position–direction covariance.
This reduced IP6 workflow does not produce the shared full-detector map below.

## Generate and validate the shared map

The official shared map must be generated with `epic_craterlake_material_map`. This purpose-built configuration contains the full central tracker and B0. Do not generate the official map from `epic_ip6_extended`, which omits the central tracking detector volumes; use that configuration only for the final consumer check.

Run from a clean `scripts/material_map` work directory so the requested ACTS scripts and patches are reproducible. The workflow:

* records DD4hep material with a geantino scan;
* maps material onto the configured ACTS layer and boundary surfaces;
* produces regenerated/current comparison plots and per-surface diagnostics;
* smoke-tests the generated map with `epic_ip6_extended`.

Prerequisites:

1. At least 25 GB of free disk space and approximately two hours. Check available space before starting.
2. A current installed ePIC geometry with `$DETECTOR_PATH` set.

Run:

```sh
./run_material_map_validation.sh --nevents 1000 --nparticles 5000
```

Visually inspect the regenerated/current comparisons in `Validation/`, the diagnostics in `Surfaces/`, and the ACTS geometry plots in `plots/`. For a B0 change, also run targeted validation over the B0 acceptance (`eta > 4`); the generic validation does not adequately populate those surfaces.

Record the geometry commit, container and ACTS versions, event and particle counts, output file size, and SHA-256 with the validation artifact.

## Use a local material map with EICrecon
```sh
eicrecon -Pacts:MaterialMap=/your_path/material-map.cbor
```

Both nominal IP6 configurations explicitly select 5×41 optics and the realistic B0 map through `material_map`
and `material_map_url` in `configurations/ip6_extended.yml` and
`configurations/ip6_extended_5x41.yml`. The usual file loader downloads a
content-specific cache filename. The artifact, checksum, validation and provenance
are published in the immutable [fork release](https://github.com/tom-bleher/epic/releases/tag/b0-ip6-material-20260906-cbc0605e892b).
The 823,552-byte map has SHA-256
`cbc0605e892b7777db3c818a51e56c51ddb19e3f7c91e813c110aeab1e1e691c`.
Keep it separate from the shared full-detector map and from maps for simplified
or vacuum geometries. New fork maps require a new release and matching YAML URL;
never replace an already published artifact.
The simplified B0 comparison uses its own [validated map](https://github.com/tom-bleher/epic/releases/tag/b0-ip6-simplified-material-20260906-bc8d86fe2fb2).

## Update the official material map

1. Generate the map locally as above or download the `material_map` CI artifact.
2. Review and retain the validation plots and provenance.
3. Publish the full-detector `material-map.cbor` through a pull request to [eic/epic-data](https://github.com/eic/epic-data). Publish the reduced IP6 map as a separate artifact, preserving the full-detector map.
4. After the full-detector map pull request merges, pin its immutable **merge commit SHA** in the `epic-data/raw/<SHA>/material-map.cbor` URL in `compact/tracking/material_map.xml`. Do not point production geometry at a fork or an unmerged commit.
5. From a fresh cache, verify the URL, size, and SHA-256.
6. Because maps are keyed by ACTS GeometryIdentifier, validate the separate IP6 map with the ACTS 47.7 workflow above in `epic_ip6_extended_5x41`. Every expected B0 approach disc must have mapped material, and targeted material closure must pass before updating its configured map artifact.



References:
* [ACTS how_to](https://acts.readthedocs.io/en/latest/examples/howto/material_mapping.html)
* [Presentation at Tracking meeting](https://indico.bnl.gov/event/22490/contributions/87822/attachments/52848/90391/Auto%20Script%20for%20ACTS%20Material%20Map%20V30.pdf)
