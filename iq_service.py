"""
Serviço de conexão com a IQ Option via biblioteca iqair.
Mantém uma instância global conectada e expõe métodos para candles.
SOMENTE PARA USO EM CONTA DEMO (PRACTICE).
"""
from __future__ import annotations

import os
import time
import threading

from iqair.client import IQOptionClient

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

IQ_EMAIL = os.getenv("IQ_EMAIL", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD", "")

_api: IQOptionClient | None = None
_lock = threading.RLock()

# Cache de streams ativos: {(asset, interval): True}
_streams: dict[tuple[str, int], bool] = {}


def connect() -> tuple[bool, str]:
    global _api
    with _lock:
        if _api is not None:
            return True, "Já conectado"
        if not IQ_EMAIL or not IQ_PASSWORD:
            return False, "IQ_EMAIL/IQ_PASSWORD ausentes no .env"
        try:
            _api = IQOptionClient(IQ_EMAIL, IQ_PASSWORD)
            ok, reason = _api.connect()
            if not ok:
                _api = None
                return False, f"Falha: {reason}"
            _api.change_balance("PRACTICE")
            return True, "Conectado à conta PRACTICE"
        except Exception as e:
            _api = None
            return False, f"Exceção: {e}"


def reconnect(email: str, password: str) -> tuple[bool, str]:
    """Troca a conta em memória; credenciais não são persistidas."""
    global _api, IQ_EMAIL, IQ_PASSWORD
    with _lock:
        old_api = _api
        try:
            if old_api is not None:
                for asset, interval in list(_streams):
                    try:
                        old_api.stop_candles_stream(asset, interval)
                    except Exception:
                        pass
                _streams.clear()
            client = IQOptionClient(email.strip(), password)
            ok, reason = client.connect()
            if not ok:
                return False, f"Falha: {reason}"
            client.change_balance("PRACTICE")
            _api = client
            IQ_EMAIL, IQ_PASSWORD = email.strip(), password
            return True, "Conta conectada na conta PRACTICE"
        except Exception as exc:
            return False, f"Exceção: {exc}"


def is_connected() -> bool:
    return _api is not None


def get_balance():
    if _api is None:
        return None
    try:
        return _api.get_balance()
    except Exception:
        return None


# Lista padrão de ativos (forex) disponíveis na IQ Option
DEFAULT_ASSETS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
    "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
    "EURCHF", "AUDJPY", "CADJPY", "CHFJPY", "EURAUD",
]


def list_assets() -> list[str]:
    """Retorna lista fixa de pares — evita dependência do init interno."""
    return list(DEFAULT_ASSETS)


def _normalize(c: dict) -> dict:
    """Padroniza o dicionário de candle da IQ Option."""
    return {
        "time": int(c.get("from") or c.get("at") or 0),
        "open": float(c.get("open", 0) or 0),
        "high": float(c.get("max") or c.get("high") or 0),
        "low": float(c.get("min") or c.get("low") or 0),
        "close": float(c.get("close", 0) or 0),
        "volume": float(c.get("volume", 0) or 0),
    }


def get_candles(asset: str, interval: int = 300, count: int = 200) -> list[dict]:
    """Busca candles históricos via IQ Option."""
    if _api is None:
        return []
    try:
        raw = _api.get_candles(asset, interval, count, time.time())
        if isinstance(raw, dict):
            raw = raw.get("candles") or raw.get("data") or []
        return [_normalize(c) for c in (raw or [])]
    except Exception as e:
        print(f"[iq] erro get_candles({asset}, {interval}): {e}")
        return []


def start_stream(asset: str, interval: int = 300, count: int = 200) -> bool:
    """Inicia streaming em tempo real de um ativo."""
    if _api is None:
        return False
    key = (asset, interval)
    with _lock:
        if _streams.get(key):
            return True
        try:
            _api.start_candles_stream(asset, interval, count)
            _streams[key] = True
            return True
        except Exception as e:
            print(f"[iq] erro start_stream({asset}, {interval}): {e}")
            return False


def stop_stream(asset: str, interval: int = 300):
    if _api is None:
        return
    key = (asset, interval)
    with _lock:
        if not _streams.get(key):
            return
        try:
            _api.stop_candles_stream(asset, interval)
        except Exception:
            pass
        _streams.pop(key, None)


def get_realtime_candles(asset: str, interval: int = 300) -> list[dict]:
    """Lê os candles atualizados do cache do stream."""
    if _api is None:
        return []
    try:
        raw = _api.get_realtime_candles(asset, interval)
        if not isinstance(raw, dict):
            return []
        # Formato típico: {interval: {timestamp: candle}} ou {timestamp: candle}
        data = raw.get(str(interval)) or raw.get(interval)
        if isinstance(data, dict):
            candles = list(data.values())
        else:
            # Fallback: assume que o dict é o próprio conjunto de candles
            candles = [
                v for v in raw.values()
                if isinstance(v, dict) and "close" in v
            ]
        return [_normalize(c) for c in candles]
    except Exception as e:
        print(f"[iq] erro get_realtime_candles({asset}): {e}")
        return []


def get_candles_smart(asset: str, interval: int = 300, count: int = 200) -> list[dict]:
    """
    Estratégia híbrida:
      1) Tenta pegar do stream em tempo real.
      2) Se vazio, busca históricos.
      3) Retorna os últimos `count` candles.
    """
    candles = get_realtime_candles(asset, interval)
    if len(candles) < 30:
        candles = get_candles(asset, interval, count)
    return candles[-count:] if len(candles) > count else candles