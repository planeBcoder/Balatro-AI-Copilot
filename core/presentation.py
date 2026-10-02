"""Shared recommendation text and a tiny non-executable HUD block protocol."""
from core.probability import NAMES

SUITS = {"S": "♠", "H": "♥", "C": "♣", "D": "♦"}
HAND_NAMES = {**NAMES, "high_card": "高牌", "five_kind": "五条", "flush_house": "同花葫芦", "flush_five": "同花五条"}
TAGS = {"M", "A", "B", "P", "K", "R", "S", "F"}


def clean(text):
    # Model-authored newlines/tabs cannot create additional styled blocks.
    return " ".join(str(text).split())[:800]


def card_label(code):
    return ("10" if code[0] == "T" else code[0]) + SUITS[code[1]]


def format_candidate(c):
    return ("打出 " if c["action"] == "play" else "弃掉 ") + "、".join(card_label(code) for code in c["cards"])


def probability_label(c):
    p = c.get("probability")
    if not p or p.get("event") not in NAMES or (p["event"] == "straight_flush" and p["value"] < .15):
        return ""
    text = f"补牌含{NAMES[p['event']]}：{p['value'] * 100:.1f}%"
    return text + (" · 单轮精确" if p["method"] == "exact" else f" · 模拟 {p['sample_count']:,} 次")


def metric(c, calculation):
    score = c.get("expected_score")
    if score is not None:
        label = f"本手 {score:g} 分"
        if c.get("score_certified"):
            shortfall = calculation.get("shortfall")
            if shortfall is not None:
                label += f" / 还差 {shortfall:g}"
            label += " · 可直接过关" if c["id"] in calculation.get("immediate_finish_ids", []) else " · 本地验算"
        else:
            label += " · 条件估算，未校验特殊规则"
        return label
    next_play = c.get("next_play")
    if next_play and (next_play.get("finish_next_play_probability") or 0) > 0:
        p = next_play["finish_next_play_probability"]
        provenance = "单轮精确" if next_play["method"] == "exact" else f"粗估 {next_play['sample_count']} 次"
        return f"补牌后可一手补足差额：{p*100:.0f}% · {provenance}"
    if next_play and next_play.get('expected_best_score') is not None:
        return f"补牌后下一手平均 {next_play['expected_best_score']:g} 分 · 粗估 {next_play['sample_count']} 次，非过关率"
    return probability_label(c)


def document(decision, calculation, state, elapsed):
    context = f"{state.blind.name or '盲注'}  {state.score}/{state.blind.target or '?'} · 出牌 {state.hands_left} · 弃牌 {state.discards_left}"
    blocks = [("M", context)]
    lookup = {c["id"]: c for c in calculation["candidates"]}
    for rec in decision.recommendations:
        c = lookup[rec.candidate_id]
        blocks.append(("A" if rec.rank == 1 else "B", ("推荐 · " if rec.rank == 1 else "备选 · ") + format_candidate(c)))
        if text := metric(c, calculation):
            blocks.append(("P", text))
        if c["action"] == "discard":
            blocks.append(("K", "保留 " + "、".join(card_label(state.hand[i].code) for i in c["keep_indices"])))
        blocks.append(("R", rec.reason))
    blocks.append(("S", decision.summary))
    limitations = calculation.get("score_limitations") or []
    if limitations:
        blocks.append(("F", "验算范围："+"；".join(limitations[:2])))
    blocks.append(("F", f"耗时 {elapsed:.1f} 秒 · 补牌概率不是整轮胜率"))
    blocks = [(tag, clean(text)) for tag, text in blocks]
    plain = "\n\n".join(text for _, text in blocks)
    hud = "BACP_BLOCKS_V1\n"+"\n".join(tag+"\t"+text for tag, text in blocks)
    return blocks, plain, hud


def shop_document(decision, calculation, state, elapsed):
    blocks=[('M',f"商店 · Ante {state.ante or '?'} · 余额 ${calculation['money']:g} · 小丑 {calculation['joker_slots']}"),
        ('K','本地计算 + AI 商店参谋' if calculation.get('ai_reviewed') else '本地计算 · AI 未参与本次建议')]
    if calculation.get('next_blind'):
        blocks.append(('K','下一盲注：'+{'Small':'小盲','Big':'大盲','Boss':'Boss'}.get(calculation['next_blind'],calculation['next_blind'])))
    lookup={c['id']:c for c in calculation['candidates']}
    for rec in decision.recommendations:
        c=lookup[rec.candidate_id]
        blocks.append(('A' if rec.rank==1 else 'B',('推荐 · ' if rec.rank==1 else '备选 · ')+c['label']))
        cost=f"支出 ${c['cost']:g}"+(f" · 卖出收入 ${c['sale_proceeds']:g}" if c['sale_proceeds'] else '')
        blocks.append(('P',cost+f" · 剩余 ${c['cash_after']:g}"))
        blocks.append(('K',f"当前现金利息档位 ${c['interest_now']:g} → ${c['interest_after']:g}（不是结算预测）"))
        if c.get('build_score') is not None:
            base=calculation.get('baseline_score')
            blocks.append(('P',f"本地构筑抽样分 {base:g} → {c['build_score']:g} · {calculation['sample_count']} 组共同样本，非胜率" if base is not None else f"本地构筑抽样分 {c['build_score']:g}，非胜率"))
        elif c['action'] not in {'leave','reroll'}:
            blocks.append(('K','本地未量化此效果；按可见效果与构筑定性判断。'))
        if c['local_note']:blocks.append(('K',c['local_note']))
        blocks.append(('R',('AI 判断：' if calculation.get('ai_reviewed') else '本地判断：')+rec.reason))
    blocks.append(('S',decision.summary))
    blocks.append(('F',f"只提供建议，不代操作；每次买卖或刷新后重新 F9。耗时 {elapsed:.1f} 秒。"))
    blocks.append(('F','抽样分不保证下一轮过关；未模拟未来成长与包内随机内容。'))
    if calculation.get('ai_error'):blocks.append(('F',calculation['ai_error']))
    blocks=[(tag,clean(text)) for tag,text in blocks]
    return blocks,'\n\n'.join(text for _,text in blocks),'BACP_BLOCKS_V1\n'+'\n'.join(tag+'\t'+text for tag,text in blocks)
