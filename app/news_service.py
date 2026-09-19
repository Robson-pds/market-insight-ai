"""Calendario economico da Biquote para bloquear sinais perto de eventos relevantes."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time

import requests

CALENDAR_URL = (os.getenv("BIQUOTE_CALENDAR_URL") or "").strip() or "https://biquote.io/api/calendar/upcoming"
CALENDAR_CACHE_SECONDS = 60
_calendar_cache: dict = {"expires_at": 0.0, "payload": None}
_calendar_lock = threading.Lock()
CURRENCY_BY_ASSET = {
    "EUR": "EUR", "GBP": "GBP", "USD": "USD", "JPY": "JPY",
    "CHF": "CHF", "CAD": "CAD", "AUD": "AUD", "NZD": "NZD",
}

NEWS_DESCRIPTIONS = {
    "interest rate": "Decisão ou referência de juros. Pode alterar o fluxo de capital e a força da moeda.",
    "inflation": "Medida de inflação. Ajuda a indicar pressão sobre preços e possíveis decisões de juros.",
    "cpi": "Índice de preços ao consumidor, um dos principais termômetros de inflação.",
    "ppi": "Índice de preços ao produtor, sinal inicial de pressão de custos na economia.",
    "employment": "Indicador de emprego. Mostra a força do mercado de trabalho e pode mexer com juros.",
    "nonfarm": "Relatório de empregos dos Estados Unidos, normalmente de alto impacto para o dólar.",
    "payroll": "Relatório de criação de empregos, usado para avaliar a saúde do mercado de trabalho.",
    "gdp": "Produto Interno Bruto. Mede o crescimento ou contração da economia.",
    "retail sales": "Vendas no varejo. Indicam a força do consumo das famílias.",
    "unemployment": "Taxa de desemprego. Mostra a proporção da população economicamente ativa sem trabalho.",
    "manufacturing": "Atividade industrial. Ajuda a medir o ritmo do setor manufatureiro.",
    "services": "Atividade do setor de serviços, importante para avaliar o ritmo da economia.",
    "pmi": "Índice de gerentes de compras. Leituras acima de 50 sugerem expansão; abaixo, contração.",
    "central bank": "Comunicação ou decisão de banco central, com potencial de alterar expectativas de mercado.",
}


def _detalhe_erro(exc: Exception) -> str:
    """Texto curto do erro — melhor que mostrar só o nome da exceção."""
    texto = str(exc).strip()
    return texto or type(exc).__name__


def _get_calendar_payload() -> list[dict]:
    now = time.monotonic()
    with _calendar_lock:
        cached = _calendar_cache["payload"]
        if cached is not None and _calendar_cache["expires_at"] > now:
            return cached

        if not CALENDAR_URL.lower().startswith(("http://", "https://")):
            raise ValueError(
                "BIQUOTE_CALENDAR_URL vazia ou inválida no .env — configure a URL do calendário Biquote"
            )
        response = requests.get(CALENDAR_URL, timeout=8)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("resposta do calendario nao e uma lista")
        _calendar_cache.update(payload=payload, expires_at=now + CALENDAR_CACHE_SECONDS)
        return payload


def describe_event(name: str, sector: str | None = None) -> str:
    text = f"{name} {sector or ''}".lower()
    for keyword, description in NEWS_DESCRIPTIONS.items():
        if keyword in text:
            return description
    return "Indicador ou evento macroeconômico agendado; sua divulgação pode aumentar a volatilidade da moeda."


def _currencies(asset: str) -> set[str]:
    asset = asset.upper().replace("=X", "")
    return {code for code in CURRENCY_BY_ASSET if code in asset}


def get_news_risk(asset: str, now: datetime | None = None) -> dict:
    """Retorna eventos de alto impacto proximos ao ativo.

    A indisponibilidade da Biquote nunca e tratada como ausencia de noticias.
    """
    now = now or datetime.now(timezone.utc)
    try:
        payload = _get_calendar_payload()
    except Exception as exc:
        return {
            "available": False,
            "source": "biquote",
            "blocked": False,
            "events": [],
            "warning": f"Calendario economico indisponivel: {_detalhe_erro(exc)}",
        }

    events = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        currency = str(item.get("currency") or "").strip().upper()
        impact = str(item.get("importance") or "").strip().lower()
        event_time_text = item.get("time")
        if currency not in _currencies(asset) or impact not in {"high", "holiday"}:
            continue
        try:
            event_time = datetime.fromisoformat(str(event_time_text).replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        minutes = (event_time - now).total_seconds() / 60
        if -15 <= minutes <= 45:
            events.append({
                "currency": currency,
                "title": str(item.get("name") or "Evento").strip(),
                "impact": impact,
                "time": event_time.isoformat(),
                "minutes_until": round(minutes, 1),
                "actual": item.get("actual"),
                "forecast": item.get("forecast"),
                "previous": item.get("previous"),
            })

    return {
        "available": True,
        "source": "biquote",
        "blocked": bool(events),
        "events": events,
        "warning": "Entrada bloqueada perto de evento de alto impacto da Biquote." if events else "Nenhum evento de alto impacto no intervalo analisado.",
    }


def get_calendar_events(hours: int = 24, importance: str = "all") -> dict:
    """Busca eventos futuros da Biquote para a tela de noticias."""
    try:
        payload = _get_calendar_payload()
    except Exception as exc:
        return {
            "available": False,
            "source": "biquote",
            "events": [],
            "warning": f"Calendario economico indisponivel: {_detalhe_erro(exc)}",
        }

    now = datetime.now(timezone.utc)
    limit = now.timestamp() + max(1, hours) * 3600
    events = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_time = item.get("time")
        try:
            event_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        event_importance = str(item.get("importance") or "low").lower()
        if event_time.timestamp() < now.timestamp() or event_time.timestamp() > limit:
            continue
        if importance != "all" and event_importance != importance:
            continue
        events.append({
            "id": item.get("id"),
            "currency": item.get("currency") or item.get("countryCode"),
            "title": item.get("name") or "Evento econômico",
            "description": describe_event(str(item.get("name") or "Evento econômico"), item.get("sector")),
            "importance": event_importance,
            "time": event_time.isoformat(),
            "actual": item.get("actual"),
            "forecast": item.get("forecast"),
            "previous": item.get("previous"),
            "sector": item.get("sector"),
        })
    events.sort(key=lambda event: event["time"])
    return {
        "available": True,
        "source": "biquote",
        "events": events,
        "hours": hours,
        "warning": None,
    }
