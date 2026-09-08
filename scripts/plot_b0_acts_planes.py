#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Plot an export_b0_acts_planes JSON as a two-page PDF; no geometry is inferred.

Run inside eic-shell:
  python scripts/plot_b0_acts_planes.py surfaces.json assets/b0_acts_planes.pdf \
      --geometry-label "installed B0 XML SHA256: <hash>" --acts-version 47.7.0
Coordinates and rectangle corners come from ACTS transforms. Dashed rings are
approach-disc bounds, not the outlines of physical support plates.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--geometry-label", required=True)
    parser.add_argument("--acts-version", required=True)
    args = parser.parse_args()
    if args.output.suffix.lower() != ".pdf":
        parser.error("The output must be a PDF")
    data = json.loads(args.input.read_text())
    if data["length_unit"] != "mm":
        raise ValueError("Expected millimetres")
    surfaces = data["surfaces"]
    sensors = [s for s in surfaces if s["kind"] == "measurement"]
    approaches = [s for s in surfaces if s["kind"] == "material approach"]
    passives = [s for s in surfaces if s["kind"] == "passive material"]
    layers = sorted({s["layer"] for s in sensors})
    if len(layers) != 8 or len(approaches) != 16:
        raise ValueError("Expected eight B0 faces and sixteen approach discs")
    for s in surfaces:
        s["T"] = np.asarray(s["transform"]).reshape(3, 4)
    # Use the actual first approach's axes. Its z' axis points downstream.
    frame = next(s["T"][:, :3] for s in approaches if s["layer"] == layers[0])
    origin = np.mean([s["T"][:, 3] for s in approaches if s["layer"] in layers[:2]], axis=0)
    angle = np.arctan2(frame[0, 2], frame[2, 2])
    blue, teal, orange, wine = "#2166ac", "#018571", "#c3650d", "#91003f"
    color = {layer: blue if i % 2 == 0 else teal for i, layer in enumerate(layers)}

    def points(s):
        if s["kind"] == "measurement":
            xy = np.array([[-s["a"], -s["b"]], [s["a"], -s["b"]],
                           [s["a"], s["b"]], [-s["a"], s["b"]]])
        else:
            theta = np.linspace(0, 2 * np.pi, 181)
            xy = s["b"] * np.column_stack((np.cos(theta), np.sin(theta)))
        return xy @ s["T"][:, :2].T + s["T"][:, 3]

    def local(p):
        return (p - origin) @ frame

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "pdf.fonttype": 42})
    provenance = (f"{args.geometry_label} | epic_ip6_extended | ACTS {args.acts_version} | "
                  f"layer envelope z = {data['layer_envelope_z_mm']:g} mm")
    legend = [Line2D([0], [0], color=blue, lw=3, label="Front sensor planes"),
              Line2D([0], [0], color=teal, lw=3, label="Back sensor planes"),
              Line2D([0], [0], color=orange, ls="--", label="Material approach surfaces")]
    if passives:
        legend.append(Line2D([0], [0], color=wine, ls=":", lw=2,
                             label="Passive window material"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(args.output, metadata={"Title": "B0 ACTS measurement planes", "Subject": provenance}) as pdf:
        fig = plt.figure(figsize=(12, 10))
        gs = fig.add_gridspec(3, 2, height_ratios=[0.75, 2.7, 1.9], hspace=0.56, wspace=0.38)
        fig.suptitle(f"Where ACTS sees B0: {len(sensors)} sensor planes on four stations", fontsize=18, y=.975)
        ax = fig.add_subplot(gs[0, :])
        for s in sensors:
            p = points(s) / 1000
            ax.plot(p[:, 2], p[:, 0], color=color[s["layer"]], lw=.5)
        for s in passives:
            p = points(s) / 1000
            ax.plot(p[:, 2], p[:, 0], color=wine, lw=1.2, ls=":")
            ax.annotate("Window", (s["T"][2, 3]/1000, .045), ha="center", fontsize=9)
        z = np.linspace(0, 7, 100)
        ax.plot(z, z * np.tan(angle), color=".4", ls=":", label="Outgoing hadron beam reference axis")
        ax.plot(0, 0, "ko", ms=4)
        ax.annotate("IP", (0, 0), xytext=(6, 5), textcoords="offset points")
        ax.annotate("B0", (6.5, -.16), xytext=(0, 15), textcoords="offset points", ha="center")
        ax.set(xlim=(-.15, 7), ylim=(-.43, .15), xlabel="Lab z [m]", ylabel="Lab x [m]")
        ax.set_aspect("equal", adjustable="box")
        ax.text(.43, .94, f"Hadron-axis rotation: {angle * 1000:.1f} mrad", transform=ax.transAxes)
        ax.grid(alpha=.15)
        ax = fig.add_subplot(gs[1, :])
        for s in sensors + approaches:
            p = points(s)
            ax.plot(p[:, 2], p[:, 0], color=color[s["layer"]] if s["kind"] == "measurement" else orange,
                    lw=.55 if s["kind"] == "measurement" else 1.1, ls="--" if s["kind"] == "material approach" else "-", alpha=.8)
        for s in passives:
            p = points(s)
            ax.plot(p[:, 2], p[:, 0], color=wine, lw=1.4, ls=":")
            ax.text(s["T"][2, 3], 60,
                    f"Steel window\nz ≈ {s['T'][2, 3]/1000:.3f} m", ha="center", va="bottom", color=wine)
        z = np.array([5750, 6900])
        ax.plot(z, z * np.tan(angle), ":", color=".4")
        for i in range(4):
            group = [s for s in sensors if s["layer"] in layers[2*i:2*i+2]]
            center = np.mean([s["T"][:, 3] for s in group], axis=0)
            ax.text(center[2], -12, f"Station {i+1}\nz ≈ {center[2]/1000:.3f} m", ha="center", va="bottom")
        ax.set(xlim=(5750, 6900), ylim=(-355, 95 if passives else 25), xlabel="Lab z [mm]", ylabel="Lab x [mm]",
               title="Side view • true aspect ratio; transverse tilt is small but retained")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=.15)
        ax = fig.add_subplot(gs[2, 0])
        first = [s for s in surfaces if s["layer"] in layers[:2]]
        for s in first:
            p = local(points(s))
            ax.plot(p[:, 2], p[:, 0], color=color[s["layer"]] if s["kind"] == "measurement" else orange,
                    lw=.8, ls="-" if s["kind"] == "measurement" else "--")
        ax.set(xlabel="B0-local z′ [mm]", ylabel="B0-local x′ [mm]",
               title="Station 1 axial detail • different axis scales")
        ax.grid(alpha=.15)
        ax = fig.add_subplot(gs[2, 1]); ax.axis("off")
        thickness = sorted({round(s["thickness"], 9) for s in sensors})
        if len(thickness) != 1:
            raise ValueError("Expected a common sensor thickness")
        spreads = [np.ptp([local(s["T"][:, 3])[2] for s in sensors if s["layer"] == layer]) for layer in layers]
        spread_label = f"{min(spreads):.2f}" if np.ptp(spreads) < 1e-6 else f"{min(spreads):.2f}–{max(spreads):.2f}"
        text = ("Solid planes are the silicon midplanes.\n"
                f"Each physical sensor is {thickness[0]*1000:g} μm thick;\n"
                "the ACTS measurement surface has zero thickness.\n\n"
                f"Sensor depth span per face: {spread_label} mm.\n"
                f"Eight sensor layers{' + one passive window layer' if len(passives)==1 else ''}.\n"
                "Two approaches per sensor layer → sixteen discs.\n"
                "Approaches carry mapped material; they are not sensors.\n"
                + ("The dotted disc is the steel exit window at its physical z.\n"
                   if passives else "") +
                "\nThe next page shows all sensor footprints and gaps.\n"
                "Dashed circles are ACTS approach bounds, not supports.")
        ax.text(0, .96, text, va="top", linespacing=1.5)
        fig.legend(handles=legend, loc="lower center", bbox_to_anchor=(.5, .045),
                   ncol=len(legend), frameon=False)
        fig.text(.05, .022, provenance, fontsize=8, color=".35")
        fig.subplots_adjust(top=.90, bottom=.13)
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(2, 4, figsize=(13, 8), sharex=True, sharey=True)
        fig.suptitle("Every ACTS measurement plane, viewed along the downstream axis", fontsize=17, y=.97)
        theta = np.linspace(0, 2*np.pi, 181)
        for i, layer in enumerate(layers):
            ax = axes[i % 2, i // 2]
            group = [s for s in sensors if s["layer"] == layer]
            polys = [local(points(s))[:, :2] for s in group]
            ax.add_collection(PolyCollection(polys, facecolors=color[layer], edgecolors="white", linewidths=.25, alpha=.85))
            a = next(s for s in approaches if s["layer"] == layer)
            for r in (a["a"], a["b"]):
                xy = r * np.column_stack((np.cos(theta), np.sin(theta)))
                p = local(xy @ a["T"][:, :2].T + a["T"][:, 3])
                ax.plot(p[:, 0], p[:, 1], color=orange, ls="--", lw=.85)
            # Show an actual sensor's local measurement basis; do not infer front/back handedness.
            sample = min(group, key=lambda s: np.linalg.norm(local(s["T"][:, 3])[:2] - [-65, 60]))
            p = local(sample["T"][:, 3]); basis = frame.T @ sample["T"][:, :2]
            for k, label in enumerate(("u", "v")):
                end = p[:2] + 24 * basis[:2, k]
                ax.annotate("", end, p[:2], arrowprops={"arrowstyle": "->", "color": "#111", "lw": 1.2})
                ax.text(*end, label, fontsize=9, color="#111", va="bottom")
            zlocal = np.mean([local(s["T"][:, 3])[2] for s in group])
            side = "Front" if i % 2 == 0 else "Back"
            ax.set_title(f"Station {i//2+1} · {side} · layer {layer}\n{len(group)} sensors; mean z′ = {zlocal:.3f} mm", fontsize=9)
            ax.set(xlim=(-155, 155), ylim=(-155, 155), xticks=[-100, 0, 100], yticks=[-100, 0, 100])
            ax.set_aspect("equal"); ax.grid(alpha=.12)
            if i % 2: ax.set_xlabel("B0-local x′ [mm]")
            if i // 2 == 0: ax.set_ylabel("B0-local y′ [mm]")
        fig.text(.5, .105, "All panels use the same axes. u and v show actual local measurement directions on one sensor per face.", ha="center", fontsize=10)
        fig.text(.5, .078, "z′ = 0 is midway between station 1's four approach centres; every panel is an orthographic projection, not an exploded layout.", ha="center", fontsize=9)
        fig.text(.05, .035, provenance, fontsize=8, color=".35")
        fig.subplots_adjust(top=.88, bottom=.16, wspace=.12, hspace=.30)
        pdf.savefig(fig); plt.close(fig)
    print(f"Wrote {args.output}: {len(sensors)} measurement planes, "
          f"{len(approaches)} approach discs, {len(passives)} passive discs")


if __name__ == "__main__":
    main()
