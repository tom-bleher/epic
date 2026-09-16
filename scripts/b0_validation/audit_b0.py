#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare actual DD4hep/ACTS surfaces and material traces without guessing IDs."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import numpy as np


def read(path: Path) -> dict:
    def invalid(value): raise ValueError(f'Invalid JSON number {value}')
    return json.loads(path.read_text(), parse_constant=invalid)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''): h.update(block)
    return h.hexdigest()


def compare_surfaces(dd4hep: dict, acts: dict, position_tolerance_mm: float = .001,
                     normal_tolerance: float = 1e-8, expected_stations: int = 4,
                     expected_layers: int = 8) -> dict:
    if dd4hep.get('schema_version') != 1 or acts.get('schema_version') != 1:
        raise ValueError('Unsupported manifest schema')
    if not (math.isfinite(position_tolerance_mm) and position_tolerance_mm > 0 and
            math.isfinite(normal_tolerance) and normal_tolerance > 0):
        raise ValueError('Tolerances must be positive/finite')
    if expected_stations <= 0 or expected_layers <= 0 or expected_layers % expected_stations:
        raise ValueError('Expected station/layer counts must be positive and divisible')
    sensors, surfaces = dd4hep['sensors'], acts['surfaces']
    if not sensors or not surfaces: raise ValueError('Empty geometry is not a passing audit')
    ids = [str(s['id']) for s in surfaces]
    if len(set(ids)) != len(ids): raise ValueError('Duplicate ACTS geometry IDs')
    dd_ids = [(s['system'], s['layer'], s['module'], s['sensor']) for s in sensors]
    if len(set(dd_ids)) != len(dd_ids): raise ValueError('Duplicate DD4hep sensor placement IDs')
    centres = np.asarray([s['position_mm'] for s in sensors], float)
    normals = np.asarray([s['normal'] for s in sensors], float)
    if centres.shape != (len(sensors), 3) or normals.shape != centres.shape or not np.isfinite(centres).all() or not np.isfinite(normals).all():
        raise ValueError('Invalid DD4hep positions/normals')
    if not np.allclose(np.linalg.norm(normals, axis=1), 1, atol=normal_tolerance):
        raise ValueError('DD4hep normals are not unit vectors')
    matched, failures, used = [], [], set()
    for surface in surfaces:
        centre, normal = np.asarray(surface['position_mm'], float), np.asarray(surface['normal'], float)
        if centre.shape != (3,) or normal.shape != (3,) or not np.isfinite(centre).all() or not np.isfinite(normal).all():
            raise ValueError('Invalid ACTS position/normal')
        if abs(np.linalg.norm(normal) - 1) > normal_tolerance: raise ValueError('ACTS normal not unit length')
        distances = np.linalg.norm(centres - centre, axis=1)
        compatible = np.flatnonzero(distances <= position_tolerance_mm)
        if len(compatible) != 1:
            failures.append({'surface': str(surface['id']), 'compatible_sensor_count': len(compatible)}); continue
        index = int(compatible[0])
        error = 1 - abs(float(normals[index] @ normal))
        if index in used or error > normal_tolerance:
            failures.append({'surface': str(surface['id']), 'duplicate_match': index in used, 'normal_error': error}); continue
        used.add(index)
        matched.append({'surface': str(surface['id']), 'dd4hep_path': sensors[index]['path'],
                        'dd4hep_ids': dd_ids[index], 'station': surface['station'],
                        'distance_mm': float(distances[index]), 'normal_error': max(0.0, error)})
    stations = Counter(int(s['station']) for s in surfaces)
    layers = Counter(int(s['layer']) for s in surfaces)
    layer_stations = {}
    for s in surfaces: layer_stations.setdefault(s['layer'], set()).add(s['station'])
    station_layers = {}
    for s in surfaces: station_layers.setdefault(s['station'], set()).add(s['layer'])
    structural = (len(stations) == expected_stations and len(layers) == expected_layers
                  and all(len(v) == 1 for v in layer_stations.values())
                  and all(len(v) == expected_layers // expected_stations for v in station_layers.values()))
    return {'passed': not failures and len(used) == len(sensors) == len(surfaces) and structural,
            'dd4hep_sensors': len(sensors), 'acts_surfaces': len(surfaces),
            'stations': dict(stations), 'layers': dict(layers), 'structural_match': structural,
            'unmatched_dd4hep': [sensors[i]['path'] for i in range(len(sensors)) if i not in used],
            'failures': failures, 'matches': matched,
            'limitations': ['Normal sign is allowed to reverse; complete in-plane axis/covariance round-trip is a separate test.',
                            'Centre/normal correspondence does not certify surface bounds or material.']}


def validate_ray(ray: dict) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float, float]]]:
    origin, direction = np.asarray(ray['origin_mm'], float), np.asarray(ray['direction'], float)
    if origin.shape != (3,) or direction.shape != (3,) or not np.isfinite(origin).all() or not np.isfinite(direction).all() or np.linalg.norm(direction) == 0:
        raise ValueError('Invalid ray origin/direction')
    direction /= np.linalg.norm(direction)
    segments = []
    last = -math.inf
    for segment in ray['segments']:
        a, b, weight = (float(segment[k]) for k in ('s_begin_mm', 's_end_mm', 't_over_x0'))
        if not all(math.isfinite(v) for v in (a, b, weight)) or a < 0 or b < a or weight < 0 or a < last - 1e-6:
            raise ValueError('Invalid/overlapping/unordered material intervals')
        segments.append((a, b, weight)); last = b
    return origin, direction, segments


