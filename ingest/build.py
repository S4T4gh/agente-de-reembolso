"""Constroi o indice a partir de kb/ e grava em storage/.

    python -m ingest.build
"""

from __future__ import annotations

import json
import pickle
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIR_KB = RAIZ / "kb"
DIR_STORAGE = RAIZ / "storage"


def _texto_arquivo(caminho: Path) -> str:
    if caminho.suffix.lower() == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(str(caminho)).paragraphs)
    import fitz

    return "".join(page.get_text() for page in fitz.open(str(caminho)))


def _chunkar(texto: str, fonte: str, max_chars: int = 1400) -> list[dict]:
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()

    # Tabela URS: so preambulo + amostra de codigos (lookup vai via JSON)
    if fonte.startswith("tabela_urs"):
        cabeca = texto[:3500]
        amostra = []
        for m in re.finditer(r"(\d{8})\n([^\n]+)\n([\d,.—\-]+)", texto):
            amostra.append(f"TUSS {m.group(1)} | {m.group(2).strip()} | teto URS {m.group(3)}")
            if len(amostra) >= 80:
                break
        texto = cabeca + "\n\nAmostra de procedimentos:\n" + "\n".join(amostra)

    blocos: list[str] = []
    partes = re.split(r"(?=(?:Art\.|ARTIGO)\s+\d+)", texto, flags=re.IGNORECASE)
    if len(partes) <= 2:
        partes = re.split(r"(?=\n\d+\.\d*\s)", "\n" + texto)

    for parte in partes:
        parte = parte.strip()
        if len(parte) < 50:
            continue
        if len(parte) <= max_chars:
            blocos.append(parte)
        else:
            step = max_chars - 250
            for i in range(0, len(parte), step):
                pedaco = parte[i : i + max_chars].strip()
                if len(pedaco) >= 50:
                    blocos.append(pedaco)

    return [
        {
            "id": f"{fonte}::{i}",
            "texto": bloco,
            "metadata": {"fonte": fonte, "chunk_id": i},
        }
        for i, bloco in enumerate(blocos)
    ]


def _extrair_tabela_urs(texto: str) -> dict:
    urs = 95.10
    m = re.search(r"1\s*URS\s*=\s*R\$\s*([\d.]+,\d{2})", texto)
    if m:
        urs = float(m.group(1).replace(".", "").replace(",", "."))

    procedimentos: dict[str, dict] = {}
    padrao = re.compile(
        r"(?P<code>\d{8})\s*\n(?P<desc>[^\n]+(?:\n(?!\d{8})[^\n]+){0,3}?)\n"
        r"(?P<teto>[\d]+(?:[.,]\d+)?|—|-)\s*\n",
        re.MULTILINE,
    )
    for m in padrao.finditer(texto):
        code = m.group("code")
        desc = re.sub(r"\s+", " ", m.group("desc")).strip()
        teto_raw = m.group("teto").replace(",", ".")
        teto_urs = None
        if teto_raw not in {"—", "-", "–"}:
            try:
                teto_urs = float(teto_raw)
            except ValueError:
                teto_urs = None
        procedimentos[code] = {"descricao": desc[:200], "teto_urs": teto_urs}
    return {"urs_brl": urs, "exercicio": 2026, "procedimentos": procedimentos}


def main() -> int:
    from llama_index.core import Settings, VectorStoreIndex
    from llama_index.core.schema import TextNode

    from app.llm import carregar_env, criar_embeddings_llamaindex

    carregar_env()
    DIR_STORAGE.mkdir(parents=True, exist_ok=True)

    print("Lendo kb/...")
    todos: list[dict] = []
    tabela_urs = None
    for arquivo in sorted(DIR_KB.iterdir()):
        if arquivo.suffix.lower() not in {".pdf", ".docx"}:
            continue
        print(f"  {arquivo.name}")
        texto = _texto_arquivo(arquivo)
        if arquivo.name.startswith("tabela_urs"):
            tabela_urs = _extrair_tabela_urs(texto)
        todos.extend(_chunkar(texto, fonte=arquivo.name))

    print(f"{len(todos)} chunks")
    Settings.embed_model = criar_embeddings_llamaindex()
    nodes = [
        TextNode(text=c["texto"], id_=c["id"], metadata=c["metadata"]) for c in todos
    ]

    print("Gerando embeddings...")
    index = VectorStoreIndex(nodes, show_progress=True)
    persist_dir = DIR_STORAGE / "vector"
    persist_dir.mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(persist_dir))

    with (DIR_STORAGE / "bm25_nodes.pkl").open("wb") as f:
        pickle.dump(nodes, f)

    (DIR_STORAGE / "meta.json").write_text(
        json.dumps(
            {
                "n_chunks": len(nodes),
                "fontes": sorted({c["metadata"]["fonte"] for c in todos}),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if tabela_urs is None:
        tabela_urs = {"urs_brl": 95.10, "exercicio": 2026, "procedimentos": {}}
    (DIR_STORAGE / "tabela_urs.json").write_text(
        json.dumps(tabela_urs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"URS={tabela_urs['urs_brl']} | {len(tabela_urs['procedimentos'])} codigos | "
        f"gravado em {DIR_STORAGE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
