"""Guardrails de privacidade, escopo e sanitizacao de saida."""

from __future__ import annotations

import re

CPF_COMPLETO = re.compile(r"\b(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})\b")
CARTEIRINHA = re.compile(r"\b(\d{4}\s?\d{4}\s?\d{4}\s?\d{4})\b")
CID = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d)?\b")
# Datas e valores nao entram na leitura da carteirinha.
_DATA = re.compile(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b")
_DINHEIRO = re.compile(r"(?i)(?:r\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}\b")
# Bloco numerico: digitos ligados por espaco, ponto, hifen, barra ou parenteses.
_BLOCO = re.compile(r"\d(?:[\s.\-/,()]*\d)*")


def normalizar_carteirinha(texto: str) -> str | None:
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 16:
        return digitos
    return None


def _sem_ruido_numerico(texto: str) -> str:
    """Tira data e valor em reais para nao colar esses digitos na carteirinha."""
    limpo = _DATA.sub(" ", texto or "")
    return _DINHEIRO.sub(" ", limpo)


def _adicionar(encontradas: list[str], cart: str | None) -> None:
    if cart and cart not in encontradas:
        encontradas.append(cart)


def _do_bloco(bloco: str, encontradas: list[str]) -> None:
    """Aceita 16 digitos no bloco, ou quatro grupos de 4, sem fatiar um numero maior."""
    grupos = re.findall(r"\d+", bloco)
    if not grupos:
        return
    juntos = "".join(grupos)
    if len(juntos) == 16:
        _adicionar(encontradas, juntos)
        return
    for i in range(len(grupos)):
        acc = ""
        for j in range(i, len(grupos)):
            if len(grupos[j]) != 4:
                break
            acc += grupos[j]
            if len(acc) == 16:
                _adicionar(encontradas, acc)
                break
            if len(acc) > 16:
                break
    for grupo in grupos:
        if len(grupo) == 16:
            _adicionar(encontradas, grupo)


def extrair_carteirinhas(texto: str) -> list[str]:
    """Le a carteirinha sem exigir a palavra carteirinha.

    Vale numero corrido, grupos de 4, e separadores (espaco, ponto, hifen, barra),
    mesmo quando a frase tambem traz data ou valor.
    """
    encontradas: list[str] = []
    bruto = texto or ""
    limpo = _sem_ruido_numerico(bruto)
    for m in CARTEIRINHA.finditer(limpo):
        _adicionar(encontradas, normalizar_carteirinha(m.group(1)))
    for m in _BLOCO.finditer(limpo):
        _do_bloco(m.group(0), encontradas)
    digitos = re.sub(r"\D", "", limpo)
    if len(digitos) == 16:
        _adicionar(encontradas, digitos)
    return encontradas


def digitos_quase_carteirinha(texto: str) -> int | None:
    """Maior bloco numerico perto de 16, quando a carteirinha nao fechou."""
    limpo = _sem_ruido_numerico(texto or "")
    maior = 0
    for m in _BLOCO.finditer(limpo):
        n = len(re.sub(r"\D", "", m.group(0)))
        if n > maior:
            maior = n
    if 12 <= maior <= 20 and maior != 16:
        return maior
    return None


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
