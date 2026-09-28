"""Supervisor LangGraph: roteamento e handoff entre triagem, documento e normas."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.documento.node import documento_node
from app.agents.normas.node import normas_node
from app.agents.state import AgentState
from app.agents.triagem.node import triagem_node
from app.guardrails.privacidade import extrair_carteirinhas
from app.texto import fold

_checkpointer = MemorySaver()
_grafo_compilado = None

# Intencoes tipicas encaminhadas ao subagente de normas.
_PERGUNTAS_NORMAS = (
    "por que", "porque", "acupuntura", "prazo", "limite", "320", "240",
    "volta", "quanto", "relatorio", "como fica", "e o meu",
    "nao consegue", "sistema", "perder", "recorrer", "so isso", "data", "enfim",
    "cobertura", "cobre", "teto", "coparticip", "quando", "da pra", "da para",
)


def _parece_pergunta(mensagem: str) -> bool:
    if not (mensagem or "").strip():
        return False
    if "?" in mensagem:
        return True
    m = fold(mensagem)
    return any(p in m for p in _PERGUNTAS_NORMAS)


def _cat(state: AgentState) -> str | None:
    """Retorna a categoria documental corrente como string."""
    v = state.get("categoria_documento")
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _pendente_ativo(state: AgentState) -> dict | None:
    """Anexo aguardando carteirinha. Dict vazio representa ausencia no checkpoint."""
    pend = state.get("anexo_pendente")
    if not pend or not isinstance(pend, dict) or not pend.get("base64"):
        return None
    return pend


def _supervisor_route(state: AgentState) -> str:
    """Mapeia o handoff do supervisor para o proximo no do grafo."""
    handoff = state.get("handoff", "")
    if handoff == "triagem":
        return "triagem"
    if handoff == "documento":
        return "documento"
    if handoff == "normas":
        return "normas"
    return END


def _pos_triagem(state: AgentState) -> str:
    """Encaminhamento apos a triagem."""
    handoff = state.get("handoff", "")
    if handoff == "documento":
        return "documento"
    if handoff == "normas":
        return "normas"
    return END


def _pos_documento(state: AgentState) -> str:
    """Apos documento: so normas calcula se o veredito liberou o calculo."""
    if state.get("handoff") == "normas" or state.get("pronto_para_calculo"):
        return "normas"
    return END


def supervisor_node(state: AgentState) -> AgentState:
    """Define o proximo subagente com base em anexo, identidade e intencao."""
    state["handoff"] = ""

    # Anexo enviado neste turno.
    if state.get("anexo_b64"):
        state["handoff"] = "triagem" if not state.get("carteirinha_sessao") else "documento"
        return state

    # Anexo recebido antes da carteirinha: processa apos identificacao.
    pend = _pendente_ativo(state)
    if pend and state.get("carteirinha_sessao"):
        cat = _cat(state)
        ja_analisado = bool(state.get("documento")) and cat not in (None, "INVALIDO")
        if ja_analisado:
            state["anexo_pendente"] = {}
        else:
            state["anexo_b64"] = pend.get("base64")
            state["anexo_mime"] = pend.get("mime_type")
            state["anexo_nome"] = pend.get("filename")
            state["anexo_pendente"] = {}
            state["handoff"] = "documento"
            return state

    mensagem = fold(state.get("mensagem", ""))
    carts = extrair_carteirinhas(state.get("mensagem", ""))
    sess = state.get("carteirinha_sessao")
    fala_terceiro = any(
        k in mensagem
        for k in ("esposa", "esposo", "marido", "filho", "filha", "dependente", "conjuge")
    )
    # Pedido sobre terceiro (art. 8) permanece na triagem.
    if sess and (any(c != sess for c in carts) or fala_terceiro):
        state["handoff"] = "triagem"
        return state

    if not state.get("carteirinha_sessao"):
        state["handoff"] = "triagem"
        return state

    # Duvida do turno: normas (KB), mesmo com pendencia de documento.
    if _parece_pergunta(state.get("mensagem", "")):
        state["handoff"] = "normas"
        return state

    if state.get("pronto_para_calculo"):
        state["handoff"] = "normas"
        return state

    if (state.get("decisao") or state.get("documento")) and any(
        p in mensagem for p in _PERGUNTAS_NORMAS
    ):
        state["handoff"] = "normas"
        return state

    if state.get("documento") and state.get("decisao"):
        state["handoff"] = "normas"
        return state

    state["handoff"] = "triagem"
    return state


def _construir_grafo():
    """Compila o grafo LangGraph com checkpointer de sessao."""
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("triagem", triagem_node)
    g.add_node("documento", documento_node)
    g.add_node("normas", normas_node)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        _supervisor_route,
        {"triagem": "triagem", "documento": "documento", "normas": "normas", END: END},
    )
    g.add_conditional_edges(
        "triagem",
        _pos_triagem,
        {"documento": "documento", "normas": "normas", END: END},
    )
    g.add_conditional_edges(
        "documento",
        _pos_documento,
        {"normas": "normas", END: END},
    )
    g.add_edge("normas", END)
    return g.compile(checkpointer=_checkpointer)


def obter_grafo():
    """Retorna o grafo compilado (singleton por processo)."""
    global _grafo_compilado
    if _grafo_compilado is None:
        _grafo_compilado = _construir_grafo()
    return _grafo_compilado


def resetar_sessoes() -> None:
    """Limpa checkpointer e forca recompilacao do grafo."""
    global _checkpointer, _grafo_compilado
    _checkpointer = MemorySaver()
    _grafo_compilado = None


def processar_turno(session_id: str, mensagem: str, anexo: dict | None = None) -> AgentState:
    """Executa um turno isolado, persistindo estado por session_id."""
    grafo = obter_grafo()
    config = {"configurable": {"thread_id": session_id}}

    entrada: AgentState = {
        "session_id": session_id,
        "mensagem": mensagem or "",
        "handoff": "",
        "resposta": "",
    }

    if anexo:
        entrada["anexo_b64"] = anexo["base64"]
        entrada["anexo_mime"] = anexo.get("mime_type", "application/pdf")
        entrada["anexo_nome"] = anexo.get("filename", "anexo")
        snap = grafo.get_state(config)
        prev = snap.values if snap and snap.values else {}
        # Novo documento apos INVALIDO reabre a apuracao.
        if prev.get("categoria_documento") == "INVALIDO" or (
            hasattr(prev.get("categoria_documento"), "value")
            and getattr(prev.get("categoria_documento"), "value", None) == "INVALIDO"
        ):
            entrada["decisao"] = None
            entrada["categoria_documento"] = None
            entrada["verificacao"] = None
            entrada["pronto_para_calculo"] = False
    else:
        entrada["anexo_b64"] = ""

    return grafo.invoke(entrada, config)
