#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Record, remap and independently validate IP6 material with ACTS 47.7.

Outputs stay in a new campaign directory. A successful run does not install or
publish its candidate: acceptance plots and bin occupancy still need review.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import acts
import uproot

from validate_b0_material import geometry_hashes, verify_recording


def run(command, log, check=True):
    print("Running", " ".join(map(str, command)), flush=True)
    with log.open("w") as stream:
        return subprocess.run(list(map(str, command)), stdout=stream,
                              stderr=subprocess.STDOUT, check=check).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nevents", type=int, default=1000)
    parser.add_argument("--nparticles", type=int, default=5000)
    parser.add_argument("--work-dir", type=Path, default=Path("b0-campaign"))
    parser.add_argument("--recording", type=Path, help="Reuse a provenance-verified recording")
    parser.add_argument("--probe", type=Path, help="Existing b0-material-probe; otherwise build locally")
    parser.add_argument("--acts-source", type=Path,
                        default=os.environ.get("ACTS_SOURCE_DIR"))
    parser.add_argument("--binning-map", type=Path,
                        help="Optional current-geometry mapping configuration; default is XML-declared binning")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--training-fraction", type=float, default=0.9)
    parser.add_argument("--validation-entries", type=int,
                        help="Limit validation range, reserving later entries for final testing")
    args = parser.parse_args()
    if tuple(acts.__version__) != (47, 7, 0):
        parser.error("This workflow requires ACTS 47.7.0")
    if min(args.nevents, args.nparticles, args.sample_size) <= 0:
        parser.error("Event, particle and validation sample counts must be positive")
    if not 0 < args.training_fraction < 1:
        parser.error("Leave a nonempty, disjoint validation range")
    config = os.environ.get("DETECTOR_CONFIG", "")
    if config not in ("epic_ip6_extended", "epic_ip6_extended_5x41"):
        parser.error("Source epic_ip6_extended (5x41) before running")
    xml = (Path(os.environ["DETECTOR_PATH"]) / f"{config}.xml").resolve()
    includes = geometry_hashes(xml)
    beamfiles = [Path(p).name for p in includes
                 if Path(p).parent.name == "fields" and Path(p).name.startswith("beamline_")]
    if beamfiles != ["beamline_5x41.xml"]:
        parser.error(f"Expected 5x41 field configuration, found {beamfiles}")
    constants = [node.get("value") for node in ET.parse(xml).iter("constant")
                 if node.get("name") == "material-map"]
    if len(constants) != 1:
        parser.error("Expected one material-map constant in the active entry point")
    reference = (xml.parent / constants[0]).resolve()
    if not reference.is_file():
        parser.error(f"Configured reference map is missing: {reference}")
    if args.work_dir.exists():
        parser.error("Choose a new campaign directory; existing results are never overwritten")
    args.work_dir.mkdir(parents=True)
    work = args.work_dir.resolve()
    scripts = Path(__file__).resolve().parent
    if args.probe is None:
        run(["cmake", "-S", scripts / "b0_validation", "-B", work / "build",
             "-DCMAKE_BUILD_TYPE=Release"], work / "configure.log")
        run(["cmake", "--build", work / "build", "-j", os.cpu_count() or 1],
            work / "build.log")
        args.probe = work / "build/b0-material-probe"
    truth = args.recording.resolve() if args.recording else work / "geant4_material_tracks.root"
    if args.recording is None:
        command = [sys.executable, scripts / "record_b0_material.py", "--xml", xml,
                   "--output", truth, "--events", args.nevents,
                   "--particles", args.nparticles, "--eta-range", -7, 7]
        if args.acts_source:
            command.extend(["--acts-source", args.acts_source])
        run(command, work / "recording.log")
    entries = uproot.open(truth)["material-tracks"].num_entries
    provenance = verify_recording(truth, entries, xml, includes)
    recording = provenance["recording"]
    if not provenance.get("compiled_geometry_verified"):
        parser.error("A campaign requires recorded construction-library provenance; record a fresh scan")
    if (recording["events"], recording["particles_per_event"]) != (args.nevents, args.nparticles):
        parser.error("Requested counts differ from recording provenance")
    if recording["eta_range"] != [-7, 7]:
        parser.error("Campaign recording must cover eta -7 to 7; targeted scans are additional checks")
    training = int(entries * args.training_fraction)
    if not 0 < training < entries:
        parser.error("Too few recorded entries for a train/validation split")
    validation_entries = (entries - training if args.validation_entries is None
                          else args.validation_entries)
    if not 0 < validation_entries <= entries - training:
        parser.error("Validation range must fit after the training range")
    common = [sys.executable, scripts / "validate_b0_material.py", "--truth", truth,
              "--probe", args.probe.resolve(), "--xml", xml, "--map", reference,
              "--sample-size", args.sample_size, "--first-input-entry", training,
              "--max-input-entries", validation_entries, "--candidate-limit", validation_entries]
    regeneration = list(common)
    binning_options = ["--native-binning"]
    if args.binning_map:
        regeneration[regeneration.index("--map") + 1] = args.binning_map.resolve()
        binning_options = []
    regenerated_status = run(regeneration + binning_options + ["--output", work / "regenerated", "--remap",
                             "--training-entries", training], work / "regenerated.log", check=False)
    # The same held-out rays compare the existing and regenerated maps.
    current_status = run(common + ["--output", work / "current"], work / "current.log", check=False)
    candidate = work / "regenerated/candidate.cbor"
    manifest = {
        "acts": list(acts.__version__), "geometry_config": config,
        "geometry_include_sha256": includes, "recording_provenance": provenance,
        "recorded_entries": entries, "training_entries": training,
        "validation_first_entry": training, "validation_entries_available": entries - training,
        "validation_entries_selected": validation_entries,
        "candidate": str(candidate), "candidate_bytes": candidate.stat().st_size if candidate.exists() else None,
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.exists() else None,
        "reference": str(reference),
        "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "regenerated_exit_code": regenerated_status, "current_exit_code": current_status,
        "status": ("Numerical checks passed; review plots and occupancy before installation/publication"
                   if regenerated_status == 0 else "Candidate failed validation; do not install"),
    }
    (work / "campaign.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Candidate and validation evidence: {work}")
    if regenerated_status:
        raise SystemExit(regenerated_status)


if __name__ == "__main__":
    main()
