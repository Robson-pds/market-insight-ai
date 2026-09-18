"""Gestão de entradas — Market Insight AI.

Responsabilidades:
- Armazenar as configurações de entrada (conta demo/oficial, valor mínimo,
  valor máximo de perda diária, estratégia Fixa/Soros e nível do Soros).
- Registrar entradas no histórico (manuais ou executadas na IQ Option).
- Apurar automaticamente o resultado das entradas executadas (thread de fundo).
- Gerar relatórios filtrados por dia, semana, mês ou ano.

Persistência local em SQLite (market.db). Sempre em pt-BR.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import iq_service

DB_PATH = Path(__file__).resolve().parent / "market.db"

_LOCK = threading.RLock()
_WORKER_THREAD: threading.Thread | None = None
_APURACAO_EM_ANDAMENTO: set[int] = set()
_BD_INICIALIZADO = False

DEFAULTS = {
    "conta": "PRACTICE",
    "valor_entrada": "2.00",
    "valor_max_perda": "9.00",
    "estrategia": "soros",
    "soros_nivel": "3",
}

DIRECOES = ("CALL", "PUT")
PERIODOS = ("dia", "semana", "mes", "ano")
STATUS_ABERTO = "ABERTA"
STATUS_FECHADOS = ("WIN", "LOSS", "EMPATE")  # resultado financeiro conhecido
STATUS_TODOS = ("ABERTA", "WIN", "LOSS", "EMPATE", "CANCELADA", "ERRO")
STATUS_EDITAVEIS = ("ABERTA", "WIN", "LOSS", "EMPATE", "CANCELADA")


@contextmanager
def _db():
    """Conexão SQLite com commit automático e fechamento garantido."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _garantir_bd() -> None:
    """Inicializa o banco uma única vez quando necessário (lazy init)."""
    global _BD_INICIALIZADO
    if _BD_INICIALIZADO:
        return
    with _LOCK:
        if not _BD_INICIALIZADO:
            _init_db()
            # Se outro módulo já criou via iniciar(), apenas sincroniza a flag
            _BD_INICIALIZADO = True


