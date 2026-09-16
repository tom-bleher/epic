#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Adapt the repository's geantino scanner to reproducible, identified B0 rays.

The underlying DDG4 textual scan is rounded. This tool is a material-budget
regression, not a micrometre-precision substitute for unrounded stepping data.
"""
from __future__ import annotations
import argparse
import importlib.machinery
import importlib.util
import json
import math
from pathlib import Path
from audit_b0 import file_hash, read, validate_ray


def intervals(rows: list[dict], length_mm: float) -> list[dict]:
    if not math.isfinite(length_mm) or length_mm <= 0:
        raise ValueError('Scan length must be finite and positive')
    output, previous_s, previous_x0 = [], 0.0, 0.0
    for row in rows:
        end, cumulative_x0 = 10 * float(row['path_length']), float(row['int_X0'])
        if not all(math.isfinite(v) for v in (end, cumulative_x0)) or end < previous_s or cumulative_x0 < previous_x0:
            raise ValueError('Malformed/nonmonotonic DDG4 material scan')
        delta = cumulative_x0 - previous_x0
        a, b = previous_s, min(end, length_mm)
        if a > length_mm: break
        fraction = 1 if end == a else max(0, (b - a) / (end - a))
        output.append({'s_begin_mm': a, 's_end_mm': b, 't_over_x0': delta * fraction,
                       'material': str(row['material'])})
        previous_s, previous_x0 = end, cumulative_x0
        if end >= length_mm: break
    if not output or previous_s < length_mm:
        raise ValueError('Scanner did not cover the requested path interval')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compact', type=Path, required=True)
    parser.add_argument('--rays', type=Path, required=True)
    parser.add_argument('--length-mm', type=float, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    ray_file = read(args.rays)
    if ray_file.get('schema_version') != 1 or not ray_file.get('rays'):
        raise ValueError('Expected a nonempty version-1 ray file')
    if len({str(r['ray_id']) for r in ray_file['rays']}) != len(ray_file['rays']):
        raise ValueError('Duplicate ray IDs')
    scanner_path = Path(__file__).resolve().parents[2] / 'bin' / 'g4MaterialScan_to_csv'
    loader = importlib.machinery.SourceFileLoader('b0_existing_material_scanner', str(scanner_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    import g4units
    scanner = module.g4MaterialScanner(str(args.compact.resolve(strict=True)))
    result = []
    for ray in ray_file['rays']:
        origin, direction, _ = validate_ray({**ray, 'segments': []})
        frame = scanner.scan((origin * g4units.mm).tolist(), direction.tolist())
        result.append({'ray_id': str(ray['ray_id']), 'origin_mm': origin.tolist(),
                       'direction': direction.tolist(), 'segments': intervals(frame.to_dict('records'), args.length_mm)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'schema_version': 1, 'producer': 'DDG4 textual geantino scan',
        'precision_note': 'DDG4 printed path lengths and cumulative radiation lengths are rounded.',
        'compact_sha256': file_hash(args.compact), 'rays_sha256': file_hash(args.rays),
        'scanner_sha256': file_hash(scanner_path), 'rays': result}, indent=2, allow_nan=False) + '\n')
    return 0

if __name__ == '__main__': raise SystemExit(main())
