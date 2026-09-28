"""Estado compartilhado da sessao (LangGraph AgentState)."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    session_id: str
    mensagem: str
    handoff: str

    # Anexo do turno corrente
    anexo_b64: str | None
    anexo_mime: str | None
    anexo_nome: str | None
    anexo_pendente: dict | None

    # Identidade e dados MCP
    carteirinha_sessao: str | None
    carteirinha: str | None
    pediu_carteirinha: bool
    beneficiario: dict | None
    historico: list
    terceiros_recusados: list

    # Documento fiscal analisado
    documento: dict | None
    anexos_processados: list
    tem_relatorio: bool

    # Desfecho estruturado
    categoria_documento: Any
    decisao: Any
    valor_solicitado_brl: Any
    valor_reembolso_brl: Any
    regras_aplicadas: list
    protocolo: str | None
    pendencias: list

    # Veredito da apuracao (OK antes de qualquer aprovacao)
    verificacao: str | None
    pronto_para_calculo: bool

    resposta: str
