#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Record matched IP6 material with the native ACTS 47.7 Geant4 example."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import acts
import acts.examples
import acts.examples.dd4hep
import acts.examples.geant4
import uproot

from validate_b0_material import geometry_hashes, geometry_plugin_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New ROOT output file")
    parser.add_argument("--acts-source", type=Path, default=os.environ.get("ACTS_SOURCE_DIR"))
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--particles", type=int, default=1000)
    parser.add_argument("--eta-range", type=float, nargs=2, default=(-7., 7.))
    args = parser.parse_args()
    if tuple(acts.__version__) != (47, 7, 0):
        parser.error("Use ACTS 47.7.0 with its Geant4 examples enabled")
    if args.events <= 0 or args.particles <= 0 or not args.eta_range[0] < args.eta_range[1]:
        parser.error("Positive event/particle counts and increasing eta range required")
    if args.output.suffix != ".root" or args.output.exists():
        parser.error("Choose a new output filename ending in .root")
    if args.acts_source is None:
        parser.error("Set ACTS_SOURCE_DIR or pass --acts-source")
    source = args.acts_source / "Examples/Scripts/Python/material_recording.py"
    spec = importlib.util.spec_from_file_location("acts_native_material_recording", source)
    recording = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recording)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    detector = acts.examples.dd4hep.DD4hepDetector(
        acts.examples.dd4hep.DD4hepDetector.Config(
            xmlFileNames=[str(args.xml.resolve())], envelopeR=1., envelopeZ=5.,
            logLevel=acts.logging.WARNING, dd4hepLogLevel=acts.logging.WARNING))
    includes = geometry_hashes(args.xml)
    plugins = geometry_plugin_hashes()
    recording.runMaterialRecording(
        detector, acts.examples.Sequencer(events=args.events, numThreads=1),
        tracksPerEvent=args.particles, etaRange=tuple(args.eta_range),
        materialTrackCollectionName="material-tracks",
        outputFileBase=str(args.output.resolve().with_suffix(""))).run()
    if includes != geometry_hashes(args.xml) or plugins != geometry_plugin_hashes():
        raise RuntimeError("Geometry XML or construction library changed during recording")
    provenance = {
        "acts": list(acts.__version__), "xml": str(args.xml.resolve()),
        "geometry_include_sha256": includes,
        "geometry_plugins": plugins,
        "container": os.environ.get("SINGULARITY_CONTAINER"),
        "native_recording_source": str(source.resolve()),
        "native_recording_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "events": args.events, "particles_per_event": args.particles,
        "thrown_particles": args.events * args.particles,
        "eta_range": args.eta_range, "eta_uniform": True,
        "phi_range_radians": [0., 2. * 3.141592653589793],
        "vertex_mm": [0., 0., 0.], "seed": 228,
        "recorded_entries": uproot.open(args.output)["material-tracks"].num_entries,
        "output": str(args.output.resolve()),
        "output_sha256": hashlib.file_digest(args.output.open("rb"), "sha256").hexdigest(),
    }
    Path(str(args.output) + ".json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
