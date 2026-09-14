"""Export current ACTS geometry and plot B0 material, surfaces, and volume bounds."""
import argparse
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import acts
import acts.examples.dd4hep
from acts.examples.json import JsonMaterialWriter, JsonFormat
from acts.json import MaterialMapJsonConverter
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import SymLogNorm


def slab_values(surface):
    material = surface['value']['material']
    if material['type'] != 'binned':
        raise ValueError('Expected mapped binned approach material')
    values = np.array([[s['thickness']/s['material'][0] if s['material'] else 0.
                        for s in row] for row in material['data']])
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Invalid material values')
    return values


def edges(axis):
    if axis['type'] == 'equidistant':
        return np.linspace(axis['min'], axis['max'], axis['bins']+1)
    return np.asarray(axis['boundaries'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xml', required=True, type=Path)
    parser.add_argument('--map', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--material-max', type=float)
    parser.add_argument('--geometry-json', type=Path, help='Replot a previously exported geometry without rebuilding it')
    args = parser.parse_args()
    args.xml, args.map, args.output = [p.resolve() for p in (args.xml, args.map, args.output)]
    if not any(node.get('ref','').endswith('/fields/beamline_5x41.xml') for node in ET.parse(args.xml).iter('include')):
        parser.error('This B0 validation requires an explicit 5x41 beamline field include')
    version = (acts.version.major, acts.version.minor, acts.version.patch)
    if version != (44,4,0):
        raise RuntimeError('Use bundled ACTS 44.4.0')
    args.output.mkdir(parents=True, exist_ok=True)
    geometry_json = args.geometry_json.resolve() if args.geometry_json else args.output/'geometry-map.json'
    os.chdir(args.output)
    if args.geometry_json is None:
        cfg = acts.examples.dd4hep.DD4hepDetector.Config()
        cfg.xmlFileNames = [str(args.xml)]
        cfg.envelopeR, cfg.envelopeZ = 1., 5.
        cfg.logLevel = cfg.dd4hepLogLevel = acts.logging.WARNING
        cfg.materialDecorator = acts.IMaterialDecorator.fromFile(str(args.map))
        detector = acts.examples.dd4hep.DD4hepDetector(cfg)
        converter = MaterialMapJsonConverter.Config(processNonMaterial=True,
            processSensitives=True, processApproaches=True, processRepresenting=True,
            processBoundaries=True, processVolumes=True, context=acts.GeometryContext())
        JsonMaterialWriter(level=acts.logging.WARNING, converterCfg=converter,
            fileName='geometry-map', writeFormat=JsonFormat.Json).write(detector.trackingGeometry())
    else:
        previous = json.loads(Path('surface-statistics.json').read_text())
        if previous['map_sha256'] != hashlib.sha256(args.map.read_bytes()).hexdigest():
            raise ValueError('Cached geometry belongs to another map')
        provenance = json.loads(Path(str(args.map)+'.provenance.json').read_text())
        if provenance['xml_sha256'] != hashlib.sha256(args.xml.read_bytes()).hexdigest():
            raise ValueError('Current XML differs from the cached map geometry')
    data = json.loads(geometry_json.read_text())
    volumes = [e['volume'] for e in data['Volumes']['entries']
               if e['value']['NAME'] == 'B0TrackerSubAssembly::PositiveEndcap']
    if len(volumes) != 1:
        raise RuntimeError('Expected one B0 tracking volume')
    surfaces = [e for e in data['Surfaces']['entries'] if e.get('volume') == volumes[0]]
    discs = sorted([e for e in surfaces if 'approach' in e and e['value']['type']=='DiscSurface'],
                   key=lambda e:(e['layer'],e['approach']))
    if len(discs) != 16:
        raise RuntimeError('Current B0 geometry must have 16 planar approach surfaces')
    stats = [dict(layer=e['layer'], approach=e['approach'], bins=slab_values(e).size,
                  nonzero=int(np.count_nonzero(slab_values(e))),
                  median=float(np.median(slab_values(e))),
                  p95=float(np.quantile(slab_values(e),.95)), maximum=float(slab_values(e).max()))
             for e in discs]
    maximum = max(s['maximum'] for s in stats)
    limit = args.material_max or max(maximum,.001)
    if limit < maximum:
        raise ValueError('--material-max would clip material')
    norm = SymLogNorm(linthresh=.001,vmin=0,vmax=limit)
    assets = Path('assets'); assets.mkdir(exist_ok=True)
    title = f'EPIC B0 | {args.xml.stem} | 5×41 GeV geometry | ACTS 44.4.0'
    fig, axes = plt.subplots(4,4,figsize=(16,11),layout='constrained',sharex=True,sharey=True)
    for ax, entry in zip(axes.flat,discs):
        bins = entry['value']['material']['binUtility']['binningdata']
        if [b['value'] for b in bins] != ['AxisPhi','AxisR']:
            raise ValueError('Expected phi/r approach-map axes')
        im=ax.pcolormesh(np.degrees(edges(bins[0])),edges(bins[1]),slab_values(entry),
                         norm=norm,cmap='viridis',rasterized=True)
        ax.set_title(f"Layer {entry['layer']} / approach {entry['approach']}")
    fig.supxlabel('Surface-local φ [deg]'); fig.supylabel('Surface-local r [mm]')
    fig.colorbar(im,ax=axes,label='Normal-incidence X/X₀',shrink=.8)
    fig.suptitle(title)
    fig.savefig(assets/'b0_material_surfaces.pdf'); plt.close(fig)

    fig, axes = plt.subplots(2,1,figsize=(13,8),layout='constrained')
    seen=set()
    for entry in surfaces:
        value=entry['value']; transform=value['transform']
        translation=np.array(transform['translation'] or [0,0,0])
        rotation=np.array(transform['rotation']).reshape(3,3,order='F') if transform['rotation'] else np.eye(3)
        if 'sensitive' in entry:
            axes[0].plot(translation[2],translation[0],'.',c='0.6',ms=2)
            axes[1].plot(translation[2],np.hypot(*translation[:2]),'.',c='0.6',ms=2)
        if 'approach' not in entry or value['type']!='DiscSurface': continue
        for radius in value['bounds']['values'][:2]:
            phi=np.linspace(-np.pi,np.pi,200)
            points=rotation@np.array([radius*np.cos(phi),radius*np.sin(phi),np.zeros_like(phi)])+translation[:,None]
            label=f"Approach {entry['approach']}" if entry['approach'] not in seen else None
            color='tab:blue' if entry['approach']==1 else 'tab:orange'
            axes[0].plot(points[2],points[0],c=color,lw=.6,label=label)
            axes[1].plot(points[2],np.hypot(points[0],points[1]),c=color,lw=.6)
            seen.add(entry['approach'])
    axes[0].legend(); axes[0].set_ylabel('Global x [mm]')
    axes[1].set(ylabel='Global R [mm]',xlabel='Global z [mm]')
    for ax in axes: ax.grid(alpha=.2)
    fig.suptitle(title+'\nTransformed approach-disc rims and sensor centers (gray)')
    fig.savefig(assets/'b0_acts_geometry.pdf'); plt.close(fig)
    fig, axes = plt.subplots(1,2,figsize=(13,5),layout='constrained')
    phi=np.linspace(-np.pi,np.pi,200)
    for entry in surfaces:
        if 'boundary' not in entry: continue
        value=entry['value']; bounds=value['bounds']['values']; transform=value['transform']
        translation=np.array(transform['translation'] or [0,0,0])
        rotation=np.array(transform['rotation']).reshape(3,3,order='F') if transform['rotation'] else np.eye(3)
        if value['type']=='DiscSurface': rings=[(radius,0.) for radius in bounds[:2]]
        elif value['type']=='CylinderSurface': rings=[(bounds[0],z) for z in [-bounds[1],bounds[1]]]
        else: continue
        for radius,z in rings:
            points=rotation@np.array([radius*np.cos(phi),radius*np.sin(phi),np.full_like(phi,z)])+translation[:,None]
            axes[0].plot(points[2],points[0],c='tab:blue',lw=.7)
            axes[1].plot(points[2],np.hypot(points[0],points[1]),c='tab:blue',lw=.7)
        # A circular rim projects to a point in R-z. Connect the two rims
        # along fixed azimuths so cylindrical sides and annular end faces
        # remain visible in both orthographic projections.
        for angle in (0., np.pi/2, np.pi, 3*np.pi/2):
            local=np.array([[radius*np.cos(angle),radius*np.sin(angle),z]
                            for radius,z in rings]).T
            points=rotation@local+translation[:,None]
            axes[0].plot(points[2],points[0],c='tab:blue',lw=.7)
            axes[1].plot(points[2],np.hypot(points[0],points[1]),c='tab:blue',lw=.7)
    axes[0].set_ylabel('Global x [mm]'); axes[1].set_ylabel('Global R [mm]')
    for ax in axes: ax.set_xlabel('Global z [mm]'); ax.grid(alpha=.2)
    fig.suptitle(title+'\nB0 tracking-volume boundary outlines (global coordinates)'
                 '\nShared inner-cylinder boundary extends into preceding volumes')
    fig.savefig(assets/'b0_acts_volume.pdf'); plt.close(fig)
    report=dict(xml=str(args.xml),map=str(args.map),map_sha256=hashlib.sha256(args.map.read_bytes()).hexdigest(),
                acts=version,volume=volumes[0],sensitive_surfaces=sum('sensitive' in e for e in surfaces),surface_stats=stats)
    Path('surface-statistics.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
