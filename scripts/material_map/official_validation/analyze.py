"""Analyze identical recorded rays, ACTS44 navigation, and independent TGeo steps."""
import argparse
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
import acts
from pathlib import Path
import awkward as ak
import numpy as np
import uproot
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ACTS_VERSION = (acts.version.major, acts.version.minor, acts.version.patch)

def integrate(z0, z1, weight, lo, hi):
    dz = z1-z0
    return float(np.sum(weight*np.divide(np.maximum(0, np.minimum(z1,hi)-np.maximum(z0,lo)), dz,
                                        out=np.zeros_like(dz), where=dz>0)))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode', choices=['prepare','analyze','run'])
    p.add_argument('--truth', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--config', default=None)
    p.add_argument('--xml', required=True, type=Path)
    p.add_argument('--map', required=True, type=Path)
    p.add_argument('--first-entry', type=int, default=4000000)
    p.add_argument('--max-input-entries', type=int, default=200000)
    p.add_argument('--sample-size', type=int, default=1000)
    p.add_argument('--training-first-entry', type=int)
    p.add_argument('--training-entry-count', type=int)
    p.add_argument('--truth-provenance', type=Path)
    p.add_argument('--axis-max', type=float)
    p.add_argument('--padding-mm', type=float, default=5.)
    p.add_argument('--require-material-validation', action='store_true')
    p.add_argument('--probe', type=Path)
    a=p.parse_args()
    a.config = a.config or a.xml.stem
    if ACTS_VERSION != (44,4,0): p.error('This recorder convention is validated only for bundled ACTS 44.4.0')
    for key in ['truth','xml','map','output','probe']:
        value=getattr(a,key)
        if value is not None: setattr(a,key,value.resolve())
    if not any(node.get('ref','').endswith('/fields/beamline_5x41.xml') for node in ET.parse(a.xml).iter('include')):
        p.error('This B0 validation requires an explicit 5x41 beamline field include')
    if a.first_entry<0 or min(a.max_input_entries,a.sample_size)<=0: p.error('Entry range and sample size must be positive')
    a.output.mkdir(parents=True,exist_ok=True)
    tree=uproot.open(a.truth)['material-tracks']
    if a.first_entry>=tree.num_entries: p.error('--first-entry is past the truth tree')
    if a.mode in ('prepare','run'):
        v=tree.arrays(['v_x','v_y','v_z','v_px','v_py','v_pz','v_eta'],entry_start=a.first_entry,entry_stop=a.first_entry+a.max_input_entries,library='np')
        idx=np.flatnonzero((v['v_eta']>4)&(v['v_eta']<6))
        rays=[dict(entry=int(a.first_entry+i),eta=float(v['v_eta'][i]),origin=[float(v['v_'+c][i]) for c in 'xyz'],
                   direction=[float(v['v_p'+c][i]) for c in 'xyz']) for i in idx]
        if len(rays)<a.sample_size: raise RuntimeError('Too few eta-selected candidate rays for requested sample')
        (a.output/'rays.json').write_text(json.dumps(rays))
        print('candidate rays',len(rays))
        if a.mode=='prepare': return
        if a.probe is None: p.error('--probe executable required for run')
        with (a.output/'probe.log').open('w') as log:
            subprocess.run([str(a.probe),str(a.xml),str(a.map),str(a.output/'rays.json'),str(a.output/'probe.json'),str(a.sample_size),str(a.padding_mm)],cwd=a.output,stdout=log,stderr=subprocess.STDOUT,check=True)
        a.probe=a.output/'probe.json'
    probe=json.loads(a.probe.read_text()); rays=probe['rays']
    if len(rays)<a.sample_size: raise RuntimeError(f'Only {len(rays)} accepted rays; requested {a.sample_size}')
    rays=rays[:a.sample_size]
    first=min(r['entry'] for r in rays); last=max(r['entry'] for r in rays)
    data=tree.arrays(['mat_z','mat_dz','mat_step_length','mat_X0','v_x','v_y','v_z','v_px','v_py','v_pz'],entry_start=first,entry_stop=last+1,library='ak')
    names=['before_first','between_first_last','last_to_exit']
    rows=[]
    for r in rays:
        d=data[r['entry']-first]
        assert np.allclose([float(d['v_'+c]) for c in 'xyz'],r['origin'],rtol=0,atol=1e-9)
        assert np.allclose([float(d['v_p'+c]) for c in 'xyz'],r['direction'],rtol=0,atol=1e-9)
        z0=ak.to_numpy(d['mat_z']); length=ak.to_numpy(d['mat_step_length'])
        z1=z0+ak.to_numpy(d['mat_dz'])*length; x0=ak.to_numpy(d['mat_X0'])
        if not all(np.isfinite(v).all() for v in (z0,z1,length,x0)) or np.any(z1<z0) or np.any(length<0) or np.any(x0<=0):
            raise ValueError('Nonfinite, reversed, negative-length, or nonpositive-X0 recorded material step')
        w=np.divide(length,x0,out=np.zeros_like(length),where=x0>0)
        bounds=[r['origin'][2],r['hits'][0]['position'][2],r['hits'][-1]['position'][2],r['envelope_exit_z']]
        assert np.isfinite(bounds).all() and np.all(np.diff(bounds)>0), bounds
        row={'entry':r['entry'],'eta':r['eta'],'stations':sorted(set(h['station'] for h in r['hits'])),
             'bounds_z_mm':bounds,'intervals':{},'navigation_error':r.get('navigation_error'),
             'missing_sensor_ids':sorted(set(h['id'] for h in r['hits'])-set(r.get('navigated_sensor_ids',[])))}
        row['post_envelope_X0']={kind:sum(s['x0'] for s in r.get(kind,[]) if s['position'][2]>r['envelope_exit_z']+1e-6) for kind in ['navigation','intersection']}
        measurement_z=sorted(set(h['position'][2] for h in r['hits']))
        row['measurement_gaps']=[]
        for lo,hi in zip(measurement_z[:-1],measurement_z[1:]):
            row['measurement_gaps'].append({'z_mm':[lo,hi],'geant4':integrate(z0,z1,w,lo,hi), **{kind:sum(s['x0'] for s in r.get(kind,[]) if lo<=s['position'][2]<hi) for kind in ['navigation','intersection']}})
        for name,lo,hi in zip(names,bounds[:-1],bounds[1:]):
            vals={'geant4':integrate(z0,z1,w,lo,hi)}
            tg=r['tgeo']; vals['tgeo']=integrate(np.array([s['z0'] for s in tg]),np.array([s['z1'] for s in tg]),np.array([s['x0'] for s in tg]),lo,hi)
            for kind in ['navigation','intersection']:
                vals[kind]=sum(s['x0'] for s in r.get(kind,[]) if lo<=s['position'][2]<hi)
            row['intervals'][name]=vals
        rows.append(row)
    summary={}
    for name in names:
        ar={k:np.array([r['intervals'][name][k] for r in rows]) for k in ['geant4','tgeo','navigation','intersection']}
        g=ar['geant4']; m=ar['navigation']
        summary[name]={'mean_X0':{k:float(v.mean()) for k,v in ar.items()},
                       'median_X0':{k:float(np.median(v)) for k,v in ar.items()},
                       'p95_X0':{k:float(np.percentile(v,95)) for k,v in ar.items()},
                       'map_over_geant4_mean':float(m.mean()/g.mean()) if g.mean()>0 else None,
                       'g4_tgeo_mean_absolute_difference_X0':float(np.mean(abs(g-ar['tgeo']))),
                       'zero_map_truth_above_0p01_X0':int(np.sum((m<=1e-6)&(g>.01))),
                       'over_5x_truth_excess_above_0p1_X0':int(np.sum((m>5*g)&(m-g>.1)))}
    provenance=json.loads((a.truth_provenance or a.truth.parent/'material-map.cbor.provenance.json').read_text())
    with a.truth.open('rb') as stream:
        actual_truth_sha256=hashlib.file_digest(stream,'sha256').hexdigest()
    assert actual_truth_sha256==provenance['truth_sha256']
    xml_sha256=hashlib.sha256(a.xml.read_bytes()).hexdigest()
    assert xml_sha256==provenance['xml_sha256'], 'XML differs from recording sidecar'
    map_sha256=hashlib.sha256(a.map.read_bytes()).hexdigest()
    map_sidecar=Path(str(a.map)+'.provenance.json')
    map_provenance=json.loads(map_sidecar.read_text()) if map_sidecar.exists() else {}
    if 'map_sha256' in map_provenance: assert map_provenance['map_sha256']==map_sha256
    if 'truth_sha256' in map_provenance: assert map_provenance['truth_sha256']==actual_truth_sha256
    if 'xml_sha256' in map_provenance: assert map_provenance['xml_sha256']==xml_sha256
    if 'acts' in map_provenance: assert tuple(map_provenance['acts'])==ACTS_VERSION
    for fingerprints in ['geometry_include_sha256','geometry_plugin_sha256']:
        for path, digest in map_provenance.get(fingerprints,{}).items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest, f'Geometry artifact changed: {path}'
    training_start=a.training_first_entry
    training_count=a.training_entry_count
    training_range_source='CLI assertion' if training_start is not None or training_count is not None else 'map sidecar'
    if training_start is None: training_start=map_provenance.get('training_entry_start')
    if training_count is None: training_count=map_provenance.get('training_entry_count')
    if training_start is not None and training_start<0 or training_count is not None and training_count<=0:
        raise ValueError('Invalid training entry range')
    held_out=training_start is not None and training_count is not None and all(not training_start<=r['entry']<training_start+training_count for r in rays)
    scope='Disjoint recorded-entry holdout' if held_out else 'Training overlap unknown or present; diagnostic only'
    result={'config':a.config,'environment':{'acts':list(ACTS_VERSION),'container':os.environ.get('SINGULARITY_CONTAINER',os.environ.get('APPTAINER_CONTAINER','unreported'))},
            'beam_configuration':'5x41 GeV; straight material rays, magnetic field not applied',
            'recording_provenance':provenance,
            'map':str(a.map),'map_sha256':map_sha256,'map_provenance':map_provenance,
            'xml':str(a.xml),'xml_sha256':xml_sha256,
            'training_first_entry':training_start,'training_entry_count':training_count,'training_range_source':training_range_source,'disjoint_holdout':held_out,
            'verified_truth_sha256':actual_truth_sha256,
            'provenance_limit':'Original recording sidecar fingerprints top XML only, not recursive includes or compiled plugins; current map provenance cannot retrospectively establish recording geometry identity',
            'truth':str(a.truth),'probe':str(a.probe),'accepted_rays':len(rows),'candidate_rays_examined':probe['examined'],
            'acceptance':'4 < eta < 6; sensitive intersections in >=3 B0 stations',
            'observed_eta_range':[min(r['eta'] for r in rays),max(r['eta'] for r in rays)],
            'material_screening_passed':all(abs(s['mean_X0']['navigation']-s['mean_X0']['geant4'])<=max(.001,.25*s['mean_X0']['geant4']) and s['zero_map_truth_above_0p01_X0']==0 for s in summary.values()) and all(summary[n]['over_5x_truth_excess_above_0p1_X0']==0 for n in names[1:]),
            'screening_criteria':'Engineering diagnostic, not ACTS standard: each mean within max(0.001 X0,25%); no zero-map rays above0.01 X0 truth; no between/after interval rays over5x truth with0.1 X0 excess',
            'geometry_preflight_passed':probe['preflight']['duplicate_detector_ids']==0 and probe['sensors']>0 and probe['sensors']==sum(l['sensors'] for l in probe['preflight']['layers']) and len(probe['preflight']['layers'])==8 and all(l['mapped_planar_approaches']==l['planar_approaches']==2 and l['sensor_samples_outside']==l['approach_samples_outside']==0 for l in probe['preflight']['layers']),
            'sample_scope':scope,
            'precision':'Full precision JSON; numpy linear percentiles; displayed numbers rounded to 3 significant figures',
            'truth_step_convention':'ACTS44 Geant4 recorder stores pre-step in mat_z; true endpoint = mat_z + mat_dz * mat_step_length. Writer mat_sz/mat_ez incorrectly center about pre-step and are not used.',
            'preflight':probe['preflight'],'navigation_errors':sum(bool(r['navigation_error']) for r in rows),
            'rays_missing_sensor_intersections':sum(bool(r['missing_sensor_ids']) for r in rows),
            'summary':summary,'rays':rows}
    result['navigation_passed']=result['navigation_errors']==result['rays_missing_sensor_intersections']==0
    result['post_envelope_material_passed']=all(all(value<=1e-9 for value in r['post_envelope_X0'].values()) for r in rows)
    result['truth_crosscheck_passed']=all(s['g4_tgeo_mean_absolute_difference_X0']<=max(.001,.05*s['mean_X0']['geant4']) for s in summary.values())
    result['truth_crosscheck_criterion']='Per broad interval mean absolute Geant4/TGeo difference <= max(0.001 X0, 5% of mean Geant4 X0)'
    result['post_envelope_criterion']='Both navigation and direct intersection: summed material at z > first exit + 1e-6 mm <= 1e-9 X0 per ray'
    result['material_validation_passed']=result['geometry_preflight_passed'] and result['material_screening_passed'] and result['post_envelope_material_passed'] and result['truth_crosscheck_passed'] and held_out
    result['validation_passed']=result['material_validation_passed'] and result['navigation_passed']
    (a.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    target=a.output/'assets'; target.mkdir(exist_ok=True)
    fig,axs=plt.subplots(1,3,figsize=(12.5,4.5),sharex=True,sharey=True,layout='constrained')
    maxval=max(r['intervals'][n][k] for r in rows for n in names for k in ['geant4','navigation','intersection'])
    lim=a.axis_max or 10**np.ceil(np.log10(max(maxval*1.1,.01)))
    if lim<maxval: raise ValueError('--axis-max would clip observed material')
    for ax,n,title in zip(axs,names,['Before first sensor','Between first and last sensors','Last sensor to tracking-volume exit']):
        g=np.array([r['intervals'][n]['geant4'] for r in rows])
        for k,marker,color in [('navigation','o','#23649c'),('intersection','x','#c66b21')]:
            ax.scatter(g,[r['intervals'][n][k] for r in rows],s=9,marker=marker,alpha=.4,color=color,label='ACTS '+k)
        ax.plot([0,lim],[0,lim],color='0.5',lw=1)
        ax.set_xscale('symlog',linthresh=.001); ax.set_yscale('symlog',linthresh=.001)
        ax.set(xlim=(0,lim),ylim=(0,lim),title=title,xlabel=r'Geant4 truth $X/X_0$'); ax.grid(alpha=.2)
    axs[0].set_ylabel(r'Mapped $X/X_0$'); axs[0].legend(fontsize=8)
    fig.suptitle(f'ePIC B0 | {a.config} | 5×41 GeV geometry | ACTS {".".join(map(str,ACTS_VERSION))} | {len(rows)} identical rays\n4 < η < 6; ≥3 stations; straight geantinos; {scope}')
    fig.savefig(target/'b0_geant4_map_closure.pdf'); plt.close(fig)
    print(json.dumps({k:v for k,v in result.items() if k!='rays'},indent=2))
    if a.require_material_validation and not result['material_validation_passed']: raise SystemExit(4)

if __name__=='__main__': main()
