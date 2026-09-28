"""Subagente de documento: classificacao, extracao e VERIFICACAO.

Nao aprova valor aqui. So escreve o veredito; o calculo fica no no de normas
depois que verificacao == OK.
"""

from __future__ import annotations

from decimal import Decimal

from app.agents.documento.extrator import extrair_anexo
from app.agents.state import AgentState
from app.calculo.verificacao import verificar_elegibilidade
from app.guardrails.privacidade import sanitizar_resposta
from app.schemas import Categoria, Decisao
from app.tools import mcp_client


def _limpar_anexo(state: AgentState) -> None:
    state["anexo_b64"] = ""
    state["anexo_pendente"] = {}


def _sessao_ano(beneficiario: dict, historico: list) -> int:
    """Calcula o numero da sessao no ano civil a partir do historico MCP."""
    base = len(historico or []) + 1
    if "sessoes_terapia_ano" in beneficiario:
        return max(base, int(beneficiario["sessoes_terapia_ano"]))
    st = beneficiario.get("sessoes_terapia", {})
    if isinstance(st, dict):
        return max(base, int(st.get("ano_corrente", base)))
    return base


def _aplicar_bloqueio(state: AgentState, veredito, valor: Decimal | None) -> None:
    """Grava decisao de bloqueio sem inventar valor de reembolso."""
    state["verificacao"] = "BLOQUEADO"
    state["pronto_para_calculo"] = False
    state["valor_reembolso_brl"] = None
    if valor is not None:
        state["valor_solicitado_brl"] = valor
    if veredito.decisao is not None:
        state["decisao"] = (
            veredito.decisao.value
            if hasattr(veredito.decisao, "value")
            else veredito.decisao
        )
    state["regras_aplicadas"] = list(veredito.regras)
    state["pendencias"] = list(veredito.pendencias)


