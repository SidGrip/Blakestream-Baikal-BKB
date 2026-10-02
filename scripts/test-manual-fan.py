#!/usr/bin/env python3
import os
import runpy
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, 'overlay', 'opt', 'scripta', 'bin')


class ManualFanTests(unittest.TestCase):
    def test_curve_defaults_and_saved_values(self):
        fan = runpy.run_path(os.path.join(BIN, 'fan-controller.py'))
        saved = {}
        fan['load_config'].__globals__['load_json'] = lambda path, default: [] if path.endswith('miner.options.json') else saved
        cfg = fan['load_config']()
        self.assertEqual([cfg[k] for k in ('min_fan', 'target_temp', 'mid_fan', 'mid_temp', 'full_temp')],
                         [40, 36, 75, 43, 50])
        saved.update(userAutoFanMin=30, userAutoFanTarget=35, userAutoFanMidFan=65,
                     userAutoFanMidTemp=42, userAutoFanFull=49)
        cfg = fan['load_config']()
        self.assertEqual([cfg[k] for k in ('min_fan', 'target_temp', 'mid_fan', 'mid_temp', 'full_temp')],
                         [30, 35, 65, 42, 49])
        for value in (-1, 0, 19, 20):
            saved.update(userAutoFanTarget=value, userAutoFanMidTemp=value)
            cfg = fan['load_config']()
            self.assertEqual(cfg['target_temp'], 20)
            self.assertGreater(cfg['mid_temp'], cfg['target_temp'])
        self.assertEqual(fan['parse_baikal_options'](''), (40, 55))
        self.assertEqual(fan['parse_baikal_options']('400:40:120'), (40, 99))
        runtime = runpy.run_path(os.path.join(BIN, 'build-miner-runtime.py'))
        self.assertEqual(runtime['normalize_baikal_options']('400:40:120'), '400:40:99')

    def test_queued_countdown_keeps_its_start_until_acknowledged(self):
        fan = runpy.run_path(os.path.join(BIN, 'fan-controller.py'))
        saved = {}
        env = fan['write_state'].__globals__
        env['load_json'] = lambda path, default: saved.copy()
        def write(path, data):
            saved.clear()
            saved.update(data)
        env['write_json_atomic'] = write
        cfg = {'request_revision': 'first', 'requested_at': 1000}
        fan['write_state'](True, 'queued', cfg, 80)
        self.assertEqual(saved['pending_since'], 1000)
        fan['write_state'](True, 'queued', cfg, 80)
        self.assertEqual(saved['pending_since'], 1000)
        cfg.update(request_revision='second', requested_at=1010)
        fan['write_state'](True, 'queued', cfg, 80)
        self.assertEqual(saved['pending_since'], 1010)
        fan['write_state'](True, 'ok', cfg, 80)
        self.assertEqual(saved['pending_since'], 0)

    def test_runtime_and_controller_floor(self):
        runtime = runpy.run_path(os.path.join(BIN, 'build-miner-runtime.py'))
        fan = runpy.run_path(os.path.join(BIN, 'fan-controller.py'))
        for value, expected in ((0, 40), (-1, 40), (39, 40), (40, 40),
                                (70, 70), (100, 100), (101, 100), ('bad', 70)):
            options = [{'key': 'baikal-fan', 'value': str(value)}]
            self.assertEqual(runtime['load_saved_options'](options)['baikal-fan'], str(expected))
            def load(path, default):
                return options if path.endswith('miner.options.json') else {}
            fan['load_config'].__globals__['load_json'] = load
            self.assertEqual(fan['load_config']()['manual_fan'], expected)
            self.assertEqual(fan['load_config']()['min_fan'], 40)

    def test_automatic_floor_is_independent_of_manual_default(self):
        fan = runpy.run_path(os.path.join(BIN, 'fan-controller.py'))
        for value, expected in ((0, 20), (19, 20), (20, 20), (30, 30),
                                (40, 40), (101, 100), ('bad', 40)):
            def load(path, default):
                return [] if path.endswith('miner.options.json') else {'userAutoFanMin': value}
            fan['load_config'].__globals__['load_json'] = load
            cfg = fan['load_config']()
            self.assertEqual(cfg['min_fan'], expected)
            self.assertEqual(cfg['manual_fan'], 70)
            self.assertEqual(fan['compute_fan'](cfg, cfg['target_temp'], expected)[0], expected)
            self.assertEqual(fan['compute_fan'](cfg, cfg['full_temp'], expected)[0], 100)


if __name__ == '__main__':
    unittest.main()
