"""Onde a chave entra e como falar com o modelo. Já pronto — use e siga.

O modelo da prova é o **Gemini 2.5 Flash Lite**, servido por um gateway da
banca. Você não configura provedor, não escolhe modelo e não passa chave de
nuvem nenhuma: as duas linhas do seu `.env` bastam.

    BOOTCAMP_LLM_ENDPOINT=https://...        # a URL que você recebeu
    BOOTCAMP_API_KEY=bc26_...                # a sua chave, individual

Este arquivo é encanamento, não é a prova. Está pronto de propósito: ninguém é
avaliado por acertar `base_url`. O que se avalia é o agente que você constrói em
cima disto.

    from app.llm import criar_llm, criar_embeddings

    llm = criar_llm()                        # LangChain / LangGraph
    emb = criar_embeddings()

    from app.llm import criar_llm_llamaindex, criar_embeddings_llamaindex

    Settings.llm = criar_llm_llamaindex()    # LlamaIndex
    Settings.embed_model = criar_embeddings_llamaindex()

A sua chave tem crédito de 1 milhão de tokens de entrada e 1 milhão de saída.
`GET {endpoint}/saldo`, com a mesma chave, mostra quanto sobrou. Esgotou, as
chamadas passam a devolver 429 — e não há recarga. Reenviar a base de
conhecimento inteira a cada turno queima o crédito antes do fim da prova.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MODELO = "moonshotai/kimi-k3"
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSOES = 1536

RAIZ = Path(__file__).resolve().parents[1]


class FaltaConfiguracao(RuntimeError):
    pass


def carregar_env() -> None:
    """Lê o `.env` da raiz do repositório, sem depender de biblioteca.

    O que já está no ambiente vence o arquivo — é assim que a banca injeta as
    credenciais dela no `docker run`, sem que o seu `.env` atrapalhe.
    """
    env = RAIZ / ".env"
    if not env.exists():
        return
    for linha in env.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _ambiente() -> tuple[str, str]:
    carregar_env()
    endpoint = os.getenv("BOOTCAMP_LLM_ENDPOINT", "").strip().rstrip("/")
    chave = os.getenv("BOOTCAMP_API_KEY", "").strip()
    if not endpoint or not chave:
        raise FaltaConfiguracao(
            "preencha BOOTCAMP_LLM_ENDPOINT e BOOTCAMP_API_KEY no .env "
            "(copie de .env.example)")
    endpoint = re.sub(r"/chat/completions$", "", endpoint)
    endpoint = re.sub(r"/v1beta$", "/v1", endpoint)
    if not endpoint.endswith("/v1"):
        endpoint += "/v1"
    return endpoint, chave


def _modelo() -> str:
    carregar_env()
    return os.getenv("BOOTCAMP_LLM_MODEL", MODELO).strip() or MODELO


def _usar_kimi() -> bool:
    endpoint, _ = _ambiente()
    return "nvidia.com" in endpoint or _modelo().startswith("moonshotai/")


class _Resposta:
    def __init__(self, content: str):
        self.content = content


def _texto_mensagem(mensagem) -> tuple[str, str]:
    tipo = getattr(mensagem, "type", None) or "user"
    papeis = {"system": "system", "human": "user", "ai": "assistant"}
    papel = papeis.get(tipo, "user")
    conteudo = getattr(mensagem, "content", mensagem)
    if isinstance(conteudo, list):
        partes = []
        for item in conteudo:
            if isinstance(item, dict):
                partes.append(str(item.get("text") or item.get("content") or ""))
            else:
                partes.append(str(item))
        conteudo = "".join(partes)
    return papel, str(conteudo)


def _completar_chat(mensagens, temperature: float = 0, max_tokens: int = 2048) -> str:
    """Chama o Kimi K3 no formato compativel com a API da NVIDIA."""
    import httpx

    endpoint, chave = _ambiente()
    corpo = {
        "model": _modelo(),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [
            {"role": papel, "content": texto}
            for papel, texto in (_texto_mensagem(m) for m in mensagens)
        ],
    }
    with httpx.Client(timeout=120) as cliente:
        resposta = cliente.post(
            f"{endpoint}/chat/completions",
            headers={
                "Authorization": f"Bearer {chave}",
                "Content-Type": "application/json",
            },
            json=corpo,
        )
        resposta.raise_for_status()
        dados = resposta.json()
    mensagem = (dados.get("choices") or [{}])[0].get("message") or {}
    conteudo = mensagem.get("content") or mensagem.get("reasoning_content") or ""
    if isinstance(conteudo, list):
        conteudo = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in conteudo
        )
    return str(conteudo).strip()


class _ChatKimi:
    def __init__(self, temperature: float = 0):
        self.temperature = temperature

    def invoke(self, mensagens, **_extra):
        if isinstance(mensagens, str):
            mensagens = [type("M", (), {"type": "human", "content": mensagens})()]
        return _Resposta(_completar_chat(mensagens, self.temperature))


# ------------------------------------------------------- LangChain / LangGraph
def criar_llm(**extra):
    """Chat via Kimi K3 quando o endpoint e o da NVIDIA."""
    temperature = extra.pop("temperature", 0)
    if _usar_kimi():
        return _ChatKimi(temperature=temperature)
    from langchain_google_genai import ChatGoogleGenerativeAI

    endpoint, chave = _ambiente()
    base = re.sub(r"/v1$", "", endpoint)
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite", google_api_key=chave, base_url=base,
        temperature=temperature, **extra)


def criar_embeddings(**extra):
    """Embeddings do Gemini, 1536 dimensões."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    endpoint, chave = _ambiente()
    return GoogleGenerativeAIEmbeddings(
        model=f"models/{MODELO_EMBEDDING}", google_api_key=chave,
        base_url=endpoint, **extra)


# ------------------------------------------------------------------ LlamaIndex
def criar_llm_llamaindex(**extra):
    from llama_index.llms.google_genai import GoogleGenAI

    endpoint, chave = _ambiente()
    return GoogleGenAI(model=MODELO, api_key=chave,
                       http_options={"base_url": endpoint}, **extra)


def criar_embeddings_llamaindex(**extra):
    from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

    endpoint, chave = _ambiente()
    return GoogleGenAIEmbedding(model_name=MODELO_EMBEDDING, api_key=chave,
                                http_options={"base_url": endpoint}, **extra)


def saldo() -> dict:
    """Quanto ainda resta do seu crédito."""
    import httpx

    endpoint, chave = _ambiente()
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{endpoint}/saldo", headers={"Authorization": f"Bearer {chave}"})
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    # python -m app.llm  -> confere a configuração antes de você começar
    try:
        print("chat      :", criar_llm().invoke("Responda apenas: pronto.").content.strip())
        print("embeddings:", len(criar_embeddings().embed_query("teste")), "dimensões")
        print("saldo     :", saldo())
    except FaltaConfiguracao as erro:
        raise SystemExit(f"{erro}")
