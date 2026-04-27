from __future__ import annotations
# Owns the input_ref encoding (value + optional context hint in one column).
# api.schemas.CreateInvestigationBody MUST reject newlines — they'd smuggle a fake hint here.

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
