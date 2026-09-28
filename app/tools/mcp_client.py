"""Cliente MCP da operadora (URL e token exclusivos de ambiente)."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from app.llm import carregar_env


def _cfg() -> tuple[str, str]:
    carregar_env()
    url = os.getenv("MCP_OPERADORA_URL", "").strip()
    token = os.getenv("MCP_OPERADORA_TOKEN", "").strip()
    if not url:
        raise RuntimeError("MCP_OPERADORA_URL nao definida")
    return url, token


@asynccontextmanager
async def _sessao():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url, token = _cfg()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _resultado(result: Any) -> dict:
    if result is None:
        return {}
    if getattr(result, "structuredContent", None):
        return dict(result.structuredContent)
    parts = []
    for item in getattr(result, "content", []) or []:
        texto = getattr(item, "text", None)
        if texto:
            parts.append(texto)
    if not parts:
        return {}
    bruto = "\n".join(parts)
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        return {"raw": bruto}


async def _chamar(nome: str, argumentos: dict) -> dict:
    async with _sessao() as session:
        result = await session.call_tool(nome, arguments=argumentos)
        return _resultado(result)


def _sync(coro):
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def consultar_beneficiario(carteirinha: str) -> dict:
    digitos = "".join(c for c in carteirinha if c.isdigit())
    try:
        return _sync(_chamar("consultar_beneficiario", {"carteirinha": digitos}))
    except Exception as exc:
        raise RuntimeError(f"MCP consultar_beneficiario falhou: {exc}") from exc


def consultar_historico(carteirinha: str) -> dict:
    digitos = "".join(c for c in carteirinha if c.isdigit())
    try:
        return _sync(_chamar("consultar_historico", {"carteirinha": digitos}))
    except Exception as exc:
        raise RuntimeError(f"MCP consultar_historico falhou: {exc}") from exc


def abrir_protocolo(carteirinha: str, payload: dict) -> dict:
    digitos = "".join(c for c in carteirinha if c.isdigit())
    try:
        return _sync(
            _chamar("abrir_protocolo", {"carteirinha": digitos, "payload": payload})
        )
    except Exception as exc:
        raise RuntimeError(f"MCP abrir_protocolo falhou: {exc}") from exc
