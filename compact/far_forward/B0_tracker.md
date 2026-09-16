# B0 tracker geometry and readout contract

This document records the intended software contract of the realistic B0 tracker geometry in `B0_tracker.xml`. It is a companion to the compact XML and geometry driver, not a substitute for detector-backed overlap, navigation, or material validation.

## Physical organization

The tracker is modeled as **four physical stations** inside the B0 region. Each station has two staggered module faces, represented as separate ACTS/DD4hep layer assemblies:

| Station | Back layer ID | Front layer ID |
|---:|---:|---:|
| 1 | 1 | 2 |
| 2 | 3 | 4 |
| 3 | 5 | 6 |
| 4 | 7 | 8 |

The layer ID contract is

```text
layer_id = 2 * (station - 1) + (1 for back, 2 for front)
```

The station support remains at the station origin while the sensitive module stacks are offset to the two support faces.

The current source layout contains:

- 8 front/back layer faces;
- 172 TrackingUnit module placements;
- 3 sensitive silicon sensors per TrackingUnit;
- 516 sensitive sensor volumes in total.

These counts are regression expectations for this specific layout revision. They must not be used as a substitute for checking the initialized DD4hep/ACTS detector.

## TrackingUnit model

Each TrackingUnit contains:

- one FR4 PCB;
- one outer aluminium sheet;
- three passive silicon ASICs;
- three 50 um sensitive silicon sensors.

Each module placement receives its own TrackingUnit volume tree. This is intentional: the stock Geant4 tracker action must not merge deposits from different physical modules through shared leaf-volume identity.

The support is modeled as an Al/Air/Al sandwich according to the constants in the compact XML. Engineering provenance for the exact outlines, dimensions and omitted materials is tracked separately; values in this compact file are simulation inputs and must not automatically be described as final engineering specifications.

## Front/back coverage semantics

The two faces of one physical station are **staggered coverage**, not two complete coincident detector planes.

Therefore these quantities are distinct and must remain distinct in validation and reconstruction:

1. physical stations intersected;
2. sensitive surfaces intersected;
3. digitized/reconstructed measurements produced.

A four-station track is not required to produce eight measurements. Validation samples must include both single-face trajectories and trajectories passing through front/back overlap regions.

## Readout identity

The B0 readout currently encodes:

```text
system, layer, module, sensor, x, y
```

The intended hierarchy is:

```text
B0 detector
  -> layer face (physical station + front/back)
    -> module within that face
      -> sensitive sensor within the module
        -> readout channel/cell
```

Current module IDs are assigned from the 1-based order of `<module>` entries inside each layer's `<module_positions>` block. Reordering those entries can therefore change persisted module identity even if the physical coordinates are unchanged. Until explicit placement IDs are introduced, **module ordering is part of the persisted readout schema and must not be changed casually**.

Sensitive physical volumes receive a 1-based `sensor` PhysVolID according to sensitive-component order in the TrackingUnit definition. Reordering sensitive components likewise changes persisted sensor identity.

A planned hardening task tracks making these identities explicit and validating the DetElement/PhysVolID/cellID/ACTS round trip.

## Effective-resolution versus physical-readout modes

The default compact currently uses a 70 um `CartesianGridXY` segmentation as an **effective spatial-resolution proxy**. It must not be described as the physical AC-LGAD electrode pitch.

Detector-response studies that use physical AC-LGAD channels must use a separately identified readout/profile and an explicit charge-sharing/timing reconstruction model. Results from the effective-resolution mode and physical-response mode must be labeled separately.

## ACTS material-map coupling

The realistic B0 geometry changes both sensitive surfaces and passive material relative to a thin-plane telescope. Tracking benchmarks must use an ACTS material map generated and validated for the same geometry/configuration revision.

A successful file load is not proof of compatibility. Validation should establish:

- expected B0 sensitive surfaces and station identities;
- material coverage on intended mapping surfaces;
- material amount **and longitudinal placement** along representative B0 trajectories;
- provenance tying the map to the detector configuration and geometry/plugin revision.

Do not loosen CKF cuts to compensate for an unverified or stale material map.

## Required validation layers

### Source contract

Before DD4hep construction, validate compact invariants including:

- four stations with exactly one front and one back layer each;
- positive/finite component dimensions and positions;
- nonempty sensitive content;
- readout field capacities;
- nonempty module-placement sets and no accidental duplicate placements;
- expected source inventory for the selected layout.

### Initialized detector identity

For every sensitive sensor, establish the round trip:

```text
DD4hep DetElement/path
 -> inherited PhysVolID fields
 -> decoded cellID layer/module/sensor
 -> reconstructed tracker hit
 -> ACTS sensitive surface
```

The initialized detector—not source arithmetic—is authoritative for the final sensor count and mapping.

### Geometry/navigation

Run detector-backed checks covering:

- overlap/containment validation;
- module centers and sensor edges;
- front/back overlap regions;
- aperture-adjacent regions;
- representative charged-particle navigation through all four stations.

Unexpected surface lookup or `globalToLocal` failures must be counted in validation runs rather than silently interpreted as tracking inefficiency.

### Material

Compare detailed DD4hep/Geant4 material with the ACTS material representation on matched trajectories. Agreement in total `X/X0` alone is insufficient when the material centroid/placement differs relative to the measurements.

## Coordinate precision

B0 global hit positions are several metres from the origin and reconstructed hit positions may be stored in single precision. Surface-coordinate conversion tolerances therefore require an explicit numerical precision test; the tolerance is not a detector-resolution parameter and should not be inflated to hide a geometry mismatch.

## Change-control rules

Changes to any of the following require deliberate compatibility review:

- layer/station ordering;
- module ordering or explicit IDs;
- sensitive-component ordering;
- physical sensor/support geometry;
- readout segmentation;
- material composition/thickness;
- ACTS envelope/material-surface construction.

A change affecting sensitive surfaces or material invalidates geometry-dependent simulation/validation artifacts until they are regenerated or re-certified.

## Related validation work

- Geometry/material audit: ePIC PR #2.
- Physical AC-LGAD readout profiles: ePIC PR #3.
- Geometry/detector-response issue drafts: ePIC PR #1.
- Reconstruction-side B0 tracking roadmap: `tom-bleher/EICrecon` PR #3.

This document intentionally avoids claiming that the current geometry is fully engineering-validated; it defines what must remain true and what evidence is required before making that claim.
