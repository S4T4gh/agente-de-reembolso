"""Guardrails de saída — art. 7º e art. 8º."""

from __future__ import annotations

import re

CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CID = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d)?\b")


def mascarar_cpf(texto: str) -> str:
    def _sub(m: re.Match) -> str:
        dig = re.sub(r"\D", "", m.group(0))
        if len(dig) != 11:
            return "***"
        return f"***.{dig[3:6]}.{dig[6:9]}-**"

    return CPF.sub(_sub, texto)


def sanitizar(
    texto: str,
    *,
    carteirinhas_terceiro: list[str] | None = None,
) -> str:
    out = mascarar_cpf(texto or "")
    # Remove códigos CID acidentais
    out = CID.sub("[código clínico omitido]", out)
    for cart in carteirinhas_terceiro or []:
        dig = re.sub(r"\D", "", cart)
        if dig and dig in re.sub(r"\D", "", out):
            # remove a sequência da carteirinha de terceiro
            out = re.sub(r"\d[\d\s]{10,20}\d", "[carteirinha omitida]", out, count=1)
    return out.strip()


def extrair_carteirinha(mensagem: str) -> str | None:
    digitos = re.sub(r"\D", "", mensagem or "")
    # Carteirinha ANS tipicamente 16 dígitos
    m = re.search(r"(7042\d{12})", digitos)
    if m:
        return m.group(1)
    m = re.search(r"(\d{16})", digitos)
    if m:
        return m.group(1)
    return None


def menciona_terceiro(mensagem: str) -> bool:
    t = (mensagem or "").lower()
    return any(
        k in t
        for k in (
            "esposa",
            "esposo",
            "marido",
            "filho",
            "filha",
            "dependente",
            "cônjuge",
            "conjuge",
            "mãe",
            "mae",
            "pai",
            "titular",
            "dela",
            "dele também",
            "junto",
        )
    ) and bool(re.search(r"\d{4}", mensagem or ""))
