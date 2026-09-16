# B0 surface and material audit

Companion to tom-bleher/EICrecon PRs #4–#7. No geometry dimensions, sensor pitch, default material map or standard reconstruction settings are changed by these tools.

## Sensitive surfaces

Build/source this geometry fork, then use the DD4hep apply plugin:

```sh
geoPluginRun -input /absolute/epic_ip6_extended.xml \
  -plugin epic_B0GeometryAudit /absolute/dd4hep-b0.json
```

The plugin walks actual sensitive DetElements, recording inherited system/layer/module/sensor IDs, nominal global centres, normals and box sizes. It does not infer cell identity from a sensor display name or assume every PCB contributes three successive measurements to a track.

In the matching EICrecon build enable the opt-in B0 telescope and request a station manifest through `b0_telescope:B0TelescopeSeeds:stationMapFile`. Then:

```sh
python scripts/b0_validation/audit_b0.py surfaces \
  --dd4hep dd4hep-b0.json --acts stations.json --out surface-audit.json
```

The audit requires one-to-one centre matching, compatible normals, unique identifiers, complete sensor coverage, four physical stations and eight ACTS layers by default. Use explicit expected counts for the official four-layer variant. It fails on missing/ambiguous/coincident matches rather than arbitrarily pairing nearest surfaces. The resulting crosswalk records actual IDs on both sides. Opposite normal signs are allowed; full in-plane axes, surface bounds and covariance round trips require additional detector-backed checks.

## Material rays

`scan_b0_material.py` reuses the existing `bin/g4MaterialScan_to_csv` DDG4 geantino scanner. Inputs are version-1 JSON with a nonempty `rays` list; each ray has a unique string `ray_id`, `origin_mm` and `direction`. Place rays through sensor interiors, module/PCB boundaries, support-only regions, front/back overlaps and aperture edges. Use identical straight rays and path intervals for Geant4 and ACTS.

```sh
python scripts/b0_validation/scan_b0_material.py \
  --compact /absolute/epic_ip6_extended.xml --rays rays.json \
  --length-mm 1000 --out geant4-material.json
```

The adapter converts DDG4 printed centimetres to millimetres and differences cumulative X/X0 rather than dividing rounded thicknesses. It records hashes of the compact, ray specification and underlying scanner. This textual scanner has finite printed precision; for precision work export unrounded stepping data into the same schema. DDG4 parser-format changes must be checked in the chosen eic-shell.

Canonical material input is `{schema_version:1,rays:[...]}`. Each ray includes `origin_mm`, `direction` and ordered non-overlapping `segments`, each containing `s_begin_mm`, `s_end_mm`, and dimensionless `t_over_x0`. ACTS material surfaces can be represented by zero-length weighted intervals. The ACTS propagation/material recorder must supply its corresponding trace; this PR does not claim a material-map JSON alone is a propagated ray trace.

```sh
python scripts/b0_validation/audit_b0.py material \
  --reference geant4-material.json --mapped acts-material.json \
  --absolute-x0 0.001 --relative-x0 0.05 \
  --centroid-mm 2 --cumulative-x0 0.02 --out material-audit.json
```

Those tolerances are examples, not validated B0 requirements. The audit compares integrated radiation length, material centroid, second moment and maximum cumulative discrepancy along the ray. Equal total material at the wrong longitudinal location is not silently accepted. Negative/nonfinite material, changed rays, duplicate/missing ray IDs and unordered overlapping intervals fail.

## Reproducible material-map lifecycle

Regenerate maps using the repository's existing mapping workflow after changes to sensor/support geometry or material binning. Record the complete compact include closure, compiled geometry library, field configuration, input rays, mapping command/software versions and resulting map hash. EICrecon PR #7 supplies a run-provenance driver with recursive XML and explicit library/asset hashing. A matching filename or source commit alone is not evidence of a matching loaded map. This PR intentionally does not overwrite a working map or publish an unvalidated replacement.

## Tests and remaining validation

`python -m unittest discover -s scripts/b0_validation -v` runs eight pure NumPy tests covering layer/station structure, ordering, duplicate/missing/shifted sensors, normal errors, material double-counting/displacement, ray identity and scan unit conversion/clipping. They are synthetic regressions. The C++ exporter and DDG4 adapter require full-stack execution with the actual realistic geometry; a passing synthetic fixture is not a completed detector audit.
