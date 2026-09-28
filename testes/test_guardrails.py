"""Guardrails de privacidade e escopo."""

from __future__ import annotations

import unittest

from app.guardrails.privacidade import (
    extrair_carteirinhas,
    mascarar_cpf,
    sanitizar_resposta,
)


class TestGuardrails(unittest.TestCase):
    def test_mascara_cpf(self):
        out = mascarar_cpf("CPF 123.456.789-09 do paciente")
        self.assertNotIn("123.456.789-09", out)
        self.assertIn("***", out)

    def test_omite_cid(self):
        out = sanitizar_resposta("O CID F41.1 consta no pedido")
        self.assertNotIn("F41.1", out)
        self.assertIn("CID omitido", out)

    def test_omite_diagnostico_explicito(self):
        out = sanitizar_resposta("Diagnostico: transtorno de ansiedade generalizada")
        self.assertNotIn("ansiedade", out.lower())
        self.assertIn("omitida", out.lower())

    def test_omite_carteirinha_terceiro(self):
        out = sanitizar_resposta(
            "Vou consultar 7042 5199 0887 3310 da esposa",
            carteirinhas_proibidas=["7042519908873310"],
        )
        self.assertNotIn("7042519908873310", "".join(c for c in out if c.isdigit()))
        self.assertIn("omitida", out.lower())

    def test_extrai_carteirinha(self):
        carts = extrair_carteirinhas("minha carteirinha e 7042 8813 5561 0029")
        self.assertEqual(carts, ["7042881355610029"])


class TestForaDeEscopo(unittest.TestCase):
    def test_terceiro_sem_desfecho_marca_fora(self):
        from app.agents.triagem.node import triagem_node

        state = {
            "mensagem": (
                "minha esposa tambem precisa, carteirinha 7042 5199 0887 3310"
            ),
            "carteirinha_sessao": "7042881355610029",
            "decisao": None,
            "regras_aplicadas": [],
        }
        out = triagem_node(state)
        self.assertEqual(out.get("decisao"), "FORA_DE_ESCOPO")
        self.assertIn("ART-8", out.get("regras_aplicadas") or [])
        self.assertIn("recuso", (out.get("resposta") or "").lower())

    def test_terceiro_com_aprovado_preserva_desfecho(self):
        from app.agents.triagem.node import triagem_node

        state = {
            "mensagem": "esposa 7042 5199 0887 3310",
            "carteirinha_sessao": "7042881355610029",
            "decisao": "APROVADO",
            "categoria_documento": "CONSULTA_MEDICA",
            "valor_reembolso_brl": 144.0,
            "regras_aplicadas": ["ART-35"],
        }
        out = triagem_node(state)
        self.assertEqual(out.get("decisao"), "APROVADO")
        self.assertIn("recuso", (out.get("resposta") or "").lower())


if __name__ == "__main__":
    unittest.main()
