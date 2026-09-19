"""Consultor de IA para análise de entradas — Market Insight AI.

Monta um contexto com os dados atuais da análise (sinais por expiração,
votos dos indicadores, preço, notícias) e envia para um modelo de linguagem
(OpenAI por padrão; qualquer endpoint compatível via base_url, ex.: Ollama
ou LM Studio). A resposta é devolvida estruturada:
{direcao, confianca, justificativa, riscos}.

A configuração (link http, modelo, token, headers adicionais) pode ser
definida no .env OU salva pela aba de configuração da interface (persistida
no SQLite; a config salva tem prioridade sobre o .env).

Sem token o módulo retorna None e a interface informa que a função está
desativada. Nunca é recomendação de investimento.
"""
from __future__ import annotations

import json
import os

import analysis
from trade_manager import get_config_valor, set_config_raw

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Chaves usadas na tabela de configuração compartilhada com o trade_manager
_CHAVE_BASE_URL = "ai_base_url"
_CHAVE_MODEL = "ai_model"
_CHAVE_API_KEY = "ai_api_key"
_CHAVE_HEADERS = "ai_headers"

_DEFAULT_MODEL = "gpt-4o-mini"


def _config() -> dict:
    """Config efetiva: a config salva na UI tem prioridade sobre o .env."""
    return {
        "base_url": str(get_config_valor(_CHAVE_BASE_URL) or "").strip()
        or (os.getenv("AI_BASE_URL") or "").strip(),
        "model": str(get_config_valor(_CHAVE_MODEL) or "").strip()
        or os.getenv("AI_MODEL") or _DEFAULT_MODEL,
        "api_key": str(get_config_valor(_CHAVE_API_KEY) or "").strip()
        or (os.getenv("OPENAI_API_KEY") or "").strip(),
        "headers": str(get_config_valor(_CHAVE_HEADERS) or "").strip(),
    }


def disponivel() -> bool:
    return bool(_config()["api_key"])


def config_publica() -> dict:
    """Config para exibir na interface — nunca devolve o token completo."""
    cfg = _config()
    chave = cfg["api_key"]
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "api_key_set": bool(chave),
        "api_key_tail": chave[-4:] if chave else "",
        "headers": cfg["headers"],
    }


