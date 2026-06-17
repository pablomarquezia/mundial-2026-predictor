#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import unittest
import math
from modelo import poisson, probs, build_model, get_combined_strength, RANKING_FACTOR, conf_map


class TestPoisson(unittest.TestCase):
    def test_zero_lambda_returns_1_for_k0(self):
        self.assertEqual(poisson(0, 0), 1.0)

    def test_zero_lambda_returns_0_for_k1(self):
        self.assertEqual(poisson(0, 1), 0.0)

    def test_negative_lambda_returns_0_for_k1(self):
        self.assertEqual(poisson(-1, 1), 0.0)

    def test_poisson_lambda1_k0(self):
        self.assertAlmostEqual(poisson(1.0, 0), math.exp(-1), places=6)

    def test_poisson_lambda1_k1(self):
        self.assertAlmostEqual(poisson(1.0, 1), math.exp(-1), places=6)

    def test_poisson_lambda2_k2(self):
        expected = math.exp(-2) * (2**2) / math.factorial(2)
        self.assertAlmostEqual(poisson(2.0, 2), expected, places=6)


class TestProbs(unittest.TestCase):
    def test_probs_returns_dict_with_all_keys(self):
        p = probs(1.0, 1.0)
        self.assertIn("w1", p)
        self.assertIn("dr", p)
        self.assertIn("w2", p)
        self.assertIn("ml", p)
        self.assertIn("pml", p)

    def test_probs_sum_to_100(self):
        p = probs(1.0, 1.0)
        total = p["w1"] + p["dr"] + p["w2"]
        self.assertAlmostEqual(total, 100.0, delta=0.2)

    def test_probs_ml_is_list_of_two_ints(self):
        p = probs(1.0, 1.0)
        self.assertEqual(len(p["ml"]), 2)
        self.assertIsInstance(p["ml"][0], int)
        self.assertIsInstance(p["ml"][1], int)

    def test_symmetric_teams_equal_probs(self):
        p = probs(1.5, 1.5)
        self.assertAlmostEqual(p["w1"], p["w2"], delta=0.1)

    def test_strong_team_favored(self):
        p = probs(2.5, 0.8)
        self.assertGreater(p["w1"], p["w2"])

    def test_pml_between_0_and_100(self):
        p = probs(1.0, 1.0)
        self.assertGreaterEqual(p["pml"], 0)
        self.assertLessEqual(p["pml"], 100)


class TestBuildModel(unittest.TestCase):
    def test_returns_tuple_of_three(self):
        strengths, league_avg, all_teams = build_model()
        self.assertIsInstance(strengths, dict)
        self.assertIsInstance(league_avg, float)
        self.assertIsInstance(all_teams, list)

    def test_all_teams_non_empty(self):
        _, _, all_teams = build_model()
        self.assertGreater(len(all_teams), 0)

    def test_league_avg_positive(self):
        _, league_avg, _ = build_model()
        self.assertGreater(league_avg, 0)

    def test_strengths_contains_all_teams(self):
        strengths, _, all_teams = build_model()
        for team in all_teams:
            self.assertIn(team, strengths)

    def test_team_strength_has_required_keys(self):
        strengths, _, all_teams = build_model()
        for team in all_teams:
            s = strengths[team]
            for key in ("attack", "defense", "attack_hist", "defense_hist",
                        "attack_form", "defense_form", "form_obs", "pj", "pj_hist"):
                self.assertIn(key, s, f"{team} missing key {key}")

    def test_strength_values_positive(self):
        strengths, _, all_teams = build_model()
        for team in all_teams:
            s = strengths[team]
            self.assertGreater(s["attack"], 0, f"{team} attack <= 0")
            self.assertGreater(s["defense"], 0, f"{team} defense <= 0")

    def test_estimated_flag_present(self):
        strengths, _, all_teams = build_model()
        for team in all_teams:
            s = strengths[team]
            self.assertIn("estimated", s)


class TestGetCombinedStrength(unittest.TestCase):
    def test_returns_two_floats(self):
        strengths, _, _ = build_model()
        atk, dfn = get_combined_strength("Argentina", strengths)
        self.assertIsInstance(atk, float)
        self.assertIsInstance(dfn, float)
        self.assertGreater(atk, 0)
        self.assertGreater(dfn, 0)

    def test_unknown_team_returns_default(self):
        strengths, _, _ = build_model()
        atk, dfn = get_combined_strength("NonExistent", strengths)
        self.assertGreater(atk, 0)
        self.assertGreater(dfn, 0)


class TestRankingCoverage(unittest.TestCase):
    def test_all_teams_have_ranking_factor(self):
        all_teams = [t for c in conf_map.values() for t in c]
        missing = [t for t in all_teams if t not in RANKING_FACTOR]
        self.assertEqual(missing, [],
                         f"Teams missing from RANKING_FACTOR: {missing}")


if __name__ == "__main__":
    unittest.main()
