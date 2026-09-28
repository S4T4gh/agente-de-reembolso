"""Subagente de triagem - carteirinha, MCP e escopo (art. 8)."""

from __future__ import annotations

from typing import Any

from app.guardrails.saida import extrair_carteirinha, menciona_terceiro
from app.tools import mcp_client


def _sessoes_ano(benef: dict) -> int:
    if "sessoes_terapia_ano" in benef:
        return int(benef.get("sessoes_terapia_ano") or 0)
    st = benef.get("sessoes_terapia") or {}
    if isinstance(st, dict):
        return int(st.get("ano_corrente") or st.get("ano") or 0)
    return 0


def _somar_reembolsado(historico: list) -> float:
    total = 0.0
    for p in historico or []:
        v = p.get("valor_reembolsado_brl")
        if v is None:
            v = p.get("valor_reembolso_brl")
        if v is not None:
            total += float(v)
    return round(total, 2)


def rodar(state: dict) -> dict:
    """Identifica beneficiario e aplica guardrail de terceiro."""
    msg = state.get("mensagem") or ""
    updates: dict[str, Any] = {"handoff": "supervisor"}

    carteirinha_sessao = state.get("carteirinha")
    nova = extrair_carteirinha(msg)
    if (
        carteirinha_sessao
        and nova
        and nova != carteirinha_sessao
        and menciona_terceiro(msg)
    ):
        updates["fora_escopo_terceiro"] = True
        updates["resposta"] = (
            "Entendo o interesse, mas este atendimento esta vinculado apenas a "
            "carteirinha que abriu a conversa. Pedidos de conjuge, dependente ou "
            "outro beneficiario precisam de um atendimento proprio, aberto com a "
            "carteirinha correspondente. Posso seguir com a analise do seu pedido "
            "aqui — me diga como prefere continuar."
        )
        return updates

    if not carteirinha_sessao and nova:
        updates["carteirinha"] = nova
        carteirinha_sessao = nova

    if carteirinha_sessao and not state.get("beneficiario"):
        try:
            benef = mcp_client.consultar_beneficiario(carteirinha_sessao)
            hist = mcp_client.consultar_historico(carteirinha_sessao)
            pedidos = hist.get("pedidos") or []
            updates["beneficiario"] = benef
            updates["historico"] = pedidos
            updates["reembolsado_ano"] = _somar_reembolsado(pedidos)
            updates["dados_calculo"] = {
                **(state.get("dados_calculo") or {}),
                "sessoes_anteriores": len(pedidos),
                "sessoes_cadastro": _sessoes_ano(benef),
            }
        except Exception as exc:  # noqa: BLE001
            updates["resposta"] = (
                "Nao localizei a carteirinha informada no cadastro. "
                "Pode conferir os digitos e me enviar novamente?"
            )
            updates["pendencias"] = ["carteirinha_invalida"]
            _ = exc

    return updates
