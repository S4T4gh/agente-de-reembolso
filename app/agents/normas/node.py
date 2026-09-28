"""Subagente de normas: so decide DEPOIS do veredito; responde a pergunta do turno."""

from __future__ import annotations

import re
from decimal import Decimal

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import AgentState
from app.calculo.reembolso import LIMITE_ANUAL_URS, URS_2026, _reembolsado_ano, calcular_reembolso
from app.calculo.verificacao import verificar_elegibilidade
from app.guardrails.privacidade import sanitizar_resposta
from app.llm import criar_llm
from app.rag.retriever import buscar_normas
from app.schemas import Categoria, Decisao
from app.texto import fold


def _as_str(v) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _parece_pergunta(mensagem: str) -> bool:
    """Detecta duvida do beneficiario (nao pedido de documento do agente)."""
    if not (mensagem or "").strip():
        return False
    if "?" in mensagem:
        return True
    m = fold(mensagem)
    pistas = (
        "por que", "porque", "quanto", "quando", "como ", "o que", "qual ",
        "sera", "sera que", "da pra", "da para", "cobertura", "prazo", "limite",
        "volta", "acupuntura", "recorrer", "perder", "coparticip", "teto",
        "relatorio", "sessao", "sessoes", "plano cobre", "ainda da",
    )
    return any(p in m for p in pistas)


def _formatar_decisao(state: AgentState) -> str:
    decisao = _as_str(state.get("decisao"))
    valor_sol = state.get("valor_solicitado_brl")
    valor_reemb = state.get("valor_reembolso_brl")
    cat_txt = _as_str(state.get("categoria_documento"))
    regras = state.get("regras_aplicadas") or []
    proto = state.get("protocolo")

    if decisao == Decisao.ESCALADO_ANALISTA.value:
        return (
            f"Seu pedido de {cat_txt or 'reembolso'} foi encaminhado para analise humana. "
            f"Protocolo {proto}. Nao e possivel informar valor de reembolso antes da conclusao."
        )

    if decisao == Decisao.PENDENTE_DOCUMENTO.value:
        pend = ", ".join(state.get("pendencias") or [])
        return (
            f"Seu pedido esta pendente de documento ({pend}). "
            "Nao foi negado: o atendimento permanece aberto aguardando o arquivo."
        )

    if decisao in (Decisao.APROVADO.value, Decisao.APROVADO_PARCIAL.value) and valor_reemb is not None:
        texto = (
            f"Conclui a analise do seu pedido de reembolso ({cat_txt or 'despesa'}). "
            f"Valor pago: R$ {float(valor_sol):.2f}. "
        )
        if decisao == Decisao.APROVADO_PARCIAL.value:
            texto += (
                f"Reembolso aprovado parcialmente: R$ {float(valor_reemb):.2f}. "
                "O valor ficou abaixo do solicitado porque incidem o teto do procedimento "
                "e/ou o saldo do limite anual."
            )
        else:
            texto += (
                f"Reembolso aprovado: R$ {float(valor_reemb):.2f}. "
                "A diferenca em relacao ao valor pago, se houver, vem da coparticipacao."
            )
        if proto:
            texto += f" Protocolo: {proto}."
        return texto

    if decisao == Decisao.NEGADO.value:
        return f"Infelizmente o pedido foi indeferido. Dispositivos aplicados: {', '.join(regras)}."

    return ""


