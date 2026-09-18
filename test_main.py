import unittest
from unittest.mock import patch

import main


def analysis_result(directions, scores):
    signals = {
        expiry: {"signal": direction, "score": score, "reason": "teste"}
        for expiry, direction, score in zip(("1min", "5min", "15min"), directions, scores)
    }
    return {"signals": signals, "news": {"blocked": False}}


class OpportunitiesTests(unittest.TestCase):
    def test_single_opportunity_exposes_only_three_expiries(self):
        result = analysis_result(["CALL", "PUT", "AGUARDAR"], [4, 3, 1])
        with (
            patch.object(main, "_ensure_connected"),
            patch.object(main.iq_service, "list_assets", return_value=["EURUSD"]),
            patch.object(main.analysis, "analyze_asset", return_value=result),
        ):
            response = main.opportunity("eurusd", "trend_pullback")

        self.assertEqual(set(response["signals"]), {"1min", "5min", "15min"})
        self.assertEqual(response["scores"], {"1min": 4, "5min": 3, "15min": 1})

    def test_consensus_ranks_above_higher_conflicting_score(self):
        results = {
            "CONFLITO": analysis_result(["CALL", "PUT", "CALL"], [3, 3, 3]),
            "ALINHADO": analysis_result(["PUT", "PUT", "PUT"], [2, 2, 2]),
        }
        with (
            patch.object(main, "_ensure_connected"),
            patch.object(main.iq_service, "list_assets", return_value=["CONFLITO", "ALINHADO"]),
            patch.object(main.analysis, "analyze_asset", side_effect=lambda asset, _strategy: results[asset]),
        ):
            response = main.opportunities("support_resistance")

        self.assertEqual([item["asset"] for item in response["pairs"]], ["ALINHADO", "CONFLITO"])
        self.assertEqual(response["pairs"][0]["max_score"], 9)


if __name__ == "__main__":
    unittest.main()