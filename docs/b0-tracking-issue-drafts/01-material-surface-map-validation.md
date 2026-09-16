# B0: validate realistic front/back ACTS surfaces, material placement, and material-map reproducibility

## Motivation

The realistic B0 geometry now models four physical stations with front/back sensor-module layers, passive PCB/ASIC/aluminium components, and an Al/air/Al support sandwich. ACTS sees eight front/back tracking layers while reconstruction groups them into four physical stations.

This geometry is materially different from a thin four-plane telescope. A representative trajectory can cross substantial passive material between/around the sensitive planes, and the **location** of that material relative to the measurements matters to the fitted scattering/process noise.

The geometry builder intentionally assigns each sensitive sensor a `VolPlane` with inner/outer material thickness spanning the full tracking-unit stack, and layer-level material mapping projects support material onto adjacent tracking layers. The B0 seeder documentation also notes strong sensitivity to using a material map that matches the geometry.

Relevant files:

- `compact/far_forward/B0_tracker.xml`
- `src/B0Tracker_geo.cpp`
- B0 material-map generation / refresh scripts used by the branch

Companion reconstruction work lives in `tom-bleher/EICrecon` on `feat/b0-tracking`.

## Goal

Demonstrate quantitatively that:

1. Geant4 and ACTS see consistent sensitive-plane locations;
2. the effective material encountered by representative B0 trajectories is consistent between the detailed DD4hep geometry and the ACTS material representation;
3. generated B0 material maps are reproducible and tightly coupled to the geometry revision they describe.

## Required checks

### 1. Sensitive-surface geometry

For every front/back B0 layer and representative modules/sensors, compare:

- DD4hep sensitive-volume center and orientation;
- ACTS surface center and normal;
- cellID `layer/module/sensor` identity;
- station/front/back assignment;
- expected within-station and inter-station z separations in the ion frame.

Add an automated geometry check that catches accidental surface collapse, sign flips, duplicated placements, or incorrect front/back ordering.

### 2. Geant4 material scan

For representative trajectories spanning the B0 acceptance, record integrated material quantities from the detailed geometry, at least:

- total `X/X0`;
- interaction-length equivalent where available;
- cumulative material versus path length / z;
- which detector components dominate.

Include trajectories through:

- module centers;
- support-dominated regions;
- module overlaps/edges;
- near the beam-pipe aperture;
- both sides of the transverse acceptance.

### 3. ACTS material comparison

Using the material map generated for the same geometry SHA, propagate the same trajectories through ACTS and compare cumulative material with the DD4hep/Geant4 scan.

Agreement in total `X/X0` alone is not sufficient: large shifts in where scattering is applied relative to the measurements should be visible in the comparison.

### 4. Material-map provenance and refresh

Make material-map provenance explicit and reproducible. Record at least:

- `epic` commit SHA;
- detector configuration / compact file;
- field/config where relevant;
- generation command/script version;
- output map hash;
- timestamp/tool versions if needed for reproducibility.

A geometry change affecting B0 material or sensitive surfaces should make it obvious that the old map is stale.

## Acceptance criteria

- [ ] Automated check verifies all expected B0 sensitive ACTS surfaces and front/back ordering.
- [ ] Representative DD4hep/Geant4 material scans are produced and stored/reproducible.
- [ ] The same trajectories are evaluated in ACTS using the generated material map.
- [ ] Differences in cumulative `X/X0` and material location are quantified.
- [ ] A documented tolerance/validation criterion is established.
- [ ] Material-map generation records the geometry SHA and output hash.
- [ ] B0 reconstruction benchmarks in EICrecon can report/verify the material-map provenance.
- [ ] A stale/mismatched material map cannot silently be presented as the validated B0 configuration.

## Reconstruction cross-check

After geometry/material validation, rerun the B0 prompt-proton benchmark with:

1. the matched material map;
2. deliberately mismatched/old map;
3. material disabled where technically useful as a diagnostic.

Quantify changes in:

- CKF success rate;
- chi2/residuals;
- momentum and angle resolution;
- pull widths after independent final-refit semantics are available.

This will distinguish a tracking-window problem from a material-description problem.

## Non-goals

- Tuning CKF windows to compensate for an incorrect material map.
- Treating eight front/back layers as eight uniformly thin silicon planes.
- Replacing the detailed DD4hep geometry with an ACTS-only approximation.
