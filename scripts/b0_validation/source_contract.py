#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Validate source-level invariants of the realistic B0 tracker compact XML.

This complements the detector-backed DD4hep/ACTS audit in this directory.  It
catches malformed compact edits before geometry construction: invalid component
sizes, missing/duplicate station faces, readout-field overflow, empty layers,
and duplicate module placements.  It also emits a deterministic source inventory
that can be compared with the initialized-detector manifest.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

UNITS = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "um": 1.0e-3,
    "rad": 1.0,
    "mrad": 1.0e-3,
    "deg": math.pi / 180.0,
    "degree": math.pi / 180.0,
}
MATH_NAMES = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "atan": math.atan,
    "sqrt": math.sqrt,
    "abs": abs,
    "pi": math.pi,
    "Pi": math.pi,
}
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConstantEvaluator:
    """Evaluate the compact-expression subset used by the B0 source contract."""

    def __init__(self, compact_files: list[Path]):
        self.raw: dict[str, str] = {}
        self.cache: dict[str, float] = {}
        for path in compact_files:
            root = ET.parse(path).getroot()
            for constant in root.iter("constant"):
                name = constant.get("name")
                value = constant.get("value")
                if name and value is not None:
                    self.raw.setdefault(name, value)

    def eval_name(self, name: str, stack: tuple[str, ...] = ()) -> float:
        if name in self.cache:
            return self.cache[name]
        if name not in self.raw:
            raise KeyError(f"constant '{name}' not found")
        if name in stack:
            raise ValueError(f"circular constant reference at '{name}'")
        value = self.eval_expr(self.raw[name], stack + (name,))
        self.cache[name] = value
        return value

    def eval_expr(self, expr: str, stack: tuple[str, ...] = ()) -> float:
        names: dict[str, object] = {}
        for ident in set(IDENT_RE.findall(expr)):
            if ident in UNITS:
                names[ident] = UNITS[ident]
            elif ident in MATH_NAMES:
                names[ident] = MATH_NAMES[ident]
            else:
                names[ident] = self.eval_name(ident, stack)
        return float(eval(expr, {"__builtins__": {}}, names))  # noqa: S307


def _finite(evaluator: ConstantEvaluator, raw: str | None, label: str, errors: list[str]) -> float | None:
    if raw is None:
        errors.append(f"{label}: missing value")
        return None
    try:
        value = evaluator.eval_expr(raw)
    except (KeyError, TypeError, ValueError, SyntaxError) as exc:
        errors.append(f"{label}: cannot evaluate {raw!r}: {exc}")
        return None
    if not math.isfinite(value):
        errors.append(f"{label}: non-finite value {value}")
        return None
    return value


