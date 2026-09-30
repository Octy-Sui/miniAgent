"""Tests for classification-tree-nwise/scripts/nwise.py."""

from __future__ import annotations

import importlib.util
import itertools
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "classification-tree-nwise" / "scripts" / "nwise.py"
spec = importlib.util.spec_from_file_location("nwise", SCRIPT)
nwise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nwise)

PARAMS = {
    "Browser": ["Chrome", "Firefox", "Safari", "Edge"],
    "Locale": ["en", "fr", "nl"],
    "Sort": ["relevance", "price asc", "price desc", "newest"],
    "Tier": ["free", "premium"],
}
CONSTRAINTS = [{"Tier": "free", "Sort": "price asc"}, {"Tier": "free", "Sort": "price desc"}]


def pairs_of(test):
    names = list(PARAMS)
    return {tuple((n, test[n]) for n in combo) for combo in itertools.combinations(names, 2)}


class GenerateTests(unittest.TestCase):
    def test_pairwise_covers_every_feasible_pair(self):
        tests, required, impossible = nwise.generate(PARAMS, CONSTRAINTS, 2)
        covered = set().union(*(pairs_of(t) for t in tests))
        self.assertEqual(impossible, [])
        self.assertTrue(set(required) <= covered)

    def test_pairwise_respects_constraints(self):
        tests, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        self.assertFalse(any(nwise.violates(t, CONSTRAINTS) for t in tests))

    def test_pairwise_is_much_smaller_than_cartesian(self):
        tests, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        self.assertLessEqual(len(tests), 20)
        self.assertEqual(nwise.allowed_size(PARAMS, CONSTRAINTS), 72)

    def test_each_choice_uses_every_value(self):
        tests, _, _ = nwise.generate(PARAMS, [], 1)
        for name, values in PARAMS.items():
            self.assertEqual({t[name] for t in tests}, set(values))
        self.assertEqual(len(tests), 4)

    def test_implied_impossible_tuple_is_reported(self):
        params = {"A": ["a1", "a2"], "B": ["b1"], "C": ["c1", "c2"]}
        # A=a1 forces C=c2 (b1 is the only B value), so A=a1 with C=c1 can never occur.
        constraints = [{"A": "a1", "B": "b1", "C": "c1"}]
        tests, _, impossible = nwise.generate(params, constraints, 2)
        self.assertIn((("A", "a1"), ("C", "c1")), impossible)
        self.assertFalse(any(nwise.violates(t, constraints) for t in tests))

    def test_output_is_deterministic(self):
        first, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        second, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        self.assertEqual(first, second)


class VerifyTests(unittest.TestCase):
    def test_verify_accepts_generated_set(self):
        tests, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        report = nwise.verify(tests, PARAMS, CONSTRAINTS, 2)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["problems"], [])

    def test_verify_reports_missing_pairs(self):
        tests, _, _ = nwise.generate(PARAMS, CONSTRAINTS, 2)
        report = nwise.verify(tests[:-1], PARAMS, CONSTRAINTS, 2)
        self.assertTrue(report["missing"])

    def test_verify_flags_constraint_violations_and_unknown_values(self):
        bad = [
            {"Browser": "Chrome", "Locale": "en", "Sort": "price asc", "Tier": "free"},
            {"Browser": "Opera", "Locale": "en", "Sort": "newest", "Tier": "free"},
        ]
        report = nwise.verify(bad, PARAMS, CONSTRAINTS, 2)
        self.assertEqual(len(report["problems"]), 2)


if __name__ == "__main__":
    unittest.main()
