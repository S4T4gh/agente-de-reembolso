"""Recuperacao hibrida: BM25, busca densa, fusao RRF e rerank."""

from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

from llama_index.core import Settings, StorageContext, load_index_from_storage
from llama_index.core.schema import NodeWithScore, TextNode

from app.llm import carregar_env, criar_embeddings_llamaindex

RAIZ = Path(__file__).resolve().parents[2]
DIR_STORAGE = RAIZ / "storage"


@lru_cache(maxsize=1)
def _carregar():
    carregar_env()
    Settings.embed_model = criar_embeddings_llamaindex()
    storage = StorageContext.from_defaults(persist_dir=str(DIR_STORAGE / "vector"))
    index = load_index_from_storage(storage)
    with (DIR_STORAGE / "bm25_nodes.pkl").open("rb") as f:
        nodes: list[TextNode] = pickle.load(f)
    from llama_index.retrievers.bm25 import BM25Retriever

    bm25 = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=12)
    dense = index.as_retriever(similarity_top_k=12)
    return dense, bm25


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
    fundidos = _rrf([dense.retrieve(query), bm25.retrieve(query)])
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
