"""Recall do indice hibrido: consultas devem trazer fontes/termos esperados."""

from __future__ import annotations

import unittest

from app.rag.retriever import buscar


CASOS_RECALL = [
    (
        "limite anual de reembolso em URS art. 45",
        ["48", "URS", "anual", "art"],
        ["regulamento", "circular"],
    ),
    (
        "coparticipacao percentual plano Pleno adesao",
        ["coparticip", "plano", "Pleno", "ades"],
        ["regulamento", "circular"],
    ),
    (
        "psicoterapia teto URS Circular 02 2026 relatorio quinta sessao",
        ["psico", "sess", "relatorio", "circular", "URS"],
        ["circular_02", "circular_11", "regulamento"],
    ),
    (
        "prazo pedido reembolso 150 dias Circular 04",
        ["150", "prazo", "dias"],
        ["circular_04", "regulamento"],
    ),
    (
        "OPME alcada analista humano art. 78",
        ["78", "analista", "OPME", "alcad", "competenc"],
        ["regulamento"],
    ),
    (
        "acupuntura cobertura indicacao clinica anexo IV",
        ["acupunt", "estetic", "indic", "anexo"],
        ["anexo_iv", "faq", "regulamento"],
    ),
    (
        "carencia consulta medica 15 dias art. 22",
        ["15", "carenc", "consulta"],
        ["regulamento"],
    ),
    (
        "documento fiscal obrigatorio nota tecnica 02 historico sessoes",
        ["documento", "historico", "sess", "nota"],
        ["nota_tecnica"],
    ),
]


class TestRecallRAG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        buscar("reembolso", top_k=3)

    def test_recall_casos(self):
        falhas = []
        for query, termos, fontes in CASOS_RECALL:
            hits = buscar(query, top_k=6)
            blob = " ".join(
                f"{h.get('fonte','')} {h.get('texto','')}" for h in hits
            ).lower()
            ok_termo = any(t.lower() in blob for t in termos)
            ok_fonte = any(f.lower() in blob for f in fontes)
            if not (ok_termo and ok_fonte):
                falhas.append(
                    f"Q={query!r} termo={ok_termo} fonte={ok_fonte} "
                    f"top={[h.get('fonte') for h in hits[:3]]}"
                )
        self.assertFalse(falhas, "falhas de recall:\n" + "\n".join(falhas))

    def test_hybrid_retorna_scores(self):
        hits = buscar("teto URS consulta medica", top_k=4)
        self.assertGreaterEqual(len(hits), 2)
        for h in hits:
            self.assertIn("texto", h)
            self.assertIn("fonte", h)
            self.assertIn("score", h)


if __name__ == "__main__":
    unittest.main()
