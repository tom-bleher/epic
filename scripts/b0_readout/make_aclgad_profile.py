#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Create an immutable physical-pitch B0 detector profile; never edit the input tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

READOUT = Path("compact/far_forward/B0_tracker.xml")
MANIFEST = "b0_readout_contract.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def contained(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"Non-relative profile asset: {relative}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Profile asset escapes its root: {relative}")
    if not resolved.is_file():
        raise ValueError(f"Profile asset is not a file: {relative}")
    return resolved


def xml_closure(root: Path, compact: str) -> list[str]:
    """Resolve local XML includes, refusing cycles, unresolved variables and escapes."""
    root = root.resolve(strict=True)
    seen, active = set(), set()
    def visit(path: Path) -> None:
        path = path.resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"XML include escapes profile: {path}")
        if path in active:
            raise ValueError(f"XML include cycle: {path}")
        if path in seen:
            return
        active.add(path)
        for element in ET.parse(path).iter():
            if element.tag.rsplit("}", 1)[-1] != "include":
                continue
            ref = element.get("ref")
            if not ref or any(token in ref for token in (":", "$", "~")) or Path(ref).is_absolute():
                raise ValueError(f"Only resolved local include refs are supported: {ref!r}")
            visit(path.parent / ref)
        active.remove(path)
        seen.add(path)
    visit(contained(root, compact))
    if (root / READOUT).resolve() not in seen:
        raise ValueError("Selected compact does not include B0_tracker.xml")
    return sorted(str(path.relative_to(root)) for path in seen)


