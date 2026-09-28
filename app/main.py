"""API HTTP do agente: /health, /chat e /reset."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI

from app.llm import carregar_env
from app.schemas import ChatRequest, ChatResponse, Categoria, Decisao

carregar_env()
logger = logging.getLogger("agente")

app = FastAPI(title="Agente de Reembolso")


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _enum_str(v):
    if v is None:
        return None
    if isinstance(v, (Categoria, Decisao)):
        return v.value
    if isinstance(v, str) and v:
        return v
    return None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Processa o turno e devolve ChatResponse; falhas internas nao geram HTTP 500."""
    try:
        from app.agents.supervisor import graph as gmod

        anexo = req.anexo.model_dump() if req.anexo else None
        estado = gmod.processar_turno(req.session_id, req.mensagem or "", anexo)
    except Exception:
        logger.exception("falha no turno session_id=%s", req.session_id)
        return ChatResponse(
            resposta=(
                "Tive uma instabilidade momentanea ao processar sua mensagem. "
                "Pode repetir o pedido ou reenviar o documento? "
                "Seu atendimento permanece aberto."
            ),
            pendencias=["falha_interna_temporaria"],
        )

    try:
        resposta = (estado.get("resposta") or "").strip()
        if len(resposta) < 20:
            resposta = (
                "Estou analisando seu pedido de reembolso. "
                "Se puder, envie carteirinha e o documento fiscal da despesa."
            )
        return ChatResponse(
            resposta=resposta,
            categoria_documento=_enum_str(estado.get("categoria_documento")),
            decisao=_enum_str(estado.get("decisao")),
            valor_solicitado_brl=_dec(estado.get("valor_solicitado_brl")),
            valor_reembolso_brl=_dec(estado.get("valor_reembolso_brl")),
            regras_aplicadas=list(estado.get("regras_aplicadas") or []),
            protocolo=estado.get("protocolo"),
            pendencias=list(estado.get("pendencias") or []),
        )
    except Exception:
        logger.exception("falha ao montar ChatResponse")
        return ChatResponse(
            resposta=(
                "Recebi sua mensagem e estou organizando a analise. "
                "Pode confirmar a carteirinha ou reenviar o comprovante, se ainda nao enviou?"
            ),
        )


@app.post("/reset")
def reset() -> dict:
    try:
        from app.agents.supervisor.graph import resetar_sessoes

        resetar_sessoes()
    except Exception:
        logger.exception("falha no reset")
    return {"status": "ok"}