def cumulative(segments: list[tuple[float, float, float]], s: float) -> float:
    return sum(w * (float(s >= b) if a == b else max(0, min(1, (s - a) / (b - a)))) for a, b, w in segments)


def material_moments(segments: list[tuple[float, float, float]]) -> dict:
    total = sum(w for a, b, w in segments)
    if total == 0: return {'t_over_x0': 0.0, 'centroid_mm': None, 'variance_mm2': None}
    mean = sum(w * (a + b) / 2 for a, b, w in segments) / total
    second = sum(w * (a*a + a*b + b*b) / 3 for a, b, w in segments) / total
    return {'t_over_x0': total, 'centroid_mm': mean, 'variance_mm2': max(0, second - mean*mean)}


def compare_material(a: dict, b: dict, absolute_x0: float, relative_x0: float,
                     centroid_mm: float, cumulative_x0: float) -> dict:
    oa, da, sa = validate_ray(a); ob, db, sb = validate_ray(b)
    if str(a['ray_id']) != str(b['ray_id']) or not np.allclose(oa, ob, atol=1e-6, rtol=0) or not np.allclose(da, db, atol=1e-10, rtol=0):
        raise ValueError('Material traces do not describe the same ray')
    if any(not math.isfinite(v) or v < 0 for v in (absolute_x0, relative_x0, centroid_mm, cumulative_x0)):
        raise ValueError('Material tolerances must be finite/nonnegative')
    ma, mb = material_moments(sa), material_moments(sb)
    delta = abs(ma['t_over_x0'] - mb['t_over_x0'])
    centroids = (ma['centroid_mm'], mb['centroid_mm'])
    displacement = None if any(v is None for v in centroids) else abs(centroids[0] - centroids[1])
    knots = sorted({x for start, end, _ in sa + sb for x in (start, end)})
    samples = knots + [float(np.nextafter(k, -math.inf)) for k in knots]
    cumulative_difference = max((abs(cumulative(sa, k) - cumulative(sb, k)) for k in samples), default=0)
    passed = (delta <= absolute_x0 + relative_x0 * ma['t_over_x0']
              and (displacement is None or displacement <= centroid_mm)
              and cumulative_difference <= cumulative_x0)
    return {'ray_id': str(a['ray_id']), 'passed': passed, 'reference': ma, 'mapped': mb,
            'absolute_x0_difference': delta, 'centroid_displacement_mm': displacement,
            'max_cumulative_x0_difference': cumulative_difference}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    surfaces = sub.add_parser('surfaces')
    surfaces.add_argument('--dd4hep', type=Path, required=True)
    surfaces.add_argument('--acts', type=Path, required=True)
    surfaces.add_argument('--position-tolerance-mm', type=float, default=.001)
    surfaces.add_argument('--expected-stations', type=int, default=4)
    surfaces.add_argument('--expected-layers', type=int, default=8)
    material = sub.add_parser('material')
    material.add_argument('--reference', type=Path, required=True)
    material.add_argument('--mapped', type=Path, required=True)
    for name in ('absolute-x0', 'relative-x0', 'centroid-mm', 'cumulative-x0'):
        material.add_argument('--' + name, type=float, required=True)
    for child in (surfaces, material): child.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'surfaces':
        result = compare_surfaces(read(args.dd4hep), read(args.acts), args.position_tolerance_mm,
                                  expected_stations=args.expected_stations, expected_layers=args.expected_layers)
        files = [args.dd4hep, args.acts]
    else:
        def load(path):
            data = read(path)
            if data.get('schema_version') != 1: raise ValueError('Unsupported ray schema')
            rows = {str(ray['ray_id']): ray for ray in data['rays']}
            if len(rows) != len(data['rays']) or not rows: raise ValueError('Empty/duplicate ray set')
            return rows
        a, b = load(args.reference), load(args.mapped)
        if a.keys() != b.keys(): raise ValueError('Reference/mapped ray IDs differ')
        rows = [compare_material(a[k], b[k], args.absolute_x0, args.relative_x0, args.centroid_mm, args.cumulative_x0) for k in a]
        result = {'passed': all(r['passed'] for r in rows), 'rays': rows}
        files = [args.reference, args.mapped]
    result['inputs'] = [{'path': str(p.resolve()), 'sha256': file_hash(p)} for p in files]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return 0 if result['passed'] else 1

if __name__ == '__main__': raise SystemExit(main())
