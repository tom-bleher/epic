#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run inside ACTS47: python scripts/material_map/b0_validation/test_recording_provenance.py."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_b0_material import verify_recording


class RecordingProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.truth = self.root / "material.root"
        self.truth.write_bytes(b"test recording bytes")
        self.xml = self.root / "source" / "epic_ip6_extended_5x41.xml"
        self.includes = {str(self.xml): "entry hash",
                         str(self.xml.parent / "compact" / "tracker.xml"): "tracker hash"}
        self.metadata = {"acts": [47, 7, 0], "recorded_entries": 100,
                         "output_sha256": hashlib.sha256(self.truth.read_bytes()).hexdigest(),
                         "xml": str(self.xml), "geometry_include_sha256": self.includes}
        self.sidecar = Path(str(self.truth) + ".json")
        self.write_metadata()

    def write_metadata(self):
        self.sidecar.write_text(json.dumps(self.metadata))

    def verify(self, **options):
        return verify_recording(self.truth, 100, self.xml, self.includes, **options)

    def test_matching_recording_and_relocated_geometry(self):
        self.assertTrue(self.verify()["material_geometry_verified"])
        moved_xml = self.root / "frozen" / self.xml.name
        moved = {str(moved_xml): "entry hash",
                 str(moved_xml.parent / "compact" / "tracker.xml"): "tracker hash"}
        self.assertTrue(verify_recording(self.truth, 100, moved_xml, moved)
                        ["material_geometry_verified"])

    def test_modified_root_is_rejected(self):
        self.truth.write_bytes(b"different recording bytes")
        with self.assertRaisesRegex(RuntimeError, "ROOT checksum"):
            self.verify()

    def test_changed_geometry_is_rejected(self):
        changed = dict(self.includes)
        changed[str(self.xml.parent / "compact" / "tracker.xml")] = "changed tracker"
        with self.assertRaisesRegex(RuntimeError, "geometry differs"):
            verify_recording(self.truth, 100, self.xml, changed)

    def test_wrong_version_or_count_is_rejected(self):
        for key, value, message in [("acts", [44, 4, 0], "ACTS 47.7"),
                                    ("recorded_entries", 99, "entry count")]:
            with self.subTest(key=key):
                original = self.metadata[key]
                self.metadata[key] = value
                self.write_metadata()
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify()
                self.metadata[key] = original

    def test_missing_sidecar_requires_explicit_directions_only(self):
        self.sidecar.unlink()
        with self.assertRaisesRegex(RuntimeError, "JSON sidecar"):
            self.verify()
        result = self.verify(directions_only=True)
        self.assertFalse(result["material_geometry_verified"])


if __name__ == "__main__":
    unittest.main()
