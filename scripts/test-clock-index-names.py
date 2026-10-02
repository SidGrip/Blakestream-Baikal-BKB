#!/usr/bin/env python3
"""Keep the active clock-selector contracts aligned across runtime and tools."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ClockIndexNames(unittest.TestCase):
    def test_active_sources_do_not_use_superseded_selector_names(self):
        roots = [ROOT / 'overlay/var/www', ROOT / 'overlay/opt/scripta/bin']
        files = [p for root in roots for p in root.rglob('*')
                 if p.is_file() and p.suffix in ('.php', '.js', '.html', '.css', '.py')]
        files.extend(ROOT / 'scripts' / name for name in (
            'bkb-clock-trial.py', 'export-clock-baseline.py', 'publish-clock-baseline.py'))
        obsolete = re.compile(r'RequestedPLLIndex|SentPLLIndex|SentClockCode|'
                              r'baikal-(?:board|experimental)-pll|bkb-v1-pll-1|'
                              r'\bpll(?:_index|_indices)?\b|\bbs\w*PLL\w*\b')
        for path in files:
            self.assertIsNone(obsolete.search(path.read_text()), str(path.relative_to(ROOT)))

    def test_public_index_label_and_accessible_clock_context(self):
        status = (ROOT / 'overlay/var/www/partials/status.html').read_text()
        miner = (ROOT / 'overlay/var/www/partials/miner.html').read_text()
        self.assertIn("? 'Index' : 'Clock'", status)
        self.assertIn('Show clock-table index', status)
        self.assertIn('Global clock index', miner)
        self.assertIn('Board {{board}} clock index', miner)
        self.assertEqual(miner.count('<span>Index</span>'), 3)

    def test_script_entrypoints_and_baseline_names(self):
        qa = (ROOT / 'scripts/qa').read_text()
        for name in ('test-clock-index-ui.js', 'test-clock-index-settings.php'):
            self.assertIn(name, qa)
            self.assertTrue((ROOT / 'scripts' / name).is_file())
        self.assertTrue((ROOT / 'CLOCK-BASELINE.md').is_file())
        self.assertIn('CLOCK-BASELINE.md', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
