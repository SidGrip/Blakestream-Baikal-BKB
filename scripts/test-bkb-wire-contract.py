#!/usr/bin/env python3
"""Cross-layer regressions using actual diagnostic API field names."""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / 'overlay/opt/scripta/bin'
spec = importlib.util.spec_from_file_location('recorder', BIN / 'bkb-diagnostics-recorder.py')
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)
report_spec = importlib.util.spec_from_file_location('report', BIN / 'bkb-diagnostics-report.py')
report = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(report)


def screening_leg(role, start):
    provenance = 'running-binary-file-sha256'
    history = {'v': 1, 'host': [{'id': 1, 'sha256': 'a' * 64,
                                'provenance': provenance, 'path': '/proc/123/exe'}],
               'boards': []}
    for board in range(3):
        rows = []
        for minute in range(361):
            elapsed = 600 + minute * 60
            rows.append({
                't': start + minute * 60, 'tm': elapsed,
                'dms': elapsed * 1000000, 'de': elapsed,
                'pe': str(start), 'ps': 'api', 'se': 0, 'ses': 'd',
                'a': minute * 30, 'da': minute * 30 * 32,
                'rj': 0, 'hw': 0, 'en': 1, 'al': 1,
                'ee': 0, 'es': 'd', 're': 0, 'rs': 'd',
                'pool': 'at1.example:3334', 'wk': 'test.board%d' % board,
                'ep': board, 'fo': 0, 'cm': 'global', 'rc': 400,
                'cg': 1, 'vg': 1, 'cv': 1, 'pend': 0, 'inf': 0, 'sc': 30,
                'fan': 80, 'tmp': 40, 'ss': 64, 'ac': 64, 'av': 1,
                'hid': 1, 'hp': provenance, 'fl': role, 'fw': 3,
                'sync': 'co-snapshot', 'diag': {},
            })
        history['boards'].append({'id': board, 'samples': rows})
    return {'role': role, 'history': history, 'start': start, 'end': start + 21600}


def wire_row(board):
    return {
        'ID': 'BKLU%d' % board, 'DiagBoard': board,
        'Chip Count': 64, 'StartupSuccessCount': 56 + board,
        'AsicVersion': 1, 'FWV': 3, 'HWV': 1791,
        'DiagInfoValid': 1, 'DiagLastInfoOk': 1,
        'LocalNoncesRaw': ','.join(str(i + board) for i in range(64)),
        'LocalErrorsRaw': ','.join('0' for _ in range(64)),
        'DiagInvalidId': 7, 'DiagInvalidWorkIdx': 8,
        'DiagUsbTxErrors': 9, 'DiagUsbRxErrors': 10,
        'DiagPid': 123, 'DiagStartUnix': 1790640000,
        'DiagSnapMonoUs': 60000000 + board, 'DiagStatEpoch': 2,
        'DiagRouteEpoch': 3, 'DiagEnableEpoch': 4,
        'ClockMode': 'global', 'RequestedClock': 400,
        'ClockGeneration': 1, 'VerifiedClockGeneration': 0,
        'CommandVerified': False, 'GlobalClockPending': False,
        'GlobalClockInflight': False, 'ClockRetryPending': False,
        'ClockReapplyPending': False, 'AttemptedClockCode': 30,
        'SentClockIndex': 30, 'BridgeStatusEcho': 400,
        'BridgeStatusEchoCode': 30, 'Effective Pool': board,
        'Failover Active': False, 'FanPercent': 80,
        'DiagTraceEnabled': 1, 'DiagTraceLive': 100 + board,
        'DiagTraceUsed': 68000 + 680 * board,
        'DiagTraceCapacity': 2097120, 'DiagTraceOverwritten': 0,
        'DiagTraceDropped': 0, 'DiagTraceRecorded': 100 + board,
    }


EVIDENCE = {'deployment': 'manually-verified', 'cold_start': 'supervised-manual',
            'warm_up': 'recorded', 'firmware_readback': 'verified'}


