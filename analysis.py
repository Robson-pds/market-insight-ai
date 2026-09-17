"""
Motor de análise para opções binárias.
Fonte: IQ Option via iq_service.
"""
from __future__ import annotations

import time
import numpy as np
import pandas as pd

import iq_service
import news_service


# Timeframes em segundos (o que a IQ Option usa internamente)
TIMEFRAMES = {
    "1min":  {"label": "1 minuto",   "interval": 60},
    "5min":  {"label": "5 minutos",  "interval": 300},
    "15min": {"label": "15 minutos", "interval": 900},
    "30min": {"label": "30 minutos", "interval": 1800},
}

STRATEGIES = {
    "trend_pullback": {
        "name": "Retração na tendência",
        "description": "Busca uma correção até a média em uma tendência confirmada.",
    },
    "breakout": {
        "name": "Rompimento de faixa",
        "description": "Exige fechamento além da máxima ou mínima recente com expansão de volatilidade.",
    },
    "mean_reversion": {
        "name": "Reversão à média",
        "description": "Procura exaustão nas bandas e confirmação de retorno pelo RSI.",
    },
    "support_resistance": {
        "name": "Suporte e resistência",
        "description": "Procura rejeição clara em zonas extremas recentes.",
    },
    "momentum": {
        "name": "Momentum",
        "description": "Exige alinhamento de médias, MACD e força direcional.",
    },
}

