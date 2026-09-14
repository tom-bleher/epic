"""Compare two B0 validation reports on verified identical recorded rays."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np


def read(path):
    return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',required=True,type=Path)
    parser.add_argument('--after',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    before,after=read(args.before),read(args.after)
    for key in ['config','xml_sha256','verified_truth_sha256','acceptance','truth_step_convention','beam_configuration']:
        if before[key]!=after[key]: raise ValueError(f'Incompatible report {key}')
    if before['environment']['acts']!=after['environment']['acts']:
        raise ValueError('Reports use different ACTS versions')
    rows=[{r['entry']:r for r in report['rays']} for report in [before,after]]
    if set(rows[0])!=set(rows[1]) or any(len(row)!=report['accepted_rays'] for row,report in zip(rows,[before,after])):
        raise ValueError('Reports do not contain identical unique ray entries')
    probes=[{r['entry']:r for r in read(Path(report['probe']))['rays']} for report in [before,after]]
    entries=sorted(rows[0])
    names=['before_first','between_first_last','last_to_exit']
    titles=['Before first sensor','Between first and last sensors','Last sensor to tracking-volume exit']
    for entry in entries:
        for key in ['origin','direction','hits','envelope_exit_z']:
            if probes[0][entry][key]!=probes[1][entry][key]:
                raise ValueError(f'Ray {entry}: different {key}')
        if rows[0][entry]['bounds_z_mm']!=rows[1][entry]['bounds_z_mm']:
            raise ValueError('Different interval boundaries')
        for name in names:
            if rows[0][entry]['intervals'][name]['geant4']!=rows[1][entry]['intervals'][name]['geant4']:
                raise ValueError('Different Geant4 truth for identical ray')
    values={name:dict(truth=np.array([rows[0][e]['intervals'][name]['geant4'] for e in entries]),
                      before=np.array([rows[0][e]['intervals'][name]['navigation'] for e in entries]),
                      after=np.array([rows[1][e]['intervals'][name]['navigation'] for e in entries])) for name in names}
    if any(not np.isfinite(v).all() or np.any(v<0) for group in values.values() for v in group.values()):
        raise ValueError('Nonfinite or negative material')
    maximum=max(v.max() for group in values.values() for v in group.values())
    limit=10**np.ceil(np.log10(max(maximum*1.1,.01)))
    colors={'truth':'#333333','before':'#c66b21','after':'#23649c'}
    labels={'truth':'Geant4 truth','before':'Unbounded map','after':'Bounded map'}
    header=f"EPIC B0 | {after['config']} | 5×41 GeV geometry | {len(entries)} identical rays"
    subtitle='4 < η < 6; ≥3 stations; straight geantinos; bounded-map sample disjoint from training' if after['disjoint_holdout'] else '4 < η < 6; ≥3 stations; straight geantinos; diagnostic sample'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with PdfPages(args.output) as pdf:
        fig,axes=plt.subplots(1,3,figsize=(13,4.8),sharex=True,sharey=True)
        fig.subplots_adjust(left=.07,right=.99,bottom=.17,top=.81,wspace=.07)
        for ax,name,title in zip(axes,names,titles):
            for key,marker in [('before','x'),('after','o')]:
                ax.scatter(values[name]['truth'],values[name][key],s=9,alpha=.35,marker=marker,color=colors[key],label=labels[key],rasterized=True)
            ax.plot([0,limit],[0,limit],color='0.5',lw=1)
            ax.set_xscale('symlog',linthresh=.001); ax.set_yscale('symlog',linthresh=.001)
            ax.set(xlim=(0,limit),ylim=(0,limit),title=title,xlabel='Geant4 truth X/X₀'); ax.grid(alpha=.2)
        axes[0].set_ylabel('ACTS navigation X/X₀'); axes[0].legend(fontsize=8)
        fig.suptitle(header+'\n'+subtitle)
        pdf.savefig(fig); plt.close(fig)
        fig,axes=plt.subplots(1,3,figsize=(13,4.8),sharey=True)
        fig.subplots_adjust(left=.07,right=.99,bottom=.20,top=.81,wspace=.07)
        for ax,name,title in zip(axes,names,titles):
            means=[float(values[name][key].mean()) for key in ['truth','before','after']]
            ax.bar(range(3),means,color=[colors[k] for k in ['truth','before','after']],width=.65)
            for x,mean in enumerate(means): ax.annotate(f'{mean:.3g}',(x,mean),xytext=(0,4),textcoords='offset points',ha='center')
            ax.set_xticks(range(3),[labels[k] for k in ['truth','before','after']],rotation=15)
            ax.set_yscale('symlog',linthresh=.001); ax.set_ylim(0,limit); ax.set_title(title); ax.grid(axis='y',alpha=.2)
        axes[0].set_ylabel('Mean material X/X₀')
        fig.suptitle(header+'\n'+subtitle)
        pdf.savefig(fig); plt.close(fig)
    result=dict(before_report_sha256=hashlib.sha256(args.before.read_bytes()).hexdigest(),
                after_report_sha256=hashlib.sha256(args.after.read_bytes()).hexdigest(),
                before_map_sha256=before['map_sha256'],after_map_sha256=after['map_sha256'],
                accepted_rays=len(entries),same_origins_directions_intersections_and_truth=True,
                before_sample_scope=before['sample_scope'],after_sample_scope=after['sample_scope'],
                mean_X0={name:{key:float(v.mean()) for key,v in group.items()} for name,group in values.items()},
                precision='Full precision JSON; displayed means rounded to three significant figures')
    args.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