def patch_readout(path: Path, pitch: float) -> None:
    if not math.isfinite(pitch) or pitch <= 0:
        raise ValueError("Pitch must be finite and positive")
    # Current 16 mm B0 sensors need complete Voronoi cells, not half pixels.
    channels = 16 / pitch
    if not 1 <= channels <= 65536 or abs(channels - round(channels)) > 1e-8:
        raise ValueError("Pitch must tile the current 16 mm sensor")
    tree = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    root = tree.getroot()
    readouts = root.findall("./readouts/readout[@name='B0TrackerHits']")
    if len(readouts) != 1:
        raise ValueError("Expected exactly one B0TrackerHits readout")
    segments = readouts[0].findall("segmentation")
    if len(segments) != 1 or segments[0].get("type") != "CartesianGridXY":
        raise ValueError("Expected a CartesianGridXY B0 readout")
    if root.findall("./define/constant[@name='B0ACLGADReadoutVersion']"):
        raise ValueError("Input is already an AC-LGAD profile")
    for axis in ("x", "y"):
        segments[0].set(f"grid_size_{axis}", f"{pitch:.17g}*mm")
        # Cell centers i*pitch+offset. For an even number of cells use half pitch.
        offset = pitch / 2 if round(channels) % 2 == 0 else 0
        segments[0].set(f"offset_{axis}", f"{offset:.17g}*mm")
    define = root.find("define")
    if define is None:
        raise ValueError("B0 definitions are missing")
    ET.SubElement(define, "constant", name="B0ACLGADReadoutVersion", value="1")
    for comment in readouts[0].findall("comment"):
        readouts[0].remove(comment)
    comment = ET.SubElement(readouts[0], "comment")
    comment.text = ("Physical channel pitch for the opt-in b0_aclgad response. "
                    "The pitch is not the reconstructed position resolution. "
                    "Regenerate simulated events; do not reinterpret effective-grid cellIDs.")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def make_profile(source: Path, destination: Path, compact: str, pitch: float,
                 material_map: Path | None = None) -> Path:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if not source.is_dir() or destination.exists() or destination.resolve().is_relative_to(source):
        raise ValueError("Use a fresh output directory outside the source tree")
    contained(source, compact)
    contained(source, str(READOUT))
    xml_closure(source, compact)
    # Explicitly reject external links: silently retaining one can load the old
    # readout/field despite the new profile's filename. Internal links are copied.
    for path in source.rglob("*"):
        if ".git" in path.relative_to(source).parts:
            continue
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(source):
            raise ValueError(f"External symlink in source tree: {path}")
        if path.is_symlink() and path.resolve(strict=True).is_dir():
            raise ValueError(f"Directory symlink is not supported: {path}")
    if material_map is not None:
        material_map = material_map.resolve(strict=True)
        if not material_map.is_file():
            raise ValueError("Material map must be a regular file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".b0-profile-", dir=destination.parent))
    try:
        tree = temporary / "detector"
        shutil.copytree(source, tree, symlinks=False,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        original = sha256(tree / READOUT)
        patch_readout(tree / READOUT, pitch)
        files = {str(p.relative_to(tree)): sha256(p) for p in sorted(tree.rglob("*")) if p.is_file()}
        manifest = {
            "schema_version": 1, "kind": "B0ACLGADReadout", "response_version": 1,
            "readout": "B0TrackerHits", "pitch_mm": pitch,
            "compact": compact, "readout_file": str(READOUT),
            "xml_closure": xml_closure(tree, compact),
            "original_readout_sha256": original, "files": files,
            "simulation_policy": "regenerate-with-this-profile",
            "material_map": None if material_map is None else
                {"path": str(material_map), "sha256": sha256(material_map)},
            "material_note": "Readout-only change; validate sensitive-volume IDs and ACTS mapping before reusing a map.",
        }
        (tree / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        # Directory creation above and this second check prevent accidental overwrite.
        if destination.exists():
            raise FileExistsError(destination)
        tree.rename(destination)
    finally:
        shutil.rmtree(temporary)
    return destination / MANIFEST


def verify_profile(root: Path) -> dict:
    root = root.resolve(strict=True)
    with (root / MANIFEST).open() as stream:
        manifest = json.load(stream)
    if (manifest.get("schema_version"), manifest.get("kind"), manifest.get("response_version")) != \
            (1, "B0ACLGADReadout", 1):
        raise ValueError("Unsupported B0 readout contract")
    files = manifest.get("files", {})
    if not files or manifest.get("compact") not in files or str(READOUT) not in files:
        raise ValueError("Incomplete B0 readout contract")
    for name, digest in files.items():
        if sha256(contained(root, name)) != digest:
            raise ValueError(f"Profile hash mismatch: {name}")
    # Unrecorded files may shadow an include or calibration; refuse rather than
    # treating a partial manifest as proof of a complete profile.
    present = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and str(p.relative_to(root)) != MANIFEST}
    if present != set(files):
        raise ValueError("Profile file set changed")
    if xml_closure(root, manifest["compact"]) != manifest.get("xml_closure"):
        raise ValueError("XML include closure changed")
    material = manifest.get("material_map")
    if material and sha256(Path(material["path"])) != material["sha256"]:
        raise ValueError("Material-map hash mismatch")
    return manifest


def simulate_profile(profile: Path, out: Path, executable: str, arguments: list[str],
                     assets: list[Path]) -> Path:
    """Run npsim/DDsim and bind a successful output to the verified detector profile."""
    profile = profile.resolve(strict=True)
    contract = verify_profile(profile)
    binary = shutil.which(executable)
    if not binary:
        raise ValueError(f"Simulation executable not found: {executable}")
    # Controlled arguments are appended last. Disallow aliases/abbreviations of
    # the compact/output switches and the end-of-options marker as well.
    for arg in arguments:
        option = arg.split("=", 1)[0]
        if arg == "--" or (option.startswith("--") and
            any(full.startswith(option) or option.startswith(full)
                for full in ("--compactFile", "--outputFile"))):
            raise ValueError("Do not override controlled compact/output arguments")
    out = out.absolute()
    if out.resolve().is_relative_to(profile):
        raise ValueError("Simulation output must be outside the immutable profile")
    output = out / "sim.edm4hep.root"
    invocation = [binary, *arguments, "--compactFile", str(profile / contract["compact"]),
                  "--outputFile", str(output)]
    inputs = {str(path.resolve(strict=True)): sha256(path.resolve(strict=True)) for path in assets}
    binary_hash = sha256(Path(binary))
    profile_hash = sha256(profile / MANIFEST)
    receipt = {"schema_version": 1, "kind": "B0ACLGADSimulation", "status": "planned",
               "created_utc": datetime.now(timezone.utc).isoformat(), "command": invocation,
               "profile": str(profile), "profile_sha256": profile_hash,
               "executable": {"path": binary, "sha256": binary_hash}, "assets": inputs,
               "limitations": ["Explicit --asset inputs are hashed; source SHA is not installed-library identity.",
                               "This records execution provenance, not detector-response calibration."]}
    out.mkdir(parents=True, exist_ok=False)
    path = out / "simulation.json"
    def save() -> None:
        path.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    save()
    try:
        receipt["status"] = "running"; save()
        with (out / "simulation.log").open("w") as log:
            subprocess.run(invocation, check=True, stdout=log, stderr=subprocess.STDOUT)
        verify_profile(profile)
        if sha256(profile / MANIFEST) != profile_hash or sha256(Path(binary)) != binary_hash:
            raise ValueError("Simulation inputs changed during execution")
        for name, digest in inputs.items():
            if sha256(Path(name)) != digest:
                raise ValueError(f"Simulation asset changed: {name}")
        receipt["output"] = {"path": str(output), "sha256": sha256(output)}
        receipt["status"] = "completed"; save()
    except Exception as error:
        receipt["status"] = "failed"; receipt["error"] = str(error); save()
        raise
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--compact", required=True, help="Relative top-level compact XML")
    create.add_argument("--pitch-mm", type=float, default=.5)
    create.add_argument("--material-map", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("profile", type=Path)
    simulation = sub.add_parser("simulate")
    simulation.add_argument("--profile", type=Path, required=True)
    simulation.add_argument("--out", type=Path, required=True)
    simulation.add_argument("--executable", default="npsim")
    simulation.add_argument("--asset", type=Path, action="append", default=[])
    simulation.add_argument("--argument", action="append", default=[],
                            help="One literal simulator argument per occurrence, e.g. --argument=--enableGun")
    args = parser.parse_args()
    if args.command == "create":
        print(make_profile(args.source, args.output, args.compact, args.pitch_mm, args.material_map))
    elif args.command == "simulate":
        print(simulate_profile(args.profile, args.out, args.executable, args.argument, args.asset))
    else:
        contract = verify_profile(args.profile)
        print(f"Verified {len(contract['files'])} profile assets")


if __name__ == "__main__":
    main()