def _responder_pergunta(mensagem: str, state: AgentState) -> str:
    lower = fold(mensagem)
    benef = state.get("beneficiario") or {}
    historico = state.get("historico") or []
    plano = benef.get("plano", "")
    adesao = benef.get("data_adesao", "")
    decisao = _as_str(state.get("decisao"))

    if any(p in lower for p in ("240", "inteiro", "coparticip", "nao volta", "por que nao", "porque nao")):
        return (
            f"No plano {plano}, com adesao desde {adesao}, incide coparticipacao sobre o valor apurado "
            "(menor entre o pago e o teto do procedimento). Por isso nao volta o valor integral do recibo - "
            "a coparticipacao e deduzida conforme art. 44."
        )

    if "acupuntura" in lower:
        return (
            "Acupuntura e procedimento de fronteira: so ha cobertura/reembolso com indicacao clinica "
            "expressa no relatorio. Sem essa indicacao, presume-se finalidade estetica ou de bem-estar "
            "geral, o que nao esta coberto pelo plano (Anexo IV)."
        )

    if any(p in lower for p in ("limite anual", "ja pedi", "so isso", "bastante reembolso")):
        limite = LIMITE_ANUAL_URS * URS_2026
        ja = _reembolsado_ano(historico)
        saldo = limite - ja
        return (
            f"Sim, existe limite anual de reembolso por beneficiario: 48 URS "
            f"(R$ {limite:.2f} em 2026). Pelo historico, voce ja recebeu R$ {ja:.2f} este ano, "
            f"restando saldo de R$ {saldo:.2f}. Quando o saldo e menor que o valor calculado, "
            "o reembolso fica parcial - art. 45."
        )

    if any(p in lower for p in ("prazo", "perder", "recorrer", "reanalise")):
        return (
            "O pedido de reembolso deve ser feito dentro do prazo do regulamento (art. 12, "
            "150 dias corridos apos a Circular 04/2025). Perdido o prazo, o direito decai e o "
            "pedido e indeferido sem exame do merito. Do indeferimento cabe pedido de reanalise "
            "(art. 20), mas isso nao reabre o prazo perdido."
        )

    if "relatorio" in lower:
        return (
            "O relatorio clinico e exigido a partir de certo ponto do acompanhamento: "
            "da 5a sessao de psicoterapia no ano civil em diante (Circular 02/2026), "
            "ou quando o valor pago excede 75% do teto. "
            "Sem o relatorio, o pedido fica pendente - nao negado - com o atendimento aberto "
            "aguardando esse documento. Pode pedir a psicologa e enviar aqui."
        )

    if decisao == Decisao.ESCALADO_ANALISTA.value and any(
        p in lower for p in ("quanto", "volta", "valor", "calcula", "sistema", "nao consegue")
    ):
        proto = state.get("protocolo") or "ja aberto"
        return (
            "Sobre quanto volta: nao informo valor de reembolso agora. "
            "Pedidos de material/OPME nao sao decididos pela analise automatizada e vao para "
            f"analista humano (art. 78). Protocolo {proto} permanece aberto ate a conclusao."
        )

    if any(p in lower for p in ("320", "volta quanto", "quanto volta", "quanto sai", "volta")):
        vr = state.get("valor_reembolso_brl")
        vs = state.get("valor_solicitado_brl")
        if vr is not None and vs is not None:
            return (
                f"Sobre os R$ {float(vs):.2f} do recibo, apos aplicar teto, coparticipacao e limite anual, "
                f"o reembolso calculado e R$ {float(vr):.2f}."
            )

    if any(p in lower for p in ("data", "quando foi", "enfim", "no papel")):
        return (
            "Para a analise, prevalece a data do atendimento constante no documento fiscal, "
            "nao a memoria da conversa. Usei a data do recibo que voce enviou."
        )

    if any(p in lower for p in ("como fica", "meu pedido", "e o meu")):
        base = _formatar_decisao(state)
        if base:
            return base

    if any(p in lower for p in ("quantas", "sessoes", "sessao")):
        return (
            "O numero da sessao no ano nao precisa vir do recibo nem da sua memoria: "
            "a operadora apura pelo historico de pedidos (Nota Tecnica 02, campo C8). "
            "Por isso nao uso o chute que voce der na conversa."
        )

    # Duvida do turno: consulta a base normativa (nao so o estado interno).
    ctx = buscar_normas(mensagem)
    llm = criar_llm()
    situacao = (
        f"decisao={_as_str(state.get('decisao'))}; "
        f"categoria={_as_str(state.get('categoria_documento'))}; "
        f"pendencias={state.get('pendencias') or []}"
    )
    conversa = "\n".join(state.get("dialogo") or [])
    prompt = [
        SystemMessage(content=(
            "Voce e assistente de reembolso da SaudeMais. Responda em portugues, claro e empatico. "
            "PRIORIDADE: responder exatamente a pergunta do beneficiario neste turno, "
            "usando a conversa anterior. A pessoa pode ter informado a carteirinha ou um documento antes. "
            "Nao peça documento novo se a pessoa so fez uma duvida sobre regra/valor/prazo. "
            "NUNCA revele CPF completo, codigos CID ou hipoteses diagnosticas. "
            "Se o pedido estiver ESCALADO_ANALISTA / OPME, diga que nao informa valor agora. "
            "Use apenas as normas fornecidas. Se a norma nao cobrir, diga com honestidade.\n\n"
            f"Conversa ate aqui:\n{conversa or '(inicio)'}\n\n"
            f"Estado do pedido (contexto, nao roteiro): {situacao}\n\n"
            f"Normas:\n{ctx[:6000]}"
        )),
        HumanMessage(content=mensagem),
    ]
    state["voz_ia"] = True
    return llm.invoke(prompt).content


