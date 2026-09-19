# AGENTS.md — Market Insight AI

Ferramenta educacional de análise de opções binárias (IQ Option): indicadores técnicos, sinais por expiração, calendário econômico, backtest walk-forward e gestão de entradas (SQLite). Backend **FastAPI** + frontend estático SPA. Todo o código, comentários e texto de UI em **pt-BR**.

## Project

- Stack: Python 3.11 (`Dockerfile`/`requirements.txt`), FastAPI + uvicorn, pandas/numpy, `iqair` (cliente IQ Option), OpenAI-compatível opcional.
- Entry point: `main:app` (FastAPI) — subida via `python run.py` (bootstrap: cria `.env`, instala deps, inicia uvicorn) ou `uvicorn main:app`.
- Config via `.env` (ver `.env.example`): `IQ_EMAIL`/`IQ_PASSWORD`, `IQ_ACCOUNT_TYPE` (`PRACTICE`/`REAL`), `MARKET_DB_PATH`, `OPENAI_API_KEY`, `AI_BASE_URL`, `HOST`, `PORT`.

## Commands

```bash
bash setup.sh                        # cria .venv, instala deps, copia .env.example → .env
source .venv/bin/activate && python run.py          # roda o servidor (http://127.0.0.1:8000)
uvicorn main:app --host 127.0.0.1 --port 8000       # alternativa direta
python -m unittest discover -s . -p "test_*.py"     # testes (requer deps instaladas)
docker compose up --build                           # alternativa containerizada
```

Observações: não há linter/type-check configurado. `test_iq.py` é script manual de conexão (exige credenciais reais no `.env`), não teste unitário.

## Architecture

- `main.py` — rota/API FastAPI (`/api/*`): connect/login/status, oportunidades, candles, análise, backtest, radar, entradas/relatório, indicadores/parâmetros de estratégia, IA. Orquestra os módulos abaixo.
- `iq_service.py` — wrapper global da IQ Option (`iqair`): conexão única (`_api`), troca de conta, candles, compra binária, payout. Estado em singleton com `RLock`.
- `analysis.py` — motor de análise: `TIMEFRAMES` (1/5/15min), `STRATEGIES` (trend_pullback, breakout, mean_reversion, support_resistance, momentum), indicadores (EMA, ADX, RSI, MACD, Bollinger…), cache `_SIGNAL_CACHE`, acerto histórico por janela rolante, `walkforward_asset` (backtest).
- `news_service.py` — calendário econômico Biquote (cache 60s + lock), `get_news_risk` bloqueia sinais perto de eventos de alto impacto.
- `trade_manager.py` — gestão de entradas: config (`set_config`/`get_estado_completo`), histórico/CRUD em SQLite (`market.db` via `_resolver_db_path`; fallback `/tmp`/memória em serverless), worker de apuração em thread, relatórios por período.
- `ai_advisor.py` — segunda opinião via LLM (OpenAI ou endpoint compatível via `AI_BASE_URL`); desativada sem `OPENAI_API_KEY`.
- `static/` — SPA (index.html, app.js, style.css) servida em `/`.

## Conventions

- Idioma: código, comentários, mensagens de erro e UI em **pt-BR**; sinais em maiúsculo (`CALL`/`PUT`/`AGUARDAR`).
- Módulos carregam `.env` com python-dotenv na importação, em `try/except` defensivo.
- Serviços expõem funções que retornam `(ok: bool, msg: str)`; `main.py` traduz em `HTTPException` (400/404/500/503).
- Estado global em singletons de módulo (`iq_service._api`, `analysis._SIGNAL_CACHE`, `news._calendar_cache`); thread-safe com locks. Cleanup explícito ao resetar caches.
- Testes: `unittest` + `unittest.mock.patch` (sem pytest). Nenhuma chamada real à IQ Option nos testes — mock de `analysis.iq_service`, `news_service`, `time`.
- Não commitar `.env`, `market.db`, `.venv`, `__pycache__` (ver `.gitignore`).
- Python 3.10+ syntax (`X | None`, `list[str]`, `from __future__ import annotations`).

## Notes

- (vazio — adicione aqui descobertas futuras por sessão)