def _parse_headers(dados) -> dict:
    """Converte headers (dict ou string JSON) em dict; {} quando vazio."""
    if dados in (None, "", {}):
        return {}
    if isinstance(dados, dict):
        return {str(k): str(v) for k, v in dados.items()}
    texto = str(dados).strip()
    if not texto:
        return {}
    try:
        obj = json.loads(texto)
    except ValueError as exc:
        raise ValueError(f"Headers adicionais não são um JSON válido: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError('Headers adicionais devem ser um objeto JSON (ex.: {"X-Key": "valor"}).')
    return {str(k): str(v) for k, v in obj.items()}


def _config_com_overrides(dados: dict | None) -> dict:
    """Config efetiva aplicando overrides temporários (sem persistir)."""
    cfg = _config()
    if not dados:
        return cfg
    if "base_url" in dados:
        cfg["base_url"] = str(dados["base_url"] or "").strip()
    if "model" in dados:
        cfg["model"] = str(dados["model"] or "").strip() or _DEFAULT_MODEL
    if "api_key" in dados:
        cfg["api_key"] = str(dados["api_key"] or "").strip()
    if "headers" in dados:
        cfg["headers"] = json.dumps(_parse_headers(dados["headers"]), ensure_ascii=False)
    return cfg


def salvar_config(dados: dict) -> tuple[bool, str]:
    """Persiste a configuração da IA no SQLite.

    Aceita um dict com as chaves: base_url, model, api_key e headers
    (dict ou string JSON). Campos ausentes (None) não alteram o valor salvo;
    string vazia limpa a chave (voltando a valer o .env quando houver).
    """
    permitidas = {"base_url", "model", "api_key", "headers"}
    desconhecidas = set(dados) - permitidas
    if desconhecidas:
        return False, f"Campos desconhecidos: {', '.join(sorted(desconhecidas))}"
    try:
        _parse_headers(dados.get("headers"))  # valida antes de gravar
        mapeamento = {
            "base_url": _CHAVE_BASE_URL,
            "model": _CHAVE_MODEL,
            "api_key": _CHAVE_API_KEY,
            "headers": _CHAVE_HEADERS,
        }
        for campo, chave in mapeamento.items():
            if campo not in dados:
                continue
            valor = dados[campo]
            if campo == "headers":
                valor = json.dumps(_parse_headers(valor), ensure_ascii=False)
            else:
                valor = str(valor or "").strip()
            set_config_raw(chave, valor)
    except ValueError as exc:
        return False, str(exc)
    return True, "Configuração da IA salva."


def _criar_cliente(cfg: dict, timeout: int):
    from openai import OpenAI
    kwargs = {"api_key": cfg["api_key"], "timeout": timeout}
    if cfg.get("base_url"):
        kwargs["base_url"] = cfg["base_url"]
    headers = _parse_headers(cfg.get("headers"))
    if headers:
        kwargs["default_headers"] = headers
    return OpenAI(**kwargs)


def _montar_contexto(ativo: str, strategy: str) -> str:
    """Constrói o texto com os parâmetros e a lógica para a IA."""
    try:
        dados = analysis.analyze_asset(ativo, strategy)
    except ValueError as exc:
        return f"Erro ao preparar a análise: {exc}"

    linha = f"Ativo: {ativo} | Estratégia de análise: {dados.get('strategy_name')}\n"
    linha += f"Descrição: {dados.get('strategy_description', '')}\n"
    for expiry, sinal in dados.get("signals", {}).items():
        acerto = sinal.get("historical_accuracy", {})
        acerto_txt = (
            f"{acerto['rate']:.1f}% ({acerto.get('sample_size', 0)} casos)"
            if acerto.get("rate") is not None
            else acerto.get("label", "sem amostra")
        )
        linha += (
            f"\n[{expiry} min] Sinal: {sinal.get('signal')} | Score: {sinal.get('score')} | "
            f"Motivo: {sinal.get('reason', '')} | Estimativa histórica: {acerto_txt}"
        )
        votos = sinal.get("votos") or []
        if votos:
            linha += " | Votos dos indicadores: "
            linha += "; ".join(
                f"{v['nome']}={('CALL' if v['voto'] > 0 else 'PUT' if v['voto'] < 0 else 'neutro')} ({v['motivo']})"
                for v in votos
            )
        resumo = sinal.get("resumo_votos")
        if resumo:
            linha += (
                f" | Resumo: {resumo['bulls']} CALL / {resumo['bears']} PUT / "
                f"{resumo['neutros']} neutros (confiança {resumo['confianca']}%)"
            )
    news = dados.get("news", {})
    if news and news.get("available"):
        linha += f"\n\nPróximos eventos econômicos: {len(news.get('events', []))} "
        linha += f"(bloqueio por notícia de alto impacto: {news.get('blocked', False)})"
    return linha


def _parse_json_resposta(texto: str) -> dict | None:
    """Extrai o primeiro objeto JSON válido da resposta do modelo."""
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
    try:
        return json.loads(texto)
    except Exception:
        pass
    # Fallback: procura um bloco { ... } no texto
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio != -1 and fim > inicio:
        try:
            return json.loads(texto[inicio:fim + 1])
        except Exception:
            pass
    return None


def consultar(ativo: str, strategy: str, instrucao_extra: str = "", timeout: int = 60) -> dict | None:
    """Consulta a IA e devolve a análise estruturada (ou None se indisponível)."""
    cfg = _config()
    if not cfg["api_key"]:
        return None

    contexto = _montar_contexto(ativo, strategy)
    instrucao = (instrucao_extra or "").strip()

    sistema = (
        "Você é um analista quantitativo de opções binárias (uso educacional). "
        "Receba os dados de um ativo e emita APENAS um JSON com os campos: "
        '"direcao" ("CALL", "PUT" ou "AGUARDAR"), "confianca" (0 a 100), '
        '"justificativa" (2-4 frases em pt-BR citando os indicadores) e '
        '"riscos" (lista curta de riscos em pt-BR). '
        "Se os sinais forem conflitantes ou fracos, prefira AGUARDAR. "
        "Nunca prometa retorno; a análise é educacional e não é recomendação de investimento."
    )
    if instrucao:
        sistema += " Instruções adicionais do usuário: " + instrucao

    try:
        cliente = _criar_cliente(cfg, timeout)
        resposta = cliente.chat.completions.create(
            model=cfg["model"],
            temperature=0.2,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": contexto},
            ],
        )
        texto = (resposta.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"[AI] erro na chamada: {exc}")
        return {"erro": f"Falha ao consultar a IA: {exc}"}

    dados_json = _parse_json_resposta(texto)
    if not dados_json:
        return {"erro": "A IA não respondeu em formato JSON.", "resposta_bruta": texto[:500]}

    direcao = str(dados_json.get("direcao", "AGUARDAR")).upper()
    if direcao not in ("CALL", "PUT", "AGUARDAR"):
        direcao = "AGUARDAR"
    try:
        confianca = min(100.0, max(0.0, float(dados_json.get("confianca", 0))))
    except (TypeError, ValueError):
        confianca = 0.0
    return {
        "direcao": direcao,
        "confianca": round(confianca, 1),
        "justificativa": str(dados_json.get("justificativa", "")).strip(),
        "riscos": dados_json.get("riscos") or [],
        "modelo": cfg["model"],
        "contexto": contexto,
    }


def testar(dados: dict | None = None, timeout: int = 15) -> dict:
    """Testa a conexão com a configuração fornecida (ou a salva) sem persistir."""
    try:
        cfg = _config_com_overrides(dados)
    except ValueError as exc:
        return {"ok": False, "erro": str(exc)}
    if not cfg["api_key"]:
        return {"ok": False, "erro": "Token não configurado. Informe o token (ou OPENAI_API_KEY no .env)."}
    try:
        cliente = _criar_cliente(cfg, timeout)
        resposta = cliente.chat.completions.create(
            model=cfg["model"],
            max_tokens=8,
            messages=[{"role": "user", "content": "Responda apenas com a palavra OK."}],
        )
        texto = (resposta.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "modelo": cfg["model"],
            "base_url": cfg["base_url"] or "(padrão da OpenAI)",
            "resposta": texto[:120],
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc)}