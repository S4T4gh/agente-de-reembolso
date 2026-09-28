"""Casos ficticios exibidos na pagina inicial."""

from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CASOS = RAIZ / "casos_treino"
ANEXOS = RAIZ / "anexos" / "treino"

ROTULOS = {
    "APROVADO": "Aprovado",
    "APROVADO_PARCIAL": "Aprovado parcial",
    "PENDENTE_DOCUMENTO": "Pendente de documento",
    "NEGADO": "Negado",
    "FORA_DE_ESCOPO": "Fora de escopo",
    "ESCALADO_ANALISTA": "Escalado para analista",
}

RESUMOS = {
    "01_fora_de_ordem": (
        "O recibo da dermatologista chega antes da carteirinha. "
        "No meio, ela pergunta do plano da esposa."
    ),
    "02_sessao_pelo_historico": (
        "A sess\u00e3o de terapia s\u00f3 consta no hist\u00f3rico. "
        "O relat\u00f3rio chega depois e o teto corta o valor."
    ),
    "03_invalido_e_alcada": (
        "O primeiro arquivo \u00e9 uma conta de luz. "
        "O segundo \u00e9 uma pr\u00f3tese acima da al\u00e7ada autom\u00e1tica."
    ),
}


def _moeda(valor) -> str | None:
    if valor is None:
        return None
    texto = f"{float(valor):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def listar() -> list[dict]:
    """Roteiros publicos, sem CPF."""
    if not CASOS.is_dir():
        return []
    saida = []
    for pasta in sorted(p for p in CASOS.iterdir() if (p / "esperado.json").is_file()):
        esperado = json.loads((pasta / "esperado.json").read_text(encoding="utf-8"))
        persona = json.loads((pasta / "persona.json").read_text(encoding="utf-8"))
        turnos = []
        for linha in (pasta / "turnos.jsonl").read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            item = json.loads(linha)
            anexo = item.get("anexo") or {}
            turnos.append({
                "mensagem": item.get("mensagem") or "",
                "anexo": anexo.get("filename") or None,
            })
        reembolso = _moeda(esperado.get("valor_reembolso_brl"))
        pedido = _moeda(esperado.get("valor_solicitado_brl"))
        valor = "sem valor autom\u00e1tico" if reembolso is None else f"{reembolso} de {pedido}"
        saida.append({
            "id": pasta.name,
            "nome": persona.get("nome") or pasta.name,
            "resumo": RESUMOS.get(pasta.name) or esperado.get("_titulo") or pasta.name,
            "desfecho": ROTULOS.get(esperado.get("decisao"), str(esperado.get("decisao") or "")),
            "valor": valor,
            "turnos": turnos,
        })
    return saida


def anexo(nome: str) -> Path | None:
    """So os PDFs citados nos roteiros."""
    if not nome or any(parte in nome for parte in ("/", "\\", "..")):
        return None
    permitidos = {t["anexo"] for caso in listar() for t in caso["turnos"] if t["anexo"]}
    if nome not in permitidos:
        return None
    caminho = (ANEXOS / nome).resolve()
    if not caminho.is_file() or ANEXOS.resolve() not in caminho.parents:
        return None
    return caminho
