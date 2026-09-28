"""Guardrails de privacidade, escopo e sanitizacao de saida."""

from __future__ import annotations

import re

CPF_COMPLETO = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")
CARTEIRINHA = re.compile(r"\b(\d{4}\s?\d{4}\s?\d{4}\s?\d{4})\b")
CID = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d)?\b")


def normalizar_carteirinha(texto: str) -> str | None:
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 16:
        return digitos
    return None


def extrair_carteirinhas(texto: str) -> list[str]:
    encontradas: list[str] = []
    for m in CARTEIRINHA.finditer(texto):
        cart = normalizar_carteirinha(m.group(1))
        if cart and cart not in encontradas:
            encontradas.append(cart)
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 16 and digitos not in encontradas:
        encontradas.append(digitos)
    return encontradas


def mascarar_cpf(texto: str) -> str:
    def _sub(m: re.Match) -> str:
        return f"***.{m.group(2)}.{m.group(3)}-**"

    return CPF_COMPLETO.sub(_sub, texto)


def sanitizar_resposta(texto: str, carteirinhas_proibidas: list[str] | None = None) -> str:
    texto = mascarar_cpf(texto)
    texto = CID.sub("[CID omitido]", texto)
    # Remove hipotese diagnostica atribuida ao beneficiario.
    padroes_dx = [
        re.compile(r"(?i)\b(diagnostico|hipotese diagnostica|cid-?10)\s*[:\-]\s*[^\n.]+"),
        re.compile(r"(?i)\bo paciente (apresenta|tem|possui)\s+[^\n.]{5,80}"),
    ]
    for p in padroes_dx:
        texto = p.sub("[informacao clinica omitida]", texto)
    for cart in carteirinhas_proibidas or []:
        dig = re.sub(r"\D", "", cart or "")
        if len(dig) == 16 and dig in re.sub(r"\D", "", texto):
            padrao = re.compile(r"\d{4}\s?\d{4}\s?\d{4}\s?\d{4}")
            texto = padrao.sub("[carteirinha omitida]", texto)
            break
    return texto
