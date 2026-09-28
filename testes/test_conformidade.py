"""Conformidade estrutural com o enunciado (sem LLM)."""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


class TestArquitetura(unittest.TestCase):
    def test_supervisor_tem_quatro_nos(self):
        from app.agents.supervisor import graph as gmod

        src = Path(gmod.__file__).read_text(encoding="utf-8", errors="replace")
        for nome in ("supervisor", "triagem", "documento", "normas"):
            self.assertIn(f'"{nome}"', src)
        self.assertIn("StateGraph", src)
        self.assertIn("MemorySaver", src)
        self.assertIn("handoff", src)

    def test_nao_e_monolito_if_elif_no_main(self):
        main = (RAIZ / "app" / "main.py").read_text(encoding="utf-8", errors="replace")
        self.assertIn("processar_turno", main)
        self.assertNotIn("if categoria ==", main)

    def test_checkpointer_por_session_id(self):
        src = (RAIZ / "app" / "agents" / "supervisor" / "graph.py").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn("thread_id", src)
        self.assertIn("session_id", src)

    def test_rag_hibrido_bm25_denso_rrf_rerank(self):
        from app.rag import retriever

        src = Path(retriever.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertIn("BM25Retriever", src)
        self.assertIn("_rrf", src)
        self.assertIn("_rerank", src)
        self.assertIn("vector", src)

    def test_indice_em_storage(self):
        self.assertTrue((RAIZ / "storage" / "bm25_nodes.pkl").exists())
        self.assertTrue((RAIZ / "storage" / "vector" / "docstore.json").exists())
        self.assertTrue((RAIZ / "storage" / "tabela_urs.json").exists())
        self.assertTrue((RAIZ / "storage" / "meta.json").exists())

    def test_dockerfile_so_copia_storage(self):
        df = (RAIZ / "Dockerfile").read_text(encoding="utf-8", errors="replace")
        self.assertIn("COPY storage/", df)
        self.assertIn("COPY app/", df)
        self.assertIn("8000", df)
        # Nao executa ingest no build/start ù so menciona em comentario
        self.assertNotIn("RUN python -m ingest", df)
        self.assertNotIn("CMD.*ingest", df)

    def test_mcp_url_so_por_env(self):
        from app.tools import mcp_client

        src = Path(mcp_client.__file__).read_text(encoding="utf-8", errors="replace")
        self.assertIn("MCP_OPERADORA_URL", src)
        self.assertIn("consultar_beneficiario", src)
        self.assertIn("consultar_historico", src)
        self.assertIn("abrir_protocolo", src)
        self.assertNotIn("http://localhost:9000", src)
        self.assertNotIn("https://", src)

    def test_env_vars_permitidas(self):
        permitidas = {
            "BOOTCAMP_LLM_ENDPOINT",
            "BOOTCAMP_API_KEY",
            "MCP_OPERADORA_URL",
            "MCP_OPERADORA_TOKEN",
        }
        usadas = set()
        for p in (RAIZ / "app").rglob("*.py"):
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr == "getenv":
                        if node.args and isinstance(node.args[0], ast.Constant):
                            usadas.add(node.args[0].value)
                if isinstance(node, ast.Subscript):
                    if (
                        isinstance(node.value, ast.Attribute)
                        and node.value.attr == "environ"
                        and isinstance(node.slice, ast.Constant)
                    ):
                        usadas.add(node.slice.value)
        relevantes = {u for u in usadas if u.startswith(("BOOTCAMP_", "MCP_"))}
        extras = relevantes - permitidas
        self.assertFalse(extras, f"env vars extras: {extras}")


class TestContratoPydantic(unittest.TestCase):
    def test_categorias_completas(self):
        from app.schemas import Categoria

        esperadas = {
            "CONSULTA_MEDICA",
            "SESSAO_TERAPIA",
            "EXAME_DIAGNOSTICO",
            "RELATORIO_CLINICO",
            "MATERIAL_OPME",
            "DESPESA_NAO_COBERTA",
            "INVALIDO",
        }
        self.assertEqual({c.value for c in Categoria}, esperadas)

    def test_decisoes_completas(self):
        from app.schemas import Decisao

        esperadas = {
            "APROVADO",
            "APROVADO_PARCIAL",
            "PENDENTE_DOCUMENTO",
            "NEGADO",
            "FORA_DE_ESCOPO",
            "ESCALADO_ANALISTA",
        }
        self.assertEqual({d.value for d in Decisao}, esperadas)

    def test_chat_response_campos(self):
        from app.schemas import ChatResponse

        campos = set(ChatResponse.model_fields)
        self.assertEqual(
            campos,
            {
                "resposta",
                "categoria_documento",
                "decisao",
                "valor_solicitado_brl",
                "valor_reembolso_brl",
                "regras_aplicadas",
                "protocolo",
                "pendencias",
            },
        )

    def test_rotas_main(self):
        from app.main import app

        rotas = {r.path for r in app.routes if hasattr(r, "path")}
        self.assertIn("/health", rotas)
        self.assertIn("/chat", rotas)
        self.assertIn("/reset", rotas)


if __name__ == "__main__":
    unittest.main()
