"""Testes deterministicos de calculo e classificacao."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.agents.documento.extrator import _classificar
from app.calculo.reembolso import calcular_reembolso
from app.schemas import Categoria, Decisao


class TestClassificacao(unittest.TestCase):
    def test_consulta(self):
        self.assertEqual(
            _classificar("Recibo de consulta medica dermatologica CRM 123"),
            Categoria.CONSULTA_MEDICA,
        )

    def test_terapia(self):
        self.assertEqual(
            _classificar("Recibo de sessao de psicoterapia TUSS 50000462"),
            Categoria.SESSAO_TERAPIA,
        )

    def test_exame(self):
        self.assertEqual(
            _classificar("Recibo de exame laboratorial hemograma"),
            Categoria.EXAME_DIAGNOSTICO,
        )

    def test_relatorio(self):
        self.assertEqual(
            _classificar("Relatorio clinico circunstanciado do profissional"),
            Categoria.RELATORIO_CLINICO,
        )

    def test_opme(self):
        self.assertEqual(
            _classificar("Nota fiscal de protese ortopedica OPME artroplastia"),
            Categoria.MATERIAL_OPME,
        )

    def test_nao_coberta(self):
        self.assertEqual(
            _classificar("Recibo de procedimento estetico harmonizacao facial"),
            Categoria.DESPESA_NAO_COBERTA,
        )

    def test_invalido_energia(self):
        self.assertEqual(
            _classificar("Conta de energia eletrica kwh bandeira tarifaria"),
            Categoria.INVALIDO,
        )


class TestCalculoDecisoes(unittest.TestCase):
    def test_aprovado_consulta(self):
        r = calcular_reembolso(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_pago=Decimal("240"),
            codigo_tuss="10101012",
            data_atendimento="15/05/2026",
            plano="Pleno",
            data_adesao="2025-09-01",
            historico=[],
        )
        self.assertEqual(r.decisao, Decisao.APROVADO)
        self.assertEqual(r.valor_reembolso, Decimal("144.00"))
        self.assertIn("ART-35", r.regras)
        self.assertIn("ART-44", r.regras)

    def test_parcial_psico_circ02(self):
        hist = [{"valor_reembolsado_brl": "4453.53"}]
        r = calcular_reembolso(
            categoria=Categoria.SESSAO_TERAPIA,
            valor_pago=Decimal("320"),
            codigo_tuss="50000462",
            data_atendimento="25/04/2026",
            plano="Essencial",
            data_adesao="2020-01-01",
            historico=hist,
            sessao_ano=5,
            tem_relatorio=True,
        )
        self.assertEqual(r.decisao, Decisao.APROVADO_PARCIAL)
        self.assertEqual(r.valor_reembolso, Decimal("111.27"))
        self.assertIn("CIRC-02-2026", r.regras)

    def test_pendente_relatorio(self):
        r = calcular_reembolso(
            categoria=Categoria.SESSAO_TERAPIA,
            valor_pago=Decimal("320"),
            codigo_tuss="50000462",
            data_atendimento="25/04/2026",
            plano="Essencial",
            data_adesao="2020-01-01",
            historico=[{}] * 4,
            sessao_ano=5,
            tem_relatorio=False,
        )
        self.assertEqual(r.decisao, Decisao.PENDENTE_DOCUMENTO)
        self.assertTrue(r.pendencias)

    def test_escalado_opme(self):
        r = calcular_reembolso(
            categoria=Categoria.MATERIAL_OPME,
            valor_pago=Decimal("9200"),
            codigo_tuss=None,
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2019-01-01",
        )
        self.assertEqual(r.decisao, Decisao.ESCALADO_ANALISTA)
        self.assertIsNone(r.valor_reembolso)
        self.assertTrue(r.escalonar)
        self.assertEqual(r.regras, ["ART-78"])

    def test_escalado_alcada(self):
        r = calcular_reembolso(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_pago=Decimal("6000"),
            codigo_tuss="10101012",
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2019-01-01",
        )
        self.assertEqual(r.decisao, Decisao.ESCALADO_ANALISTA)
        self.assertIsNone(r.valor_reembolso)

    def test_negado_carencia_consulta(self):
        r = calcular_reembolso(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_pago=Decimal("200"),
            codigo_tuss="10101012",
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2026-05-01",
        )
        self.assertEqual(r.decisao, Decisao.NEGADO)
        self.assertIn("ART-22", r.regras)

    def test_negado_carencia_exame(self):
        r = calcular_reembolso(
            categoria=Categoria.EXAME_DIAGNOSTICO,
            valor_pago=Decimal("300"),
            codigo_tuss="40304361",
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2026-04-01",
        )
        self.assertEqual(r.decisao, Decisao.NEGADO)
        self.assertIn("ART-23", r.regras)

    def test_negado_despesa_nao_coberta(self):
        r = calcular_reembolso(
            categoria=Categoria.DESPESA_NAO_COBERTA,
            valor_pago=Decimal("1500"),
            codigo_tuss=None,
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2019-01-01",
        )
        self.assertEqual(r.decisao, Decisao.NEGADO)
        self.assertIsNone(r.valor_reembolso)

    def test_negado_status_inativo(self):
        r = calcular_reembolso(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_pago=Decimal("200"),
            codigo_tuss="10101012",
            data_atendimento="10/05/2026",
            plano="Pleno",
            data_adesao="2019-01-01",
            status="SUSPENSO",
        )
        self.assertEqual(r.decisao, Decisao.NEGADO)
        self.assertIn("ART-30", r.regras)

    def test_negado_prazo(self):
        r = calcular_reembolso(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_pago=Decimal("200"),
            codigo_tuss="10101012",
            data_atendimento="01/01/2025",
            plano="Pleno",
            data_adesao="2019-01-01",
            data_protocolo="01/08/2025",
        )
        self.assertEqual(r.decisao, Decisao.NEGADO)
        self.assertIn("ART-12", r.regras)


if __name__ == "__main__":
    unittest.main()
