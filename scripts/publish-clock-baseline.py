#!/usr/bin/env python3
"""Generate the public lab example file from a private, completed trial export."""
import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('baseline_export', str(Path(__file__).with_name('export-clock-baseline.py')))
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--host-sha', required=True)
    parser.add_argument('--bridge-sha', required=True)
    args = parser.parse_args()
    if args.dataset.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('oversized private dataset')
    with args.dataset.open() as handle:
        data = EXPORT.public_baseline(json.load(handle), args.host_sha, args.bridge_sha)
    if not data['rows'] or not any(row['clock_index'] == 30 for row in data['rows']):
        raise ValueError('completed stock reference required for publication')
    encoded = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if len(encoded.encode('utf-8')) > 65536:
        raise ValueError('oversized public baseline')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.clock-baseline-', dir=str(args.output.parent))
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, str(args.output))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Published %d Index lab examples' % len(data['rows']))


if __name__ == '__main__':
    main()
