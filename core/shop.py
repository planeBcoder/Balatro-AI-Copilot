"""Read-only shop advice: legal plans, common-sample build comparison, AI review.

No execution transport or game callbacks. Numeric estimates are heuristic
sample means, never round win rates. Unsupported effects stay unknown.
"""
from __future__ import annotations
from itertools import combinations
import copy
import math
import random

from pydantic import BaseModel, ConfigDict, Field
from core.state_schema import State, Card
from core.errors import CopilotError
from core.scoring import score_hand, LEVEL_INCREMENTS
from core.calculation import classify


class Offer(BaseModel):
    model_config = ConfigDict(extra='allow')
    key: str
    name: str | None = None
    display_name: str | None = None
    set: str
    cost: float = Field(ge=0, allow_inf_nan=False)
    sell_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    ability: dict = Field(default_factory=dict)
    edition: str | None = None
    eternal: bool = False
    perishable: bool = False
    rental: bool = False
    debuffed: bool = False

    def label(self):
        tags=[]
        if self.edition:tags.append({'negative':'负片','foil':'闪箔','holographic':'镭射','holo':'镭射','polychrome':'多彩'}.get(self.edition,self.edition))
        for flag,text in [(self.eternal or self.ability.get('eternal'),'永恒'),(self.perishable,'易腐'),(self.rental,'租赁')]:
            if flag:tags.append(text)
        return (self.display_name or self.name or self.key)+('（'+'、'.join(tags)+'）' if tags else '')


class ShopSnapshot(BaseModel):
    model_config = ConfigDict(extra='allow')
    version: int
    ready: bool
    offers: list[Offer] = Field(default_factory=list)
    boosters: list[Offer] = Field(default_factory=list)
    vouchers: list[Offer] = Field(default_factory=list)
    jokers: list[Offer] = Field(default_factory=list)
    consumables: list[Offer] = Field(default_factory=list)
    full_deck: list[Card] = Field(default_factory=list)
    joker_limit: int = Field(ge=0, le=100)
    consumable_limit: int = Field(ge=0, le=100)
    bankrupt_at: float = Field(default=0, allow_inf_nan=False)
    reroll_cost: float = Field(ge=0, allow_inf_nan=False)
    interest_amount: float = Field(default=1, ge=0, allow_inf_nan=False)
    interest_cap: float = Field(default=25, ge=0, allow_inf_nan=False)
    no_interest: bool = False
    hand_size: int = Field(default=8, ge=1, le=20)
    hands: int = Field(default=4, ge=1, le=100)
    discards: int = Field(default=3, ge=0, le=100)


def snapshot(state):
    try:
        shop = ShopSnapshot.model_validate((state.model_extra or {}).get('shop_snapshot'))
    except (ValueError, TypeError):
        raise CopilotError('商店只读接口尚未载入，请安全退出并重启游戏一次，再按 F9。') from None
    if shop.version != 1 or not shop.ready:
        raise CopilotError('商店仍在动画或暂停中，请回到商店界面后再按 F9。')
    if state.money is None or not math.isfinite(state.money):
        raise CopilotError('商店余额不完整，请重新读取。')
    if not (shop.model_extra or {}).get('next_blind'):
        # Compatibility with the early read-only snapshot. Do not expose
        # session/action IDs to AI; only the public round lifecycle fact.
        run=((state.model_extra or {}).get('autoplay') or {}).get('fullrun') or {}
        statuses=run.get('blind_states') or {}
        upcoming=next((b for b in ['Small','Big','Boss'] if statuses.get(b)=='Upcoming'),None)
        if upcoming:shop.__pydantic_extra__['next_blind']=upcoming
    return shop


def interest(shop, cash):
    return 0 if shop.no_interest else math.floor(max(0, min(cash, shop.interest_cap))/5)*shop.interest_amount


