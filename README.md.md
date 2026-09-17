# Market Insight AI

Ferramenta **educacional e de estudo** que combina candles da IQ Option,
indicadores técnicos, contexto de múltiplos timeframes e calendário econômico.

> **Aviso legal:** Este projeto NÃO constitui recomendação de investimento,
> NÃO é prestação de serviço de análise de valores mobiliários (Resolução CVM
> nº 20/2021) e NÃO possui cobrança ou vínculo comercial. Dados podem conter
> atraso ou imprecisão. Rentabilidade passada não garante resultados futuros.

## Funcionalidades

- Análise técnica automatizada: RSI, MACD, EMA 9/21, SMA 50/200, Bollinger,
  Estocástico e Volume.
- Uma estratégia selecionável por vez: retração na tendência, rompimento de faixa,
  reversão à média, suporte/resistência ou momentum.
- Três sinais independentes para expiração de 1, 5 e 15 minutos.
- Sinal fixado até o vencimento do prazo; o contador impede troca antecipada.
- Score da regra (não é probabilidade de acerto) e modo `AGUARDAR` quando a estratégia não confirma.
- Métricas de risco: volatilidade anualizada, ATR, drawdown máximo e stop
  sugerido por 2×ATR.
- Bloqueio preventivo próximo a eventos econômicos de alto impacto.
- Walk-forward em `/api/backtest/{ticker}` para medir sinais em candles passados.
- Relatório narrativo: motor quantitativo local por padrão; LLM (OpenAI) se
  `OPENAI_API_KEY` estiver definida no `.env`.
- Gráfico interativo com preço, EMA21, SMA50 e Bandas de Bollinger.
- Interface dark-mode responsiva, pronta para uso local ou intranet.

## Rodando localmente

### Opção A — script automático

```bash
# Linux / Mac
bash setup.sh
source .venv/bin/activate
python run.py
```

```bat
:: Windows
setup.bat
.venv\Scripts\activate
python run.py
```

### Opção B — manual

```bash
python -m venv .venv
source .venv/bin/activate       # ou .venv\Scripts\activate no Windows
pip install -r requirements.txt
cp .env.example .env            # opcional
python run.py
```

Abra **http://127.0.0.1:8000**.

## Rodando com Docker

```bash
docker compose up --build
```

Acesse **http://localhost:8000**.

## Configuração (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `OPENAI_API_KEY` | vazio | Opcional. Se presente, o relatório narrativo é gerado por LLM. |
| `BIQUOTE_CALENDAR_URL` | `https://biquote.io/api/calendar/upcoming` | Endpoint público do calendário econômico Biquote. |
| `HOST` | `127.0.0.1` | Host do servidor. Use `0.0.0.0` para expor na rede. |
| `PORT` | `8000` | Porta HTTP. |

## Endpoints

- `GET /` — interface web.
- `GET /api/analyze/{ticker}` — análise completa (JSON).
- `GET /api/strategies` — catálogo das estratégias disponíveis.
- `GET /api/analyze/{ticker}?strategy=breakout` — três sinais da estratégia escolhida.
- `GET /api/backtest/{ticker}?expiry=1min` — métrica walk-forward do timeframe.
- `GET /api/health` — verificação de status.

Exemplos de tickers: `PETR4.SA`, `VALE3.SA`, `ITUB4.SA`, `AAPL`, `MSFT`,
`BTC-USD`, `ETH-USD`, `^BVSP`.