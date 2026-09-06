#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Paired B0 material audit using ACTS 47.7 and recorded Geant4 ray directions.

Run inside eic-shell-acts47. The companion C++ executable independently traces
the current TGeo geometry, intersects ACTS material surfaces, and navigates ACTS.
Existing Geant4 steps cross-check TGeo through z=8 m; downstream beam-dependent
Roman-pot changes make the old scan unsuitable as whole-detector 5x41 truth.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import acts
import awkward as ak
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import uproot


def integral(steps, low, high, point_steps=False):
    """Integrate slabs at their ACTS position or finite truth step segments."""
    total = 0.0
    for step in steps:
        if point_steps:
            if low <= step["position"][2] < high:
                total += step["x0"]
        else:
            start, end = sorted((step["z0"], step["z1"]))
            overlap = max(0.0, min(end, high) - max(start, low))
            if end > start:
                total += step["x0"] * overlap / (end - start)
    return total


def summarize(values):
    array = np.asarray(values)
    return {"mean": float(array.mean()), "median": float(np.median(array)),
            "p95": float(np.percentile(array, 95)), "max": float(array.max())}


def geometry_hashes(xml):
    """Fingerprint the actual compact include chain used by the probe."""
    files = {}
    pending = [xml.resolve()]
    while pending:
        path = pending.pop()
        if str(path) in files:
            continue
        content = path.read_bytes()
        files[str(path)] = hashlib.sha256(content).hexdigest()
        for node in ET.fromstring(content).iter("include"):
            if "ref" not in node.attrib:
                continue
            reference = Path(os.path.expandvars(node.attrib["ref"]))
            pending.append((reference if reference.is_absolute() else path.parent / reference).resolve())
    return dict(sorted(files.items()))


def verify_recording(truth, entries, xml, includes, directions_only=False):
    """Check ROOT identity and compact XML; compiled geometry needs a separate audit."""
    with truth.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"root_sha256": digest, "material_geometry_verified": False,
              "geometry_verification_scope": "Recursive compact XML hashes; excludes compiled plugins and external resources"}
    if directions_only:
        return result
    sidecar = Path(str(truth) + ".json")
    if not sidecar.is_file():
        raise RuntimeError("Material truth requires the record_b0_material.py JSON sidecar; "
                           "record a matched scan or use --directions-only for a TGeo audit")
    raw = sidecar.read_bytes()
    recording = json.loads(raw)
    if tuple(recording.get("acts", ())) != (47, 7, 0):
        raise RuntimeError("Recorded material must identify ACTS 47.7.0")
    if recording.get("output_sha256") != digest:
        raise RuntimeError("Recorded material ROOT checksum differs from its provenance")
    if recording.get("recorded_entries") != entries:
        raise RuntimeError("Recorded material entry count differs from its provenance")

    def relative_hashes(hashes, entrypoint):
        entrypoint = Path(entrypoint).resolve()
        normalized = {}
        for filename, checksum in hashes.items():
            path = Path(filename)
            if path == entrypoint:
                key = "<entrypoint>"
            elif path.is_relative_to(entrypoint.parent):
                key = str(path.relative_to(entrypoint.parent))
            else:
                key = str(path)
            normalized[key] = checksum
        return normalized

    recorded = relative_hashes(recording["geometry_include_sha256"], recording["xml"])
    current = relative_hashes(includes, xml)
    if recorded != current:
        different = sorted(key for key in recorded.keys() | current.keys()
                           if recorded.get(key) != current.get(key))
        raise RuntimeError(f"Recorded material geometry differs in compact files: {different}")
    result.update(material_geometry_verified=True, sidecar=str(sidecar.resolve()),
                  sidecar_sha256=hashlib.sha256(raw).hexdigest(), recording=recording)
    return result


def prepare(tree, args):
    arrays = tree.arrays(["v_x", "v_y", "v_z", "v_px", "v_py", "v_pz", "v_eta"],
                         entry_start=args.first_input_entry,
                         entry_stop=args.first_input_entry + args.max_input_entries, library="np")
    selected = np.flatnonzero((arrays["v_eta"] > 4.) & (arrays["v_eta"] < 6.))
    selected = selected[:args.candidate_limit]
    rays = [{"entry": int(i + args.first_input_entry), "eta": float(arrays["v_eta"][i]),
             "origin": [float(arrays[f"v_{c}"][i]) for c in "xyz"],
             "direction": [float(arrays[f"v_p{c}"][i]) for c in "xyz"]}
            for i in selected]
    if not rays:
        raise RuntimeError("No recorded rays in 4 < eta < 6")
    return rays


