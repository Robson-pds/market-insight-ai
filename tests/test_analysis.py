import os
import sys
import unittest
from unittest.mock import patch

# Permite importar os módulos do app/ sem instalação (layout: app/ + tests/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

import numpy as np
import pandas as pd

import analysis


def make_candles(interval=60, count=100):
    return [
        {
            "time": (index + 1) * interval,
            "open": 1.0 + index / 1000,
            "high": 1.2 + index / 1000,
            "low": 0.8 + index / 1000,
            "close": 1.1 + index / 1000,
            "volume": 10,
        }
        for index in range(count)
    ]


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        analysis._SIGNAL_CACHE.clear()

    def test_analyze_asset_uses_one_candle_horizon_for_each_expiry(self):
        captured = []
        safe_news = {"available": True, "blocked": False, "events": [], "warning": None}
        with (
            patch.object(analysis.time, "time", return_value=20_000_000),
            patch.object(analysis.news_service, "get_news_risk", return_value=safe_news),
            patch.object(analysis.iq_service, "get_candles_smart", side_effect=lambda _asset, interval, _count: make_candles(interval)),
            patch.object(analysis, "estimate_historical_accuracy", side_effect=lambda _df, _strategy, horizon: captured.append(horizon) or {"rate": None, "sample_size": 0, "label": "teste"}),
        ):
            analysis.analyze_asset("EURUSD")

        self.assertEqual(captured, [1, 1, 1])

    def test_unavailable_calendar_invalidates_actionable_cache(self):
        unavailable = {"available": False, "blocked": False, "events": [], "warning": "indisponível"}
        analysis._SIGNAL_CACHE[("EURUSD", "trend_pullback", "1min")] = {
            "signal": "CALL",
            "expires_at": 99_999_999,
            "proximity": {},
            "historical_accuracy": {},
        }
        with (
            patch.object(analysis.time, "time", return_value=20_000_000),
            patch.object(analysis.news_service, "get_news_risk", return_value=unavailable),
            patch.object(analysis.iq_service, "get_candles_smart", side_effect=lambda _asset, interval, _count: make_candles(interval)),
        ):
            result = analysis.analyze_asset("EURUSD")

        self.assertTrue(all(item["signal"] == "AGUARDAR" for item in result["signals"].values()))
        self.assertFalse(analysis._SIGNAL_CACHE)

    def test_rsi_is_defined_for_uninterrupted_rise(self):
        prices = np.arange(1.0, 82.0)
        frame = pd.DataFrame({
            "Open": prices,
            "High": prices + 0.2,
            "Low": prices - 0.2,
            "Close": prices,
            "Volume": 1.0,
        })
        result = analysis.compute_indicators(frame)
        self.assertEqual(result["RSI"].iloc[-1], 100.0)

    def test_invalid_ohlc_is_removed(self):
        candles = make_candles(count=2)
        candles[1]["high"] = candles[1]["close"] - 0.1
        self.assertEqual(len(analysis.candles_to_df(candles)), 1)

    def test_proximity_uses_strategy_maximum(self):
        self.assertEqual(analysis._proximity(2, 3), {"label": "ATENÇÃO", "percent": 66.7})
        self.assertEqual(analysis._proximity(3, 3), {"label": "SINAL MUITO PRÓXIMO", "percent": 100.0})


if __name__ == "__main__":
    unittest.main()