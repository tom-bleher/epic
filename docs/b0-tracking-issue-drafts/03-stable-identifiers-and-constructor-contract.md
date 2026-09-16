# B0: make sensor/module identity explicit and harden geometry construction

## Motivation

The realistic B0 geometry has a sensible physical hierarchy (`layer -> module -> sensor`) and the readout already carries separate `layer`, `module`, and `sensor` fields. A source-level review nevertheless found several identity and construction invariants that are implicit rather than enforced.

Current behavior in `src/B0Tracker_geo.cpp` includes:

- module IDs restart at 1 for each front/back layer and are assigned from the order of `<module>` entries inside `<module_positions>`;
- sensitive physical volumes receive a `sensor` PhysVolID (`1..N`), but the child sensor `DetElement` is currently constructed with the parent `moduleID` as its numeric DetElement ID;
- the driver validates several missing XML elements and the sensor readout-field capacity, but does not yet fail early on every malformed geometry case (for example an empty sensitive-component set before calculating sensitive extents);
- the detector-ID documentation in `compact/definitions.xml` has stale prose around the B0 allocation and should be made consistent with the constants actually used by `B0Tracker`.

None of these observations by itself proves a collision in the current persisted B0 cell IDs. The concern is maintainability and reproducibility: code that later relies on `DetElement::id()`, or a harmless-looking reordering of module placements, should not silently change physical identity semantics.

## Goals

1. Define one explicit identity contract for detector, layer face, module, and sensor.
2. Preserve existing persisted cellID assignments unless an intentional, documented migration is made.
3. Fail construction with actionable messages when compact edits violate the contract.
4. Make source ordering requirements explicit, or replace them with explicit placement IDs.

## Proposed implementation

### Sensor DetElement IDs

Give each sensitive child DetElement an ID corresponding to its sensor index within the module, instead of reusing the module ID. Add a detector-backed test that checks agreement among:

- child DetElement numeric ID;
- inherited PhysVolID fields;
- decoded `layer/module/sensor` cellID fields;
- DD4hep volume-manager lookup;
- ACTS sensitive-surface identity.

Do not infer success merely from unique DetElement paths.

### Stable module IDs

The current module ID is the 1-based position in `<module_positions>`. Choose and document one of two contracts:

1. **Explicit IDs (preferred):** allow/require `id="..."` on module placements, preserve the current numbering in the migration, validate uniqueness/range, and decouple identity from XML ordering; or
2. **Ordering is schema:** explicitly declare ordering part of the persisted readout contract and add a manifest regression test that fails if a refactor changes the mapping.

Any migration must preserve existing IDs for the current layout unless a deliberate data-format break is approved.

### Constructor validation

Before constructing volumes/surfaces, reject at least:

- zero sensitive components;
- non-finite or non-positive component box dimensions;
- non-finite component/module positions or rotations;
- duplicate `(station, side)` layers;
- invalid/non-positive station numbers;
- derived layer, module, or sensor IDs outside the actual DD4hep readout fields;
- duplicate module IDs / placement IDs;
- missing/empty module-position sets;
- invalid support thicknesses and degenerate support polygons.

The validation should run before calculations that could turn malformed input into infinities/NaNs.

### Canonical detector-ID naming/documentation

Make the prose in `compact/definitions.xml` consistent with the constants actually used by the current B0 geometry. Consider a canonical `B0Tracker_ID` alias while retaining compatibility names where necessary.

## Validation

Companion PR #2 contains a source geometry contract audit and detector-backed surface exporter. Extend those checks so the initialized geometry proves the identity round trip:

```text
DD4hep path
 -> DetElement id
 -> inherited PhysVolID
 -> decoded cellID layer/module/sensor
 -> reconstructed hit
 -> ACTS surface
```

For the pinned current layout the source inventory is 8 layer faces, 172 module placements, 3 sensitive sensors per module, and 516 sensitive sensors. Treat those numbers as a regression contract for this layout, not as a replacement for inspecting the initialized detector.

## Acceptance criteria

- [ ] Sensor child DetElement IDs have documented sensor-level semantics.
- [ ] Module identity no longer changes silently when XML is reordered, or ordering is explicitly declared and regression-tested as schema.
- [ ] Constructor rejects malformed sensitive/component/layer/module inputs before geometry creation.
- [ ] Readout-field capacities for layer/module/sensor are validated against the actual ID descriptor.
- [ ] Detector-ID documentation matches the constants and geometry in use.
- [ ] DD4hep -> cellID -> ACTS identity round-trip test passes for every sensitive sensor.
- [ ] Existing current-layout persisted IDs are preserved unless an intentional migration is documented.

## Non-goals

- Renumbering the current detector merely for aesthetic consistency.
- Treating unique volume names as a substitute for a persisted identity contract.
- Changing the physical B0 layout or AC-LGAD response model in this issue.