def analyze(probe, tree, output, provenance):
    rays = probe["rays"]
    # One contiguous read is considerably cheaper than one basket read per ray.
    first, last = min(r["entry"] for r in rays), max(r["entry"] for r in rays)
    directions_only = provenance.get("directions_only", False)
    truth = None if directions_only else tree.arrays(
        ["mat_z", "mat_dz", "mat_step_length", "mat_X0"],
        entry_start=first, entry_stop=last + 1, library="ak")
    samples = {name: {kind: [] for kind in ("tgeo", "intersection", "navigation")}
               for name in ("before_first", "between_first_last", "last_to_envelope_exit", "after_last_to_8m",
                            "downstream_8m", "through_last")}
    crosscheck = {"geant4": [], "tgeo": [], "geant4_through_last": [], "tgeo_through_last": []}
    navigation_failures, missing_hits, ray_reports = [], [], []
    downstream_material = {}
    for ray in rays:
        index = ray["entry"] - first
        row = truth[index] if truth is not None else None
        # MaterialSteppingAction records the pre-step position. The historical
        # writer's mat_s*/mat_e* branches assume a midpoint and cannot be used.
        geant4 = [{"z0": float(z0), "z1": float(z0 + dz * length), "x0": float(length / x0)}
                  for z0, dz, length, x0 in zip(row["mat_z"], row["mat_dz"],
                                              row["mat_step_length"], row["mat_X0"])
                  if x0 > 0] if row is not None else []
        crosscheck["geant4"].append(integral(geant4, 0., 8000.))
        crosscheck["tgeo"].append(integral(ray["tgeo"], 0., 8000.))
        zfirst, zlast = ray["hits"][0]["position"][2], ray["hits"][-1]["position"][2]
        crosscheck["geant4_through_last"].append(integral(geant4, 0., zlast))
        crosscheck["tgeo_through_last"].append(integral(ray["tgeo"], 0., zlast))
        intervals = {"before_first": (0., zfirst),
                     "between_first_last": (zfirst, zlast),
                     "last_to_envelope_exit": (zlast, ray["envelope_exit_z"]),
                     "after_last_to_8m": (zlast, 8000.),
                     "downstream_8m": (8000., float("inf")),
                     "through_last": (0., zlast)}
        if "navigation_error" in ray:
            navigation_failures.append({"entry": ray["entry"], "error": ray["navigation_error"]})
        expected = {hit["id"] for hit in ray["hits"]}
        visited = set(ray.get("navigated_sensor_ids", []))
        if expected - visited:
            missing_hits.append({"entry": ray["entry"], "missing": sorted(expected - visited)})
        values = {}
        for interval, (low, high) in intervals.items():
            values[interval] = {}
            for kind in samples[interval]:
                value = integral(ray.get(kind, []), low, high, point_steps=kind != "tgeo")
                samples[interval][kind].append(value)
                values[interval][kind] = value
        for step in ray["tgeo"]:
            if step["z0"] >= zlast:
                downstream_material[step["path"]] = downstream_material.get(step["path"], 0.) + step["x0"]
        ray_reports.append({"entry": ray["entry"], "eta": ray["eta"],
                            "stations": sorted({hit["station"] for hit in ray["hits"]}),
                            "measurements": len(ray["hits"]), "intervals": values})
    statistics = {name: {kind: summarize(values) for kind, values in kinds.items()}
                  for name, kinds in samples.items()}
    # These are declared engineering acceptance tolerances, not ACTS standards.
    # Binned slabs redistribute thin material, so enforce aggregate closure and
    # independently reject large per-ray material excesses between measurements.
    closure = {}
    for name in ("before_first", "between_first_last", "last_to_envelope_exit"):
        actual = np.asarray(samples[name]["navigation"])
        expected = np.asarray(samples[name]["tgeo"])
        mean_ratio = float(actual.mean() / expected.mean()) if expected.mean() > 0 else None
        catastrophic = (actual > 5. * expected) & (actual - expected > .1)
        # Unpopulated fine bins can hide behind an acceptable aggregate mean.
        missing = (actual <= 1e-6) & (expected > .01)
        mean_ok = (abs(actual.mean() - expected.mean()) <= max(.001, .25 * expected.mean()))
        closure[name] = {"mean_map_over_truth": mean_ratio,
                         "mean_within_25_percent_or_0p001_X0": bool(mean_ok),
                         "rays_with_zero_map_and_truth_over_0p01_X0":
                             [rays[i]["entry"] for i in np.flatnonzero(missing)],
                         "rays_over_5_times_truth_with_excess_over_0p1_X0":
                             [rays[i]["entry"] for i in np.flatnonzero(catastrophic)]}
    material_ok = (
        all(value["mean_within_25_percent_or_0p001_X0"] and
            not value["rays_with_zero_map_and_truth_over_0p01_X0"] for value in closure.values()) and
        all(not closure[name]["rays_over_5_times_truth_with_excess_over_0p1_X0"]
            for name in ("between_first_last", "last_to_envelope_exit")))
    g4, tg = np.array(crosscheck["geant4"]), np.array(crosscheck["tgeo"])
    delta = tg - g4
    g4_last = np.asarray(crosscheck["geant4_through_last"])
    tg_last = np.asarray(crosscheck["tgeo_through_last"])
    # TGeo includes dilute air skipped by the Geant4 recording. Allow that
    # small absolute budget without accepting large conversion discrepancies.
    truth_difference = float(np.mean(np.abs(g4_last - tg_last)))
    truth_tolerance = max(.01, .05 * float(tg_last.mean()))
    truth_ok = None if directions_only else truth_difference <= truth_tolerance
    preflight = probe["preflight"]
    preflight_ok = (preflight["duplicate_detector_ids"] == 0 and
                    all(layer["planar_approaches"] == layer["mapped_planar_approaches"] and
                        layer["sensor_samples_outside"] == 0 and
                        layer["approach_samples_outside"] == 0 for layer in preflight["layers"]))
    report = {"provenance": provenance, "accepted_rays": len(rays),
              "candidate_rays_examined": probe["examined"], "preflight": preflight,
              "geometry_preflight_passed": preflight_ok,
              "material_closure_passed": material_ok, "material_closure": closure,
              "geant4_tgeo_closure_passed": truth_ok,
              "navigation_failures": navigation_failures, "missing_sensor_intersections": missing_hits,
              "statistics_X_over_X0": statistics,
              "geant4_tgeo_crosscheck_z_below_8m": {
                  "geant4_mean": float(g4.mean()), "tgeo_mean": float(tg.mean()),
                  "mean_difference": float(delta.mean()),
                  "median_absolute_difference": float(np.median(np.abs(delta))),
                  "p95_absolute_difference": float(np.percentile(np.abs(delta), 95))},
              "geant4_tgeo_crosscheck_through_last_sensor": {
                  "geant4_mean": float(np.mean(crosscheck["geant4_through_last"])),
                  "tgeo_mean": float(np.mean(crosscheck["tgeo_through_last"])),
                  "mean_absolute_difference": truth_difference,
                  "tolerance_X0": truth_tolerance},
              "largest_physical_material_contributions_after_last_hit":
                  [{"path": path, "mean_X_over_X0": value / len(rays)}
                   for path, value in sorted(downstream_material.items(), key=lambda pair: -pair[1])[:20]],
              "rays": ray_reports}
    if directions_only:
        report["geant4_tgeo_crosscheck_z_below_8m"] = None
        report["geant4_tgeo_crosscheck_through_last_sensor"] = None
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    plot(samples, rays, output / "assets" / "b0_material_closure.pdf", provenance)
    for name, stats in statistics.items():
        print(name, "mean X/X0", {kind: round(stat["mean"], 6) for kind, stat in stats.items()})
    print("Geant4/TGeo cross-check", report["geant4_tgeo_crosscheck_z_below_8m"])
    print("Geometry preflight:", preflight_ok, "navigation errors:", len(navigation_failures),
          "rays missing sensor intersections:", len(missing_hits))
    print("Material closure:", material_ok,
          {name: {key: len(value) if isinstance(value, list) else value
                  for key, value in criteria.items()} for name, criteria in closure.items()})
    print("Geant4/TGeo closure:", truth_ok)
    return 0 if (preflight_ok and material_ok and truth_ok is not False and
                 not navigation_failures and not missing_hits) else 1


