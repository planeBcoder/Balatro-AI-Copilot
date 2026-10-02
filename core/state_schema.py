from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.errors import CopilotError


class Card(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str = Field(pattern=r"^[2-9TJQKA][SHCD]$")
    enhancement: str | None = None
    seal: str | None = None
    edition: str | None = None
    debuffed: bool = False
    highlighted: bool = False


class Blind(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str | None = None
    name: str | None = None
    target: str | int | float | None = None
    boss: bool = False
    disabled: bool = False


class State(BaseModel):
    model_config = ConfigDict(extra="allow")
    game_state: str
    exported_at: str
    balatro_version: str | None = None
    ante: int | None = None
    blind: Blind
    score: str | int | float = 0
    hands_left: int | None = Field(default=None, ge=0)
    discards_left: int | None = Field(default=None, ge=0)
    money: int | float | None = None
    hand: list[Card]
    deck_remaining: list[Card]
    jokers: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int]
    poker_hands: dict[str, Any] | None = None
    request_id: str | None = None

    @model_validator(mode="after")
    def consistency(self) -> State:
        for key, cards in (("deck_remaining", self.deck_remaining), ("hand", self.hand), ("jokers", self.jokers)):
            if key in self.counts and self.counts[key] != len(cards):
                raise ValueError(f"Inconsistent {key} count")
        if "deck_remaining" not in self.counts:
            raise ValueError("Missing remaining deck count")
        if self.game_state == "SELECTING_HAND":
            if not self.hand or self.hands_left is None or self.discards_left is None:
                raise ValueError("Incomplete playable state")
        # Modified decks may contain duplicates and need not contain 52 cards.
        return self

    def ai_payload(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data.pop("request_id", None)
        data.pop("hud_fingerprint", None)
        data.pop("autoplay", None)  # Private execution identity never sent to AI.
        # Order is private implementation detail, not a forecast of future draws.
        data["deck_remaining"] = sorted(data["deck_remaining"], key=lambda c: c["code"])
        data["deck_order_semantics"] = "unordered remaining multiset; never predict draw order"
        return data


def parse_state(raw: bytes | str) -> State:
    try:
        return State.model_validate_json(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CopilotError("游戏状态不完整或牌堆数量不一致，请重试。") from exc