class BuildComparison:
    def __init__(self, state: State, shop: ShopSnapshot, samples=4):
        self.state, self.shop, self.samples = state, shop, samples
        # Sort ALL physical cards, never use private future draw order.
        pool = sorted(shop.full_deck, key=lambda c:(c.code, str(c.enhancement), str(c.edition), str(c.seal)))
        rng = random.Random(20013)
        size = shop.hand_size
        self.hands = [rng.sample(pool, size) for _ in range(samples)] if len(pool)>=size and size<=10 else []
        self.cache = {}

    def value(self, jokers, levels=None, cash=None):
        import json
        cash=float(self.state.money) if cash is None else cash
        key=json.dumps([jokers,levels,cash],sort_keys=True,ensure_ascii=False)
        if key in self.cache:return self.cache[key]
        if not self.hands:return None
        values=[]
        for hand in self.hands:
            clone=self.state.model_copy(deep=True,update={'game_state':'SELECTING_HAND',
                'hand':[c.model_copy(update={'debuffed':False}) for c in hand],
                'deck_remaining':self.shop.full_deck[:max(0,len(self.shop.full_deck)-len(hand))],
                'jokers':copy.deepcopy(jokers),'hands_left':self.shop.hands,'discards_left':self.shop.discards,
                'blind':self.state.blind.model_copy(update={'boss':False,'key':None,'disabled':True})})
            if levels is not None:clone.poker_hands=levels
            # A previous Boss's temporary debuff is not a future shop build effect.
            for j in clone.jokers:
                j['debuffed']=False
                if j['key']=='j_bull':
                    # Shop-only equivalent chips at AFTER-purchase cash. No
                    # scoring support claim for mid-play money changes.
                    factor=j['ability'].get('extra')
                    if not isinstance(factor,(int,float)):return None
                    j['key']='j_ice_cream'
                    j['ability']={'extra':{'chips':factor*max(0,cash)}}
            maxima=[]
            for left in (self.shop.hands,1):
                clone.hands_left=left
                best=0
                for n in range(1,min(5,len(hand))+1):
                    for indices in combinations(range(len(hand)),n):
                        result=score_hand(clone,list(indices),classify([hand[i].code for i in indices]))
                        if not result.certified or result.value is None:
                            self.cache[key]=None
                            return None  # Not zero; unsupported maths must not rank builds.
                        best=max(best,result.value)
                maxima.append(best)
            values.append(.75*maxima[0]+.25*maxima[1])
        value=round(sum(values)/len(values),2)
        self.cache[key]=value
        return value


