"""Subagente de triagem: identidade, escopo (art. 8) e acolhimento."""

from __future__ import annotations

from app.agents.state import AgentState
from app.guardrails.privacidade import extrair_carteirinhas, sanitizar_resposta
from app.texto import fold
from app.tools import mcp_client

_DESFECHOS_TITULAR = {
    "APROVADO",
    "APROVADO_PARCIAL",
    "PENDENTE_DOCUMENTO",
    "NEGADO",
    "ESCALADO_ANALISTA",
}


def _tem_pendente(state: AgentState) -> bool:
    pend = state.get("anexo_pendente")
    return bool(isinstance(pend, dict) and pend.get("base64"))


def _decisao_val(v) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def triagem_node(state: AgentState) -> AgentState:
    mensagem = state.get("mensagem", "")
    carteirinha_sessao = state.get("carteirinha_sessao")
    resposta_partes: list[str] = []

    carteirinhas_msg = extrair_carteirinhas(mensagem)
    msg = fold(mensagem)
    terceiro_detectado = False

    if carteirinha_sessao and carteirinhas_msg:
        for cart in carteirinhas_msg:
            if cart != carteirinha_sessao:
                terceiro_detectado = True

    if carteirinha_sessao and any(
        k in msg
        for k in ("esposa", "esposo", "marido", "filho", "filha", "dependente", "conjuge")
    ):
        terceiro_detectado = True

    if carteirinhas_msg and not carteirinha_sessao:
        cart = carteirinhas_msg[0]
        try:
            benef = mcp_client.consultar_beneficiario(cart)
            hist = mcp_client.consultar_historico(cart)
            state["carteirinha_sessao"] = cart
            state["carteirinha"] = cart
            state["beneficiario"] = benef
            state["historico"] = hist.get("pedidos", [])
            carteirinha_sessao = cart

            if _tem_pendente(state) or state.get("anexo_b64"):
                if _tem_pendente(state) and not state.get("anexo_b64"):
                    pend = state["anexo_pendente"]
                    state["anexo_b64"] = pend["base64"]
                    state["anexo_mime"] = pend.get("mime_type", "application/pdf")
                    state["anexo_nome"] = pend.get("filename", "anexo")
                    state["anexo_pendente"] = {}
                state["handoff"] = "documento"
                return state

            nome = benef.get("nome", "")
            resposta_partes.append(
                f"Obrigado, {nome.split()[0]}! Localizei seu cadastro no plano {benef.get('plano', '')}. "
                "Para seguir com o pedido de reembolso, preciso do documento fiscal da despesa - "
                "pode ser recibo ou nota com valor, data do atendimento e identificacao do prestador."
            )
        except Exception:
            resposta_partes.append(
                "Nao encontrei essa carteirinha no cadastro. Pode conferir os 16 digitos e enviar novamente?"
            )

    elif terceiro_detectado:
        # Art. 8: recusa de terceiro; FORA_DE_ESCOPO apenas sem desfecho do titular.
        dec_titular = _decisao_val(state.get("decisao"))
        tem_desfecho = dec_titular in _DESFECHOS_TITULAR
        if not tem_desfecho:
            state["decisao"] = "FORA_DE_ESCOPO"
            state["regras_aplicadas"] = list(
                dict.fromkeys(list(state.get("regras_aplicadas") or []) + ["ART-8"])
            )
        resposta_partes.append(
            "Entendi o pedido sobre outra pessoa (conjuge/dependente). "
            "Recuso de forma explicita: nao consulto plano, historico nem valores de terceiros "
            "nesta conversa, e nao uso a carteirinha dela. "
            "Quem precisar de reembolso deve abrir atendimento proprio com a carteirinha dele(a). "
            "Seu atendimento continua aberto: posso seguir com a analise do SEU pedido aqui."
        )
        if tem_desfecho:
            cat = state.get("categoria_documento")
            cat_val = cat.value if hasattr(cat, "value") else cat
            vr = state.get("valor_reembolso_brl")
            if vr is not None:
                resposta_partes.append(
                    f"Sobre o SEU pedido ({cat_val}): a analise ja aponta reembolso de R$ {float(vr):.2f}."
                )
            elif cat_val:
                resposta_partes.append(
                    f"Sobre o SEU pedido ({cat_val}): a analise preliminar ja esta registrada neste atendimento."
                )

    elif (_tem_pendente(state) or state.get("anexo_b64")) and not carteirinha_sessao:
        if state.get("anexo_b64") and not _tem_pendente(state):
            state["anexo_pendente"] = {
                "base64": state["anexo_b64"],
                "mime_type": state.get("anexo_mime"),
                "filename": state.get("anexo_nome"),
            }
        nome_arq = state.get("anexo_nome") or "anexo"
        if any(p in msg for p in ("reembolso", "consulta", "dermatolog", "pedir", "desculpa", "mandei")):
            resposta_partes.append(
                f"Recebi o anexo ({nome_arq}) e entendi que e pedido de reembolso. "
                "O arquivo ficou guardado nesta conversa. "
                "Para eu consultar o cadastro e analisar o documento, envie agora os 16 digitos "
                "da sua carteirinha (somente a sua)."
            )
        elif "carteirinha" in msg or any(ch.isdigit() for ch in mensagem):
            resposta_partes.append(
                f"Ja tenho o anexo ({nome_arq}) nesta conversa. "
                "Ainda nao validei a carteirinha: envie os 16 digitos "
                "(com ou sem espacos) para eu consultar o cadastro e analisar o arquivo."
            )
        else:
            resposta_partes.append(
                f"Recebi o seu anexo ({nome_arq}) e ja o guardei para analise de reembolso. "
                "Para consultar o cadastro e abrir a analise do documento, "
                "preciso apenas dos 16 digitos da sua carteirinha."
            )

    elif state.get("anexo_b64") and carteirinha_sessao and not state.get("documento"):
        state["handoff"] = "documento"
        return state

    elif not carteirinha_sessao:
        if any(
            p in msg
            for p in ("reembolso", "psicolog", "consulta", "terapia", "cirurgia", "exame")
        ):
            resposta_partes.append(
                "Posso ajudar com o pedido de reembolso. Para comecar, informe os 16 digitos "
                "da sua carteirinha do plano SaudeMais."
            )
        else:
            resposta_partes.append(
                "Ola! Sou o assistente de reembolso da SaudeMais. "
                "Posso orientar sobre pedidos de reembolso fora da rede - "
                "basta informar sua carteirinha e enviar o documento fiscal da despesa."
            )

    elif carteirinha_sessao and not state.get("anexo_b64") and not state.get("documento"):
        # Se a pessoa fez uma duvida (prazo/cobertura), normas responde; nao empurra so pedido de anexo.
        if any(
            p in msg
            for p in (
                "por que", "porque", "quanto", "prazo", "limite", "cobertura",
                "cobre", "acupuntura", "quando", "da pra", "?",
            )
        ) or "?" in mensagem:
            state["handoff"] = "normas"
            return state
        resposta_partes.append(
            "Certo! Quando tiver o recibo ou nota fiscal da despesa, pode enviar aqui em PDF ou foto."
        )

    elif carteirinha_sessao and state.get("documento"):
        cat = state.get("categoria_documento")
        cat_val = cat.value if hasattr(cat, "value") else cat
        # Duvida no meio do fluxo: normas (KB), em vez de so pedir o proximo documento.
        if any(
            p in msg
            for p in (
                "por que", "porque", "quanto", "prazo", "limite", "cobertura",
                "cobre", "acupuntura", "volta", "relatorio", "quando", "?",
            )
        ) or "?" in mensagem:
            state["handoff"] = "normas"
            return state
        if cat_val == "INVALIDO":
            resposta_partes.append(
                "Continuo aguardando um documento fiscal valido de despesa assistencial "
                "(recibo ou nota). O arquivo anterior nao serve para o pedido."
            )
        elif state.get("decisao"):
            resposta_partes.append(
                "Seu pedido ja tem uma analise preliminar. "
                "Pode perguntar sobre o valor, a cobertura ou o proximo passo."
            )
        else:
            resposta_partes.append(
                "Estou com o seu cadastro e o documento em maos. "
                "Se quiser, pergunte sobre o valor, a cobertura ou o andamento do pedido."
            )

    if resposta_partes:
        proibidas = []
        if carteirinhas_msg and carteirinha_sessao:
            proibidas = [c for c in carteirinhas_msg if c != carteirinha_sessao]
        state["resposta"] = sanitizar_resposta(
            " ".join(resposta_partes), carteirinhas_proibidas=proibidas
        )
        state["handoff"] = "fim"

    return state