def _init_db() -> None:
    with _LOCK, _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracao (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entradas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                criado_em TEXT NOT NULL,
                criado_ts REAL NOT NULL,
                fechado_em TEXT,
                ativo TEXT NOT NULL,
                direcao TEXT NOT NULL,
                valor REAL NOT NULL,
                expiracao INTEGER NOT NULL,
                estrategia TEXT NOT NULL,
                conta TEXT NOT NULL,
                status TEXT NOT NULL,
                resultado REAL,
                payout REAL,
                ordem_id TEXT,
                origem TEXT NOT NULL,
                observacao TEXT,
                ciclo_nivel INTEGER,
                ciclo_lucro REAL
            )
            """
        )
        # Migração: garante colunas do ciclo Soros em bancos antigos
        colunas = {linha[1] for linha in conn.execute("PRAGMA table_info(entradas)").fetchall()}
        for nome, tipo in (("ciclo_nivel", "INTEGER"), ("ciclo_lucro", "REAL")):
            if nome not in colunas:
                conn.execute(f"ALTER TABLE entradas ADD COLUMN {nome} {tipo}")
        for chave, valor in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO configuracao (chave, valor) VALUES (?, ?)",
                (chave, valor),
            )


# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
def get_config() -> dict:
    _garantir_bd()
    with _LOCK, _db() as conn:
        rows = conn.execute("SELECT chave, valor FROM configuracao").fetchall()
    config = {row["chave"]: row["valor"] for row in rows}
    for chave, valor in DEFAULTS.items():
        config.setdefault(chave, valor)
    return config


def get_config_valor(chave: str, padrao=None):
    config = get_config()
    return config.get(chave, padrao)


def set_config_raw(chave: str, valor) -> None:
    """Grava uma chave de configuração arbitrária (ex.: indicadores, acerto)."""
    _garantir_bd()
    with _LOCK, _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO configuracao (chave, valor) VALUES (?, ?)",
            (chave, str(valor)),
        )


def _num(valor, padrao: float) -> float:
    try:
        numero = float(str(valor).replace(",", "."))
        if numero != numero or numero == float("inf"):  # NaN
            return padrao
        return numero
    except (TypeError, ValueError):
        return padrao


def set_config(dados: dict) -> tuple[bool, str]:
    """Atualiza as configurações permitidas e persiste no SQLite."""
    with _LOCK:
        atuais = get_config()
        atualizado: dict[str, str] = {}

        # Conta: PRACTICE (demo) ou REAL (oficial)
        conta = str(dados.get("conta", atuais["conta"])).upper()
        if conta not in ("PRACTICE", "REAL"):
            return False, "Conta inválida. Use PRACTICE (demo) ou REAL (oficial)."
        atualizado["conta"] = conta

        # Valor mínimo de entrada
        valor = _num(dados.get("valor_entrada", atuais["valor_entrada"]), 0.0)
        if valor <= 0:
            return False, "O valor mínimo de entrada deve ser maior que zero."
        atualizado["valor_entrada"] = f"{valor:.2f}"

        # Valor máximo de perda diária
        perda = _num(dados.get("valor_max_perda", atuais["valor_max_perda"]), 0.0)
        if perda < 0:
            return False, "O valor máximo de perda não pode ser negativo (0 = sem limite)."
        atualizado["valor_max_perda"] = f"{perda:.2f}"

        # Estratégia de entrada
        estrategia = str(dados.get("estrategia", atuais["estrategia"])).lower()
        if estrategia not in ("fixa", "soros"):
            return False, "Estratégia inválida. Use fixa ou soros."
        atualizado["estrategia"] = estrategia

        # Nível do Soros
        nivel = int(_num(dados.get("soros_nivel", atuais["soros_nivel"]), 3))
        if estrategia == "soros":
            if not 1 <= nivel <= 20:
                return False, "O nível do Soros deve estar entre 1 e 20."
        else:
            nivel = max(1, nivel)
        atualizado["soros_nivel"] = str(nivel)

        with _db() as conn:
            for chave, valor_str in atualizado.items():
                conn.execute(
                    "INSERT OR REPLACE INTO configuracao (chave, valor) VALUES (?, ?)",
                    (chave, valor_str),
                )

    # Aplica a conta no cliente da IQ Option (efeito imediato, se conectado)
    if atualizado.get("conta"):
        iq_service.set_account_type(atualizado["conta"])
    return True, "Configurações salvas."


# ---------------------------------------------------------------------------
# Estado derivado (Soros + perda diária) — sempre recalculado do histórico
# ---------------------------------------------------------------------------
def _decidir_ciclo(lucro: float, nivel: int, nivel_max: int) -> tuple[int, float, bool]:
    """Decisão do ciclo Soros no momento da criação de uma entrada.

    Mesma regra do TradeTelegram:
    - Com lucro acumulado e nível dentro do máximo → reinveste (valor base +
      lucro) e o nível sobe.
    - Com o nível estourado → ciclo zera e a entrada volta ao valor base.
    Retorna (nível pós-decisão, lucro pós-decisão, reinvestiu).
    """
    if lucro > 0:
        if nivel <= nivel_max:
            return nivel + 1, lucro, True
        return 0, 0.0, False
    return nivel, 0.0, False


def _recalcular_estado(entradas_fechadas: list[sqlite3.Row]) -> dict:
    """Reconstrói o ciclo Soros e a perda do dia a partir do histórico fechado.

    Lógica Soros (mesma do TradeTelegram):
    - A decisão de reinvestir acontece ANTES da entrada e fica gravada no
      snapshot da entrada (ciclo_nivel/ciclo_lucro), permitindo reconstruir
      o ciclo fielmente mesmo com edições manuais.
    - WIN acumula o lucro; LOSS/EMPATE zeram o ciclo.
    - Entradas do tipo 'fixa' não participam do ciclo.
    """
    soros_nivel_max = int(get_config_valor("soros_nivel", "3"))
    soros_lucro = 0.0
    soros_nivel_atual = 0

    for entrada in entradas_fechadas:
        if entrada["estrategia"] != "soros":
            continue
        if entrada["ciclo_nivel"] is not None and entrada["ciclo_lucro"] is not None:
            # Snapshot da decisão gravado na criação
            soros_nivel_atual = int(entrada["ciclo_nivel"])
            soros_lucro = float(entrada["ciclo_lucro"])
        else:
            # Fallback para registros antigos: repropõe a decisão
            soros_nivel_atual, soros_lucro, _ = _decidir_ciclo(
                soros_lucro, soros_nivel_atual, soros_nivel_max
            )
        resultado = entrada["resultado"] or 0.0
        if entrada["status"] == "WIN" and resultado > 0:
            soros_lucro += resultado
        else:
            soros_lucro = 0.0
            soros_nivel_atual = 0

    hoje = date.today().isoformat()
    perda_dia = sum(
        (e["resultado"] or 0.0) for e in entradas_fechadas
        if e["criado_em"][:10] == hoje and e["status"] in STATUS_FECHADOS
    )
    limite = _num(get_config_valor("valor_max_perda", "0"), 0.0)
    bloqueado = limite > 0 and perda_dia <= -limite

    return {
        "soros_lucro": round(soros_lucro, 2),
        "soros_nivel_atual": soros_nivel_atual,
        "soros_nivel_max": soros_nivel_max,
        "perda_dia": round(perda_dia, 2),
        "perda_dia_limite": limite,
        "perda_dia_bloqueado": bloqueado,
    }


def _entradas_fechadas() -> list[sqlite3.Row]:
    _garantir_bd()
    with _LOCK, _db() as conn:
        return conn.execute(
            "SELECT * FROM entradas ORDER BY id ASC"
        ).fetchall()


def calcular_valor_sugerido() -> dict:
    """Valor que seria usado na próxima entrada executada, conforme estratégia."""
    config = get_config()
    base = _num(config["valor_entrada"], 2.0)
    estado = _recalcular_estado(_entradas_fechadas())

    if config["estrategia"] == "soros":
        lucro = estado["soros_lucro"]
        nivel = estado["soros_nivel_atual"]
        if lucro > 0 and nivel <= estado["soros_nivel_max"]:
            return {"valor": round(base + lucro, 2), "contador": nivel + 1}
    return {"valor": base, "contador": 0}


def get_estado_completo() -> dict:
    """Configurações + estado atual (Soros, perda diária, valor sugerido)."""
    config = get_config()
    estado = _recalcular_estado(_entradas_fechadas())
    sugerido = calcular_valor_sugerido()
    return {
        "config": config,
        "estado": {
            **estado,
            "valor_sugerido": sugerido["valor"],
            "contador_proxima": sugerido["contador"],
        },
        "conectado": iq_service.is_connected(),
        "conta_ativa_api": iq_service.get_balance_mode_safe(),
    }


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------
def _para_dict(entrada: sqlite3.Row) -> dict:
    return {chave: entrada[chave] for chave in entrada.keys()}


def listar_entradas(periodo: str = "dia") -> list[dict]:
    if periodo not in PERIODOS:
        raise ValueError(f"Período inválido: {periodo}")
    inicio = _inicio_periodo(periodo).isoformat(sep=" ")
    _garantir_bd()
    with _LOCK, _db() as conn:
        rows = conn.execute(
            "SELECT * FROM entradas WHERE criado_em >= ? ORDER BY id DESC",
            (inicio,),
        ).fetchall()
    return [_para_dict(row) for row in rows]


def _inicio_periodo(periodo: str) -> datetime:
    hoje = date.today()
    if periodo == "dia":
        return datetime.combine(hoje, datetime.min.time())
    if periodo == "semana":
        return datetime.combine(hoje - timedelta(days=hoje.weekday()), datetime.min.time())
    if periodo == "mes":
        return datetime.combine(hoje.replace(day=1), datetime.min.time())
    # ano
    return datetime.combine(hoje.replace(month=1, day=1), datetime.min.time())


def criar_entrada(
    ativo: str,
    direcao: str,
    expiracao: int,
    valor: float | None = None,
    executar: bool = False,
    origem: str | None = None,
    observacao: str = "",
) -> tuple[bool, dict | str]:
    """
    Registra uma entrada no histórico.

    - executar=True: envia a ordem para a IQ Option primeiro (se conectado e
      dentro do limite de perda diária); o resultado será apurado sozinho.
    - executar=False: registro manual; o resultado deve ser marcado à mão.
    """
    ativo = (ativo or "").strip().upper().replace("=X", "")
    if len(ativo) < 2:
        return False, "Informe um ativo válido."
    direcao = (direcao or "").upper()
    if direcao not in DIRECOES:
        return False, "Direção inválida. Use CALL ou PUT."
    try:
        expiracao = int(expiracao)
    except (TypeError, ValueError):
        return False, "Expiração inválida."
    if expiracao not in (1, 5, 15):
        return False, "Expiração inválida. Use 1, 5 ou 15 minutos."

    config = get_config()
    base = _num(config["valor_entrada"], 2.0)
    estado = _recalcular_estado(_entradas_fechadas())

    with _LOCK:
        # Decisão do ciclo Soros (snapshot da criação)
        estrategia = config["estrategia"]
        if estrategia == "soros":
            ciclo_nivel, ciclo_lucro, reinvestiu = _decidir_ciclo(
                estado["soros_lucro"],
                estado["soros_nivel_atual"],
                estado["soros_nivel_max"],
            )
        else:
            ciclo_nivel, ciclo_lucro, reinvestiu = None, None, False

        # Valor efetivo da entrada
        valor_efetivo = None
        if valor is not None:
            valor_efetivo = _num(valor, 0.0)
        if valor_efetivo is None:
            if reinvestiu:
                valor_efetivo = round(base + estado["soros_lucro"], 2)
            else:
                valor_efetivo = base
        if valor_efetivo <= 0:
            return False, "O valor da entrada deve ser maior que zero."
        valor_efetivo = round(valor_efetivo, 2)

        if executar:
            if not iq_service.is_connected():
                return False, "Não conectado à IQ Option. Verifique o .env / login."
            if estado["perda_dia_bloqueado"]:
                return False, (
                    "Limite de perda diária atingido "
                    f"({estado['perda_dia']:.2f} <= -{estado['perda_dia_limite']:.2f}). "
                    "Nova ordem bloqueada."
                )

        agorats = time.time()
        agora = datetime.now().isoformat(sep=" ", timespec="seconds")

        ordem_id = None
        origem_final = origem or ("executada" if executar else "manual")
        payout = None

        if executar:
            # Envia a ordem para a IQ Option
            ok, retorno = iq_service.buy(ativo, valor_efetivo, direcao, expiracao)
            if not ok:
                return False, f"Falha ao executar a ordem: {retorno}"
            ordem_id = str(retorno)
            payout = iq_service.get_payout(ativo, expiracao)

        with _db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO entradas (
                    criado_em, criado_ts, ativo, direcao, valor, expiracao,
                    estrategia, conta, status, resultado, payout, ordem_id,
                    origem, observacao, ciclo_nivel, ciclo_lucro
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agora, agorats, ativo, direcao, valor_efetivo, expiracao,
                    estrategia, config["conta"], STATUS_ABERTO,
                    payout, ordem_id, origem_final, observacao or None,
                    ciclo_nivel, ciclo_lucro,
                ),
            )
            entrada_id = cursor.lastrowid

    entrada = _para_dict(buscar_entrada(entrada_id))
    if executar and ordem_id:
        threading.Thread(
            target=apurar_entrada,
            args=(entrada_id,),
            daemon=True,
        ).start()

    return True, entrada


def buscar_entrada(entrada_id: int) -> sqlite3.Row | None:
    _garantir_bd()
    with _LOCK, _db() as conn:
        return conn.execute(
            "SELECT * FROM entradas WHERE id = ?", (entrada_id,)
        ).fetchone()


def editar_entrada(entrada_id: int, campos: dict) -> tuple[bool, dict | str]:
    """Edita manualmente status/resultado/observação de uma entrada."""
    with _LOCK:
        entrada = buscar_entrada(entrada_id)
        if entrada is None:
            return False, "Entrada não encontrada."

        status = campos.get("status")
        resultado = campos.get("resultado")
        observacao = campos.get("observacao")

        atualizacoes: list[str] = []
        parametros: list = []

        if status is not None:
            status = str(status).upper()
            if status not in STATUS_EDITAVEIS:
                return False, f"Status inválido. Use: {', '.join(STATUS_EDITAVEIS)}."
            atualizacoes.append("status = ?")
            parametros.append(status)
            if status in STATUS_FECHADOS:
                atualizacoes.append("fechado_em = ?")
                parametros.append(datetime.now().isoformat(sep=" ", timespec="seconds"))
            else:
                atualizacoes.append("fechado_em = NULL")

        if resultado is not None:
            resultado_num = _num(resultado, 0.0)
            if status is None or status in STATUS_FECHADOS:
                atualizacoes.append("resultado = ?")
                parametros.append(round(resultado_num, 2))
        elif status in STATUS_FECHADOS and entrada["resultado"] is None:
            return False, "Informe o resultado (valor ganho/perdido) ao fechar a entrada."

        if observacao is not None:
            atualizacoes.append("observacao = ?")
            parametros.append(observacao or None)

        if not atualizacoes:
            return False, "Nada para atualizar."

        parametros.append(entrada_id)
        with _db() as conn:
            conn.execute(
                f"UPDATE entradas SET {', '.join(atualizacoes)} WHERE id = ?",
                parametros,
            )

    return True, _para_dict(buscar_entrada(entrada_id))


def excluir_entrada(entrada_id: int) -> tuple[bool, str]:
    with _LOCK, _db() as conn:
        cursor = conn.execute("DELETE FROM entradas WHERE id = ?", (entrada_id,))
        if cursor.rowcount == 0:
            return False, "Entrada não encontrada."
    return True, "Entrada excluída."


# ---------------------------------------------------------------------------
# Apuração automática do resultado (IQ Option)
# ---------------------------------------------------------------------------
def apurar_entrada(entrada_id: int) -> None:
    """Consulta o resultado da ordem na IQ Option e atualiza a entrada.

    Roda em thread própria. Binárias (turbo/binary) são apuradas via
    get_betinfo com timeout; se não houver resposta dentro do tempo, a
    entrada permanece ABERTA e a próxima rodada tenta novamente.
    """
    with _LOCK:
        if entrada_id in _APURACAO_EM_ANDAMENTO:
            return
        _APURACAO_EM_ANDAMENTO.add(entrada_id)

    try:
        entrada = buscar_entrada(entrada_id)
        if entrada is None or entrada["status"] != STATUS_ABERTO or not entrada["ordem_id"]:
            return
        if not iq_service.is_connected():
            return

        expiracao = int(entrada["expiracao"] or 1)
        timeout = expiracao * 60 + 150  # margem extra para o servidor fechar
        resultado_final, payout_final = _consultar_resultado(entrada["ordem_id"], timeout)

        if resultado_final is None:
            return  # permanece ABERTA; outra rodada tentará de novo

        status = "WIN" if resultado_final > 0 else ("EMPATE" if resultado_final == 0 else "LOSS")
        with _db() as conn:
            conn.execute(
                """
                UPDATE entradas
                   SET status = ?, resultado = ?, payout = COALESCE(?, payout),
                       fechado_em = ?
                 WHERE id = ?
                """,
                (
                    status,
                    round(resultado_final, 2),
                    payout_final,
                    datetime.now().isoformat(sep=" ", timespec="seconds"),
                    entrada_id,
                ),
            )
    finally:
        with _LOCK:
            _APURACAO_EM_ANDAMENTO.discard(entrada_id)


def _consultar_resultado(ordem_id: str, timeout_segundos: int) -> tuple[float | None, float | None]:
    """Polling get_betinfo até a ordem fechar (win != '')."""
    api = iq_service.get_api()
    if api is None:
        return None, None
    inicio = time.time()
    while time.time() - inicio < timeout_segundos:
        try:
            check, data = api.get_betinfo(ordem_id)
            if check and data:
                registro = (data.get("result", {}).get("data", {}) or {}).get(str(ordem_id))
                if registro and registro.get("win") not in (None, ""):
                    profit = float(registro.get("profit") or 0)
                    deposit = float(registro.get("deposit") or 0)
                    payout = None
                    if profit > 0 and deposit > 0:
                        payout = round(profit / deposit, 4)
                    return profit - deposit, payout
        except Exception:
            pass
        time.sleep(1)
    return None, None


def _worker() -> None:
    """Varre entradas executadas vencidas e dispara a apuração."""
    while True:
        try:
            agora = time.time()
            with _LOCK, _db() as conn:
                rows = conn.execute(
                    """
                    SELECT id, criado_ts, expiracao FROM entradas
                     WHERE status = ? AND ordem_id IS NOT NULL
                    """,
                    (STATUS_ABERTO,),
                ).fetchall()
            for row in rows:
                vencida_em = row["criado_ts"] + int(row["expiracao"] or 1) * 60 + 10
                if agora >= vencida_em:
                    with _LOCK:
                        if row["id"] not in _APURACAO_EM_ANDAMENTO:
                            threading.Thread(
                                target=apurar_entrada,
                                args=(row["id"],),
                                daemon=True,
                            ).start()
        except Exception:
            pass
        time.sleep(5)


def iniciar() -> None:
    """Inicializa o banco, aplica a conta configurada e liga o worker."""
    global _WORKER_THREAD, _BD_INICIALIZADO
    with _LOCK:
        _init_db()
        _BD_INICIALIZADO = True
        conta = get_config_valor("conta", "PRACTICE")
        iq_service.set_account_type(conta)
        if _WORKER_THREAD is None or not _WORKER_THREAD.is_alive():
            _WORKER_THREAD = threading.Thread(target=_worker, daemon=True)
            _WORKER_THREAD.start()
        print("[trade] módulo de entradas iniciado (SQLite: market.db)")


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------
def relatorio(periodo: str) -> dict:
    if periodo not in PERIODOS:
        raise ValueError(f"Período inválido: {periodo}")
    inicio = _inicio_periodo(periodo)
    entradas = listar_entradas(periodo)

    fechadas = [e for e in entradas if e["status"] in STATUS_FECHADOS]
    wins = [e for e in fechadas if e["status"] == "WIN"]
    losses = [e for e in fechadas if e["status"] == "LOSS"]
    empates = [e for e in fechadas if e["status"] == "EMPATE"]

    ganho_bruto = sum(e["resultado"] or 0 for e in fechadas if (e["resultado"] or 0) > 0)
    perda_bruta = sum(e["resultado"] or 0 for e in fechadas if (e["resultado"] or 0) < 0)
    liquido = sum(e["resultado"] or 0 for e in fechadas)
    operado = sum(e["valor"] for e in entradas if e["status"] in STATUS_FECHADOS + (STATUS_ABERTO,))
    decididas = len(wins) + len(losses)

    por_dia: dict[str, dict] = {}
    for e in fechadas:
        dia = e["criado_em"][:10]
        bloco = por_dia.setdefault(dia, {"entradas": 0, "wins": 0, "losses": 0, "empates": 0, "liquido": 0.0})
        bloco["entradas"] += 1
        bloco["liquido"] = round(bloco["liquido"] + (e["resultado"] or 0), 2)
        if e["status"] == "WIN":
            bloco["wins"] += 1
        elif e["status"] == "LOSS":
            bloco["losses"] += 1
        else:
            bloco["empates"] += 1
    dias_ordenados = [
        {**bloco, "dia": dia}
        for dia, bloco in sorted(por_dia.items(), reverse=True)
    ]

    return {
        "periodo": periodo,
        "inicio": inicio.isoformat(sep=" "),
        "fim": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "total_entradas": len(entradas),
        "abertas": len([e for e in entradas if e["status"] == STATUS_ABERTO]),
        "canceladas": len([e for e in entradas if e["status"] == "CANCELADA"]),
        "erros": len([e for e in entradas if e["status"] == "ERRO"]),
        "wins": len(wins),
        "losses": len(losses),
        "empates": len(empates),
        "taxa_acerto": round(len(wins) / decididas * 100, 1) if decididas else None,
        "ganho_bruto": round(ganho_bruto, 2),
        "perda_bruta": round(perda_bruta, 2),
        "liquido": round(liquido, 2),
        "valor_operado": round(operado, 2),
        "por_dia": dias_ordenados,
    }