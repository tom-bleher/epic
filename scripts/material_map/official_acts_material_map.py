#!/usr/bin/env python3
"""Generate an ACTS material map with the ACTS bundled in eic-shell.

Run from a clean work directory after sourcing the intended local ePIC install.
The script deliberately uses the native DD4hep adapter available in the official
eic-shell; it does not depend on the retired custom ACTS overlay.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import acts
import acts.examples
import acts.examples.dd4hep
from acts.examples.json import JsonFormat


def detector(xml: Path, material_map=None):
    cfg = acts.examples.dd4hep.DD4hepDetector.Config()
    cfg.xmlFileNames = [str(xml)]
    cfg.envelopeR = 1.0
    cfg.envelopeZ = 5.0
    cfg.logLevel = acts.logging.WARNING
    cfg.dd4hepLogLevel = acts.logging.WARNING
    if material_map is not None:
        cfg.materialDecorator = acts.IMaterialDecorator.fromFile(str(material_map))
    return acts.examples.dd4hep.DD4hepDetector(cfg)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def geometry_hashes(xml):
    result = {}
    pending = [xml.resolve()]
    while pending:
        path = pending.pop()
        if str(path) in result:
            continue
        result[str(path)] = sha256(path)
        for node in ET.fromstring(path.read_bytes()).iter("include"):
            if "ref" in node.attrib:
                ref = Path(os.path.expandvars(node.attrib["ref"]))
                pending.append((ref if ref.is_absolute() else path.parent / ref).resolve())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--particles", type=int, default=5000)
    parser.add_argument("--eta", type=float, nargs=2, default=(-8.0, 8.0))
    parser.add_argument("--truth", type=Path, help="Reuse a checksum-verified Geant4 recording")
    parser.add_argument("--binning-map", type=Path, help="Use this configuration's existing surface binning")
    parser.add_argument("--mapper", type=Path, required=True, help="Built bounded-material-map executable")
    parser.add_argument("--training-entries", type=int, help="Map the first N recorded entries; reserve later entries for validation")
    args = parser.parse_args()

    if args.events <= 0 or args.particles <= 0:
        parser.error("event and particle counts must be positive")
    if args.training_entries is not None and args.training_entries <= 0:
        parser.error("--training-entries must be positive")
    args.mapper = args.mapper.resolve()
    if not args.mapper.is_file():
        parser.error("Build the bounded mapper before running this command")
    if not args.eta[0] < args.eta[1]:
        parser.error("--eta requires an increasing range")
    args.xml = args.xml.resolve()
    args.output = args.output.resolve()
    if not args.xml.is_file():
        parser.error(f"geometry does not exist: {args.xml}")
    if args.output.suffix != ".cbor":
        parser.error("--output must end in .cbor")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error(f"refusing to overwrite existing map: {args.output}")
    includes = geometry_hashes(args.xml)
    if args.truth is not None:
        truth = args.truth.resolve()
        sidecar = truth.parent / "material-map.cbor.provenance.json"
        if not sidecar.is_file():
            parser.error(f"Recording provenance missing: {sidecar}")
        recording = json.loads(sidecar.read_text())
        if tuple(recording["acts"]) != tuple(acts.__version__):
            parser.error("Recording ACTS version differs from the active official shell")
        if sha256(truth) != recording["truth_sha256"]:
            parser.error("Recorded truth checksum differs from its provenance")
        if sha256(args.xml) != recording["xml_sha256"]:
            parser.error("Recording top-level XML differs from the requested geometry")
        if "geometry_include_sha256" in recording and recording["geometry_include_sha256"] != includes:
            parser.error("Recording compact include chain differs from the requested geometry")
        run_mapping(args, truth, recording, includes)
        return

    # The packaged native examples are used directly to keep their behaviour
    # tied to the installed ACTS release.
    from acts.examples import (
        EventGenerator,
        FixedMultiplicityGenerator,
        GaussianVertexGenerator,
        ParametricParticleGenerator,
        RandomNumbers,
    )
    from acts.examples.hepmc3 import HepMC3InputConverter
    from acts.examples.geant4 import Geant4MaterialRecording
    from acts.examples.root import RootMaterialTrackWriter

    work = args.output.parent
    truth = work / "geant4_material_tracks.root"
    for path in (truth,):
        if path.exists():
            parser.error(f"refusing to overwrite existing output: {path}")

    det = detector(args.xml)
    rnd = RandomNumbers(seed=228)
    sequencer = acts.examples.Sequencer(events=args.events, numThreads=1)
    generator = EventGenerator(
        level=acts.logging.INFO,
        generators=[EventGenerator.Generator(
            multiplicity=FixedMultiplicityGenerator(n=1),
            vertex=GaussianVertexGenerator(stddev=acts.Vector4(0, 0, 0, 0), mean=acts.Vector4(0, 0, 0, 0)),
            particles=ParametricParticleGenerator(pdg=acts.PdgParticle.eInvalid, charge=0, randomizeCharge=False,
                mass=0, p=(1 * acts.UnitConstants.GeV, 10 * acts.UnitConstants.GeV), eta=tuple(args.eta),
                numParticles=args.particles, etaUniform=True),
        )], randomNumbers=rnd)
    sequencer.addReader(generator)
    converter = HepMC3InputConverter(level=acts.logging.INFO, inputEvent=generator.config.outputEvent,
        outputParticles="particles_initial", outputVertices="vertices_initial", mergePrimaries=False)
    sequencer.addAlgorithm(converter)
    recorder = Geant4MaterialRecording(level=acts.logging.INFO, detector=det, randomNumbers=rnd,
        inputParticles=converter.config.outputParticles, outputMaterialTracks="material-tracks")
    sequencer.addAlgorithm(recorder)
    sequencer.addWriter(RootMaterialTrackWriter(prePostStep=True, recalculateTotals=True,
        inputMaterialTracks="material-tracks", filePath=str(truth), level=acts.logging.INFO))
    sequencer.run()
    recording = {
        "acts": list(acts.__version__), "xml": str(args.xml), "xml_sha256": sha256(args.xml),
        "geometry_include_sha256": includes, "events": args.events,
        "particles_per_event": args.particles, "eta": args.eta, "seed": 228,
        "truth": str(truth), "truth_sha256": sha256(truth),
    }
    # Persist truth identity even if subsequent mapping fails.
    args.output.with_suffix(args.output.suffix + ".provenance.json").write_text(json.dumps(recording, indent=2) + "\n")
    run_mapping(args, truth, recording, includes, det)


def run_mapping(args, truth, recording, includes, det=None):
    import uproot
    from acts.examples.json import JsonMaterialWriter
    from acts.json import MaterialMapJsonConverter

    entries = uproot.open(truth)["material-tracks"].num_entries
    # Keep a disjoint tail for paired B0 validation by default.
    count = args.training_entries or entries - max(1, min(200000, entries // 5))
    if not 0 < count < entries:
        raise ValueError("Training must leave a nonempty recorded range for validation")
    binning = args.binning_map
    if binning is None:
        det = det or detector(args.xml)
        binning = args.output.parent / "geometry-map.json"
        if binning.exists():
            raise FileExistsError(binning)
        cfg = MaterialMapJsonConverter.Config(processSensitives=True, processApproaches=True,
            processRepresenting=True, processBoundaries=True, processVolumes=False,
            context=acts.GeometryContext())
        writer = JsonMaterialWriter(level=acts.logging.WARNING, converterCfg=cfg,
            fileName=str(binning.with_suffix("")), writeFormat=JsonFormat.Json)
        writer.write(det.trackingGeometry())
    binning = binning.resolve()
    subprocess.run([str(args.mapper), str(args.xml), str(binning), str(truth),
                    str(args.output), "0", str(count), "5"], check=True)
    if geometry_hashes(args.xml) != includes:
        raise RuntimeError("Compact geometry changed while mapping")
    provenance = {
        "acts": list(acts.__version__), "xml": str(args.xml), "xml_sha256": sha256(args.xml),
        "geometry_include_sha256": includes, "map": str(args.output), "map_sha256": sha256(args.output),
        "truth": str(truth), "truth_sha256": recording["truth_sha256"],
        "training_entry_start": 0, "training_entry_count": count, "recorded_entries": entries,
        "binning_map": str(binning), "binning_sha256": sha256(binning),
        "mapper_sha256": sha256(args.mapper), "original_recording_provenance": recording,
        "geometry_plugin_sha256": {str(path): sha256(path) for path in
                                    sorted((args.xml.parents[2] / "lib").glob("libepic*.so"))},
        "container": os.environ.get("SINGULARITY_CONTAINER", os.environ.get("APPTAINER_CONTAINER", "unreported")),
        "geometry_commit": subprocess.check_output(["git", "-C", str(Path(__file__).resolve().parents[2]),
                                                     "rev-parse", "HEAD"], text=True).strip(),
        "historical_include_hashes_available": "geometry_include_sha256" in recording,
        "mapping": "Finite pre-step segments clipped at first tracking-envelope exit; retained midpoint assignment",
    }
    args.output.with_suffix(args.output.suffix + ".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
