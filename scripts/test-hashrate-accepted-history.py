#!/usr/bin/env python3
"""Exercise the recorder transform without importing its filesystem startup."""
import ast
import copy
import math
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'overlay/opt/scripta/bin/hashrate-history-recorder.py'
tree = ast.parse(path.read_text())
tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'append_accepted_sample']
namespace = {'math': math}
exec(compile(tree, str(path), 'exec'), namespace)
append = namespace['append_accepted_sample']


class AcceptedHistory(unittest.TestCase):
    def setUp(self):
        self.board = {'samples': [[1000, 56000]]}
        self.summary = {'version': 2, 'updated': 1000, 'boards': {
            '1': {'context': {'pid': 1, 'cg': 1}, '15m': {'valid': True, 'ghs': 63.25}}}}

    def test_accepted_rate_and_timestamp_are_not_the_model(self):
        append(self.board, self.summary, 1, 1030, 0)
        self.assertEqual(self.board['accepted_samples'], [[1000, 63250]])
        self.assertEqual(self.board['samples'], [[1000, 56000]])

    def test_duplicate_snapshot_is_not_repeated(self):
        append(self.board, self.summary, 1, 1030, 0)
        append(self.board, self.summary, 1, 1090, 0)
        self.assertEqual(len(self.board['accepted_samples']), 1)

    def test_stale_future_and_missing_rates_are_gaps(self):
        for now, data in [(1201, self.summary), (999, self.summary), (1030, {})]:
            board = {}
            append(board, data, 1, now, 0)
            self.assertIsNone(board['accepted_samples'][-1][1])

    def test_clock_or_process_change_breaks_line(self):
        append(self.board, self.summary, 1, 1030, 0)
        self.summary['updated'] = 1060
        self.summary['boards']['1']['context']['cg'] = 2
        # Persisted JSON is independent of the next recorder's loaded summary.
        self.board['accepted_context'] = {'pid': 1, 'cg': 1}
        append(self.board, self.summary, 1, 1090, 0)
        self.assertEqual(self.board['accepted_samples'], [[1000, 63250], [1059, None], [1060, 63250]])

    def test_invalid_values_and_measuring_are_gaps(self):
        for value in (None, True, '63', float('nan'), float('inf'), -1):
            data = copy.deepcopy(self.summary)
            data['boards']['1']['15m']['ghs'] = value
            board = {}
            append(board, data, 1, 1030, 0)
            self.assertIsNone(board['accepted_samples'][-1][1])
        self.summary['boards']['1']['15m']['valid'] = False
        append(self.board, self.summary, 1, 1030, 0)
        self.assertIsNone(self.board['accepted_samples'][-1][1])

    def test_retention(self):
        self.board['accepted_samples'] = [[1, 3], [999, 4]]
        append(self.board, self.summary, 1, 1030, 998)
        self.assertTrue(all(row[0] >= 998 for row in self.board['accepted_samples']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