def shop_candidates(state: State, samples=12):
    shop=snapshot(state)
    money=float(state.money)
    owned=[j.model_dump() for j in shop.jokers]
    comparison=BuildComparison(state,shop,samples)
    baseline=comparison.value(owned)
    options=[]
    def add(action,label,cost=0,sale=0,target=None,sell=None,build=None,levels=None,note=''):
        cash=money+sale-cost
        if cash<shop.bankrupt_at:return
        value=comparison.value(build,levels,cash) if build is not None else None
        if build is not None and any(j['key']=='j_bull' for j in build):
            bull=sum(j['ability'].get('extra',0)*max(0,cash) for j in build if j['key']=='j_bull' and isinstance(j['ability'].get('extra'),(int,float)))
            note+=f' 斗牛按操作后现金的静态效果为 +{bull:g} 筹码；未预测本手收入变化。'
        options.append({'id':f's{len(options)+1}','action':action,'label':label,
            'cost':cost,'sale_proceeds':sale,'cash_after':cash,
            'interest_now':interest(shop,money),'interest_after':interest(shop,cash),
            'target':target,'sell':sell,'build_score':value,
            'gain_ratio':round(value/baseline,4) if value is not None and baseline else None,
            'local_note':note})
    add('leave','保留资金，离开商店',note='不消费；保留当前构筑和利息档位。')
    for i,o in enumerate(shop.offers):
        target={'area':'offers','index':i,'key':o.key,'name':o.label()}
        negative=o.edition in {'negative','e_negative'}
        if o.set=='Joker':
            new=o.model_dump()
            if len(owned)<shop.joker_limit+int(negative):
                add('buy',f'买入 {o.label()}',o.cost,target=target,build=owned+[new],note='租赁每轮扣款。' if o.rental else '')
            else:
                for j,old in enumerate(shop.jokers):
                    if old.eternal or old.ability.get('eternal'):continue
                    # Selling a negative Joker also removes its granted slot.
                    old_negative=old.edition in {'negative','e_negative'}
                    if len(owned)-1>=shop.joker_limit-int(old_negative)+int(negative):continue
                    build=owned[:j]+owned[j+1:]+[new]
                    add('swap',f'卖出 {old.label()} → 买入 {o.label()}',o.cost,old.sell_cost,target,
                        {'area':'jokers','index':j,'key':old.key,'name':old.label()},build,
                        note='先卖后买；收益按替换后的整套组合比较。')
        elif o.set in {'Planet','Tarot','Spectral'} and len(shop.consumables)<shop.consumable_limit+int(negative):
            levels=None
            kind=(o.ability.get('consumeable') or {}).get('hand_type')
            if o.set=='Planet' and kind in LEVEL_INCREMENTS and kind in (state.poker_hands or {}):
                levels=copy.deepcopy(state.poker_hands)
                dc,dm=LEVEL_INCREMENTS[kind]
                levels[kind]['chips']+=dc;levels[kind]['mult']+=dm;levels[kind]['level']+=1
            add('buy',f'买入 {o.label()}',o.cost,target=target,build=owned if levels else None,
                levels=levels,note='行星估算假设购买后使用；未模拟成长/随机后续。' if levels else '效果由 AI 定性评估，未量化计分收益。')
    for area,items,action in [('boosters',shop.boosters,'open'),('vouchers',shop.vouchers,'redeem')]:
        for i,o in enumerate(items):
            add(action,('开启 ' if action=='open' else '兑换 ')+o.label(),o.cost,
                target={'area':area,'index':i,'key':o.key,'name':o.label()},note='未知包内内容不提前读取。' if action=='open' else '持久效果由 AI 定性评估。')
    add('reroll','刷新商店',shop.reroll_cost,note='刷新后的商品未知；只核算刷新费用和余额。')
    # Legal cheap/synergy choices are not removed merely for lacking a large hand.
    result={'kind':'shop','candidates':options,'baseline_score':baseline,
        'sample_count':samples,'method':'common unordered full-deck samples; 75% first hand + 25% last hand',
        'money':money,'joker_slots':f'{len(shop.jokers)}/{shop.joker_limit}',
        'interest_now':interest(shop,money),
        'next_blind':(shop.model_extra or {}).get('next_blind'),
        'limitations':['抽样构筑分不是胜率，也不保证下一盲注过关。',
            '不量化未来成长、随机包内容及未支持效果；利息仅是当前现金档位，未预测结算收入。']}
    return result


def shop_ai_payload(state):
    shop=snapshot(state)
    return {'game_state':'SHOP','ante':state.ante,'money':state.money,
        'poker_hands':state.poker_hands,'shop':shop.model_dump(exclude={'full_deck'}),
        'deck_counts':{code:sum(c.code==code for c in shop.full_deck) for code in sorted({c.code for c in shop.full_deck})}}


def local_shop_decision(calculation, summary='本地建议；AI 暂不可用。'):
    from ai.shop_schema import ShopDecision, ShopRecommendation
    candidates=calculation['candidates']
    # Conservative fallback: spending requires measured material improvement;
    # unquantified choices remain available to AI, not auto-fabricated returns.
    improving=[c for c in candidates if c.get('gain_ratio') and c['gain_ratio']>=1.10]
    best=max(improving,key=lambda c:(c['gain_ratio']-1)/max(1,c['cost']-c['sale_proceeds'])) if improving else candidates[0]
    rec=ShopRecommendation(rank=1,candidate_id=best['id'],reason=best['local_note'] or '比较整套构筑的共同牌池抽样收益与消费成本。')
    return ShopDecision(recommendations=[rec],summary=summary)
