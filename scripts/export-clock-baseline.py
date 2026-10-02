#!/usr/bin/env python3
"""Export completed clock-trial observations into an algorithm-specific dataset."""
import argparse
import importlib.util
import json
import math
import os
import re
import tempfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('clock_trial', str(Path(__file__).with_name('bkb-clock-trial.py')))
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)

MEASURED_BRIDGES = {
    'caf95856393914cf01436d07724e067f41dab9d1fa98057e7b3a272c16ab4a54': '0e8467eddabf589ad94549a8d572c759',
    'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a': '18babe0c4c67d9146e230f9dc423f2f2',
}


def collect(algorithm, runs):
    if TRIAL.algorithm_key(algorithm) != algorithm or not algorithm:
        raise ValueError('canonical algorithm key required')
    dataset = {'schema': 1, 'algorithm': algorithm, 'source': 'lab-screen',
               'qualified': False, 'physical_mhz_claimed': False, 'power_measured': False,
               'observations': [], 'failed_legs': []}
    seen = set()
    for run in runs:
        summary = {}
        summary_path = Path(run) / 'summary.json'
        if summary_path.exists():
            if summary_path.stat().st_size > 1024 * 1024:
                raise ValueError('oversized run summary')
            with summary_path.open() as handle:
                summary = json.load(handle)
        for path in sorted((Path(run) / 'legs').glob('*.json')):
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('oversized leg report')
            with path.open() as handle:
                leg = json.load(handle)
            source = str(path.resolve())
            if source in seen:
                continue
            seen.add(source)
            if leg.get('status') != 'success':
                dataset['failed_legs'].append({'source': source, 'name': leg.get('name'),
                                               'reasons': leg.get('reasons', [])})
                continue
            boards = leg.get('boards', [])
            if len(boards) != 3 or sorted(b['id'] for b in boards) != [0, 1, 2]:
                raise ValueError('incomplete board result')
            if leg.get('measurement_seconds', 0) < 1800 or leg.get('monotonic_seconds', 0) < leg['measurement_seconds']:
                raise ValueError('incomplete measured window')
            for board in boards:
                raw = board.get('reported_algorithm', board.get('algorithm'))
                if TRIAL.algorithm_key(raw) != algorithm or TRIAL.algorithm_key(board.get('algorithm')) != algorithm:
                    raise ValueError('algorithm mismatch: ' + source)
                rate = board.get('rate_ghs')
                elapsed = board.get('device_elapsed_seconds', 0)
                if rate is None or not math.isfinite(rate) or rate <= 0 or elapsed < leg['measurement_seconds']:
                    raise ValueError('invalid measured rate/window')
                record = dict(board)
                record.update({'algorithm': algorithm, 'reported_algorithm': raw,
                               'source_report': source, 'leg': leg['name'],
                               'indices': leg['indices'], 'hw_policy': leg['hw_policy'],
                               'host_sha256': leg.get('host_sha256', summary.get('host_sha256')),
                               'bridge_artifact_sha256': leg.get('bridge_artifact_sha256', summary.get('bridge_artifact_sha256')),
                               'qualified': False, 'physical_mhz': None,
                               'frequency_status': 'unverified'})
                dataset['observations'].append(record)
    dataset['averages'] = average_observations(dataset['observations'])
    return dataset


def average_observations(observations):
    groups = {}
    for board in observations:
        work = board.get('difficulty_delta')
        accepted = board.get('accepted_delta')
        if (not isinstance(work, (int, float)) or isinstance(work, bool) or not math.isfinite(work) or work <= 0 or
                not isinstance(accepted, int) or isinstance(accepted, bool) or accepted <= 0):
            raise ValueError('accepted work and share counts required for an average')
        # Legacy in-flight legs may not have run identities until their summary
        # arrives. Keep those isolated by source run, never merge unknown hosts.
        run = str(Path(board['source_report']).parent.parent)
        host = board.get('host_sha256') or run
        bridge = board.get('bridge_artifact_sha256') or run
        key = (board['algorithm'], host, bridge, board['id'], board['index'], tuple(board['indices']))
        if key not in groups:
            groups[key] = {'algorithm': board['algorithm'], 'board_id': board['id'],
                           'clock_index': board['index'], 'indices': board['indices'],
                           'host_sha256': board.get('host_sha256'),
                           'bridge_artifact_sha256': board.get('bridge_artifact_sha256'),
                           'windows': 0, 'accepted_delta': 0, 'difficulty_delta': 0.0,
                           'device_elapsed_seconds': 0.0, 'hw_errors_delta': 0,
                           'startup_hw_errors': 0, 'source_reports': [], 'qualified': False,
                           'source_runs': [],
                           'nominal_mhz': board.get('nominal_mhz'), 'physical_mhz': None,
                           'frequency_status': 'unverified'}
        row = groups[key]
        row['windows'] += 1
        row['accepted_delta'] += accepted
        row['difficulty_delta'] += work
        row['device_elapsed_seconds'] += board['device_elapsed_seconds']
        row['hw_errors_delta'] += board.get('hw_errors_delta', 0)
        row['startup_hw_errors'] += board.get('startup_hw_errors', 0)
        row['source_reports'].append(board['source_report'])
        if run not in row['source_runs']:
            row['source_runs'].append(run)
        row['accepted_ghs'] = row['difficulty_delta'] * 2**32 / row['device_elapsed_seconds'] / 1e9
        row['quality_flags'] = ['hardware-errors-observed'] if row['hw_errors_delta'] + row['startup_hw_errors'] else []
    averages = [groups[key] for key in sorted(groups)]
    for row in averages:
        row.update({'estimated_mhz': None, 'estimate_kind': 'performance-equivalent',
                    'reference_accepted_ghs': None, 'relative_performance_pct': None})
        references = [ref for ref in averages if ref['algorithm'] == row['algorithm'] and
                      ref['board_id'] == row['board_id'] and ref['indices'] == [30, 30, 30] and
                      ref['host_sha256'] == row['host_sha256'] and
                      ref['bridge_artifact_sha256'] == row['bridge_artifact_sha256'] and
                      (all((row['host_sha256'], row['bridge_artifact_sha256'])) or
                       ref['source_runs'] == row['source_runs'])]
        if len(references) == 1:
            reference = references[0]
            ratio = row['accepted_ghs'] / reference['accepted_ghs']
            row['reference_accepted_ghs'] = reference['accepted_ghs']
            row['relative_performance_pct'] = 100.0 * ratio
            row['estimated_mhz'] = 400 if row['clock_index'] == 30 else round(400 * ratio)
            if row['clock_index'] == 30:
                row['estimate_kind'] = 'nominal-stock-anchor'
    return averages


