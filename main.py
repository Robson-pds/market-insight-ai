from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import iq_service
import analysis
import news_service
import trade_manager
import ai_advisor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa o módulo de entradas (config + worker de apuração) antes do
    # connect, para que a conta configurada seja aplicada na conexão.
    trade_manager.iniciar()
    # Tenta conectar à IQ Option na inicialização
    print("[startup] Conectando à IQ Option...")
    ok, msg = iq_service.connect()
    print(f"[startup] {msg}")
    yield
    # Shutdown (opcional): limpar streams
    print("[shutdown] Encerrando...")


app = FastAPI(
    title="Market Insight AI — Opções Binárias",
    version="2.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="static"), name="static")


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/api/connect")
def connect():
    """Força (re)conexão com a IQ Option."""
    ok, msg = iq_service.connect()
    return {"ok": ok, "message": msg, "balance": iq_service.get_balance()}


@app.post("/api/login")
def login(request: LoginRequest):
    if not request.email.strip() or not request.password:
        raise HTTPException(400, "Informe email e senha")
    ok, message = iq_service.reconnect(request.email, request.password)
    return {"ok": ok, "message": message, "balance": iq_service.get_balance()}


@app.get("/api/status")
def status():
    return {
        "connected": iq_service.is_connected(),
        "balance": iq_service.get_balance(),
    }


@app.get("/api/assets")
def assets():
    return {"assets": iq_service.list_assets()}


@app.get("/api/strategies")
def strategies():
    return {"strategies": analysis.STRATEGIES}


@app.get("/api/news")
def news(hours: int = 24, importance: str = "all"):
    return news_service.get_calendar_events(min(max(hours, 1), 168), importance)


def _analyze_opportunity(asset: str, strategy: str) -> dict:
    result = analysis.analyze_asset(asset, strategy)
    one_minute = result["signals"]["1min"]
    five_minutes = result["signals"]["5min"]
    fifteen_minutes = result["signals"]["15min"]
    timeframe_signals = {
        "1min": one_minute,
        "5min": five_minutes,
        "15min": fifteen_minutes,
    }
    score = sum(item["score"] for item in timeframe_signals.values())
    directions = [item["signal"] for item in timeframe_signals.values()]
    directional = [direction for direction in directions if direction in ("CALL", "PUT")]
    consensus_count = max(directional.count("CALL"), directional.count("PUT"))
    conflict_count = len(directional) - consensus_count
    return {
        "asset": asset,
        "score": score,
        "max_score": analysis.STRATEGIES[strategy]["max_score"] * 3,
        "consensus_count": consensus_count,
        "conflict_count": conflict_count,
        "news_blocked": result["news"]["blocked"],
        "signals": {key: item["signal"] for key, item in timeframe_signals.items()},
        "scores": {key: item["score"] for key, item in timeframe_signals.items()},
        "reason": one_minute["reason"],
        "proximity": {key: item.get("proximity") for key, item in timeframe_signals.items()},
        "accuracy": {key: item.get("historical_accuracy") for key, item in timeframe_signals.items()},
    }


@app.get("/api/opportunity/{asset}")
def opportunity(asset: str, strategy: str = "trend_pullback"):
    _ensure_connected()
    if strategy not in analysis.STRATEGIES:
        raise HTTPException(400, f"Estratégia desconhecida: {strategy}")
    normalized_asset = asset.upper().replace("=X", "")
    if normalized_asset not in iq_service.list_assets():
        raise HTTPException(404, f"Ativo desconhecido: {normalized_asset}")
    try:
        return _analyze_opportunity(normalized_asset, strategy)
    except Exception as exc:
        raise HTTPException(500, f"Erro na análise de {normalized_asset}: {exc}")


@app.get("/api/opportunities")
def opportunities(strategy: str = "trend_pullback"):
    _ensure_connected()
    if strategy not in analysis.STRATEGIES:
        raise HTTPException(400, f"Estratégia desconhecida: {strategy}")
    def analyze_pair(asset: str) -> dict:
        try:
            return _analyze_opportunity(asset, strategy)
        except Exception as exc:
            return {"asset": asset, "score": 0, "signals": {}, "reason": str(exc)}

    pairs = [analyze_pair(asset) for asset in iq_service.list_assets()]
    pairs.sort(
        key=lambda item: (
            item.get("consensus_count", 0),
            -item.get("conflict_count", 3),
            item["score"],
        ),
        reverse=True,
    )
    return {"strategy": strategy, "strategy_name": analysis.STRATEGIES[strategy]["name"], "pairs": pairs}


def _ensure_connected():
    """Garante que estamos conectados antes de qualquer chamada."""
    if not iq_service.is_connected():
        ok, msg = iq_service.connect()
        if not ok:
            raise HTTPException(503, f"Não foi possível conectar à IQ Option: {msg}")


@app.get("/api/candles/{asset}")
def candles(asset: str, interval: int = 300, count: int = 200):
    _ensure_connected()
    return analysis.get_chart_data(asset, interval, count)


@app.get("/api/analyze/{asset}")
def analyze(asset: str, strategy: str = "trend_pullback"):
    _ensure_connected()
    try:
        return analysis.analyze_asset(asset, strategy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro na análise: {e}")


@app.get("/api/backtest/{asset}")
def backtest(asset: str, expiry: str = "1min", count: int = 240):
    _ensure_connected()
    try:
        return analysis.walkforward_asset(asset, expiry, min(max(count, 80), 500))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erro no walk-forward: {e}")


@app.get("/api/radar")
def radar(strategy: str = "trend_pullback"):
    _ensure_connected()
    results = []
    for a in iq_service.list_assets():
        try:
            r = analysis.analyze_asset(a, strategy)
            strat = r["signals"]["1min"]
            results.append({
                "asset": a,
                "recommendation": strat.get("signal", "AGUARDAR"),
                "score": strat.get("score", 0),
                "reason": strat.get("reason", ""),
            })
        except Exception:
            continue
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"pairs": results}


# ===========================================================================
# Entradas (configuração, histórico, execução e relatório)
# ===========================================================================
class TradeConfigRequest(BaseModel):
    conta: str | None = None
    valor_entrada: float | None = None
    valor_max_perda: float | None = None
    estrategia: str | None = None
    soros_nivel: int | None = None


class NovaEntradaRequest(BaseModel):
    ativo: str
    direcao: str
    expiracao: int = 1
    valor: float | None = None
    executar: bool = False
    origem: str | None = None
    observacao: str | None = None


class EditarEntradaRequest(BaseModel):
    status: str | None = None
    resultado: float | None = None
    observacao: str | None = None


@app.get("/api/trade/config")
def trade_config():
    try:
        return trade_manager.get_estado_completo()
    except Exception as exc:
        raise HTTPException(500, f"Erro ao carregar configurações: {exc}")


@app.put("/api/trade/config")
def trade_config_salvar(request: TradeConfigRequest):
    ok, msg = trade_manager.set_config(request.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg, **trade_manager.get_estado_completo()}


@app.get("/api/trade/entradas")
def trade_entradas(periodo: str = "dia"):
    try:
        return {"periodo": periodo, "entradas": trade_manager.listar_entradas(periodo)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Erro ao listar entradas: {exc}")


@app.post("/api/trade/entradas")
def trade_nova_entrada(request: NovaEntradaRequest):
    ok, retorno = trade_manager.criar_entrada(
        ativo=request.ativo,
        direcao=request.direcao,
        expiracao=request.expiracao,
        valor=request.valor,
        executar=request.executar,
        origem=request.origem,
        observacao=request.observacao,
    )
    if not ok:
        raise HTTPException(400, retorno)
    return {"ok": True, "entrada": retorno, **trade_manager.get_estado_completo()}


@app.patch("/api/trade/entradas/{entrada_id}")
def trade_editar_entrada(entrada_id: int, request: EditarEntradaRequest):
    ok, retorno = trade_manager.editar_entrada(
        entrada_id,
        request.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(400, retorno)
    return {"ok": True, "entrada": retorno, **trade_manager.get_estado_completo()}


@app.delete("/api/trade/entradas/{entrada_id}")
def trade_excluir_entrada(entrada_id: int):
    ok, msg = trade_manager.excluir_entrada(entrada_id)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg, **trade_manager.get_estado_completo()}


@app.post("/api/trade/entradas/{entrada_id}/apurar")
def trade_apurar_entrada(entrada_id: int):
    """Dispara a apuração do resultado na IQ Option (best-effort)."""
    entrada = trade_manager.buscar_entrada(entrada_id)
    if entrada is None:
        raise HTTPException(404, "Entrada não encontrada.")
    if entrada["status"] != "ABERTA":
        return {"ok": True, "message": "Entrada já fechada.", "estado": trade_manager.get_estado_completo()}
    if not entrada["ordem_id"]:
        raise HTTPException(400, "Entrada manual: marque o resultado manualmente.")
    if not iq_service.is_connected():
        raise HTTPException(400, "Não conectado à IQ Option.")
    import threading
    threading.Thread(target=trade_manager.apurar_entrada, args=(entrada_id,), daemon=True).start()
    return {"ok": True, "message": "Apuração iniciada.", "estado": trade_manager.get_estado_completo()}


@app.get("/api/trade/relatorio")
def trade_relatorio(periodo: str = "dia"):
    try:
        return trade_manager.relatorio(periodo)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Erro no relatório: {exc}")


# ===========================================================================
# Indicadores (catálogo, ativação e percentual de acerto manual)
# ===========================================================================
class IndicadoresRequest(BaseModel):
    ativos: list[str]


@app.get("/api/indicators")
def indicators_listar():
    return {"indicadores": analysis.listar_indicadores()}


@app.put("/api/indicators")
def indicators_salvar(request: IndicadoresRequest):
    ok, msg = analysis.set_indicadores_ativos(request.ativos)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg, "indicadores": analysis.listar_indicadores()}


class AcertoRequest(BaseModel):
    taxa: float | None = None


@app.put("/api/accuracy/{expiry}")
def accuracy_salvar(expiry: str, request: AcertoRequest):
    ok, msg = analysis.set_override_acerto(expiry, request.taxa)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@app.delete("/api/accuracy/{expiry}")
def accuracy_remover(expiry: str):
    ok, msg = analysis.set_override_acerto(expiry, None)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


# ===========================================================================
# IA — análise de entrada com modelo de linguagem
# ===========================================================================
class AiAnaliseRequest(BaseModel):
    ativo: str
    strategy: str = "trend_pullback"
    instrucao_extra: str | None = None


@app.post("/api/ai/analise")
def ai_analise(request: AiAnaliseRequest):
    """Envia parâmetros + lógica atuais para a IA e devolve a análise."""
    _ensure_connected()
    if not ai_advisor.disponivel():
        raise HTTPException(503, "OPENAI_API_KEY não configurada no .env.")
    strategy = request.strategy
    if strategy not in analysis.STRATEGIES:
        raise HTTPException(400, f"Estratégia desconhecida: {strategy}")
    ativo = request.ativo.upper().replace("=X", "")
    if ativo not in iq_service.list_assets():
        raise HTTPException(404, f"Ativo desconhecido: {ativo}")
    return ai_advisor.consultar(ativo, strategy, request.instrucao_extra or "")