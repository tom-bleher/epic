# B0: version engineering provenance and make mechanical assumptions explicit

## Motivation

The current realistic B0 compact includes detailed CAD-like support outlines, staggered front/back module placements, thin sensors, ASICs, PCBs, aluminium sheets, and an Al/Air/Al support sandwich. Source-level arithmetic checks indicate the current tracking-unit placement is internally coherent, but code consistency is not the same as engineering validation.

A professional detector description should make it possible to answer, for every important dimension or placement:

- what engineering source it came from;
- which revision/date it represents;
- whether it is measured, specified, approximated, or intentionally omitted;
- what downstream geometry/material artifacts must be regenerated when it changes.

Without that provenance, later geometry updates can be physically plausible yet impossible to audit against the intended detector design.

## Scope

Create a versioned B0 mechanical-source manifest covering at least:

- four station longitudinal positions and global transform;
- support-plate outlines and their source files/revisions;
- front/back module placement coordinates and rotations;
- sensor, ASIC, PCB, aluminium and support thicknesses/dimensions;
- intentional overlaps/staggering and expected single-face/double-face coverage behavior;
- known omitted materials such as adhesives, guard regions, flex/services, fasteners or other engineering details;
- any deliberate exact-contact surfaces in the simplified model;
- B0 window thickness convention and the engineering quantity represented by its compact constant.

The manifest should distinguish an engineering reference from a simulation approximation. A value being present in XML is not evidence that it is the final engineering value.

## B0 station-face coverage contract

The front/back faces are staggered coverage, not two complete coincident detector planes. Validation and documentation should explicitly distinguish:

- physical stations crossed;
- sensitive surfaces crossed;
- digitized/reconstructed measurements produced.

Include representative single-face and overlap trajectories in acceptance tests so future reconstruction/geometry changes do not accidentally assume eight measurements for every four-station track.

## Geometry/material dependency contract

For every engineering change that can alter tracking material or sensitive surfaces, record which derived artifacts become stale, including:

- installed/generated compact XML;
- ACTS material map;
- detector/source manifest;
- geometry validation outputs;
- benchmark provenance and simulation samples where applicable.

The material-map workflow should consume or record the same mechanical-source revision identifier.

## Suggested representation

A machine-readable YAML/JSON manifest plus a concise human-readable document is preferable. Example fields:

```yaml
b0_geometry_revision: <immutable id>
source:
  drawing_or_cad: <reference>
  revision: <revision>
  date: <date>
components:
  sensor_thickness:
    value_mm: 0.05
    status: design_assumption
  support_plate_1:
    outline_source: <reference>
    status: CAD-derived
omissions:
  - adhesive layers
  - guard-ring dead area
```

Do not place proprietary/unshareable CAD content in the public repository; record a stable reference/checksum or collaboration document identifier where appropriate.

## Acceptance criteria

- [ ] Every major B0 mechanical constant/placement category has an identifiable source and status.
- [ ] The exact current support/module layout has a revision identifier that benchmarks can record.
- [ ] Intentional simulation approximations and omitted materials are explicitly listed.
- [ ] Single-face vs overlap coverage semantics are documented and regression-tested.
- [ ] The B0 window thickness convention is unambiguous (full thickness vs half-length input to the DD4hep solid).
- [ ] Geometry/material-map regeneration requirements are documented and linked to the provenance identifier.
- [ ] Validation reports record both code SHA and engineering-geometry revision.

## Non-goals

- Adding speculative material merely to make the detector model look more detailed.
- Publishing restricted engineering files.
- Replacing detector-backed overlap/material/navigation validation with documentation alone.