class Contract(unittest.TestCase):
    def test_locked_share_fields_win_as_one_sample(self):
        stat = wire_row(2)
        stat.update({'DiagAccepted': 100, 'DiagRejected': 2, 'DiagHwErrors': 3,
                     'DiagDiffAccepted': 3200., 'DiagDiffRejected': 64.,
                     'DiagDeviceElapsed': 60., 'DiagDeviceElapsedValid': 1,
                     'DiagShareSnapValid': 1})
        dev = {'Accepted': 999, 'Difficulty Accepted': 99999.,
               'Rejected': 99, 'Hardware Errors': 99, 'Device Elapsed': 99.}
        sample, _ = rec.build_one(2, stat, dev, {}, None, 1790640060., 60.)
        self.assertEqual((sample['a'], sample['da'], sample['rj'], sample['hw'], sample['de']),
                         (100, 3200., 2, 3, 60.))
        self.assertEqual(sample['sync'], 'co-snapshot')
        stat['DiagDeviceElapsedValid'] = 0
        sample, _ = rec.build_one(2, stat, dev, {}, None, 1790640060., 60.)
        self.assertNotEqual(sample['sync'], 'co-snapshot')

    def test_positive_comparison_has_all_gates(self):
        legs = [screening_leg('v1', 100000), screening_leg('stock', 130000),
                screening_leg('v1', 160000)]
        result = report.compare_legs(legs, evidence=EVIDENCE)
        self.assertEqual(result['status'], 'equivalent', result)
        self.assertEqual(result['conclusion'], 'definitive')

    def test_unsynchronized_middle_sample_blocks_definitive_result(self):
        legs = [screening_leg('v1', 100000), screening_leg('stock', 130000),
                screening_leg('v1', 160000)]
        legs[0]['history']['boards'][1]['samples'][180]['sync'] = 'stats-row'
        result = report.compare_legs(legs, evidence=EVIDENCE)
        self.assertNotEqual(result['conclusion'], 'definitive', result)

    def test_process_ticks_are_not_compared_to_unix_seconds(self):
        proc = {'pid': 123, 'epoch': '123:7654321',
                'epoch_after': '123:7654321', 'source': 'proc'}
        sample, _ = rec.build_one(2, wire_row(2), {}, {}, proc, 1790640060., 60.)
        self.assertIsNotNone(sample['pe'])
        self.assertNotIn('process_epoch', sample.get('bad', []))

    def test_comparison_does_not_certify_changed_test_conditions(self):
        for key, value in [('pool', 'eu3.example:3334'), ('fan', 100),
                           ('rc', 260), ('cm', 'individual'), ('fo', 1)]:
            legs = [screening_leg('v1', 100000), screening_leg('stock', 130000),
                    screening_leg('v1', 160000)]
            for board in legs[2]['history']['boards']:
                for sample in board['samples']:
                    sample[key] = value
            result = report.compare_legs(legs)
            self.assertNotEqual(result['status'], 'equivalent', key)
            self.assertTrue(result['reasons'], key)

    def test_comparison_does_not_certify_overlapping_legs(self):
        legs = [screening_leg('v1', 100000), screening_leg('stock', 100000),
                screening_leg('v1', 100000)]
        result = report.compare_legs(legs)
        self.assertNotEqual(result['status'], 'equivalent')

    def test_collector_reads_real_grid_and_usb_fields(self):
        sample, extra = rec.build_one(2, wire_row(2), {}, {}, None, 1790640060., 60.)
        self.assertEqual(sample['ss'], 58)
        self.assertEqual(extra['nonce'], list(range(2, 66)))
        self.assertEqual(extra['error'], [0] * 64)
        self.assertEqual(sample['diag']['usb_tx_error'], 9)
        self.assertEqual(sample['diag']['usb_rx_error'], 10)

    def test_ui_reads_real_grid_and_shared_trace_snapshot(self):
        helper = ROOT / 'overlay/var/www/inc/dev-activity.php'
        php = 'require $argv[1]; echo json_encode(dev_activity_parse_stats(json_decode(stream_get_contents(STDIN),true,512,JSON_BIGINT_AS_STRING)));'
        output = subprocess.check_output([os.environ.get('BKB_PHP') or 'php', '-r', php, str(helper)], input=json.dumps({'STATS': [wire_row(i) for i in range(3)]}).encode())
        result = json.loads(output)
        self.assertEqual(result['boards'][2]['nonce'][3][15], '65')
        self.assertEqual(result['boards'][2]['usb']['tx_error'], '9')
        self.assertIs(result['trace']['enabled'], True)
        self.assertEqual(result['trace']['recorded'], '102')

    def test_wrapped_endpoint(self):
        for url in (
            'http://quota:2;stratum+tcp://eu3.example:3334',
            'quota:2;stratum+tcp://user:password@eu3.example:3334',
            'stratum+tcp://eu3.example:3334?password=secret#fragment',
        ):
            self.assertEqual(rec.endpoint_from_url(url), 'eu3.example:3334')

    def test_hash_identifies_running_inode_after_atomic_replacement(self):
        directory = tempfile.mkdtemp(prefix='bkb-provenance-')
        process = None
        try:
            binary = pathlib.Path(directory) / 'sgminer'
            shutil.copyfile('/bin/sleep', str(binary))
            binary.chmod(0o700)
            expected = hashlib.sha256(binary.read_bytes()).hexdigest()
            process = subprocess.Popen([str(binary), '60'])
            epoch = rec.read_proc_epoch(process.pid)
            replacement = pathlib.Path(directory) / 'replacement'
            replacement.write_bytes(b'not-the-running-executable')
            os.rename(str(replacement), str(binary))
            proc = {'pid': process.pid, 'epoch': epoch, 'source': 'proc', 'exe': str(binary)}
            identity = rec.resolve_host({'host_path': str(binary)}, proc, {})
            self.assertIsNotNone(identity)
            self.assertEqual(identity['sha256'], expected)
        finally:
            if process is not None:
                process.terminate()
                process.wait()
            shutil.rmtree(directory)


if __name__ == '__main__':
    unittest.main()
