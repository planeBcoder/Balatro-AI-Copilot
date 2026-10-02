from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rank: int = Field(ge=1, le=3)
    candidate_id: str = Field(min_length=1, max_length=20)
    action: Literal["play", "discard"]
    cards: list[str] = Field(min_length=1, max_length=5)
    win_probability: None
    probability_method: Literal["unavailable"]
    reason: str = Field(min_length=1, max_length=160)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recommendations: list[Recommendation] = Field(min_length=1, max_length=3)
    recommended_rank: Literal[1]
    summary: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def ordered(self) -> Decision:
        if [r.rank for r in self.recommendations] != list(range(1, len(self.recommendations) + 1)):
            raise ValueError("Ranks must start at 1 in order")
        if len({r.candidate_id for r in self.recommendations}) != len(self.recommendations):
            raise ValueError("Duplicate candidates")
        return self


def validate_decision(raw: str, calculation: dict) -> Decision:
    decision = Decision.model_validate_json(raw)
    candidates = {c["id"]: c for c in calculation["candidates"]}
    for r in decision.recommendations:
        candidate = candidates.get(r.candidate_id)
        if candidate is None or r.action != candidate["action"] or r.cards != candidate["cards"]:
            raise ValueError("Decision references an unavailable action or card")
        finish_ids = calculation.get("immediate_finish_ids") or []
        if finish_ids and r.candidate_id not in finish_ids:
            raise ValueError("Cannot gamble instead of a certified immediate finish")
        r.reason = scrub_numbers(r.reason)
    decision.summary = scrub_numbers(decision.summary)
    return decision


def scrub_numbers(text: str) -> str:
    # No model-authored percentage reaches the UI, even in free text.
    text = re.sub(r"\d+(?:\.\d+)?\s*[%％]", "（概率以本地结果为准）", text)
    return re.sub(r"(?:保证|必定|必然|一定|肯定|稳稳)(?:可以|能|会)?(?:过关|获胜|通关)|稳赢|必胜", "优先争取过关", text)
