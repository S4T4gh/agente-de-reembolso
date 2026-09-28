"""Suite de validacao do agente: conformidade, calculo, guardrails, recall RAG e API.

Uso:
  .venv/Scripts/python -m testes.run
  .venv/Scripts/python -m testes.run --api          # inclui /health /chat /reset
  .venv/Scripts/python -m testes.run --recall-only
"""

from __future__ import annotations
