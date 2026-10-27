"""Generate an external descriptor and an NSIM preview; never execute project Makefiles."""
import argparse
from pathlib import Path
import sys

from nsim.planning import create_plan
from nsim.service import run
from nsim.storage import digest, write_json


def preview(source, work, queue=None):
    source, work = Path(source).resolve(), Path(work).resolve()
    if source == work or source in work.parents or work in source.parents:
        raise ValueError('preview workspace must be outside the ASIC source tree')
    required = ['sim/regress/Makefile', 'sim/regress/collect_results.py', 'sim/filelist/sim.f',
                'sim/refmodel/build_refmodel.sh', 'sim/testbench/top/acc_tb_top.sv']
    for relative in required:
        if not (source / relative).is_file():
            raise ValueError(f'not an expected npu8860 acc checkout: missing {relative}')
    work.mkdir(parents=True, exist_ok=False)
    config = work / 'profile'
    config.mkdir()
    for directory in ('output', 'events'):
        (work / directory).mkdir()
    import json
    descriptor = config / 'nsim.toml'
    descriptor.write_text('schema_version = 1\nname = "npu8860-acc-preview"\nsource_root = ' + json.dumps(str(source)) +
                          '\ninputs = ' + json.dumps([str(source / p) for p in required]) + '\n')
    cases = config / 'smoke.caselist'
    cases.write_text('acc_sanity_test\nacc_reset_start_test\nacc_two_job_test\n')
    submit = config / 'submit.json'
    scheduler = {'kind': 'lsf'}
    if queue:
        scheduler['queue'] = queue
    write_json(submit, {'schema_version': 1, 'name': 'acc-liveness-preview', 'adapter': 'vcs',
                       'required_checks': ['liveness'], 'scheduler': scheduler,
                       'vcs': {'filelist': 'sim/filelist/sim.f', 'golden_enabled': False, 'fsdb': True,
                               'fsdb_file': 'acc.fsdb', 'fsdb_plusargs': ['+fsdb'],
                               'compile_args': ['-sverilog', '-full64', '-kdb', '-debug_access+all',
                                                '-assert', 'svaext', '-timescale=1ns/1ps', '-ntb_opts',
                                                'uvm-1.2', '+define+UVM_NO_DEPRECATED', '+define+RD=0.1']}})
    plan = create_plan(descriptor, caselist=cases, submit_paths=[submit], seeds=[42], allow_missing_tools=True)
    before = dict(plan['input_hashes'])
    journal, _ = run(plan, [work / 'output'], work / 'events', dry_run=True)
    unchanged = all(digest(Path(path)) == expected for path, expected in before.items())
    summary = {'schema_version': 1, 'executed': False, 'journal': str(journal), 'tasks': len(plan['tasks']),
               'input_count': len(before), 'inputs_unchanged': unchanged,
               'unresolved_tools': plan['submits'][0]['unresolved_tools'],
               'scope': 'No-Golden command preview only; no DUT verdict',
               'pending': ['VCS compile/simulate acceptance', 'LSF worker execution and terminal-state acceptance',
                           'Project result checker integration (ACC log is not NSIM JSON)',
                           'Syscan build and explicit Golden library identity',
                           'Coverage options and coverage hierarchy profile', 'Verdi database and FSDB validation']}
    write_json(work / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='npu8860_ai/acc checkout')
    parser.add_argument('--work', type=Path, required=True, help='New directory outside the source tree')
    parser.add_argument('--queue', help='Site-specific queue; no global nightly default')
    args = parser.parse_args()
    try:
        import json
        print(json.dumps(preview(args.source, args.work, args.queue), indent=2))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
