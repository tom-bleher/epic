# SPDX-License-Identifier: LGPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path

from source_contract import audit_source_contract


MINIMAL_DEFINITIONS = """<lccdd><define>
<constant name="B0Tracker_Station_1_ID" value="150"/>
</define></lccdd>"""


def minimal_b0(*, sensitive=True, sensor_bits=2, duplicate_face=False, duplicate_module=False,
               bad_dimension=False):
    sens = ' sensitive="true"' if sensitive else ""
    width = "0*mm" if bad_dimension else "16*mm"
    second_module = (
        '<module posX="0*mm" posY="0*mm" rotZ="0*deg"/>'
        if duplicate_module
        else '<module posX="20*mm" posY="0*mm" rotZ="0*deg"/>'
    )
    layers = []
    for station in range(1, 5):
        for side in ("back", "front"):
            layers.append(
                f'''<layer station="{station}" side="{side}">
                <position x="0*mm" y="0*mm" z="{station * 100}*mm"/>
                <envelope zmin_tolerance="1*mm" zmax_tolerance="1*mm"/>
                <module_positions>
                  <module posX="0*mm" posY="0*mm" rotZ="0*deg"/>
                  {second_module}
                </module_positions>
                </layer>'''
            )
    if duplicate_face:
        layers.append(layers[0])
    return f'''<lccdd>
    <detectors>
      <detector id="B0Tracker_Station_1_ID" name="B0Tracker">
        <module name="TrackingUnit" offset_from_support="3*mm">
          <module_component name="Sensor" material="Silicon"{sens}>
            <box x="{width}" y="16*mm" z="0.05*mm"/>
            <position x="0*mm" y="0*mm" z="0*mm"/>
          </module_component>
        </module>
        {''.join(layers)}
      </detector>
    </detectors>
    <readouts>
      <readout name="B0TrackerHits">
        <id>system:8,layer:4,module:12,sensor:{sensor_bits},x:32:-16,y:-16</id>
      </readout>
    </readouts>
    </lccdd>'''


class SourceContractTest(unittest.TestCase):
    def audit_text(self, xml):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            b0 = root / "B0_tracker.xml"
            defs = root / "definitions.xml"
            b0.write_text(xml)
            defs.write_text(MINIMAL_DEFINITIONS)
            return audit_source_contract(b0, defs)

    def test_repository_geometry_inventory(self):
        repo = Path(__file__).resolve().parents[2]
        report = audit_source_contract(
            repo / "compact/far_forward/B0_tracker.xml",
            repo / "compact/definitions.xml",
        )
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["summary"]["layer_count"], 8)
        self.assertEqual(report["summary"]["physical_station_count"], 4)
        self.assertEqual(report["summary"]["module_count"], 172)
        self.assertEqual(report["summary"]["sensitive_components_per_module"], 3)
        self.assertEqual(report["summary"]["sensitive_sensor_count"], 516)
        self.assertEqual(
            [(entry["station"], entry["side"], entry["layer_id"]) for entry in report["layers"]],
            [(1, "back", 1), (1, "front", 2), (2, "back", 3), (2, "front", 4),
             (3, "back", 5), (3, "front", 6), (4, "back", 7), (4, "front", 8)],
        )

    def test_missing_sensitive_component_is_rejected(self):
        report = self.audit_text(minimal_b0(sensitive=False))
        self.assertTrue(any("no sensitive components" in error for error in report["errors"]))

    def test_duplicate_station_face_is_rejected(self):
        report = self.audit_text(minimal_b0(duplicate_face=True))
        self.assertTrue(any("duplicate layer" in error for error in report["errors"]))

    def test_duplicate_module_placement_is_rejected(self):
        report = self.audit_text(minimal_b0(duplicate_module=True))
        self.assertTrue(any("duplicate module placement" in error for error in report["errors"]))

    def test_nonpositive_component_dimension_is_rejected(self):
        report = self.audit_text(minimal_b0(bad_dimension=True))
        self.assertTrue(any("must be positive" in error for error in report["errors"]))

    def test_sensor_field_overflow_is_rejected(self):
        xml = minimal_b0(sensor_bits=1).replace(
            '</module_component>',
            '</module_component><module_component name="Sensor2" material="Silicon" sensitive="true">'
            '<box x="16*mm" y="16*mm" z="0.05*mm"/><position x="0*mm" y="0*mm" z="1*mm"/>'
            '</module_component>',
            1,
        )
        report = self.audit_text(xml)
        self.assertTrue(any("sensor field max id" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
