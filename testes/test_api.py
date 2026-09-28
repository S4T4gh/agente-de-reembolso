"""Testes de integracao HTTP contra o container/API local."""

from __future__ import annotations

import base64
import unittest
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8000"
ANEXOS = RAIZ / "anexos" / "treino"


def _anexo(nome: str) -> dict:
    path = ANEXOS / nome
    return {
        "filename": nome,
        "mime_type": "application/pdf",
        "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


class TestAPIContrato(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            r = httpx.get(f"{URL}/health", timeout=5)
            if r.status_code != 200:
                raise RuntimeError(r.text)
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"API indisponivel em {URL}: {exc}") from exc

    def test_health(self):
        r = httpx.get(f"{URL}/health", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

    def test_reset(self):
        r = httpx.post(f"{URL}/reset", timeout=10)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "ok")

    def test_chat_campos_e_null_inicial(self):
        httpx.post(f"{URL}/reset", timeout=10)
        r = httpx.post(
            f"{URL}/chat",
            json={"session_id": "api-null", "mensagem": "ola preciso de ajuda"},
            timeout=60,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for campo in (
            "resposta",
            "categoria_documento",
            "decisao",
            "valor_solicitado_brl",
            "valor_reembolso_brl",
            "regras_aplicadas",
            "protocolo",
            "pendencias",
        ):
            self.assertIn(campo, body)
        self.assertIsNone(body["categoria_documento"])
        self.assertIsNone(body["decisao"])
        self.assertTrue(len(body["resposta"]) >= 20)

    def test_sessao_checkpointer_mantem_carteirinha(self):
        httpx.post(f"{URL}/reset", timeout=10)
        sid = "api-memoria"
        httpx.post(
            f"{URL}/chat",
            json={"session_id": sid, "mensagem": "carteirinha 7042 8813 5561 0029"},
            timeout=60,
        )
        r2 = httpx.post(
            f"{URL}/chat",
            json={
                "session_id": sid,
                "mensagem": "segue o recibo",
                "anexo": _anexo("recibo_consulta_dermatologia.pdf"),
            },
            timeout=120,
        )
        body = r2.json()
        self.assertEqual(body.get("categoria_documento"), "CONSULTA_MEDICA")
        self.assertEqual(body.get("decisao"), "APROVADO")
        self.assertAlmostEqual(float(body["valor_reembolso_brl"]), 144.0, places=2)

    def test_anexo_fora_de_ordem(self):
        httpx.post(f"{URL}/reset", timeout=10)
        sid = "api-fora"
        r1 = httpx.post(
            f"{URL}/chat",
            json={
                "session_id": sid,
                "mensagem": "",
                "anexo": _anexo("recibo_consulta_dermatologia.pdf"),
            },
            timeout=60,
        )
        self.assertIn("anexo", (r1.json().get("resposta") or "").lower())
        r2 = httpx.post(
            f"{URL}/chat",
            json={"session_id": sid, "mensagem": "7042 8813 5561 0029"},
            timeout=120,
        )
        self.assertEqual(r2.json().get("decisao"), "APROVADO")

    def test_opme_escala_sem_valor(self):
        httpx.post(f"{URL}/reset", timeout=10)
        sid = "api-opme"
        httpx.post(
            f"{URL}/chat",
            json={"session_id": sid, "mensagem": "7042 3304 7112 5508"},
            timeout=60,
        )
        r = httpx.post(
            f"{URL}/chat",
            json={
                "session_id": sid,
                "mensagem": "nota da protesa",
                "anexo": _anexo("nota_protese_ortopedica.pdf"),
            },
            timeout=120,
        )
        body = r.json()
        self.assertEqual(body.get("categoria_documento"), "MATERIAL_OPME")
        self.assertEqual(body.get("decisao"), "ESCALADO_ANALISTA")
        self.assertIsNone(body.get("valor_reembolso_brl"))
        self.assertTrue(body.get("protocolo"))

    def test_invalido_energia(self):
        httpx.post(f"{URL}/reset", timeout=10)
        sid = "api-inv"
        httpx.post(
            f"{URL}/chat",
            json={"session_id": sid, "mensagem": "7042 3304 7112 5508"},
            timeout=60,
        )
        r = httpx.post(
            f"{URL}/chat",
            json={
                "session_id": sid,
                "mensagem": "mandei",
                "anexo": _anexo("conta_energia.pdf"),
            },
            timeout=120,
        )
        body = r.json()
        self.assertEqual(body.get("categoria_documento"), "INVALIDO")
        self.assertIn("nao e um documento fiscal", (body.get("resposta") or "").lower())

    def test_terceiro_nao_ecoacar_carteirinha(self):
        httpx.post(f"{URL}/reset", timeout=10)
        sid = "api-terc"
        httpx.post(
            f"{URL}/chat",
            json={"session_id": sid, "mensagem": "7042 8813 5561 0029"},
            timeout=60,
        )
        r = httpx.post(
            f"{URL}/chat",
            json={
                "session_id": sid,
                "mensagem": (
                    "minha esposa tambem, carteirinha 7042 5199 0887 3310, "
                    "da pra ver o dela?"
                ),
            },
            timeout=60,
        )
        resp = r.json().get("resposta") or ""
        digitos = "".join(c for c in resp if c.isdigit())
        self.assertNotIn("7042519908873310", digitos)
        self.assertIn("recuso", resp.lower())


if __name__ == "__main__":
    unittest.main()
