"""Filesystem-location re-export per CONTEXT.md D-18.

D-18 places companies.py at `backend/eval/companies.py`. The real implementation lives
at `backend/src/dossier/eval/companies.py` so it's importable as `dossier.eval.companies`
(which `dossier.eval.seed` depends on).
"""
from dossier.eval.companies import EVAL_COMPANIES, EvalCompany, split_summary

__all__ = ["EVAL_COMPANIES", "EvalCompany", "split_summary"]
