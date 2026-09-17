"""
Market Insight AI — Motor de análise técnica, risco e sentimento.
Uso educacional. Sem recomendação de investimento.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _safe(v):
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except Exception:
        return None


# ------------------------------------------------------------------
# Indicadores técnicos
# ------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]

    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()
    df["EMA9"] = close.ewm(span=9, adjust=False).mean()
    df["EMA21"] = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["BB_mid"] = mid
    df["BB_upper"] = mid + 2 * std
    df["BB_lower"] = mid - 2 * std
    df["BB_pct"] = (close - df["BB_lower"]) / (df["BB_upper"] - df["BB_lower"]).replace(0, np.nan)

    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    df["Stoch_K"] = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()

    df["Vol_SMA20"] = vol.rolling(20).mean()

    return df


# ------------------------------------------------------------------
# Motor de scoring (IA quantitativa baseada em regras ponderadas)
# ------------------------------------------------------------------
def score_signals(df: pd.DataFrame):
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    signals = []

    # RSI
    rsi = last["RSI"]
    if pd.notna(rsi):
        if rsi < 30:
            s, r = 70, f"RSI em {rsi:.1f} — zona de sobrevenda, possível reversão de alta."
        elif rsi < 45:
            s, r = 25, f"RSI em {rsi:.1f} — levemente descontado."
        elif rsi > 70:
            s, r = -70, f"RSI em {rsi:.1f} — sobrecomprado, risco de correção."
        elif rsi > 55:
            s, r = -20, f"RSI em {rsi:.1f} — levemente esticado."
        else:
            s, r = 0, f"RSI em {rsi:.1f} — neutro."
        signals.append({"name": "RSI (14)", "score": s, "reason": r,
                        "value": round(float(rsi), 2)})

    # MACD
    if pd.notna(last["MACD_hist"]) and pd.notna(prev["MACD_hist"]):
        hist = last["MACD_hist"]
        cross_up = prev["MACD_hist"] < 0 <= hist
        cross_dn = prev["MACD_hist"] > 0 >= hist
        if cross_up:
            s, r = 80, "Cruzamento de alta do MACD (histograma virou positivo)."
        elif cross_dn:
            s, r = -80, "Cruzamento de baixa do MACD (histograma virou negativo)."
        elif hist > 0:
            s, r = 40, "MACD acima da linha de sinal — momentum de alta."
        else:
            s, r = -40, "MACD abaixo da linha de sinal — momentum de baixa."
        signals.append({"name": "MACD", "score": s, "reason": r,
                        "value": round(float(hist), 4)})

    # EMA 9/21
    if pd.notna(last["EMA9"]) and pd.notna(last["EMA21"]):
        if last["EMA9"] > last["EMA21"]:
            s, r = 50, "EMA9 acima da EMA21 — tendência de curto prazo de alta."
        else:
            s, r = -50, "EMA9 abaixo da EMA21 — tendência de curto prazo de baixa."
        signals.append({"name": "EMA 9/21", "score": s, "reason": r, "value": None})

    # SMA 50/200
    if pd.notna(last["SMA50"]) and pd.notna(last["SMA200"]):
        if last["SMA50"] > last["SMA200"]:
            s, r = 60, "SMA50 acima da SMA200 — tendência primária de alta."
        else:
            s, r = -60, "SMA50 abaixo da SMA200 — tendência primária de baixa."
        signals.append({"name": "SMA 50/200", "score": s, "reason": r, "value": None})

    # Bollinger
    if pd.notna(last["BB_pct"]):
        bb = last["BB_pct"]
        if bb < 0:
            s, r = 60, "Preço abaixo da Banda de Bollinger inferior — sobrevendido."
        elif bb < 0.2:
            s, r = 30, "Preço próximo à banda inferior."
        elif bb > 1:
            s, r = -60, "Preço acima da banda superior — sobrecomprado."
        elif bb > 0.8:
            s, r = -30, "Preço próximo à banda superior."
        else:
            s, r = 0, "Preço dentro das bandas — neutro."
        signals.append({"name": "Bollinger", "score": s, "reason": r,
                        "value": round(float(bb), 2)})

    # Estocástico
    if pd.notna(last["Stoch_K"]) and pd.notna(last["Stoch_D"]):
        k, d = last["Stoch_K"], last["Stoch_D"]
        if k < 20 and k > d:
            s, r = 55, "Estocástico em sobrevenda com cruzamento de alta."
        elif k > 80 and k < d:
            s, r = -55, "Estocástico em sobrecompra com cruzamento de baixa."
        elif k < 20:
            s, r = 25, "Estocástico em zona de sobrevenda."
        elif k > 80:
            s, r = -25, "Estocástico em zona de sobrecompra."
        else:
            s, r = 0, "Estocástico neutro."
        signals.append({"name": "Estocástico", "score": s, "reason": r,
                        "value": round(float(k), 2)})

    # Volume
    if pd.notna(last["Vol_SMA20"]) and last["Vol_SMA20"] > 0:
        ratio = last["Volume"] / last["Vol_SMA20"]
        if ratio > 1.5:
            s = 20 if last["Close"] > prev["Close"] else -20
            r = f"Volume {ratio:.1f}× acima da média — confirma o movimento."
            signals.append({"name": "Volume", "score": s, "reason": r,
                            "value": round(float(ratio), 2)})

    return signals


def aggregate(signals):
    if not signals:
        return 0.0, "Neutro"
    avg = sum(s["score"] for s in signals) / len(signals)
    score = max(-100.0, min(100.0, avg))
    if score >= 55:
        label = "Compra Forte"
    elif score >= 22:
        label = "Compra"
    elif score <= -55:
        label = "Venda Forte"
    elif score <= -22:
        label = "Venda"
    else:
        label = "Neutro"
    return round(score, 1), label


def confidence(signals):
    if not signals:
        return 0.0
    pos = sum(1 for s in signals if s["score"] > 10)
    neg = sum(1 for s in signals if s["score"] < -10)
    if pos + neg == 0:
        return 25.0
    return round(max(pos, neg) / len(signals) * 100, 1)


# ------------------------------------------------------------------
# Métricas de risco
# ------------------------------------------------------------------
def risk_metrics(df: pd.DataFrame) -> dict:
    close = df["Close"].dropna()
    returns = close.pct_change().dropna()
    vol_annual = float(returns.std() * np.sqrt(252) * 100) if len(returns) > 1 else 0.0

    atr = float(df["ATR"].iloc[-1]) if pd.notna(df["ATR"].iloc[-1]) else 0.0
    price = float(close.iloc[-1])
    atr_pct = (atr / price * 100) if price else 0.0

    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = float(dd.min() * 100) if len(dd) else 0.0

    stop = price - 2 * atr
    stop_pct = (stop - price) / price * 100 if price else 0.0

    return {
        "volatility_annual_pct": round(vol_annual, 2),
        "atr": round(atr, 4),
        "atr_pct": round(atr_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "suggested_stop_loss": round(stop, 4),
        "stop_loss_pct": round(stop_pct, 2),
    }


# ------------------------------------------------------------------
# Sentimento léxico (PT/EN)
# ------------------------------------------------------------------
_POS = {"alta", "sobe", "lucro", "crescimento", "recorde", "ganho", "positivo",
        "supera", "avança", "aprovação", "otimista", "expande", "valorização",
        "forte", "melhora", "upgrade", "compra", "beats", "rally", "surge", "up"}
_NEG = {"queda", "cai", "prejuízo", "perda", "negativo", "abaixo", "risco",
        "corte", "demissão", "fraude", "investigação", "rebaixa", "venda",
        "fraco", "piora", "desaceleração", "crash", "miss", "plunge", "down"}


def score_text(text: str) -> float:
    if not text:
        return 0.0
    t = text.lower()
    pos = sum(1 for w in _POS if w in t)
    neg = sum(1 for w in _NEG if w in t)
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg) * 100, 1)


# ------------------------------------------------------------------
# LLM opcional
# ------------------------------------------------------------------
def llm_report(context: str):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=[
                {"role": "system",
                 "content": ("Você é um analista quantitativo. Gere um relatório curto (máx 5 parágrafos), "
                             "imparcial e em português do Brasil. Sempre destaque riscos, cite os indicadores "
                             "relevantes e NUNCA prometa retornos ou recomende compra/venda como certeza. "
                             "Deixe claro que é material educacional.")},
                {"role": "user", "content": context},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("[LLM] erro:", e)
        return None


# ------------------------------------------------------------------
# Orquestrador
# ------------------------------------------------------------------
def analyze_ticker(ticker: str, period: str = "6mo") -> dict:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"Sem dados para o ticker '{ticker}'. Verifique o símbolo.")

    df = compute_indicators(df)
    signals = score_signals(df)
    score, label = aggregate(signals)
    conf = confidence(signals)
    risk = risk_metrics(df)

    try:
        info = t.info or {}
    except Exception:
        info = {}

    # Notícias — suporta formato antigo e novo do yfinance
    news_items = []
    try:
        raw = t.news or []
        for n in raw[:12]:
            if not isinstance(n, dict):
                continue
            if "content" in n and isinstance(n["content"], dict):
                content = n["content"]
                title = content.get("title", "")
                prov = content.get("provider") or {}
                publisher = prov.get("displayName", "") if isinstance(prov, dict) else ""
                canon = content.get("canonicalUrl") or {}
                link = canon.get("url") if isinstance(canon, dict) else content.get("link", "")
                pub = content.get("pubDate", "")
            else:
                title = n.get("title", "")
                publisher = n.get("publisher", "")
                link = n.get("link", "")
                pub = n.get("providerPublishTime", "")
            news_items.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "published": str(pub) if pub else "",
                "sentiment": score_text(title),
            })
    except Exception as e:
        print("[news] erro:", e)

    chart_df = df.tail(180)
    chart = {
        "dates": [d.strftime("%Y-%m-%d") for d in chart_df.index],
        "close": [_safe(v) for v in chart_df["Close"]],
        "ema21": [_safe(v) for v in chart_df["EMA21"]],
        "sma50": [_safe(v) for v in chart_df["SMA50"]],
        "bb_upper": [_safe(v) for v in chart_df["BB_upper"]],
        "bb_lower": [_safe(v) for v in chart_df["BB_lower"]],
    }

    last = df.iloc[-1]
    prev_close = df["Close"].iloc[-2] if len(df) > 1 else last["Close"]
    change_pct = (last["Close"] - prev_close) / prev_close * 100 if prev_close else 0.0

    rule_summary = _build_rule_summary(ticker, label, score, conf, signals, risk)
    llm_text = llm_report(
        f"Ticker: {ticker}\nPreço atual: {last['Close']:.2f}\n"
        f"Score composto: {score} ({label})\nConfiança: {conf}%\n"
        f"Sinais: {signals}\nRisco: {risk}"
    )
    summary = llm_text or rule_summary
    summary_source = "LLM (OpenAI)" if llm_text else "Motor quantitativo (regras)"

    return {
        "ticker": ticker.upper(),
        "name": info.get("longName") or info.get("shortName") or ticker.upper(),
        "currency": info.get("currency", ""),
        "price": _safe(last["Close"]),
        "change_pct": round(float(change_pct), 2),
        "signal": {"label": label, "score": score, "confidence": conf},
        "signals": signals,
        "risk": risk,
        "news": news_items,
        "chart": chart,
        "summary": summary,
        "summary_source": summary_source,
    }


def _build_rule_summary(ticker, label, score, conf, signals, risk) -> str:
    bias = {
        "Compra Forte": "viés de alta consistente",
        "Compra": "leve viés de alta",
        "Neutro": "ausência de direção clara",
        "Venda": "leve viés de baixa",
        "Venda Forte": "viés de baixa consistente",
    }.get(label, "neutro")

    top = sorted(signals, key=lambda s: -abs(s["score"]))[:3]
    reasons = "\n".join(f"• {s['name']}: {s['reason']}" for s in top)

    return (
        f"A análise técnica de {ticker.upper()} indica {bias} "
        f"(score {score:.0f}/100, confiança {conf:.0f}%). "
        f"Volatilidade anualizada estimada: {risk['volatility_annual_pct']}%. "
        f"ATR representa {risk['atr_pct']}% do preço. "
        f"Stop sugerido (2×ATR): {risk['suggested_stop_loss']} "
        f"({risk['stop_loss_pct']}%). "
        f"Drawdown máximo no período: {risk['max_drawdown_pct']}%.\n\n"
        f"Principais sinais:\n{reasons}\n\n"
        f"Lembre-se: esta é uma ferramenta de estudo. Nenhum indicador garante o futuro."
    )
    
    
    
    # ==================================================================
# RADAR DOS PARES DE FOREX
# ==================================================================

# Lista padrão de pares. Cobrimos majors, crosses e emergentes.
DEFAULT_FOREX_PAIRS = [
    # Majors
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",
    "USDCAD=X", "AUDUSD=X", "NZDUSD=X",
    # Crosses
    "EURGBP=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X",
    "EURCHF=X", "CADJPY=X", "CHFJPY=X",
    # Emergentes
    "USDBRL=X", "USDMXN=X",
]

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"

# ==================================================================
# RADAR — Fonte de dados: Twelve Data (forex)
# ==================================================================

def _fetch_twelvedata(symbol: str, interval: str = "1day", outputsize: int = 200):
    """
    Busca dados de forex na Twelve Data e devolve um DataFrame no
    mesmo formato que o yfinance (Open, High, Low, Close, Volume).
    """
    if not TWELVE_DATA_API_KEY:
        print("[radar] TWELVE_DATA_API_KEY não configurada no .env")
        return None

    # EURUSD=X → EUR/USD
    code = symbol.replace("=X", "")
    if len(code) == 6:
        code = f"{code[:3]}/{code[3:]}"

    params = {
        "symbol": code,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }

    try:
        r = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
        data = r.json()

        if data.get("status") == "error":
            msg = data.get("message", "erro desconhecido")
            print(f"[radar] TwelveData erro {code}: {msg}")
            return None

        values = data.get("values")
        if not values:
            return None

        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })

        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Forex não tem volume — preenche com 0 para o motor não quebrar
        if "Volume" not in df.columns:
            df["Volume"] = 0
        else:
            df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)

        return df.dropna(subset=["Close"])

    except Exception as e:
        print(f"[radar] TwelveData exceção {symbol}: {e}")
        return None


def _radar_analyze_pair(pair: str, period: str = "1y"):
    """
    Análise leve de um único par via Twelve Data.
    """
    try:
        df = _fetch_twelvedata(pair, interval="1day", outputsize=200)
        if df is None or df.empty or len(df) < 30:
            return None

        df = compute_indicators(df)
        signals = score_signals(df)
        score, label = aggregate(signals)
        conf = confidence(signals)
        risk = risk_metrics(df)

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        prev_close = prev["Close"] if prev["Close"] else last["Close"]
        change_pct = (last["Close"] - prev_close) / prev_close * 100 if prev_close else 0.0

        top_signals = sorted(signals, key=lambda s: -abs(s["score"]))[:3]

        return {
            "pair": pair.replace("=X", ""),
            "ticker": pair,
            "price": _safe(last["Close"]),
            "change_pct": round(float(change_pct), 3),
            "score": score,
            "label": label,
            "confidence": conf,
            "risk": risk,
            "top_signals": top_signals,
        }
    except Exception as e:
        print(f"[radar] erro em {pair}: {e}")
        return None

def scan_forex_pairs(pairs=None, period: str = "1y") -> dict:
    """
    Varre os pares em lotes de 8 (limite da Twelve Data free tier)
    respeitando o intervalo de 60s entre lotes.
    """
    if pairs is None:
        pairs = DEFAULT_FOREX_PAIRS

    results, errors = [], []
    BATCH_SIZE = 8   # 8 créditos por minuto no plano free

    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i:i + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {executor.submit(_radar_analyze_pair, p, period): p for p in batch}
            for future in as_completed(futures):
                pair = futures[future]
                try:
                    r = future.result()
                    if r:
                        results.append(r)
                    else:
                        errors.append({"pair": pair, "error": "sem dados"})
                except Exception as e:
                    errors.append({"pair": pair, "error": str(e)})

        # Aguarda o próximo minuto antes do próximo lote
        if i + BATCH_SIZE < len(pairs):
            time.sleep(60)

    results.sort(key=lambda x: x["score"], reverse=True)

    summary = {
        "Compra Forte": [r["pair"] for r in results if r["label"] == "Compra Forte"],
        "Compra":       [r["pair"] for r in results if r["label"] == "Compra"],
        "Neutro":       [r["pair"] for r in results if r["label"] == "Neutro"],
        "Venda":        [r["pair"] for r in results if r["label"] == "Venda"],
        "Venda Forte":  [r["pair"] for r in results if r["label"] == "Venda Forte"],
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "total_scanned": len(results),
        "total_errors": len(errors),
        "errors": errors,
        "pairs": results,
        "summary": summary,
    }
    
# ==================================================================
# NOVO MOTOR DE BUSCA DE DADOS (Twelve Data)
# ==================================================================


def _fetch_twelvedata(symbol: str, interval: str = "1day", outputsize: int = 200):
    """
    Busca dados da Twelve Data e retorna um DataFrame compatível com o motor de análise.
    """
    # A Twelve Data usa o formato "EUR/USD" para forex
    formatted_symbol = symbol.replace("=X", "")
    if len(formatted_symbol) == 6:
        formatted_symbol = f"{formatted_symbol[:3]}/{formatted_symbol[3:]}"

    params = {
        "symbol": formatted_symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON"
    }

    try:
        response = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
        data = response.json()

        if data.get("status") == "error":
            print(f"[TwelveData] Erro para {symbol}: {data.get('message')}")
            return None

        # Converte a lista de valores para um DataFrame do Pandas
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        
        # Renomeia as colunas para o padrão do nosso motor (Open, High, Low, Close, Volume)
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"
        })
        
        # Converte as colunas para tipo numérico
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        # Forex não tem volume, então preenchemos com 0
        if "Volume" not in df.columns:
            df["Volume"] = 0
            
        return df

    except Exception as e:
        print(f"[TwelveData] Exceção para {symbol}: {e}")
        return None