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
- **Entradas**: configuração de forma de entrada (conta demo `PRACTICE` ou
  oficial `REAL`), valor mínimo de entrada, valor máximo de perda diária e
  estratégias **Fixa** ou **Soros** (com nível configurável, mesmo
  comportamento do TradeTelegram).
- **Execução na IQ Option**: botão na aba Entradas envia a ordem real
  (binária turbo) na conta configurada e o resultado é apurado
  automaticamente ao expirar; também é possível registrar/manter
  manualmente.
- **Histórico persistente** (SQLite `market.db`): cada entrada guarda ativo,
  direção, valor, expiração, estratégia, conta e resultado final
  (WIN/LOSS/EMPATE com o valor ganho/perdido).
- **Relatório de entradas e ganhos/perdas** por dia, semana, mês ou ano:
  totais, taxa de acerto, ganho/perda bruta, líquido e resultado por dia.
- **Aba Estratégias**: catálogo das estratégias (descrição, indicadores usados,
  troca rápida) e **ativação/desativação de indicadores** que participam dos
  votos de detalhamento e da consulta à IA.
- **Percentual de acerto editável**: o percentual de cada expiração pode ser
  ajustado em tempo de execução (override manual), voltando ao automático
  quando desejar.
- **IA — segunda opinião**: botão na aba Análise que envia os parâmetros
  atuais (sinais, votos dos indicadores, notícias) para um modelo de
  linguagem (OpenAI ou endpoint compatível) e devolve direção, confiança,
  justificativa e riscos em pt-BR.
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

> **Atenção:** o arquivo `.env` contém suas credenciais da IQ Option e NÃO
> deve ser commitado (já está no `.gitignore`). Se ele chegou a ser
> publicado em repositório público, troque a senha da conta.

| Variável | Padrão | Descrição |
|---|---|---|
| `IQ_EMAIL` | vazio | Email da conta IQ Option (demo ou real). |
| `IQ_PASSWORD` | vazio | Senha da conta IQ Option. |
| `IQ_ACCOUNT_TYPE` | `PRACTICE` | Conta usada nas operações: `PRACTICE` (demo) ou `REAL` (oficial). Também configurável na aba Configuração. |
| `OPENAI_API_KEY` | vazio | Opcional. Habilita o botão "🤖 IA — segunda opinião" na aba Análise. |
| `AI_MODEL` | `gpt-4o-mini` | Modelo usado pela IA. |
| `AI_BASE_URL` | vazio | Opcional. Endpoint compatível com a API OpenAI (ex.: Ollama/LM Studio). |
| `BIQUOTE_CALENDAR_URL` | `https://biquote.io/api/calendar/upcoming` | Endpoint público do calendário econômico Biquote. |
| `HOST` | `127.0.0.1` | Host do servidor. Use `0.0.0.0` para expor na rede. |
| `PORT` | `8000` | Porta HTTP. |

## Como adicionar um indicador (template)

Os indicadores vivem em `analysis.py`, no registro `INDICADORES_PADRAO`.
Para criar um novo, siga o template:

```python
def _vote_meu_indicador(df, i):
    valor = df["MINHA_COLUNA"].iloc[i]
    if pd.isna(valor):
        return 0, "Meu indicador sem dados"
    if valor > 50:
        return 1, f"Meu indicador {valor:.1f} — CALL"
    if valor < 50:
        return -1, f"Meu indicador {valor:.1f} — PUT"
    return 0, "Meu indicador neutro"

INDICADORES_PADRAO.append({
    "id": "meu_indicador",            # id único (será persistido na ativação)
    "nome": "Meu Indicador (50)",     # rótulo exibido na interface
    "descricao": "O que ele mede e como vota.",
    "peso": 1.0,                      # peso no voto ponderado
    "votar": _vote_meu_indicador,     # retorna (voto, motivo)
    # "preparar": fn_que_calcula_colunas,  # opcional: amplia o DataFrame
})
```

Depois de salvo, o indicador aparece na aba **Estratégias** e pode ser
ativado/desativado sem mexer em código. O cálculo das colunas base continua
em `compute_indicators(df)` (se precisar de coluna nova, adicione lá ou use
o campo opcional `preparar`).

## Endpoints

- `GET /` — interface web.
- `GET /api/analyze/{ticker}` — análise completa (JSON).
- `GET /api/strategies` — catálogo das estratégias disponíveis.
- `GET /api/analyze/{ticker}?strategy=breakout` — três sinais da estratégia escolhida.
- `GET /api/backtest/{ticker}?expiry=1min` — métrica walk-forward do timeframe.
- `GET /api/status` — conexão e saldo da IQ Option.
- `GET /api/trade/config` — configurações e estado atual (Soros, perda do dia, valor sugerido).
- `PUT /api/trade/config` — salva configurações (conta, valor, limite, estratégia, nível do Soros).
- `POST /api/trade/entradas` — registra entrada (`executar: true` executa na IQ Option).
- `PATCH /api/trade/entradas/{id}` — fecha/corrige resultado manualmente.
- `DELETE /api/trade/entradas/{id}` — remove do histórico.
- `POST /api/trade/entradas/{id}/apurar` — consulta o resultado na IQ Option.
- `GET /api/trade/entradas?periodo=dia|semana|mes|ano` — histórico filtrado.
- `GET /api/trade/relatorio?periodo=dia|semana|mes|ano` — resumo de entradas e ganhos/perdas.
- `GET /api/indicators` — catálogo de indicadores com status ativo.
- `PUT /api/indicators` — salva a lista de indicadores ativos `{"ativos": ["rsi", "macd"]}`.
- `PUT /api/accuracy/{expiry}` — define o percentual de acerto manual `{"taxa": 72.5}`.
- `DELETE /api/accuracy/{expiry}` — remove o percentual manual (volta ao automático).
- `POST /api/ai/analise` — consulta a IA `{"ativo", "strategy", "instrucao_extra?"}`.

Exemplos de tickers: `PETR4.SA`, `VALE3.SA`, `ITUB4.SA`, `AAPL`, `MSFT`,
`BTC-USD`, `ETH-USD`, `^BVSP`.