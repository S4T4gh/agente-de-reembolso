"""Utilitarios de normalizacao textual para matching de intencao."""

from __future__ import annotations

import unicodedata


def fold(texto: str) -> str:
    """Converte para minusculas e remove diacriticos."""
    if not texto:
        return ""
    n = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in n if not unicodedata.combining(c))
