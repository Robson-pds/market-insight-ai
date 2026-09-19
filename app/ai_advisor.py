"""Consultor de IA para análise de entradas — Market Insight AI.

Monta um contexto com os dados atuais da análise (sinais por expiração,
votos dos indicadores, preço, notícias) e envia para um modelo de linguagem
(OpenAI por padrão; qualquer endpoint compatível via AI_BASE_URL, ex.: Ollama
ou LM Studio). A resposta é devolvida estruturada:
{direcao, confianca, justificativa, riscos}.

Sem OPENAI_API_KEY o módulo retorna None e a interface informa que a função
está desativada. Nunca é recomendação de investimento.
"""
from __future__ import annotations

import json
import os

import analysis

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

AI_MODELO = os.getenv("AI_MODEL", "gpt-4o-mini")
AI_BASE_URL = os.getenv("AI_BASE_URL")  # opcional: Ollama/LM Studio etc.


def disponivel() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


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
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
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
        from openai import OpenAI
        kwargs = {"api_key": api_key, "timeout": timeout}
        if AI_BASE_URL:
            kwargs["base_url"] = AI_BASE_URL
        cliente = OpenAI(**kwargs)
        resposta = cliente.chat.completions.create(
            model=AI_MODELO,
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
        "modelo": AI_MODELO,
        "contexto": contexto,
    }