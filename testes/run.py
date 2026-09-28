"""Runner da suite de validacao.

  python -m testes.run
  python -m testes.run --api
  python -m testes.run --recall-only
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", action="store_true", help="inclui testes HTTP /chat")
    ap.add_argument("--recall-only", action="store_true")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if args.recall_only:
        suite.addTests(loader.loadTestsFromName("testes.test_recall"))
    else:
        for mod in (
            "testes.test_conformidade",
            "testes.test_calculo",
            "testes.test_guardrails",
            "testes.test_recall",
        ):
            suite.addTests(loader.loadTestsFromName(mod))
        if args.api:
            suite.addTests(loader.loadTestsFromName("testes.test_api"))

    result = unittest.TextTestRunner(verbosity=2 if args.v else 1).run(suite)
    print()
    print(
        f"resultado: {result.testsRun} testes | "
        f"ok={result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)} | "
        f"fail={len(result.failures)} | err={len(result.errors)} | skip={len(result.skipped)}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
