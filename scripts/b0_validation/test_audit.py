# SPDX-License-Identifier: LGPL-3.0-or-later
import copy
import unittest
from audit_b0 import compare_surfaces, compare_material, validate_ray
from scan_b0_material import intervals


def surfaces():
    sensors, layers = [], []
    for index in range(8):
        point = [2, 3, 6000 + 250 * (index // 2) + 7 * (index % 2)]
        sensors.append({'path': f'/B0/{index}', 'system': 1, 'layer': index + 1, 'module': 1, 'sensor': 1,
                        'position_mm': point, 'normal': [0, 0, 1]})
        layers.append({'id': str(18446744073709550000 + index), 'layer': 10 + index,
                       'station': index // 2, 'position_mm': point, 'normal': [0, 0, -1]})
    return {'schema_version': 1, 'sensors': sensors}, {'schema_version': 1, 'surfaces': layers}


def ray(start=10, end=20, weight=.03):
    return {'ray_id': 'one', 'origin_mm': [0, 0, 6000], 'direction': [0, 0, 1],
            'segments': [{'s_begin_mm': start, 's_end_mm': end, 't_over_x0': weight}]}


class GeometryTests(unittest.TestCase):
    def test_eight_layers_four_stations(self):
        a, b = surfaces(); self.assertTrue(compare_surfaces(a, b)['passed'])
        b['surfaces'].reverse(); self.assertTrue(compare_surfaces(a, b)['passed'])
    def test_missing_and_shifted_surfaces(self):
        a, b = surfaces(); b['surfaces'][0]['position_mm'] = [2, 3, 6000.1]
        self.assertFalse(compare_surfaces(a, b)['passed'])
        a, b = surfaces(); b['surfaces'].pop(); self.assertFalse(compare_surfaces(a, b)['passed'])
    def test_duplicate_ids_and_positions(self):
        a, b = surfaces(); b['surfaces'][1]['id'] = b['surfaces'][0]['id']
        with self.assertRaises(ValueError): compare_surfaces(a, b)
        a, b = surfaces(); a['sensors'][1]['position_mm'] = a['sensors'][0]['position_mm']
        self.assertFalse(compare_surfaces(a, b)['passed'])
    def test_invalid_normal_and_count(self):
        a, b = surfaces(); b['surfaces'][0]['normal'] = [0, 0, 2]
        with self.assertRaises(ValueError): compare_surfaces(a, b)
        with self.assertRaises(ValueError): compare_surfaces(*surfaces(), expected_stations=0)
    def test_material_agreement_and_double_count(self):
        self.assertTrue(compare_material(ray(), ray(), 0, 0, 0, 0)['passed'])
        self.assertFalse(compare_material(ray(), ray(weight=.06), .001, .01, 1, .001)['passed'])
    def test_material_location_not_just_total(self):
        result = compare_material(ray(), ray(20, 30), .001, .01, 1, .001)
        self.assertEqual(result['absolute_x0_difference'], 0)
        self.assertAlmostEqual(result['centroid_displacement_mm'], 10)
        self.assertFalse(result['passed'])
    def test_material_impulses_and_rays(self):
        self.assertTrue(compare_material(ray(10, 10), ray(10, 10), 0, 0, 0, 0)['passed'])
        other = ray(); other['direction'] = [1, 0, 1]
        with self.assertRaises(ValueError): compare_material(ray(), other, 0, 0, 0, 0)
        bad = ray(); bad['segments'].append(copy.deepcopy(bad['segments'][0]))
        with self.assertRaises(ValueError): validate_ray(bad)
    def test_scan_units_and_clipping(self):
        rows = [{'path_length': 1, 'int_X0': .01, 'material': 'Si'},
                {'path_length': 2, 'int_X0': .03, 'material': 'Al'}]
        values = intervals(rows, 15)
        self.assertEqual(values[0]['s_end_mm'], 10)
        self.assertAlmostEqual(sum(v['t_over_x0'] for v in values), .02)
        with self.assertRaises(ValueError): intervals(rows, 50)

if __name__ == '__main__': unittest.main()
