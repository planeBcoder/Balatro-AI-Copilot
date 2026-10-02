"""Local priority rules, independent of model wording and API availability."""
from ai.schemas import Decision


def local_decision(calculation: dict) -> Decision | None:
    finish = calculation.get("immediate_finish_ids") or []
    lookup = {c["id"]: c for c in calculation["candidates"]}
    if finish:
        c = lookup[finish[0]]
        return make_decision(c, "省下弃牌和剩余出牌机会，不为额外凑大牌承担补牌风险。", "采用当前成手直接结束盲注。")
    plan = calculation.get("two_hand_plan")
    if plan and not any(c["action"] == "discard" for c in lookup.values()):
        c = lookup[plan["first_id"]]
        cards = "、".join(plan["second_cards"])
        return make_decision(c, f"先得 {plan['first_score']:g} 分；手中另有 {cards} 可得 {plan['second_score']:g} 分，两手合计足够。不依赖新摸牌，但消耗两次出牌。", "现有牌即可分两手过关；出牌后按 F9 重新核对局面。")
    return None


def make_decision(candidate, reason, summary):
    return Decision.model_validate({"recommendations": [{"rank": 1, "candidate_id": candidate["id"], "action": candidate["action"],
        "cards": candidate["cards"], "win_probability": None, "probability_method": "unavailable", "reason": reason}],
        "recommended_rank": 1, "summary": summary})
