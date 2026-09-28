"""Subagente de documento — classifica e extrai campos do anexo."""

from __future__ import annotations

from typing import Any

from app.agents.documento.extrair import extrair
from app.schemas import Categoria


def rodar(state: dict) -> dict:
    updates: dict[str, Any] = {"handoff": "supervisor"}
    anexo = state.get("anexo")
    if not anexo:
        return updates

    doc = extrair(anexo)
    payload = doc.model_dump(mode="json")

    # Relatório clínico sanando pendência de terapia
    if doc.categoria == Categoria.RELATORIO_CLINICO:
        updates["tem_relatorio"] = True
        updates["documento"] = {
            **(state.get("documento") or {}),
            "relatorio_recebido": True,
            "relatorio_texto": doc.texto[:2000],
        }
        # Mantém categoria do pedido principal se já houver
        if state.get("categoria_documento") in {None, "RELATORIO_CLINICO"}:
            # Se só chegou relatório sem recibo, registra categoria do relatório
            if not (state.get("documento") or {}).get("valor_pago"):
                updates["categoria_documento"] = Categoria.RELATORIO_CLINICO.value
        return updates

    updates["documento"] = payload
    updates["categoria_documento"] = doc.categoria.value

    if doc.categoria == Categoria.INVALIDO:
        updates["decisao"] = None  # ainda não fecha — protocolo aberto
        updates["pendencias"] = []
        updates["dados_calculo"] = {
            **(state.get("dados_calculo") or {}),
            "documento_invalido": True,
        }
    return updates