def _reverificar_e_calcular(state: AgentState) -> bool:
    """Reexecuta verificacao; so calcula se OK. Contesta valor previo se bloqueado.

    Retorna True se acabou de fechar um desfecho (aprovacao ou bloqueio).
    """
    doc_info = state.get("documento") or {}
    if not doc_info and not state.get("tem_relatorio"):
        state["verificacao"] = "INCOMPLETO"
        state["pronto_para_calculo"] = False
        state["valor_reembolso_brl"] = None
        state["pendencias"] = list(
            dict.fromkeys(list(state.get("pendencias") or []) + ["documento"])
        )
        return False

    benef = state.get("beneficiario") or {}
    historico = state.get("historico") or []
    if not benef:
        state["verificacao"] = "INCOMPLETO"
        state["pronto_para_calculo"] = False
        state["decisao"] = None
        state["valor_reembolso_brl"] = None
        state["pendencias"] = ["carteirinha_beneficiario"]
        return False

    cat_raw = doc_info.get("categoria") or state.get("categoria_documento")
    if not cat_raw:
        state["verificacao"] = "INCOMPLETO"
        state["pronto_para_calculo"] = False
        state["valor_reembolso_brl"] = None
        return False

    from app.agents.documento.node import _sessao_ano

    valor = doc_info.get("valor_pago")
    valor_d = Decimal(str(valor)) if valor is not None else None
    sessao = _sessao_ano(benef, historico)
    tem_rel = bool(state.get("tem_relatorio")) or any(
        (a or {}).get("categoria") == "RELATORIO_CLINICO"
        for a in (state.get("anexos_processados") or [])
    )

    veredito = verificar_elegibilidade(
        categoria=cat_raw,
        valor_pago=valor_d,
        data_atendimento=doc_info.get("data_atendimento"),
        plano=benef.get("plano"),
        data_adesao=benef.get("data_adesao"),
        historico=historico,
        sessao_ano=sessao,
        tem_relatorio=tem_rel,
        status=benef.get("status"),
        codigo_tuss=doc_info.get("codigo_tuss"),
    )
    state["verificacao"] = veredito.status

    if veredito.status != "OK":
        # Contesta: limpa valor que possa ter ficado no estado.
        state["valor_reembolso_brl"] = None
        state["pronto_para_calculo"] = False
        if veredito.decisao is not None:
            state["decisao"] = (
                veredito.decisao.value
                if hasattr(veredito.decisao, "value")
                else veredito.decisao
            )
        state["regras_aplicadas"] = list(veredito.regras)
        state["pendencias"] = list(veredito.pendencias)
        if valor_d is not None:
            state["valor_solicitado_brl"] = valor_d
        return True

    resultado = calcular_reembolso(
        categoria=Categoria(cat_raw) if isinstance(cat_raw, str) else cat_raw,
        valor_pago=valor_d,
        codigo_tuss=doc_info.get("codigo_tuss"),
        data_atendimento=doc_info.get("data_atendimento"),
        plano=benef.get("plano"),
        data_adesao=benef.get("data_adesao"),
        historico=historico,
        sessao_ano=sessao,
        tem_relatorio=tem_rel,
        status=benef.get("status"),
    )
    state["decisao"] = resultado.decisao.value
    state["valor_reembolso_brl"] = resultado.valor_reembolso
    state["valor_solicitado_brl"] = resultado.valor_solicitado
    state["regras_aplicadas"] = resultado.regras
    state["pendencias"] = resultado.pendencias
    state["pronto_para_calculo"] = False
    state["tem_relatorio"] = False
    return True


def normas_node(state: AgentState) -> AgentState:
    mensagem = state.get("mensagem", "")
    pergunta = _parece_pergunta(mensagem)
    acabou_de_calcular = False

    precisa_calc = bool(state.get("pronto_para_calculo") or state.get("tem_relatorio"))
    if precisa_calc:
        acabou_de_calcular = _reverificar_e_calcular(state)

    decisao_texto = _formatar_decisao(state)
    lower = fold(mensagem or "")
    so_carteirinha = bool(re.fullmatch(r"[\d\s]+", (mensagem or "").strip() or "x"))

    # Prioridade: responder o que a pessoa perguntou neste turno.
    if pergunta and not so_carteirinha:
        resposta_q = _responder_pergunta(mensagem, state)
        if acabou_de_calcular and decisao_texto and not any(
            p in lower for p in ("quanto", "volta", "valor", "como fica", "e o meu")
        ):
            # Duvida normativa no mesmo turno em que fechou: responde a duvida;
            # so acrescenta o desfecho se a pergunta nao for sobre o valor.
            resposta = resposta_q
        else:
            resposta = resposta_q
    elif acabou_de_calcular and decisao_texto:
        anexos = state.get("anexos_processados") or []
        ultimo = anexos[-1] if anexos else {}
        if (ultimo or {}).get("categoria") == "RELATORIO_CLINICO":
            resposta = "Com o relatorio clinico em maos, conclui a apuracao. " + decisao_texto
        else:
            resposta = decisao_texto
    elif decisao_texto and (
        so_carteirinha
        or any(p in lower for p in ("como fica", "e o meu", "meu pedido"))
    ):
        if any(p in lower for p in ("como fica", "e o meu", "meu pedido")):
            resposta = "Sobre o SEU pedido em andamento: " + decisao_texto
        else:
            resposta = decisao_texto
    elif mensagem.strip():
        resposta = _responder_pergunta(mensagem, state)
    else:
        resposta = decisao_texto or "Analise concluida. Posso esclarecer algum ponto?"

    if not resposta:
        resposta = decisao_texto or _responder_pergunta(mensagem, state)

    # Se verificacao ficou incompleta e nao havia pergunta, explique o erro.
    if (
        state.get("verificacao") == "INCOMPLETO"
        and not pergunta
        and not (resposta and len(resposta) > 40)
    ):
        faltas = ", ".join(state.get("pendencias") or []) or "dados obrigatorios"
        resposta = (
            f"Nao posso aprovar com o estado incompleto ({faltas}). "
            "Envie o que falta para eu verificar de novo — sem isso nao calculo valor."
        )

    state["resposta"] = sanitizar_resposta(resposta)
    state["handoff"] = "supervisor"
    return state
