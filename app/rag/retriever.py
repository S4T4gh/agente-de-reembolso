"""Recuperacao hibrida: BM25, busca densa, fusao RRF e rerank."""

from __future__ import annotations

import os
import pickle
from functools import lru_cache
from pathlib import Path

from llama_index.core import Settings, StorageContext, load_index_from_storage
from llama_index.core.schema import NodeWithScore, TextNode

from app.llm import carregar_env, criar_embeddings_llamaindex

RAIZ = Path(__file__).resolve().parents[2]
DIR_STORAGE = RAIZ / "storage"


def _bm25(nodes: list[TextNode]):
    from llama_index.retrievers.bm25 import BM25Retriever

    return BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=12)


@lru_cache(maxsize=1)
def _carregar():
    carregar_env()
    with (DIR_STORAGE / "bm25_nodes.pkl").open("rb") as f:
        nodes: list[TextNode] = pickle.load(f)
    bm25 = _bm25(nodes)
    endpoint = os.environ.get("BOOTCAMP_LLM_ENDPOINT", "")
    # O indice denso foi gerado com embeddings do Gemini. No Kimi/NVIDIA ele nao casa.
    if "nvidia.com" in endpoint:
        return None, bm25
    Settings.embed_model = criar_embeddings_llamaindex()
    storage = StorageContext.from_defaults(persist_dir=str(DIR_STORAGE / "vector"))
    index = load_index_from_storage(storage)
    return index.as_retriever(similarity_top_k=12), bm25


def _rrf(listas: list[list[NodeWithScore]], k: int = 60) -> list[NodeWithScore]:
    scores: dict[str, float] = {}
    objs: dict[str, NodeWithScore] = {}
    for lista in listas:
        for rank, item in enumerate(lista):
            nid = item.node.node_id
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (k + rank + 1)
            objs[nid] = item
    ordenados = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    out: list[NodeWithScore] = []
    for nid, sc in ordenados:
        nws = objs[nid]
        nws.score = sc
        out.append(nws)
    return out


def _rerank(query: str, candidatos: list[NodeWithScore], top_n: int) -> list[NodeWithScore]:
    q = query.lower()
    tokens = {t for t in q.replace("?", " ").split() if len(t) > 2}

    def score(nws: NodeWithScore) -> float:
        texto = (nws.node.get_content() or "").lower()
        overlap = sum(1 for t in tokens if t in texto)
        return float(nws.score or 0.0) + 0.08 * overlap

    return sorted(candidatos, key=score, reverse=True)[:top_n]


def buscar(query: str, top_k: int = 6) -> list[dict]:
    dense, bm25 = _carregar()
    listas = [bm25.retrieve(query)]
    if dense is not None:
        try:
            listas.insert(0, dense.retrieve(query))
        except Exception:
            pass
    fundidos = _rrf(listas) if len(listas) > 1 else listas[0]
    finais = _rerank(query, fundidos, top_n=top_k)
    return [
        {
            "texto": n.node.get_content(),
            "fonte": (n.node.metadata or {}).get("fonte", ""),
            "score": float(n.score or 0.0),
        }
        for n in finais
    ]


def buscar_normas(query: str, top_k: int = 6, max_chars: int = 5000) -> str:
    trechos = buscar(query, top_k=top_k)
    partes: list[str] = []
    total = 0
    for t in trechos:
        bloco = f"[{t['fonte']}]\n{t['texto']}"
        if total + len(bloco) > max_chars:
            break
        partes.append(bloco)
        total += len(bloco)
    return "\n\n---\n\n".join(partes)


contexto = buscar_normas