def documento_node(state: AgentState) -> AgentState:
    b64 = state.get("anexo_b64")
    if not b64:
        state["handoff"] = "triagem"
        return state

    mime = state.get("anexo_mime") or "application/pdf"
    nome = state.get("anexo_nome") or "anexo"
    doc = extrair_anexo(b64, mime, nome)

    anexos = list(state.get("anexos_processados") or [])
    anexos.append({"nome": nome, "categoria": doc.categoria.value})
    state["anexos_processados"] = anexos

    if doc.categoria == Categoria.RELATORIO_CLINICO:
        state["tem_relatorio"] = True
        state["verificacao"] = None
        state["pronto_para_calculo"] = True
        state["resposta"] = sanitizar_resposta(
            "Recebi o relatorio clinico circunstanciado e vinculei ao seu pedido. "
            "Vou retomar a analise do reembolso com base nele."
        )
        state["handoff"] = "normas"
        _limpar_anexo(state)
        return state

    state["documento"] = {
        "categoria": doc.categoria.value,
        "valor_pago": str(doc.valor_pago) if doc.valor_pago else None,
        "data_atendimento": doc.data_atendimento,
        "codigo_tuss": doc.codigo_tuss,
        "texto_resumo": doc.texto[:500],
    }
    state["categoria_documento"] = doc.categoria.value if hasattr(doc.categoria, "value") else doc.categoria

    benef = state.get("beneficiario") or {}
    historico = state.get("historico") or []
    carteirinha = state.get("carteirinha_sessao") or ""

    if doc.categoria == Categoria.INVALIDO:
        state["verificacao"] = "BLOQUEADO"
        state["pronto_para_calculo"] = False
        state["decisao"] = None
        state["valor_solicitado_brl"] = None
        state["valor_reembolso_brl"] = None
        state["pendencias"] = []
        state["regras_aplicadas"] = ["ART-76"]
        state["resposta"] = sanitizar_resposta(
            "Esse arquivo nao e um documento fiscal de despesa assistencial - "
            "parece conta de consumo ou outro comprovante que nao serve para reembolso. "
            "O atendimento permanece aberto; envie o recibo ou a nota fiscal correta do procedimento."
        )
        state["handoff"] = "supervisor"
        _limpar_anexo(state)
        return state

    if not benef:
        state["verificacao"] = "INCOMPLETO"
        state["pronto_para_calculo"] = False
        state["pendencias"] = ["carteirinha_beneficiario"]
        state["resposta"] = sanitizar_resposta(
            "Recebi o documento. Para consultar seu cadastro e concluir a analise, "
            "preciso da sua carteirinha do plano (somente a sua)."
        )
        state["handoff"] = "triagem"
        _limpar_anexo(state)
        return state

    valor = doc.valor_pago
    sessao = _sessao_ano(benef, historico)

    # 1) Verificacao escreve o veredito ANTES de qualquer calculo/aprovacao.
    veredito = verificar_elegibilidade(
        categoria=doc.categoria,
        valor_pago=valor,
        data_atendimento=doc.data_atendimento,
        plano=benef.get("plano"),
        data_adesao=benef.get("data_adesao"),
        historico=historico,
        sessao_ano=sessao,
        tem_relatorio=bool(state.get("tem_relatorio")),
        status=benef.get("status"),
        codigo_tuss=doc.codigo_tuss,
    )
    state["verificacao"] = veredito.status

    if veredito.status == "INCOMPLETO":
        state["pronto_para_calculo"] = False
        state["decisao"] = None
        state["valor_reembolso_brl"] = None
        if valor is not None:
            state["valor_solicitado_brl"] = valor
        state["pendencias"] = list(veredito.pendencias)
        state["regras_aplicadas"] = list(veredito.regras) or ["ART-76"]
        faltas = ", ".join(veredito.pendencias) or "dados obrigatorios"
        state["resposta"] = sanitizar_resposta(
            f"Ainda nao consigo concluir a apuracao: faltam dados ({faltas}). "
            "Sem esses campos eu nao aprovo nem calculo valor — "
            "envie o documento completo ou confirme a carteirinha."
        )
        state["handoff"] = "supervisor"
        _limpar_anexo(state)
        return state

    if veredito.status == "BLOQUEADO":
        _aplicar_bloqueio(state, veredito, valor)
        carteirinha = state.get("carteirinha_sessao") or ""

        if veredito.escalonar and carteirinha:
            if not state.get("protocolo"):
                try:
                    proto = mcp_client.abrir_protocolo(
                        carteirinha,
                        {
                            "categoria": doc.categoria.value,
                            "valor_solicitado": str(valor) if valor is not None else "",
                            "motivo": "encaminhamento obrigatorio art. 78",
                        },
                    )
                    state["protocolo"] = proto.get("protocolo")
                except Exception:
                    state["protocolo"] = None
                    state["pendencias"] = list(state.get("pendencias") or []) + [
                        "falha_ao_abrir_protocolo_mcp"
                    ]
            prot_txt = state.get("protocolo") or "(em abertura)"
            state["resposta"] = sanitizar_resposta(
                f"Identifiquei pedido na categoria {doc.categoria.value} "
                f"(valor solicitado R$ {float(valor or 0):.2f}). "
                "Esses casos nao sao decididos pela analise automatizada e vao para "
                "analista humano - por isso nao informo valor de reembolso agora. "
                f"Protocolo {prot_txt}."
            )
            state["handoff"] = "supervisor"
        elif veredito.decisao == Decisao.NEGADO:
            state["resposta"] = sanitizar_resposta(
                f"O pedido foi indeferido. Dispositivos: {', '.join(veredito.regras)}. "
                "Nao ha valor de reembolso a pagar neste caso."
            )
            state["handoff"] = "supervisor"
        elif veredito.decisao == Decisao.PENDENTE_DOCUMENTO:
            pend = (
                veredito.pendencias[0]
                if veredito.pendencias
                else "documentacao complementar"
            )
            state["resposta"] = sanitizar_resposta(
                f"Analisei o recibo. Ainda falta: {pend}. "
                "O pedido fica pendente - nao negado - ate esse documento chegar; "
                "ai continuo a apuracao no mesmo atendimento."
            )
            state["handoff"] = "supervisor"
        else:
            state["handoff"] = "supervisor"
        _limpar_anexo(state)
        return state

    # 2) Veredito OK: NAO aprova aqui — encaminha ao no de normas para calcular.
    state["pronto_para_calculo"] = True
    state["decisao"] = None
    state["valor_reembolso_brl"] = None
    if valor is not None:
        state["valor_solicitado_brl"] = valor
    state["pendencias"] = []
    state["handoff"] = "normas"
    _limpar_anexo(state)
    return state