def plot(samples, rays, target, provenance):
    target.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(12., 4.2), sharex=True, sharey=True)
    intervals = ("before_first", "between_first_last", "last_to_envelope_exit")
    largest = max(max(values) for name in intervals for values in samples[name].values())
    limit = 10. ** np.ceil(np.log10(max(largest * 1.1, .01)))
    for ax, interval, title in zip(axes, intervals,
                                   ("Before first sensor", "Between first and last sensors", "Last sensor to tracking-volume exit")):
        values = samples[interval]
        ax.scatter(values["tgeo"], values["intersection"], s=15, color="#c66b21",
                   marker="x", label="Direct ACTS intersections")
        ax.scatter(values["tgeo"], values["navigation"], s=16, facecolors="none",
                   edgecolors="#23649c", label="ACTS navigation")
        ax.plot([0., limit], [0., limit], color="0.5", lw=1)
        ax.set(xscale="symlog", yscale="symlog", xlim=(0., limit), ylim=(0., limit), title=title,
               xlabel=r"Current DD4hep/TGeo truth $X/X_0$")
        ax.set_xscale("symlog", linthresh=.001)
        ax.set_yscale("symlog", linthresh=.001)
        ax.grid(alpha=.2)
    axes[0].set_ylabel(r"Mapped $X/X_0$")
    axes[1].legend(loc="upper left", fontsize=8)
    fig.suptitle(f"ePIC B0 | {provenance['geometry_config']} | {provenance['field_config']} | ACTS 47.7\n"
                 f"{len(rays)} paired geantino rays; "
                 "4 < η < 6; ≥3 stations intersected; straight rays, no magnetic deflection", fontsize=10)
    fig.tight_layout()
    fig.savefig(target)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--truth", type=Path, required=True, help="Recorded Geant4 material-tracks ROOT file")
    p.add_argument("--probe", type=Path, required=True, help="Built b0-material-probe executable")
    p.add_argument("--xml", type=Path, default=Path(os.environ.get("DETECTOR_PATH", ".")) / "epic_ip6_extended_5x41.xml")
    p.add_argument("--map", dest="material_map", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sample-size", type=int, default=100)
    p.add_argument("--max-input-entries", type=int, default=100000)
    p.add_argument("--first-input-entry", type=int, default=0, help="Use a disjoint range for held-out validation")
    p.add_argument("--candidate-limit", type=int, default=5000)
    p.add_argument("--padding-mm", type=float, default=5.)
    p.add_argument("--directions-only", action="store_true", help="Use recorded origins/directions only; do not compare another geometry's Geant4 material")
    p.add_argument("--remap", action="store_true", help="Build candidate.cbor using --map binning before held-out validation")
    p.add_argument("--training-entries", type=int, default=100000, help="First N recorded entries used for remapping")
    args = p.parse_args()
    if min(args.sample_size, args.max_input_entries, args.candidate_limit, args.training_entries) <= 0:
        p.error("Sample size, entry limits, and training count must be positive")
    if args.first_input_entry < 0 or not np.isfinite(args.padding_mm) or args.padding_mm <= 0:
        p.error("First entry must be nonnegative and padding must be finite and positive")
    if tuple(acts.__version__) != (47, 7, 0):
        p.error("This validation requires the ACTS 47.7.0 environment")
    if args.xml.name not in ("epic_ip6_extended_5x41.xml", "epic_ip6_extended.xml"):
        p.error("Use the original epic_ip6_extended or epic_ip6_extended_5x41 configuration")
    if args.remap and args.directions_only:
        p.error("Remapping requires material recorded in the matching geometry")
    args.output.mkdir(parents=True, exist_ok=True)
    tree = uproot.open(args.truth)["material-tracks"]
    if args.first_input_entry >= tree.num_entries:
        p.error("Validation range starts beyond the recorded tree")
    includes = geometry_hashes(args.xml)
    recording = verify_recording(args.truth, tree.num_entries, args.xml, includes,
                                 args.directions_only)
    training = None
    binning_map = {"path": str(args.material_map.resolve()),
                   "sha256": hashlib.sha256(args.material_map.read_bytes()).hexdigest()}
    if args.remap:
        if args.training_entries >= tree.num_entries:
            p.error("Leave a disjoint recorded range for held-out validation")
        candidate = args.output / "candidate.cbor"
        with (args.output / "remap.log").open("w") as log:
            subprocess.run([str(args.probe.resolve()), str(args.xml.resolve()),
                            str(args.material_map.resolve()), str(args.truth.resolve()),
                            str(candidate.resolve()), "0", str(args.training_entries), str(args.padding_mm)],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        training = json.loads(Path(str(candidate) + ".json").read_text())
        training["binning_map"] = binning_map
        training["recorded_geant4_sha256"] = recording["root_sha256"]
        Path(str(candidate) + ".json").write_text(json.dumps(training, indent=2) + "\n")
        args.material_map = candidate
        args.first_input_entry = max(args.first_input_entry, args.training_entries)
    elif Path(str(args.material_map) + ".json").is_file():
        training = json.loads(Path(str(args.material_map) + ".json").read_text())
        if training.get("recorded_geant4_sha256") == recording["root_sha256"]:
            args.first_input_entry = max(args.first_input_entry,
                                         training["first_entry"] + training["entries"])
    rays = prepare(tree, args)
    input_path, probe_path = args.output / "rays.json", args.output / "probe.json"
    input_path.write_text(json.dumps(rays) + "\n")
    with (args.output / "probe.log").open("w") as log:
        subprocess.run([str(args.probe.resolve()), str(args.xml.resolve()),
                        str(args.material_map.resolve()), str(input_path.resolve()),
                        str(probe_path.resolve()), str(args.sample_size), str(args.padding_mm)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    field_configs = [Path(path).name for path in includes if re.fullmatch(r"beamline_\d+x\d+\.xml", Path(path).name)]
    if len(field_configs) != 1:
        raise RuntimeError(f"Expected one beam-field configuration, found {field_configs}")
    provenance = {"acts": list(acts.__version__), "xml": str(args.xml.resolve()),
                  "geometry_config": args.xml.stem, "field_config": field_configs[0],
                  "directions_only": args.directions_only,
                  "geometry_include_sha256": includes,
                  "probe_sha256": hashlib.sha256(args.probe.read_bytes()).hexdigest(),
                  "material_map": str(args.material_map.resolve()),
                  "map_sha256": hashlib.sha256(args.material_map.read_bytes()).hexdigest(),
                  "recorded_geant4": str(args.truth.resolve()),
                  "recorded_geant4_size": args.truth.stat().st_size,
                  "recorded_geant4_entries": tree.num_entries,
                  "recording_provenance": recording,
                  "validation_first_input_entry": args.first_input_entry,
                  "padding_mm": args.padding_mm,
                  "acceptance": "4 < eta < 6; ray intersects sensitive sensors in at least 3 B0 stations",
                  "quantiles": "numpy linear; JSON full floating precision; console rounded to 6 decimals",
                  "scope": "Straight-line material/navigation closure, not charged-track fit validation",
                  "truth_reuse": ("Recorded origins/directions only; material truth from current TGeo geometry" if args.directions_only else
                      "Recorded Geant4 scan cross-check restricted to z < 8 m; current TGeo geometry used throughout")}
    provenance["training"] = training
    if training:
        provenance["training_validation_disjoint"] = (
            args.first_input_entry >= training["first_entry"] + training["entries"]
            if training.get("recorded_geant4_sha256") == recording["root_sha256"] else None)
    provenance["training_binning_map"] = training.get("binning_map") if training else None
    provenance["material_closure_criteria"] = (
        "Mean mapped/truth within max(25% of truth, 0.001 X0) before first, between sensors, "
        "and from last sensor to tracking-volume exit; no zero-map ray with truth >0.01 X0; "
        "no between-sensor or last-to-exit ray with >5x truth and >0.1 X0 excess. "
        "For matched Geant4 truth, mean absolute per-ray difference from TGeo through the "
        "last sensor must be <=max(0.01 X0, 5% of mean TGeo truth). "
        "Engineering tolerances, not ACTS standards.")
    probe = json.loads(probe_path.read_text())
    if len(probe["rays"]) < args.sample_size:
        raise RuntimeError(f"Only {len(probe['rays'])} accepted rays for requested "
                           f"sample of {args.sample_size}; increase the candidate/input limits")
    provenance["requested_sample_size"] = args.sample_size
    return analyze(probe, tree, args.output, provenance)


if __name__ == "__main__":
    raise SystemExit(main())