def save(root, algorithm, dataset):
    if not algorithm or TRIAL.algorithm_key(algorithm) != algorithm or dataset.get('algorithm') != algorithm:
        raise ValueError('algorithm namespace mismatch')
    directory = Path(root) / algorithm
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / 'baseline.json'
    if target.exists():
        with target.open() as handle:
            if json.load(handle) == dataset:
                return
    fd, temporary = tempfile.mkstemp(prefix='.baseline-', dir=str(directory))
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(dataset, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(target))
        directory_fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def public_baseline(dataset, host, bridge):
    """Project matching lab observations, never personal history, into shared examples."""
    if dataset.get('algorithm') != 'blake256r8' or not all(
            isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v) for v in (host, bridge)):
        raise ValueError('blake256r8 and explicit binary identities required')
    if bridge not in MEASURED_BRIDGES:
        raise ValueError('public example requires a recognized measured bridge')
    observations = []
    seen = set()
    for row in dataset.get('observations', []):
        if row.get('host_sha256') != host or row.get('bridge_artifact_sha256') != bridge:
            continue
        index, board, indices = row.get('index'), row.get('id'), row.get('indices')
        if (type(board) is not int or board not in (0, 1, 2) or type(index) is not int or
                not 8 <= index <= 32 or not isinstance(indices, list) or len(indices) != 3 or
                any(type(n) is not int or not 8 <= n <= 32 for n in indices) or indices[board] != index or
                row.get('algorithm') != 'blake256r8' or row.get('physical_mhz') is not None or
                row.get('qualified') is not False or row.get('rejected_delta') != 0):
            raise ValueError('invalid lab observation')
        for key in ('difficulty_delta', 'device_elapsed_seconds'):
            value = row.get(key)
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid accepted work or duration')
        if row['device_elapsed_seconds'] < 1800 or type(row.get('accepted_delta')) is not int or row['accepted_delta'] <= 0:
            raise ValueError('incomplete accepted-work window')
        for key in ('startup_hw_errors', 'hw_errors_delta'):
            if type(row.get(key, 0)) is not int or row.get(key, 0) < 0:
                raise ValueError('invalid hardware error count')
        source = row.get('source_report')
        if not isinstance(source, str) or not source:
            raise ValueError('source identity required')
        key = (source, board)
        if key in seen:
            raise ValueError('duplicate source observation')
        seen.add(key)
        observations.append(row)
    references = {}
    for board in (0, 1, 2):
        rows = [r for r in observations if r['id'] == board and r['indices'] == [30, 30, 30]]
        if rows:
            references[board] = sum(r['difficulty_delta'] for r in rows) * 2**32 / sum(
                r['device_elapsed_seconds'] for r in rows) / 1e9
    result = {'schema': 1, 'source': 'lab-example', 'algorithm': 'blake256r8',
              'host_sha256': host, 'bridge_artifact_sha256': bridge,
              # Reviewed active-slot fix leaves the table and active-chip commands unchanged.
              'compatible_bridge_chip_md5': (['18babe0c4c67d9146e230f9dc423f2f2']
                                             if bridge != TRIAL.BRIDGE_ARTIFACT_SHA256 else []),
              'bridge_chip_md5': MEASURED_BRIDGES[bridge],
              'qualified': False, 'physical_mhz': None, 'frequency_status': 'unverified',
              'power_measured': False, 'rows': []}
    for index in range(8, 33):
        rows = [r for r in observations if r['index'] == index and
                (index != 30 or r['indices'] == [30, 30, 30])]
        if not rows:
            continue
        seconds = sum(r['device_elapsed_seconds'] for r in rows)
        rate = sum(r['difficulty_delta'] for r in rows) * 2**32 / seconds / 1e9
        reference = None
        if all(r['id'] in references for r in rows):
            reference = sum(references[r['id']] * r['device_elapsed_seconds'] for r in rows) / seconds
        result['rows'].append({
            'clock_index': index, 'ghs': rate,
            'estimated_mhz': 400 if index == 30 else (400 * rate / reference if reference else None),
            'reference_ghs': reference, 'estimate_kind': 'nominal-stock-anchor' if index == 30 else 'performance-equivalent',
            'board_windows': len(rows), 'board_seconds': seconds,
            'accepted_shares': sum(r['accepted_delta'] for r in rows),
            'hw_errors': sum(r.get('startup_hw_errors', 0) + r.get('hw_errors_delta', 0) for r in rows),
            'measured_boards': sorted(set(r['id'] for r in rows)),
            'contexts': [list(t) for t in sorted(set(tuple(r['indices']) for r in rows))],
        })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--algorithm', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--run', action='append', required=True)
    args = parser.parse_args()
    save(args.root, args.algorithm, collect(args.algorithm, args.run))


if __name__ == '__main__':
    main()
