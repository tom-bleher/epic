# Material Map for ACTS
The material map needs to be updated from the default version in calibration/ when __ANY__ geometry or material thickness is changed within the tracking volume, even the change happens on a non-sensitive structure.

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
  --truth build/material_map_refresh/geant4_material_tracks.root \
  --probe build/b0_acts47_validation/bin/b0-material-probe \
  --map calibrations/materials-map-ip6-extended.cbor \
  --output build/b0_acts47_validation/pilot \
  --remap --training-entries 100000 --sample-size 100
```

Use absolute paths when running outside this checkout. Run long jobs in `tmux`
and check the recorded exit code. The remapper consumes a matched Geant4 scan.
Verify the scan's geometry provenance before reuse. The default full-map example
uses 4,500,000 recorded entries for training and holds out the remaining entries:
add `--training-entries 4500000 --sample-size 1000 --max-input-entries 318623
--candidate-limit 40000` for the existing 4,818,623-entry scan. The held-out sample
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
**highest ACTS tracking volume**, then uses native ACTS material assignment and
empty-bin correction. This retains upstream material such as the B0 entrance
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
`mat_sz`/`mat_ez` midpoint-derived branches are not used. The 18×275 and 5×41
configurations differ in downstream Roman-pot positions, so the old scan is not
used as whole-detector 5×41 truth. The current TGeo geometry supplies that truth.
For an initial audit when a matching scan is unavailable, `--directions-only`
uses another scan solely for its origins and directions and compares the map
with the loaded TGeo geometry. It explicitly omits Geant4 material cross-checks
and cannot be used with `--remap`.

`report.json` records geometry fingerprints, map checksum, training/validation
ranges, per-ray material intervals, navigation failures, and material closure.
Exit zero requires real mapped material on every intended B0 approach disc,
unique sensitive volume IDs, contained sensor/approach footprints, no missing
sensor intersections, and material closure. The declared engineering tolerances
are mean mapped/truth within 25% before the first sensor and between the first
and last sensors, with no between-sensor ray exceeding five times truth by more
than 0.1 X0. These are validation thresholds, not an ACTS standard.

The candidate stays in the work directory as `candidate.cbor`; generation does
not install it. Inspect `assets/b0_material_closure.pdf` and the report before
installing a candidate, and retain `candidate.cbor.json` with the provenance.
The plot script produces PDF only. Omit `--remap` to validate an existing map.
The named IP6 map must come from this bounded mapping and validation workflow;
the legacy `scripts/refresh_local_material_map.sh` writes the generic map and
does not supply this B0 validation. Do not copy its unbounded output over
`materials-map-ip6-extended.cbor`.

## Generate a new map with auto script
Steps:
* geantino scan to record material from dd4hep simulation
* map materials on to selected sets of ACTS surfaces and boundaries. Default: use all entrance and exit surfaces of tracking layers with grid size set in xml file ("layer_material"...)
* result validation and plots.

pre-requiests:
1. more than 10 GB disk space, and two hours of time.
2. set up your ```$DETECTOR_PATH``` and install epic.

to run:
```./run_material_map_validation.sh --nevents 1000 --nparticles 5000 ```
See comments for details.

This takes about two hours, and >10GB disk space.

## Use a local material map with EICrecon
```sh
eicrecon -Pacts:MaterialMap=/your_path/material-map.cbor
```

The nominal IP6 configurations select the realistic B0 map through `material_map`
and `material_map_url` in `configurations/ip6_extended.yml` and
`configurations/ip6_extended_5x41.yml`. The usual file loader downloads a
content-specific cache filename. The artifact, checksum, validation and provenance
are published in the immutable [fork release](https://github.com/tom-bleher/epic/releases/tag/b0-ip6-material-20260906-10f369975a72).
The 822,627-byte map has SHA-256
`10f369975a72c82f91b3c8bffde9ff0dcc5a524045c6d7baa4be91a6af6fdcd3`.
Keep it separate from the shared full-detector map and from maps for simplified
or vacuum geometries. New fork maps require a new release and matching YAML URL;
never replace an already published artifact.

## Update the official material map
1. You can either generate the map locally as described above, or download the artifact ```material_map``` from a PR CI.
2. Check the generated comparison plots for any outstanding issues. Then upload the cbor file and relevant plots to [gitlab](https://eicweb.phy.anl.gov/EIC/detectors/athena/-/issues/153).
3. Copy the url of your uploaded cbor file, and update the [path](https://github.com/eic/epic/blob/540a9e1e255e276548993449be09bd275cb3ef05/compact/tracking/definitions_craterlake.xml#L203) at the bottom epic/compact/tracking/definitions_craterlake.xml with a PR.



References:
* [ACTS how_to](https://acts.readthedocs.io/en/latest/examples/howto/material_mapping.html)
* [Presentation at Tracking meeting](https://indico.bnl.gov/event/22490/contributions/87822/attachments/52848/90391/Auto%20Script%20for%20ACTS%20Material%20Map%20V30.pdf)
