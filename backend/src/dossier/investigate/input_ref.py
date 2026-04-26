"""The InvestigationInput value type — owner of the input_ref encoding.

`investigations.input_ref` packs the user-supplied value (company name or
URL) with an optional context hint into one column. This module is the
only place that knows the rule. Callers parse and serialize through it;
the separator token is private.

The hint-newline guardrail in `api.schemas.CreateInvestigationBody` exists
because parsing here splits on this exact token — leaking a newline into
the user-supplied value would let it smuggle a fake hint.
"""
from __future__ import annotations

from dataclasses import dataclass


_HINT_SEPARATOR: str = "\n---HINT---\n"


@dataclass(frozen=True, slots=True)
class InvestigationInput:
    value: str
    context_hint: str | None = None

    @classmethod
    def parse(cls, input_ref: str | None) -> "InvestigationInput":
        if not input_ref:
            return cls(value="", context_hint=None)
        value, sep, hint = input_ref.partition(_HINT_SEPARATOR)
        return cls(value=value, context_hint=hint if sep else None)

    def serialize(self) -> str:
        if self.context_hint:
            return f"{self.value}{_HINT_SEPARATOR}{self.context_hint}"
        return self.value

    def with_value(self, value: str) -> "InvestigationInput":
        return InvestigationInput(value=value, context_hint=self.context_hint)


__all__ = ["InvestigationInput"]
