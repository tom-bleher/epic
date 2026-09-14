#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2024 Shujie Li
"""Compatibility entry point for bounded mapping in the official eic-shell."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xmlFile", type=Path, default=Path(os.environ.get("DETECTOR_PATH", ".")) /
                        (os.environ.get("DETECTOR_CONFIG", "epic_ip6_extended") + ".xml"))
    parser.add_argument("--geoFile", type=Path, default=Path("geometry-map.json"))
    parser.add_argument("--matFile", type=Path, default=Path("material-map.cbor"))
    parser.add_argument("--truth", type=Path, default=Path("geant4_material_tracks.root"))
    parser.add_argument("--mapper", type=Path, required=True, help="Built bounded-material-map executable")
    parser.add_argument("--training-entries", type=int)
    args = parser.parse_args()
    command = [sys.executable, str(Path(__file__).with_name("official_acts_material_map.py")),
               "--xml", str(args.xmlFile), "--binning-map", str(args.geoFile),
               "--output", str(args.matFile), "--truth", str(args.truth), "--mapper", str(args.mapper)]
    if args.training_entries is not None:
        command += ["--training-entries", str(args.training_entries)]
    subprocess.run(command, check=True)
