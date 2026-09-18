import sys, io, json, traceback
import iq_service, analysis

out = []
def log(*args):
    out.append(" ".join(str(a) for a in args))

ok, msg = iq_service.connect()
log("CONEXAO:", ok, msg)

for asset in ("EURUSD", "GBPUSD"):
    for expiry in ("1min", "5min", "15min"):
        interval = analysis.TIMEFRAMES[expiry]["interval"]
        try:
            raw = iq_service.get_candles_smart(asset, interval, 240)
            candles = analysis._drop_incomplete_candle(raw, interval)
            log("=" * 60)
            log(asset, expiry, "| raw:", len(raw), "| apos drop:", len(candles))
            if raw:
                log("  ultimo raw time:", raw[-1].get("time"), "| now:", int(__import__('time').time()))
            df = analysis.compute_indicators(analysis.candles_to_df(candles))
            if df.empty:
                log("  DF VAZIO")
                continue
            row = df.iloc[-1]
            log("  linhas:", len(df))
            log("  EMA20:", round(float(row['EMA20']), 6), "EMA50:", round(float(row['EMA50']), 6))
            log("  ADX:", round(float(row['ADX']), 2), "RSI:", round(float(row['RSI']), 2), "ATR:", round(float(row['ATR']), 6))
            log("  close:", round(float(row['Close']), 6), "open:", round(float(row['Open']), 6))
            log("  dist_ema20:", round(abs(float(row['Close']) - float(row['EMA20'])), 6), "limite(0.8*ATR):", round(float(row['ATR']) * 0.8, 6))
            for strat in ("trend_pullback", "breakout", "mean_reversion", "support_resistance", "momentum"):
                d = analysis._strategy_signal(df, strat)
                log("    [" + strat + "]", d["signal"], "score", d["score"])
        except Exception as e:
            log("ERRO", asset, expiry, repr(e))
            log(traceback.format_exc())

# Testa analyze_timeframe (usado pelo backtest)
try:
    interval = analysis.TIMEFRAMES["5min"]["interval"]
    candles = analysis._drop_incomplete_candle(iq_service.get_candles_smart("EURUSD", interval, 240), interval)
    df = analysis.candles_to_df(candles)
    r = analysis.analyze_timeframe(df, "5min")
    log("=" * 60)
    log("analyze_timeframe OK:", r["signal"] if r else None, "| indicadores:", len(r["indicators"]) if r else 0)
except Exception as e:
    log("analyze_timeframe ERRO:", repr(e))
    log(traceback.format_exc())

with io.open("diag_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("WROTE", len(out), "lines")