def _readout_widths(id_text: str) -> dict[str, int]:
    widths: dict[str, int] = {}
    for field in id_text.split(","):
        parts = [part.strip() for part in field.split(":")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            widths[parts[0]] = abs(int(parts[1]))
        except ValueError as exc:
            raise ValueError(f"invalid readout field {field!r}") from exc
    return widths


def _unsigned_max(width: int) -> int:
    if width <= 0:
        raise ValueError(f"bit-field width must be positive, got {width}")
    return (1 << width) - 1


def audit_source_contract(b0_xml: Path, definitions_xml: Path) -> dict:
    """Return a JSON-serializable audit report without mutating the geometry."""

    b0_xml = Path(b0_xml)
    definitions_xml = Path(definitions_xml)
    evaluator = ConstantEvaluator([definitions_xml, b0_xml])
    root = ET.parse(b0_xml).getroot()
    errors: list[str] = []

    detector = next((d for d in root.iter("detector") if d.get("name") == "B0Tracker"), None)
    if detector is None:
        return {"errors": ["B0Tracker detector not found"], "summary": {}, "layers": []}

    if detector.get("id") != "B0Tracker_Station_1_ID":
        errors.append(
            "B0Tracker detector id should use canonical constant B0Tracker_Station_1_ID; "
            f"got {detector.get('id')!r}"
        )

    readout = next((r for r in root.iter("readout") if r.get("name") == "B0TrackerHits"), None)
    if readout is None or readout.find("id") is None or not (readout.find("id").text or "").strip():
        return {"errors": errors + ["B0TrackerHits readout/id not found"], "summary": {}, "layers": []}
    try:
        widths = _readout_widths((readout.find("id").text or "").strip())
    except ValueError as exc:
        return {"errors": errors + [str(exc)], "summary": {}, "layers": []}
    for required in ("system", "layer", "module", "sensor"):
        if required not in widths:
            errors.append(f"B0TrackerHits readout lacks required '{required}' field")
    if errors:
        return {"errors": errors, "summary": {}, "layers": []}

    max_layer = _unsigned_max(widths["layer"])
    max_module = _unsigned_max(widths["module"])
    max_sensor = _unsigned_max(widths["sensor"])

    tracking_unit = next((m for m in detector.findall("module") if m.get("name") == "TrackingUnit"), None)
    if tracking_unit is None:
        return {"errors": ["TrackingUnit module not found"], "summary": {}, "layers": []}

    sensitive_components: list[str] = []
    components = list(tracking_unit.findall("module_component"))
    if not components:
        errors.append("TrackingUnit contains no module components")
    for component in components:
        name = component.get("name", "<unnamed>")
        box = component.find("box")
        if box is None:
            errors.append(f"TrackingUnit/{name}: missing box")
            continue
        for axis in ("x", "y", "z"):
            value = _finite(evaluator, box.get(axis), f"TrackingUnit/{name} box {axis}", errors)
            if value is not None and value <= 0:
                errors.append(f"TrackingUnit/{name} box {axis}: must be positive, got {value}")
        position = component.find("position")
        if position is None:
            errors.append(f"TrackingUnit/{name}: missing position")
        else:
            for axis in ("x", "y", "z"):
                _finite(evaluator, position.get(axis), f"TrackingUnit/{name} position {axis}", errors)
        if component.get("sensitive") == "true":
            sensitive_components.append(name)

    n_sensitive = len(sensitive_components)
    if n_sensitive == 0:
        errors.append("TrackingUnit contains no sensitive components")
    elif n_sensitive > max_sensor:
        errors.append(
            f"TrackingUnit has {n_sensitive} sensitive components but sensor field max id is {max_sensor}"
        )

    seen_faces: set[tuple[int, str]] = set()
    layer_records: list[dict] = []
    total_modules = 0
    station_sides: dict[int, set[str]] = {}

    for layer in detector.findall("layer"):
        raw_station = layer.get("station")
        side = layer.get("side", "")
        try:
            station = int(raw_station or "")
        except ValueError:
            errors.append(f"layer has invalid station={raw_station!r}")
            continue
        if station <= 0:
            errors.append(f"station must be positive, got {station}")
        if side not in {"front", "back"}:
            errors.append(f"station {station}: invalid side={side!r}")
            continue
        face = (station, side)
        if face in seen_faces:
            errors.append(f"duplicate layer for station {station} side {side}")
        seen_faces.add(face)
        station_sides.setdefault(station, set()).add(side)

        layer_id = 2 * (station - 1) + (2 if side == "front" else 1)
        if layer_id <= 0 or layer_id > max_layer:
            errors.append(
                f"station {station} side {side}: derived layer id {layer_id} exceeds field max {max_layer}"
            )

        position = layer.find("position")
        if position is None:
            errors.append(f"station {station} side {side}: missing position")
        else:
            for axis in ("x", "y", "z"):
                _finite(evaluator, position.get(axis), f"station {station} {side} position {axis}", errors)

        module_positions = layer.find("module_positions")
        modules = [] if module_positions is None else list(module_positions.findall("module"))
        if not modules:
            errors.append(f"station {station} side {side}: no module placements")
        if len(modules) > max_module:
            errors.append(
                f"station {station} side {side}: {len(modules)} modules exceed module field max id {max_module}"
            )

        placements: set[tuple[float, float, float]] = set()
        for module_index, module in enumerate(modules, start=1):
            values: list[float] = []
            for attr in ("posX", "posY", "rotZ"):
                value = _finite(
                    evaluator,
                    module.get(attr),
                    f"station {station} {side} module {module_index} {attr}",
                    errors,
                )
                values.append(math.nan if value is None else value)
            if all(math.isfinite(value) for value in values):
                key = tuple(values)
                if key in placements:
                    errors.append(
                        f"station {station} side {side}: duplicate module placement "
                        f"(posX={values[0]}, posY={values[1]}, rotZ={values[2]})"
                    )
                placements.add(key)

        total_modules += len(modules)
        layer_records.append(
            {
                "station": station,
                "side": side,
                "layer_id": layer_id,
                "module_count": len(modules),
                "module_id_range": [1, len(modules)] if modules else [],
            }
        )

    expected_stations = {1, 2, 3, 4}
    if set(station_sides) != expected_stations:
        errors.append(
            f"B0 physical stations must be {sorted(expected_stations)}, got {sorted(station_sides)}"
        )
    for station in sorted(expected_stations):
        sides = station_sides.get(station, set())
        if sides != {"front", "back"}:
            errors.append(f"station {station}: expected front/back layers, got {sorted(sides)}")

    layer_records.sort(key=lambda item: item["layer_id"])
    total_sensors = total_modules * n_sensitive
    return {
        "errors": errors,
        "summary": {
            "layer_count": len(layer_records),
            "physical_station_count": len(station_sides),
            "module_count": total_modules,
            "sensitive_components_per_module": n_sensitive,
            "sensitive_sensor_count": total_sensors,
            "readout_field_widths": widths,
            "readout_field_max_ids": {
                "layer": max_layer,
                "module": max_module,
                "sensor": max_sensor,
            },
        },
        "sensitive_component_order": sensitive_components,
        "layers": layer_records,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("b0_xml", type=Path)
    parser.add_argument("definitions_xml", type=Path)
    parser.add_argument("--json", type=Path, help="optional report path; stdout is always written")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = audit_source_contract(args.b0_xml, args.definitions_xml)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(text)
    if args.json:
        args.json.write_text(text)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