_SIGNAL_CACHE: dict[tuple[str, str, str], dict] = {}


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df.loc[:, "datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("datetime").sort_index()
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    # Remove duplicatas (stream pode repetir)
    df = df[~df.index.duplicated(keep="last")]
    return df


def _drop_incomplete_candle(candles: list[dict], interval: int) -> list[dict]:
    """Remove o candle correspondente ao intervalo que ainda está aberto."""
    if not candles or interval <= 0:
        return candles

    current_bucket = int(time.time()) // interval * interval
    return [candle for candle in candles if int(candle.get("time", 0)) < current_bucket]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close, high, low = df["Close"], df["High"], df["Low"]

    for p in (5, 10, 20):
        df.loc[:, f"EMA{p}"] = close.ewm(span=p, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df.loc[:, "RSI"] = 100 - (100 / (1 + rs))

    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    df.loc[:, "Stoch_K"] = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    df.loc[:, "Stoch_D"] = df["Stoch_K"].rolling(3).mean()

    rsi = df["RSI"]
    rmin, rmax = rsi.rolling(14).min(), rsi.rolling(14).max()
    df.loc[:, "StochRSI"] = (rsi - rmin) / (rmax - rmin).replace(0, np.nan) * 100

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df.loc[:, "MACD"] = ema12 - ema26
    df.loc[:, "MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df.loc[:, "MACD_hist"] = df["MACD"] - df["MACD_signal"]

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df.loc[:, "BB_upper"] = mid + 2 * std
    df.loc[:, "BB_lower"] = mid - 2 * std

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df.loc[:, "ATR"] = tr.rolling(14).mean()

    up, down = high.diff(), -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    atr14 = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr14.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr14.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df.loc[:, "ADX"] = dx.rolling(14).mean()
    df.loc[:, "PLUS_DI"] = plus_di
    df.loc[:, "MINUS_DI"] = minus_di

    tp = (high + low + close) / 3
    sma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df.loc[:, "CCI"] = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))

    df.loc[:, "WilliamsR"] = -100 * (high14 - close) / (high14 - low14).replace(0, np.nan)
    df.loc[:, "EMA50"] = close.ewm(span=50, adjust=False).mean()
    df.loc[:, "EMA9"] = close.ewm(span=9, adjust=False).mean()
    df.loc[:, "EMA21"] = close.ewm(span=21, adjust=False).mean()
    df.loc[:, "range_high_20"] = high.shift(1).rolling(20).max()
    df.loc[:, "range_low_20"] = low.shift(1).rolling(20).min()
    df.loc[:, "body"] = (close - df["Open"]).abs()
    df.loc[:, "upper_wick"] = high - pd.concat([df["Open"], close], axis=1).max(axis=1)
    df.loc[:, "lower_wick"] = pd.concat([df["Open"], close], axis=1).min(axis=1) - low
    return df


def _signal(direction: str, score: int, reason: str, indicators: list[str], min_score: int = 3) -> dict:
    score = int(score)
    if score < min_score:
        direction = "AGUARDAR"
    return {
        "signal": direction,
        "score": score,
        "reason": reason,
        "indicators": indicators,
    }


def _strategy_signal(df: pd.DataFrame, strategy: str) -> dict:
    """Aplica uma estrategia isolada; indicadores de outras estrategias nao votam."""
    if len(df) < 60:
        return _signal("AGUARDAR", 0, "Dados insuficientes para esta estrategia.", [])
    row, prev = df.iloc[-1], df.iloc[-2]
    close = float(row["Close"])
    atr = float(row["ATR"])
    if not np.isfinite(atr) or atr <= 0:
        return _signal("AGUARDAR", 0, "Volatilidade invalida ou insuficiente.", [])

    if strategy == "trend_pullback":
        up = row["EMA20"] > row["EMA50"] and row["ADX"] >= 20
        down = row["EMA20"] < row["EMA50"] and row["ADX"] >= 20
        near_ema = abs(close - row["EMA20"]) <= atr * 0.8
        call_score = sum((up, near_ema, 45 <= row["RSI"] <= 65, close > row["Open"]))
        put_score = sum((down, near_ema, 35 <= row["RSI"] <= 55, close < row["Open"]))
        call = call_score >= 3 and call_score > put_score
        put = put_score >= 3 and put_score > call_score
        direction = "CALL" if call else "PUT" if put else "AGUARDAR"
        score = max(call_score, put_score)
        return _signal(direction, score, "Retração confirmada na EMA20 dentro de tendência." if direction != "AGUARDAR" else "Retração sem confirmação suficiente.", ["EMA20/EMA50", "ADX", "RSI", "candle de rejeição"])

    if strategy == "breakout":
        expansion = row["High"] - row["Low"] >= atr * 1.1
        call_score = sum((close > row["range_high_20"], expansion, close > row["Open"]))
        put_score = sum((close < row["range_low_20"], expansion, close < row["Open"]))
        call = call_score >= 2 and call_score > put_score
        put = put_score >= 2 and put_score > call_score
        direction = "CALL" if call else "PUT" if put else "AGUARDAR"
        return _signal(direction, max(call_score, put_score), "Rompimento confirmado por evidências de faixa e expansão." if direction != "AGUARDAR" else "Rompimento ainda sem confirmação suficiente.", ["máxima/mínima de 20 candles", "ATR", "candle de expansão"], min_score=2)

    if strategy == "mean_reversion":
        call_score = sum((close <= row["BB_lower"], row["RSI"] < 35, row["RSI"] > prev["RSI"]))
        put_score = sum((close >= row["BB_upper"], row["RSI"] > 65, row["RSI"] < prev["RSI"]))
        call = call_score >= 2 and call_score > put_score
        put = put_score >= 2 and put_score > call_score
        direction = "CALL" if call else "PUT" if put else "AGUARDAR"
        return _signal(direction, max(call_score, put_score), "Reversão confirmada por evidências de banda e RSI." if direction != "AGUARDAR" else "Reversão sem confirmação suficiente.", ["Bollinger", "RSI", "reversão do RSI"], min_score=2)

    if strategy == "support_resistance":
        support = df["Low"].iloc[-21:-1].min()
        resistance = df["High"].iloc[-21:-1].max()
        call_score = sum((row["Low"] <= support + atr * 0.15, row["lower_wick"] > row["body"] * 1.2, close > row["Open"]))
        put_score = sum((row["High"] >= resistance - atr * 0.15, row["upper_wick"] > row["body"] * 1.2, close < row["Open"]))
        call = call_score >= 2 and call_score > put_score
        put = put_score >= 2 and put_score > call_score
        direction = "CALL" if call else "PUT" if put else "AGUARDAR"
        return _signal(direction, max(call_score, put_score), "Rejeição confirmada por evidências de zona e candle." if direction != "AGUARDAR" else "Rejeição sem confirmação suficiente.", ["zona de 20 candles", "pavio", "candle de rejeição"], min_score=2)

    if strategy == "momentum":
        call_score = sum((row["EMA9"] > row["EMA21"], row["MACD"] > row["MACD_signal"], row["ADX"] >= 22, row["RSI"] > 52))
        put_score = sum((row["EMA9"] < row["EMA21"], row["MACD"] < row["MACD_signal"], row["ADX"] >= 22, row["RSI"] < 48))
        call = call_score >= 3 and call_score > put_score
        put = put_score >= 3 and put_score > call_score
        direction = "CALL" if call else "PUT" if put else "AGUARDAR"
        score = max(call_score, put_score)
        return _signal(direction, score, "Momentum alinhado entre médias, MACD e ADX." if direction != "AGUARDAR" else "Momentum sem alinhamento suficiente.", ["EMA9/EMA21", "MACD", "ADX", "RSI"])

    raise ValueError(f"Estratégia desconhecida: {strategy}")


def _proximity(score: int, max_score: int = 4) -> dict:
    ratio = max(0.0, min(1.0, score / max_score))
    if ratio >= 0.75:
        label = "SINAL MUITO PRÓXIMO"
    elif ratio >= 0.5:
        label = "ATENÇÃO"
    else:
        label = "AGUARDAR"
    return {"label": label, "percent": round(ratio * 100, 1)}


def estimate_historical_accuracy(df: pd.DataFrame, strategy: str, horizon: int) -> dict:
    """Estima acerto fora da amostra atual usando candles fechados anteriores."""
    if len(df) < 80:
        return {"rate": None, "sample_size": 0, "label": "Amostra insuficiente"}
    wins = total = 0
    start = max(60, len(df) - 160)
    for end in range(start, len(df) - horizon):
        decision = _strategy_signal(df.iloc[:end], strategy)
        if decision["signal"] not in ("CALL", "PUT"):
            continue
        entry = float(df["Close"].iloc[end - 1])
        exit_price = float(df["Close"].iloc[end + horizon - 1])
        wins += int((decision["signal"] == "CALL" and exit_price > entry) or (decision["signal"] == "PUT" and exit_price < entry))
        total += 1
    return {
        "rate": round(wins / total * 100, 1) if total else None,
        "sample_size": total,
        "label": "Estimativa histórica; não garante o próximo resultado.",
    }


# --------- Votos ---------
def _vote_rsi(df, i):
    rsi = df["RSI"].iloc[i]
    prev = df["RSI"].iloc[i-1] if i >= 1 else rsi
    if pd.isna(rsi) or pd.isna(prev): return 0, "RSI sem dados"
    if rsi < 30 and rsi > prev: return 1, f"RSI {rsi:.1f} saindo de sobrevenda — CALL"
    if rsi > 70 and rsi < prev: return -1, f"RSI {rsi:.1f} saindo de sobrecompra — PUT"
    if rsi < 30: return 1, f"RSI {rsi:.1f} sobrevenda — CALL"
    if rsi > 70: return -1, f"RSI {rsi:.1f} sobrecompra — PUT"
    if rsi > 55 and rsi > prev: return 1, f"RSI {rsi:.1f} viés de alta — CALL"
    if rsi < 45 and rsi < prev: return -1, f"RSI {rsi:.1f} viés de baixa — PUT"
    return 0, f"RSI {rsi:.1f} neutro"


def _vote_stoch(df, i):
    k, d = df["Stoch_K"].iloc[i], df["Stoch_D"].iloc[i]
    pk = df["Stoch_K"].iloc[i-1] if i >= 1 else k
    pdd = df["Stoch_D"].iloc[i-1] if i >= 1 else d
    if pd.isna(k) or pd.isna(d) or pd.isna(pk) or pd.isna(pdd): return 0, "Stoch sem dados"
    if pk <= pdd and k > d and k < 30: return 1, f"Stoch cruzou p/ cima em sobrevenda ({k:.1f}) — CALL"
    if pk >= pdd and k < d and k > 70: return -1, f"Stoch cruzou p/ baixo em sobrecompra ({k:.1f}) — PUT"
    if k < 20: return 1, f"Stoch {k:.1f} sobrevenda — CALL"
    if k > 80: return -1, f"Stoch {k:.1f} sobrecompra — PUT"
    return 0, f"Stoch {k:.1f} neutro"


def _vote_stoch_rsi(df, i):
    sr = df["StochRSI"].iloc[i]
    if pd.isna(sr): return 0, "Stoch RSI sem dados"
    if sr < 20: return 1, f"Stoch RSI {sr:.1f} sobrevenda — CALL"
    if sr > 80: return -1, f"Stoch RSI {sr:.1f} sobrecompra — PUT"
    return 0, f"Stoch RSI {sr:.1f} neutro"


def _vote_macd(df, i):
    m, s = df["MACD"].iloc[i], df["MACD_signal"].iloc[i]
    h = df["MACD_hist"].iloc[i]
    ph = df["MACD_hist"].iloc[i - 1] if i >= 1 else h
    if pd.isna(m) or pd.isna(s) or pd.isna(h) or pd.isna(ph):
        return 0, "MACD sem dados"
    if m > s and h > ph:
        return 1, "MACD acima do sinal com histograma subindo — CALL"
    if m < s and h < ph:
        return -1, "MACD abaixo do sinal com histograma caindo — PUT"
    if m > s:
        return 1, "MACD acima da linha de sinal — CALL"
    if m < s:
        return -1, "MACD abaixo da linha de sinal — PUT"
    return 0, "MACD neutro"


# Peso relativo dos indicadores usados pelo motor legado de detalhamento.
INDICATOR_WEIGHTS = {
    "RSI (14)":       1.5,
    "Stochastic":     1.3,
    "Stoch RSI":      1.0,
    "MACD":           1.5,
    "EMA 5/10":       1.0,
    "EMA 10/20":      1.0,
    "Bollinger":      1.2,
    "ADX / DI":       1.3,
    "CCI":            0.8,
    "Williams %R":    0.8,
}

# Quórum mínimo para o sinal ser considerado acionável
MIN_DIRECTIONAL_VOTES = 5    # pelo menos 5 dos 10 indicadores precisam votar
MIN_MARGIN = 3               # diferença mínima entre bulls e bears
MIN_COVERAGE = 60.0          # 60% dos indicadores precisam ter opinião


def aggregate_votes(votes):
    total = len(votes)

    # Soma ponderada dos votos
    weighted_bull = sum(INDICATOR_WEIGHTS.get(v["name"], 1.0) for v in votes if v["vote"] == 1)
    weighted_bear = sum(INDICATOR_WEIGHTS.get(v["name"], 1.0) for v in votes if v["vote"] == -1)
    total_weight = sum(INDICATOR_WEIGHTS.get(v["name"], 1.0) for v in votes)

    bulls = sum(1 for v in votes if v["vote"] == 1)
    bears = sum(1 for v in votes if v["vote"] == -1)
    neutrals = sum(1 for v in votes if v["vote"] == 0)

    directional = bulls + bears
    coverage = (directional / total * 100) if total else 0.0

    # Direção majoritária (por peso)
    if weighted_bull > weighted_bear:
        direction = "CALL"
        winner = bulls
        loser = bears
    elif weighted_bear > weighted_bull:
        direction = "PUT"
        winner = bears
        loser = bulls
    else:
        direction = "NEUTRAL"
        winner = max(bulls, bears)
        loser = min(bulls, bears)

    margin = winner - loser

    # ----- Verificações de quórum -----
    # Se não há votos direcionais suficientes, é NEUTRAL
    if directional < MIN_DIRECTIONAL_VOTES:
        direction = "NEUTRAL"

    # Se a margem entre vencedor e perdedor é pequena, é NEUTRAL
    if margin < MIN_MARGIN:
        direction = "NEUTRAL"

    # Se a cobertura é baixa, é NEUTRAL
    if coverage < MIN_COVERAGE:
        direction = "NEUTRAL"

    # Confiança = concordância × cobertura
    if direction == "NEUTRAL":
        confidence = round(coverage * 0.5, 1)  # neutro reflete quão "ativo" o mercado está
        strength = "Sem direção"
    else:
        concordancia = winner / directional if directional > 0 else 0
        cobertura_factor = min(1.0, coverage / 80.0)   # 80% ou mais = fator 1.0
        confidence = round(concordancia * cobertura_factor * 100, 1)
        if confidence >= 75:
            strength = "Forte"
        elif confidence >= 55:
            strength = "Moderada"
        else:
            strength = "Fraca"

    return {
        "direction": direction,
        "confidence": confidence,
        "confluence_score": confidence,
        "strength": strength,
        "bulls": bulls,
        "bears": bears,
        "neutrals": neutrals,
        "total": total,
        "coverage": round(coverage, 1),
        "margin": margin,
    }

def analyze_timeframe(df: pd.DataFrame, tf_key: str) -> dict | None:
    if df is None or len(df) < 30:
        return None
    df = compute_indicators(df.copy())
    i = len(df) - 1

    votes = []
    for name, fn in [
        ("RSI (14)", _vote_rsi), ("Stochastic", _vote_stoch),
        ("Stoch RSI", _vote_stoch_rsi), ("MACD", _vote_macd),
        ("EMA 5/10", lambda d, i: _vote_ema(d, i, "EMA5", "EMA10")),
        ("EMA 10/20", lambda d, i: _vote_ema(d, i, "EMA10", "EMA20")),
        ("Bollinger", _vote_bollinger), ("ADX / DI", _vote_adx),
        ("CCI", _vote_cci), ("Williams %R", _vote_williams),
    ]:
        v, reason = fn(df, i)
        votes.append({"name": name, "vote": v, "reason": reason})

    agg = aggregate_votes(votes)
    last = df.iloc[-1]
    return {
        "timeframe": tf_key,
        "price": float(last["Close"]),
        "signal": agg["direction"],
        "confidence": agg["confidence"],
        "strength": agg["strength"],
        "bulls": agg["bulls"],
        "bears": agg["bears"],
        "neutrals": agg["neutrals"],
        "total": agg["total"],
        "coverage": agg["coverage"],
        "margin": agg["margin"],
        "indicators": votes,
    }


def build_strategy(expiry: str, context: dict, trigger: dict | None, context_keys: tuple[str, ...]) -> dict:
    """Emite sinal apenas quando contexto e gatilho concordam.

    O score mede confluência dos dados atuais; não é uma probabilidade de acerto.
    """
    context_frames = [context.get(key) for key in context_keys]
    signals = [item.get("signal") for item in context_frames if item]
    base = {
        "expiry": expiry,
        "context_timeframes": list(context_keys),
        "recommendation": "AGUARDAR",
        "confidence": 0,
        "confluence_score": 0,
        "strength": "Sem dados",
    }

    if len(signals) != len(context_keys):
        return {
            **base,
            "reason": "Dados insuficientes para confirmar o contexto.",
            "warning": "Sem sinal: não há candles fechados suficientes.",
        }

    if signals[0] not in ("CALL", "PUT") or any(signal != signals[0] for signal in signals):
        return {
            **base,
            "confidence": round(sum(item.get("confidence", 0) for item in context_frames) / len(context_frames), 1),
            "confluence_score": round(sum(item.get("confidence", 0) for item in context_frames) / len(context_frames), 1),
            "strength": "Sem dados",
            "reason": "Os timeframes de contexto não estão alinhados.",
            "warning": "Conflito entre timeframes: aguarde uma estrutura mais limpa.",
        }

    if not trigger or trigger.get("signal") != signals[0]:
        return {
            **base,
            "confidence": round(sum(item.get("confidence", 0) for item in (*context_frames, trigger or {})) / (len(context_frames) + 1), 1),
            "confluence_score": round(sum(item.get("confidence", 0) for item in (*context_frames, trigger or {})) / (len(context_frames) + 1), 1),
            "strength": "Sem confirmação",
            "reason": f"O gatilho de {TIMEFRAMES[expiry]['label'].split()[0]} ainda não confirma o contexto.",
            "warning": "Não operar enquanto o gatilho não confirmar a direção.",
        }

    confidence = round(
        sum(item.get("confidence", 0) for item in (*context_frames, trigger)) / (len(context_frames) + 1),
        1,
    )
    if confidence >= 75:
        strength = "Forte"
    elif confidence >= 55:
        strength = "Moderada"
    else:
        strength = "Fraca"

    return {
        **base,
        "recommendation": signals[0],
        "confidence": confidence,
        "confluence_score": confidence,
        "strength": strength,
        "reason": f"Contexto de {', '.join(context_keys)} alinhado com o gatilho para {signals[0]}.",
        "warning": "Score de confluência não representa probabilidade de acerto.",
    }
    
def analyze_asset(asset: str, strategy: str = "trend_pullback") -> dict:
    """Analisa uma unica estrategia e retorna somente os tres prazos de entrada."""
    asset = asset.upper().replace("=X", "")
    if strategy not in STRATEGIES:
        raise ValueError(f"Estratégia desconhecida: {strategy}")

    now = int(time.time())
    news = news_service.get_news_risk(asset)
    signals = {}
    for expiry in ("1min", "5min", "15min"):
        cache_key = (asset, strategy, expiry)
        cached = _SIGNAL_CACHE.get(cache_key)
        if cached and cached["expires_at"] > now and "proximity" in cached and "historical_accuracy" in cached:
            signals[expiry] = {**cached, "locked": True, "seconds_remaining": cached["expires_at"] - now}
            continue

        interval = TIMEFRAMES[expiry]["interval"]
        candles = _drop_incomplete_candle(iq_service.get_candles_smart(asset, interval, 240), interval)
        df = compute_indicators(candles_to_df(candles))
        decision = _strategy_signal(df, strategy) if not df.empty else _signal("AGUARDAR", 0, "Sem dados de mercado.", [])
        if news["blocked"]:
            decision = _signal("AGUARDAR", 0, "Entrada bloqueada por notícia de alto impacto.", [event["title"] for event in news["events"]])

        # Alinha o vencimento ao proximo fechamento de vela da IQ Option.
        # Os timeframes sao contados a partir do epoch Unix: 1m, 5m e 15m.
        expires_at = ((now // interval) + 1) * interval
        horizon = max(1, interval // 60)
        decision["proximity"] = _proximity(decision["score"])
        decision["historical_accuracy"] = estimate_historical_accuracy(df, strategy, horizon)
        signals[expiry] = {
            **decision,
            "expiry": expiry,
            "expires_at": expires_at,
            "locked": False,
            "seconds_remaining": expires_at - now,
        }
        _SIGNAL_CACHE[cache_key] = signals[expiry]

    warning = news["warning"] if not news["available"] else None
    return {
        "asset": asset,
        "strategy": strategy,
        "strategy_name": STRATEGIES[strategy]["name"],
        "strategy_description": STRATEGIES[strategy]["description"],
        "signals": signals,
        "news": news,
        "warning": warning,
    }


def walkforward_asset(asset: str, expiry: str, count: int = 240) -> dict:
    """Mede o sinal do timeframe contra o candle seguinte sem olhar o futuro."""
    if expiry not in TIMEFRAMES or expiry == "30min":
        raise ValueError("expiry deve ser 1min, 5min ou 15min")

    cfg = TIMEFRAMES[expiry]
    candles = _drop_incomplete_candle(
        iq_service.get_candles_smart(asset.upper().replace("=X", ""), cfg["interval"], count),
        cfg["interval"],
    )
    df = candles_to_df(candles)
    evaluated = wins = losses = 0
    for end in range(40, len(df)):
        signal = analyze_timeframe(df.iloc[:end], expiry)
        if not signal or signal["signal"] not in ("CALL", "PUT"):
            continue
        close_before = float(df["Close"].iloc[end - 1])
        close_after = float(df["Close"].iloc[end])
        won = (signal["signal"] == "CALL" and close_after > close_before) or (
            signal["signal"] == "PUT" and close_after < close_before
        )
        evaluated += 1
        wins += int(won)
        losses += int(not won)

    return {
        "asset": asset.upper().replace("=X", ""),
        "expiry": expiry,
        "sample_size": evaluated,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / evaluated * 100, 1) if evaluated else None,
        "note": "Proxy walk-forward de um timeframe; não substitui backtest da estratégia completa nem garante resultado futuro.",
    }


def get_chart_data(asset: str, interval: int = 300, count: int = 200) -> dict:
    """Retorna candles + EMAs + Bollinger para o gráfico."""
    candles = iq_service.get_candles_smart(asset, interval, count)
    df = candles_to_df(candles)
    if df.empty:
        return {"candles": [], "ema10": [], "ema20": [], "bb_upper": [], "bb_lower": []}
    df = compute_indicators(df)
    return {
        "candles": [
            {"time": int(idx.timestamp()), "open": float(r["Open"]),
             "high": float(r["High"]), "low": float(r["Low"]), "close": float(r["Close"])}
            for idx, r in df.iterrows()
        ],
        "ema10": [{"time": int(idx.timestamp()), "value": float(r["EMA10"])}
                  for idx, r in df.iterrows() if not pd.isna(r["EMA10"])],
        "ema20": [{"time": int(idx.timestamp()), "value": float(r["EMA20"])}
                  for idx, r in df.iterrows() if not pd.isna(r["EMA20"])],
        "bb_upper": [{"time": int(idx.timestamp()), "value": float(r["BB_upper"])}
                     for idx, r in df.iterrows() if not pd.isna(r["BB_upper"])],
        "bb_lower": [{"time": int(idx.timestamp()), "value": float(r["BB_lower"])}
                     for idx, r in df.iterrows() if not pd.isna(r["BB_lower"])],
    }