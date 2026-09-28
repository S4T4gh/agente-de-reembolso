"""A IA redige a fala final com a conversa inteira, sem alterar o que ja foi apurado."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.guardrails.privacidade import extrair_carteirinhas, sanitizar_resposta
from app.llm import criar_llm
from app.texto import fold

_ANCORAS = ("recuso", "nao e um documento fiscal", "anexo")
_PISTAS = ("?", "por que", "porque", "quanto", "quando", "como", "qual", "cobre", "acupuntura", "prazo")


def _fala_pessoa(mensagem: str, nome_anexo: str) -> str:
    texto = (mensagem or "").strip()
    if nome_anexo:
        extra = f"[enviou o arquivo {nome_anexo}]"
        texto = f"{texto} {extra}".strip() if texto else extra
    return texto or "[sem texto]"


def _preservar_ancoras(factual: str, falado: str) -> str:
    """Mantem a frase apurada quando a redacao apagaria uma conclusao obrigatoria."""
    base = fold(factual)
    nova = fold(falado)
    if any(ancora in base and ancora not in nova for ancora in _ANCORAS):
        return factual
    return falado or factual


def _trechos(consulta: str) -> str:
    try:
        from app.rag.retriever import _carregar

        _, bm25 = _carregar()
        partes = []
        for item in bm25.retrieve(consulta)[:3]:
            fonte = (item.node.metadata or {}).get("fonte", "")
            texto = (item.node.get_content() or "").replace("\n", " ")[:400]
            partes.append(f"[{fonte}] {texto}")
        return "\n".join(partes)
    except Exception:
        return ""


def _redigir(historico: list[str], fala: str, factual: str, state: dict) -> str:
    benef = state.get("beneficiario") or {}
    nome = (benef.get("nome") or "").split()
    fatos = [
        f"Nome localizado: {nome[0]}" if nome else "Nome localizado: ainda nao",
        f"Plano: {benef.get('plano') or 'ainda nao'}",
        f"Carteirinha da sessao lida: {'sim' if state.get('carteirinha_sessao') else 'nao'}",
        f"Decisao: {state.get('decisao') or 'ainda nao'}",
        f"Valor reembolso: {state.get('valor_reembolso_brl') if state.get('valor_reembolso_brl') is not None else 'ainda nao'}",
        f"Fala apurada neste turno (nao contradiga): {factual}",
    ]
    duvidas = [
        linha.split(":", 1)[-1].strip()
        for linha in historico
        if linha.startswith("Pessoa:") and any(p in fold(linha) for p in _PISTAS)
    ]
    if any(p in fold(fala) for p in _PISTAS):
        duvidas.append(fala)
    normas = _trechos(duvidas[-1]) if duvidas else ""
    if duvidas:
        fatos.insert(0, "Pergunta que voce precisa responder agora: " + duvidas[-1])
    if normas:
        fatos.append("Trechos de norma:\n" + normas)
    llm = criar_llm()
    sistema = (
        "Voce atende o beneficiario da SaudeMais neste chat e leu todas as mensagens. "
        "Responda a pergunta pendente e tambem o que a pessoa acabou de enviar, no mesmo texto. "
        "Se ela mandou so o numero da carteirinha, diga que leu esse numero e siga com a pergunta anterior. "
        "Use os trechos de norma para a duvida. Nao copie a frase pronta; ela so traz os fatos. "
        "Nao contradiga decisao, valores, protocolo nem recusa de terceiro. "
        "Nao invente reembolso. Nao revele CPF, CID nem carteirinha de outra pessoa. "
        "Nao repita o numero completo da carteirinha. Portugues claro, em poucas frases."
    )
    pedido = (
        "Conversa anterior:\n"
        + ("\n".join(historico[-12:]) or "(inicio)")
        + f"\n\nUltima fala da pessoa:\n{fala}\n\n"
        + "\n".join(fatos)
    )
    texto = (llm.invoke([
        SystemMessage(content=sistema),
        HumanMessage(content=pedido),
    ]).content or "").strip()
    if duvidas and fold(texto) == fold(factual):
        texto = (llm.invoke([
            SystemMessage(content=sistema),
            HumanMessage(content=(
                f"Pergunta pendente: {duvidas[-1]}\n"
                f"Fatos que permanecem: {factual}\n"
                f"Normas:\n{normas or 'sem trecho'}\n"
                "Escreva a resposta da pergunta e confirme que a carteirinha foi lida. "
                "Nao devolva so a frase dos fatos."
            )),
        ]).content or "").strip()
    return texto or factual


def registrar_turno(state: dict, mensagem: str, nome_anexo: str) -> None:
    """Grava o turno e, se a fala ainda nao saiu da IA, pede a redacao com contexto."""
    factual = (state.get("resposta") or "").strip()
    fala = _fala_pessoa(mensagem, nome_anexo)
    historico = list(state.get("dialogo") or [])
    if factual and not state.get("voz_ia"):
        try:
            falado = _redigir(historico, fala, factual, state)
        except Exception:
            falado = factual
        sess = state.get("carteirinha_sessao")
        proibidas = [c for c in extrair_carteirinhas(mensagem or "") if c != sess]
        factual = sanitizar_resposta(_preservar_ancoras(factual, falado), carteirinhas_proibidas=proibidas)
        state["resposta"] = factual
    historico.append(f"Pessoa: {fala}")
    if factual:
        historico.append(f"Agente: {factual}")
    state["dialogo"] = historico[-16:]
