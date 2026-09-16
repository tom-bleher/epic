# SPDX-License-Identifier: LGPL-3.0-or-later
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys
import xml.etree.ElementTree as ET
from make_aclgad_profile import make_profile, verify_profile, READOUT, MANIFEST, simulate_profile

XML = '''<lccdd><define/><readouts><readout name="B0TrackerHits"><comment>effective</comment>
<segmentation type="CartesianGridXY" grid_size_x=".070*mm" grid_size_y=".070*mm"/>
<id>system:8,layer:4,module:12,sensor:2,x:32:-16,y:-16</id></readout></readouts></lccdd>'''

class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / READOUT).parent.mkdir(parents=True)
        (self.source / READOUT).write_text(XML)
        (self.source / "detector.xml").write_text('<lccdd><include ref="compact/far_forward/B0_tracker.xml"/></lccdd>')
        self.output = self.root / "profile"

    def create(self):
        return make_profile(self.source, self.output, "detector.xml", .5)

    def test_physical_profile_preserves_source_and_id_fields(self):
        self.create()
        verify_profile(self.output)
        self.assertEqual((self.source / READOUT).read_text(), XML)
        tree = ET.parse(self.output / READOUT)
        seg = tree.find("./readouts/readout/segmentation")
        self.assertEqual(seg.get("grid_size_x"), "0.5*mm")
        self.assertEqual(seg.get("offset_x"), "0.25*mm")
        self.assertEqual(tree.find("./readouts/readout/id").text, ET.fromstring(XML).find("./readouts/readout/id").text)

    def test_refuse_overwrite_and_nested_output(self):
        self.create()
        with self.assertRaises(ValueError): self.create()
        with self.assertRaises(ValueError): make_profile(self.source, self.source / "nested", "detector.xml", .5)

    def test_hash_tampering_and_extra_assets(self):
        self.create()
        target = self.output / READOUT
        target.write_text(target.read_text() + "\n")
        with self.assertRaises(ValueError): verify_profile(self.output)

    def test_unrecorded_asset(self):
        self.create()
        (self.output / "unexpected.xml").write_text("x")
        with self.assertRaises(ValueError): verify_profile(self.output)

    def test_external_symlink(self):
        (self.root / "outside").write_text("x")
        (self.source / "unsafe").symlink_to(self.root / "outside")
        with self.assertRaises(ValueError): self.create()
        self.assertFalse(self.output.exists())

    def test_bad_pitch_and_missing_readout(self):
        for pitch in (0, -.5, float("nan"), .3):
            with self.assertRaises(ValueError): make_profile(self.source, self.output, "detector.xml", pitch)
        (self.source / READOUT).write_text("<lccdd/>")
        with self.assertRaises(ValueError): self.create()
        self.assertFalse(self.output.exists())

    def test_manifest_path_traversal(self):
        self.create()
        manifest = self.output / MANIFEST
        data = json.loads(manifest.read_text())
        data["files"]["../source/detector.xml"] = data["files"]["detector.xml"]
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError): verify_profile(self.output)

    def test_material_map_tampering(self):
        material = self.root / "material.cbor"
        material.write_bytes(b"material")
        make_profile(self.source, self.output, "detector.xml", .5, material)
        verify_profile(self.output)
        material.write_bytes(b"other")
        with self.assertRaises(ValueError): verify_profile(self.output)

    def test_include_closure_is_required(self):
        (self.source / "detector.xml").write_text('<lccdd/>')
        with self.assertRaises(ValueError): self.create()

    def test_missing_cyclic_external_and_unresolved_includes(self):
        for ref in ('missing.xml', 'detector.xml', '../outside.xml', '${UNKNOWN}/file.xml'):
            (self.root / 'outside.xml').write_text('<lccdd/>')
            (self.source / 'detector.xml').write_text(f'<lccdd><include ref="{ref}"/></lccdd>')
            with self.assertRaises((ValueError, FileNotFoundError)): self.create()
        self.assertFalse(self.output.exists())

    def test_simulation_receipt_success_and_tampered_input(self):
        self.create()
        out = self.root / 'simulation'
        def simulator(args, **kwargs):
            (out / 'sim.edm4hep.root').write_bytes(b'test output, not a detector sample')
            return subprocess.CompletedProcess(args, 0)
        with patch('make_aclgad_profile.subprocess.run', side_effect=simulator):
            receipt = simulate_profile(self.output, out, sys.executable, [], [])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'completed')
        with self.assertRaises(ValueError):
            simulate_profile(self.output, self.root / 'bad', sys.executable, ['--compact=other'], [])
        with self.assertRaises(ValueError):
            simulate_profile(self.output, self.output / 'run', sys.executable, [], [])

    def test_failed_simulation_does_not_get_completed_receipt(self):
        self.create()
        out = self.root / 'failed'
        with patch('make_aclgad_profile.subprocess.run', side_effect=subprocess.CalledProcessError(2, 'test')):
            with self.assertRaises(subprocess.CalledProcessError):
                simulate_profile(self.output, out, sys.executable, [], [])
        self.assertEqual(json.loads((out / 'simulation.json').read_text())['status'], 'failed')


if __name__ == "__main__": unittest.main()
