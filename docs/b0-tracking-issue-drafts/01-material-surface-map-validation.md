# B0: validate realistic front/back ACTS surfaces, material placement, and material-map reproducibility

## Motivation

The realistic B0 geometry now models four physical stations with front/back sensor-module layers, passive PCB/ASIC/aluminium components, and an Al/air/Al support sandwich. ACTS sees eight front/back tracking layers while reconstruction groups them into four physical stations.

This geometry is materially different from a thin four-plane telescope. A representative trajectory can cross substantial passive material between/around the sensitive planes, and the **location** of that material relative to the measurements matters to the fitted scattering/process noise.

The geometry builder intentionally assigns each sensitive sensor a `VolPlane` with inner/outer material thickness spanning the full tracking-unit stack, and layer-level material mapping projects support material onto adjacent tracking layers. The B0 seeder documentation also notes strong sensitivity to using a material map that matches the geometry.

Relevant files:

- `compact/far_forward/B0_tracker.xml`
- `src/B0Tracker_geo.cpp`
- B0 material-map generation / validation scripts used by the branch

Companion reconstruction work lives in `tom-bleher/EICrecon` on `feat/b0-tracking`. The EICrecon issue-draft roadmap now also contains a runtime geometry/material-map contract so a validated map cannot be silently replaced in production benchmarks.

## Goal

Demonstrate quantitatively that:

1. Geant4, DD4hep reconstruction and ACTS see consistent sensitive-plane locations and identities;
2. the effective material encountered by representative B0 trajectories is consistent between the detailed DD4hep geometry and the ACTS material representation;
3. material is applied at transport locations that reproduce the relevant track-state covariance, not merely the same integrated `X/X0`;
4. generated B0 material maps are reproducible and tightly coupled to the geometry revision they describe.

## Required checks

### 1. Sensitive-surface geometry

For every front/back B0 layer and every module/sensor identity, compare:

- DD4hep sensitive-volume center and orientation;
- ACTS surface center and normal;
- cellID `layer/module/sensor` identity;
- station/front/back assignment;
- expected within-station and inter-station z separations in the ion frame.

Add an automated geometry check that catches accidental surface collapse, sign flips, duplicated placements, or incorrect front/back ordering.

### 2. End-to-end sensitive-crossing round trip

Geometry conversion alone is not enough. Exercise the complete measurement contract for every sensitive sensor:

```text
physical sensitive crossing
  -> DD4hep cellID
  -> simulated hit
  -> raw hit
  -> reconstructed hit position/covariance
  -> ACTS surface + local coordinates
```

At minimum test:

- sensor centers;
- points near each sensor edge;
- module/sensor overlap regions;
- trajectories that cross multiple front/back surfaces in one physical station.

Assert that the reconstructed measurement resolves back to the same intended physical sensor/surface and station. Keep **measurement count** and **physical-station count** separate in all tests.

### 3. Geant4 material scan

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

### 4. ACTS material comparison

Using the material map generated for the same geometry SHA, propagate the same trajectories through ACTS and compare cumulative material with the DD4hep/Geant4 scan.

Agreement in total `X/X0` alone is not sufficient. Moving the same scattering strength to a different longitudinal location changes the transported position/direction covariance. In a simple drift, an angular kick with variance `sigma_alpha^2` a distance `D` from the reference plane contributes

```text
Q = sigma_alpha^2 [[D^2, D], [D, 1]]
```

to `(x, dx/dz)`, so identical integrated material can still produce different track-parameter covariance.

Add a charged-particle transport comparison on identical reference surfaces in the real B0 field. Compare at least:

- position and direction residuals;
- propagated covariance and correlations;
- pull widths;
- sensitivity to support/edge/hole regions.

This should complement, not replace, the existing straight/geantino material-closure checks.

### 5. Material-map provenance and refresh

Make material-map provenance explicit and reproducible. Record at least:

- `epic` commit SHA;
- detector configuration / compact file;
- compiled geometry-plugin identity/hash where possible;
- field/config where relevant;
- generation command/script version;
- output map hash;
- timestamp/tool versions if needed for reproducibility.

A geometry change affecting B0 material or sensitive surfaces should make it obvious that the old map is stale.

## Acceptance criteria

- [ ] Automated check verifies all expected B0 sensitive ACTS surfaces and front/back ordering.
- [ ] Every sensitive sensor passes the crossing -> cellID -> reconstructed measurement -> ACTS-surface round trip at center and edge samples.
- [ ] Representative DD4hep/Geant4 material scans are produced and stored/reproducible.
- [ ] The same trajectories are evaluated in ACTS using the generated material map.
- [ ] Differences in cumulative `X/X0` **and material location/transport effect** are quantified.
- [ ] Charged-particle same-surface residual/covariance/pull comparisons are available for representative B0 trajectories.
- [ ] A documented tolerance/validation criterion is established.
- [ ] Material-map generation records the geometry/plugin identity and output hash.
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
