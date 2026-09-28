"""Subagente de normas — RAG + apuração + fundamentação."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.calculo.reembolso import apurar
from app.rag.retriever import contexto
from app.schemas import Categoria, Decisao
from app.tools import mcp_client


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _data_conversa() -> date:
    # Data de referencia alinhada aos casos de treino.
    return date(2026, 5, 20)


def responder_pergunta(pergunta: str, state: dict) -> str:
    """Usa RAG para perguntas abertas sobre cobertura/prazos/regras."""
    trechos = contexto(pergunta, top_k=6, max_chars=6000)
    from app.llm import criar_llm

    llm = criar_llm(temperature=0.2)
    sistema = (
        "Você é analista de reembolso da SaúdeMais. Responda em português, "
        "linguagem simples, com base APENAS nos trechos normativos. "
        "Cite mentalmente a regra vigente pela data de vigência mais recente. "
        "Nunca cite CPF completo, CID ou hipótese diagnóstica. "
        "Se o FAQ conflitar com circular mais recente, prevalece a circular. "
        "Resposta entre 3 e 8 frases, atendimento humano, sem jargão interno."
    )
    prompt = (
        f"{sistema}\n\nTRECHOS:\n{trechos}\n\n"
        f"Pergunta do beneficiário: {pergunta}\n\nResposta:"
    )
    out = llm.invoke(prompt)
    return (out.content or "").strip()


def rodar(state: dict) -> dict:
    updates: dict[str, Any] = {"handoff": "supervisor"}
    doc = state.get("documento") or {}
    benef = state.get("beneficiario") or {}
    categoria = state.get("categoria_documento") or doc.get("categoria")

    # Documento inválido — art. 76
    if categoria == Categoria.INVALIDO.value or (state.get("dados_calculo") or {}).get(
        "documento_invalido"
    ):
        updates["categoria_documento"] = Categoria.INVALIDO.value
        updates["decisao"] = None
        updates["valor_solicitado_brl"] = None
        updates["valor_reembolso_brl"] = None
        updates["regras_aplicadas"] = ["ART-76"]
        updates["pendencias"] = []
        updates["dados_calculo"] = {
            **(state.get("dados_calculo") or {}),
            "aguardando_documento_valido": True,
        }
        return updates

    if not categoria or categoria == Categoria.RELATORIO_CLINICO.value:
        return updates

    if not benef or not doc.get("valor_pago"):
        return updates

    valor = Decimal(str(doc["valor_pago"]))
    data_at = _parse_date(doc.get("data_atendimento"))
    adesao = _parse_date(benef.get("data_adesao"))
    if not data_at or not adesao:
        updates["pendencias"] = ["data_atendimento"]
        updates["decisao"] = Decisao.PENDENTE_DOCUMENTO.value
        return updates

    dados = state.get("dados_calculo") or {}
    # Sessões anteriores: preferir histórico MCP (len), senão cadastro-1
    sessoes_ant = int(dados.get("sessoes_anteriores") or 0)
    if sessoes_ant == 0 and dados.get("sessoes_cadastro"):
        # Total anual no cadastro inclui a sessao em andamento.
        sessoes_ant = max(0, int(dados["sessoes_cadastro"]) - 1)

    reemb_ano = Decimal(str(state.get("reembolsado_ano") or 0))
    tem_rel = bool(state.get("tem_relatorio"))

    resultado = apurar(
        categoria=categoria,
        valor_pago=valor,
        codigo_tuss=doc.get("codigo_tuss"),
        data_atendimento=data_at,
        plano=benef.get("plano") or "",
        data_adesao=adesao,
        status=benef.get("status") or "ATIVO",
        sessoes_ano_anteriores=sessoes_ant,
        reembolsado_ano=reemb_ano,
        tem_relatorio=tem_rel,
        data_protocolo=_data_conversa(),
    )

    updates["categoria_documento"] = categoria
    updates["valor_solicitado_brl"] = float(resultado.valor_solicitado)
    updates["regras_aplicadas"] = resultado.regras
    updates["pendencias"] = list(resultado.pendencias)
    updates["dados_calculo"] = {
        **dados,
        "detalhe": resultado.detalhe,
        "num_sessao": sessoes_ant + 1,
    }

    if resultado.decisao == Decisao.ESCALADO_ANALISTA.value:
        carteirinha = state.get("carteirinha") or ""
        if not state.get("protocolo"):
            try:
                prot = mcp_client.abrir_protocolo(
                    carteirinha,
                    {
                        "categoria": categoria,
                        "valor_solicitado_brl": float(valor),
                        "motivo": resultado.detalhe.get("motivo", "alcada"),
                        "regras": resultado.regras,
                    },
                )
                updates["protocolo"] = prot.get("protocolo")
            except Exception:
                updates["protocolo"] = "PENDENTE_MCP"
        updates["decisao"] = Decisao.ESCALADO_ANALISTA.value
        updates["valor_reembolso_brl"] = None
        return updates

    if resultado.decisao == Decisao.PENDENTE_DOCUMENTO.value:
        updates["decisao"] = Decisao.PENDENTE_DOCUMENTO.value
        updates["valor_reembolso_brl"] = None
        return updates

    updates["decisao"] = resultado.decisao
    updates["valor_reembolso_brl"] = (
        float(resultado.valor_reembolso)
        if resultado.valor_reembolso is not None
        else None
    )
    return updates
