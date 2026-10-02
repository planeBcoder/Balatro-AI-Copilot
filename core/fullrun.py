"""Opt-in full-run controller and audited game callback transport.

Uses game state, not desktop coordinates. Does not inspect hidden draw order.
Training occurs in an isolated normal-rules game profile, not the user's run.
"""
from __future__ import annotations
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
import random
import re
import threading
import time
import uuid

from core.autoplay import AutoTransport, execution_metadata
from core.errors import CopilotError
from core.calculation import candidates, classify
from core.decision_policy import local_decision, make_decision
from core.scoring import score_hand, SUPPORTED, LEVEL_INCREMENTS
from core.calculation import GAME_NAMES
from core.game_hud import GameHUD
from core.presentation import card_label


def run_metadata(state):
    return execution_metadata(state).get('fullrun') or {}


class RunTransport(AutoTransport):
    def execute_run(self, state, action, target='', cancelled=lambda:False):
        meta=execution_metadata(state)
        run=run_metadata(state)
        if not self.active or cancelled():
            raise CopilotError('运行已停止')
        if not run.get('ready') or action not in {'start','blind','cash','leave','reroll','buy','open','sell','use','take','skip','front','last','unlock'}:
            raise CopilotError('动作接口未就绪')
        session,signature=meta.get('session',''),run.get('signature','')
        if not all(re.fullmatch('[a-f0-9]{64}',v) for v in (session,signature)):
            raise CopilotError('局面标识不完整')
        target=str(target)
        if target and not re.fullmatch('[1-9][0-9]{0,9}',target):
            raise CopilotError('动作实体标识无效')
        rid=uuid.uuid4().hex
        self._write('balatro_copilot_run_command.txt','\n'.join(('BACP_RUN_COMMAND_V1',rid,str(int(self.clock())),self.token,session,signature,action,target))+'\n')
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if cancelled() or not self.active:
                raise CopilotError('已停止；已提交动作不可撤回')
            try:
                path=self.root/'balatro_copilot_run_ack.txt'
                if path.stat().st_size<=256:
                    p=path.read_text(encoding='ascii').strip().split('|')
                    if p[:2]==['BACP_RUN_ACK_V1',rid]:
                        if p[2:]==['accepted','invoked']: return rid
                        raise CopilotError('游戏拒绝动作：'+','.join(p[2:]))
            except (OSError,UnicodeError): pass
            time.sleep(.04)
        raise CopilotError('动作确认超时，不重复提交')


def best_score(state):
    """Enumerate legal physical-card choices, preserving visual order."""
    best=(0,None)
    for n in range(1,min(5,len(state.hand))+1):
        for indices in combinations(range(len(state.hand)),n):
            kind=classify([state.hand[i].code for i in indices])
            if state.blind.key=='bl_psychic' and n!=5: continue
            if state.blind.key=='bl_eye' and (state.blind.model_extra or {}).get('played_hand_types',{}).get(GAME_NAMES[kind]): continue
            only=(state.blind.model_extra or {}).get('only_hand')
            if state.blind.key=='bl_mouth' and only and only!=GAME_NAMES[kind]: continue
            score=score_hand(state,list(indices),kind)
            if score.certified and score.value is not None and score.value>best[0]: best=(score.value,indices)
    return best


def annotate_general_draws(state,calc,samples=12):
    """Expected NEXT hand score, not Blind win probability. Unordered samples.

    Restrict to fully visible, supported, deterministic states. State clones
    keep real Joker and hand-level data; game RNG and callbacks never run.
    """
    if calc.get('score_limitations') or any(f!='front' for f in run_metadata(state).get('hand_facing',[])):
        return
    deck=sorted(state.deck_remaining,key=lambda c:(c.code,str(c.enhancement)))
    if not deck:return
    rng=random.Random(28033)
    for c in calc['candidates']:
        if c['action']!='discard':continue
        if c.get('next_play'):continue  # Preserve the higher-sample plain planner.
        held=[state.hand[i] for i in c['keep_indices']]
        scores=[]
        for _ in range(samples):
            drawn=rng.sample(deck,min(c['draws'],len(deck)))
            chosen={id(x) for x in drawn}
            clone=state.model_copy(update={'hand':held+drawn,'deck_remaining':[x for x in deck if id(x) not in chosen], 'discards_left':max(0,state.discards_left-1)})
            clone.jokers=json.loads(json.dumps(state.jokers))
            for j in clone.jokers:
                if j['key']=='j_green_joker':j['ability']['mult']=max(0,j['ability']['mult']-j['ability']['extra']['discard_sub'])
                if j['key']=='j_ramen':j['ability']['x_mult']=max(1,j['ability']['x_mult']-j['ability']['extra']*len(c['indices']))
            scores.append(best_score(clone)[0])
        c['next_play']={'method':'monte_carlo','sample_count':samples,'expected_best_score':round(sum(scores)/len(scores),2),'finish_next_play_probability':None}


def planned_candidate(calc,hands):
    plays=[c for c in calc['candidates'] if c['action']=='play' and c.get('score_certified')]
    if not plays:raise CopilotError('没有可验证的合法出牌候选')
    best=max(plays,key=lambda c:c['expected_score'] or 0)
    if calc.get('immediate_finish_ids'):return next(c for c in plays if c['id'] in calc['immediate_finish_ids'])
    draws=[c for c in calc['candidates'] if c['action']=='discard' and c.get('next_play')]
    if draws:
        draw=max(draws,key=lambda c:c['next_play']['expected_best_score'])
        gain=draw['next_play']['expected_best_score']
        # A discard costs no hand, but can destroy a useful made hand. Require
        # material gain; be more willing when a last hand needs improvement.
        threshold=1.12 if hands==1 else 1.35
        if gain>max(1,best['expected_score'] or 0)*threshold:return draw
    return best


def hidden_choice(state):
    """Only visible identities enter scoring. No score guarantee for dark hands."""
    facing=run_metadata(state).get('hand_facing',[])
    visible=[i for i,f in enumerate(facing) if f=='front']
    hidden=[i for i in range(len(state.hand)) if i not in visible]
    indices=[];value=0
    if visible:
        clone=state.model_copy(update={'hand':[state.hand[i] for i in visible]})
        if state.blind.key in {'bl_mark','bl_fish','bl_wheel','bl_house'}:
            clone.blind=state.blind.model_copy(update={'disabled':True})
        value,picked=best_score(clone)
        indices=[visible[i] for i in picked] if picked else visible[:5]
    action='play'
    shortfall=max(0,float(state.blind.target)-float(state.score))
    if hidden and state.discards_left and value<shortfall:
        action='discard';indices=hidden[:5]
    elif not indices:
        indices=hidden[:5]
    # The transport consistency check is the only use of hidden identities;
    # they are masked in the display, and this branch makes no AI request.
    return {'id':'hidden','action':action,'indices':indices,'cards':[state.hand[i].code for i in indices],'score_certified':False}


RATINGS={'j_joker':3,'j_half':14,'j_abstract':20,'j_banner':18,'j_blue_joker':21,
    'j_blackboard':30,'j_acrobat':26,'j_supernova':22,'j_mystic_summit':14,'j_stuntman':20,
    'j_droll':14,'j_crafty':14,'j_tribe':28,'j_order':20,'j_duo':25,'j_trio':23,'j_family':23,
    'j_jolly':8,'j_sly':9,'j_zany':10,'j_wily':10,'j_mad':10,'j_clever':10,'j_crazy':8,'j_devious':10}
RATINGS.update(j_green_joker=23,j_ride_the_bus=22,j_trousers=28,j_popcorn=16,j_ice_cream=16,j_ramen=27,j_gros_michel=18,j_cavendish=40,j_flash=24)
RATINGS.update(j_greedy_joker=19,j_lusty_joker=19,j_wrathful_joker=19,j_gluttenous_joker=19,j_smiley=20,j_scary_face=19,j_fibonacci=28,j_even_steven=23,j_odd_todd=22,j_scholar=23,j_photograph=28,j_hanging_chad=30,j_hack=27,j_sock_and_buskin=28,j_splash=12,j_constellation=29,j_runner=24,j_onyx_agate=25,j_arrowhead=25)

def build_value(state,jokers,samples=3):
    """Compare complete builds on the SAME unordered samples and real levels.

    Includes last-hand effects, excludes prior Boss restrictions/debuffs. This
    is a shop heuristic, not a guarantee about the next unknown Boss or draw.
    """
    if len(state.deck_remaining)<8:return 0
    pool=sorted(state.deck_remaining,key=lambda c:c.code)
    rng=random.Random(20013)
    normalized=[]
    for j in jokers:
        value=json.loads(json.dumps(j));value['debuffed']=False
        edition=value.get('edition')
        if isinstance(edition,dict):value['edition']=next((k for k in ['polychrome','holo','foil','negative'] if edition.get(k)),None)
        normalized.append(value)
    scores=[]
    for _ in range(samples):
        hand=[c.model_copy(update={'debuffed':False}) for c in rng.sample(pool,8)]
        clone=state.model_copy(update={'hand':hand,'jokers':normalized,'hands_left':4,'discards_left':4,'blind':state.blind.model_copy(update={'boss':False,'key':None,'disabled':True}),'deck_remaining':pool[:max(0,len(pool)-8)]})
        first=best_score(clone)[0]
        clone.hands_left=1
        last=best_score(clone)[0]
        scores.append(.75*first+.25*last)
    return sum(scores)/len(scores)


def planet_value(state,planet):
    """Value the actual build's upgrade, not poker prestige or past frequency."""
    kind=(planet.get('ability',{}).get('consumeable') or {}).get('hand_type')
    if kind not in LEVEL_INCREMENTS or kind not in (state.poker_hands or {}):return 0
    hands=json.loads(json.dumps(state.poker_hands))
    dc,dm=LEVEL_INCREMENTS[kind]
    hands[kind]['chips']+=dc;hands[kind]['mult']+=dm;hands[kind]['level']+=1
    clone=state.model_copy(update={'poker_hands':hands})
    return build_value(clone,run_metadata(state)['jokers'])


class FullRunTrainer:
    def __init__(self,root,refresh,client,output,model_budget=120,max_runs=40):
        self.root,self.refresh,self.client,self.output=root,refresh,client,output
        self.transport=RunTransport(root)
        self.stop=threading.Event()
        self.hud=GameHUD(root);self.hud.enabled=True
        self.calls=0;self.model_budget=model_budget;self.max_runs=max_runs
        self.runs=0;self.actions=0;self.shop_rolls={};self.max_ante=0
        output.mkdir(parents=True,exist_ok=True)
        self.events=(output/'events.jsonl').open('a',encoding='utf-8',buffering=1)

    def log(self,event,**kw):
        payload={'time':time.time(),'event':event,'attempt':self.runs,**kw}
        self.events.write(json.dumps(payload,ensure_ascii=False,default=str)+'\n')
        if __import__('sys').stdout is not None:
            print(json.dumps({k:v for k,v in payload.items() if k not in {'state','calculation','decision'}},ensure_ascii=False),flush=True)

    def heartbeat(self):
        while not self.stop.wait(.5):
            try:self.transport.heartbeat()
            except OSError:self.stop.set()

    def fresh(self):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and not self.stop.is_set():
            try:return self.refresh()
            except CopilotError:time.sleep(.15)
        raise CopilotError('状态请求无响应')

    def stable(self):
        deadline=time.monotonic()+35
        while time.monotonic()<deadline and not self.stop.is_set():
            s=self.fresh();m=execution_metadata(s);r=run_metadata(s)
            if m.get('cancelled_run')==self.transport.token:raise CopilotError('游戏内取消')
            if not r or not r.get('lab'):raise CopilotError('实战训练仅允许隔离存档副本')
            if r.get('win_notified') and r.get('won') and r.get('win_overlay') and s.game_state=='ROUND_EVAL':return s
            if r.get('ready') and r.get('unlock_ready'):return s
            if s.game_state=='SELECTING_HAND' and m.get('ready'):return s
            if r.get('ready') and ((s.game_state in {'MENU','GAME_OVER'}) or (s.game_state=='BLIND_SELECT' and r.get('blind_ready')) or (s.game_state=='SHOP' and r.get('shop_ready')) or (s.game_state=='ROUND_EVAL' and r.get('cash_ready')) or (s.game_state.endswith('_PACK') and r.get('pack'))):return s
            time.sleep(.15)
        raise CopilotError('游戏动作等待超时')

    def shop_choice(self,s):
        r=run_metadata(s)
        if r.get('consumables'):
            for c in r['consumables']:
                if c['set']=='Planet':return 'use',c['id']
        owned=r['jokers'];money=r['dollars'];slots=r['joker_limit']
        # Individual +mult must precede Photograph's x2. Main-effect xmult
        # should follow additive effects. These are ordinary player reorders.
        photo=next((i for i,j in enumerate(owned) if j['key']=='j_photograph'),None)
        individual_add={'j_smiley','j_scholar','j_fibonacci','j_even_steven','j_onyx_agate','j_greedy_joker','j_lusty_joker','j_wrathful_joker','j_gluttenous_joker'}
        if photo is not None:
            for j in owned[photo+1:]:
                if j['key'] in individual_add:return 'front',j['id']
        main_add={'j_joker','j_half','j_abstract','j_mystic_summit','j_supernova','j_green_joker','j_ride_the_bus','j_trousers','j_popcorn','j_flash','j_gros_michel','j_jolly','j_zany','j_mad','j_crazy','j_droll'}
        main_x={'j_ramen','j_cavendish','j_constellation','j_blackboard','j_acrobat','j_duo','j_trio','j_family','j_order','j_tribe'}
        for i,j in enumerate(owned):
            if j['key'] in main_x and any(k['key'] in main_add for k in owned[i+1:]):return 'last',j['id']
        useful=[c for c in r['shop'] if c['key'] in RATINGS and c['cost']<=money]
        useful.sort(key=lambda c:RATINGS[c['key']]+(12 if c.get('edition') else 0),reverse=True)
        if useful:
            if len(owned)<slots:
                c=max(useful,key=lambda c:build_value(s,owned+[c]))
                return 'buy',c['id']
            baseline=build_value(s,owned)
            swaps=[]
            for c in useful:
                for old in owned:
                    if not old.get('eternal'):
                        replacement=[j for j in owned if j['id']!=old['id']]+[c]
                        swaps.append((build_value(s,replacement),old,c))
            if swaps:
                value,old,c=max(swaps,key=lambda v:v[0])
                if value>baseline*1.10:
                    self.log('shop_build_upgrade',before=round(baseline,2),after=round(value,2),sell=old['key'],buy=c['key'])
                    return 'sell',old['id']
        planets=[c for c in r['shop'] if c['set']=='Planet' and c['cost']<=money]
        if planets and len(r['consumables'])<2:
            best=max(planets,key=lambda c:planet_value(s,c))
            if planet_value(s,best)>build_value(s,owned)*1.05:
                return 'buy',best['id']
        packs=[c for c in r['boosters'] if c['cost']<=money and (c['ability'].get('name','').find('Celestial')>=0 or (len(owned)<slots and c['ability'].get('name','').find('Buffoon')>=0))]
        if packs:return 'open',packs[0]['id']
        key=(self.runs,s.ante,(s.model_extra or {}).get('round'))
        rolls=self.shop_rolls.get(key,0)
        # Preserve interest early, search harder late or when build is weak.
        reserve=10 if s.ante<=2 and len(owned)>=2 else 3
        if rolls<3 and money>=r['reroll_cost']+reserve:
            self.shop_rolls[key]=rolls+1
            return 'reroll',''
        return 'leave',''

    def play_choice(self,s):
        # Face-down ranks must not be leaked into decision-making.
        if any(f!='front' for f in run_metadata(s).get('hand_facing',[])):
            return hidden_choice(s),None,'visible_cards_only'
        calc=candidates(s,500)
        if not any(c['action']=='play' for c in calc['candidates']):
            discards=[c for c in calc['candidates'] if c['action']=='discard']
            if discards:return max(discards,key=lambda c:c['draws']),calc,'seek_unused_hand_type'
            indices=list(range(min(5,len(s.hand))))
            return {'id':'blocked_hand','action':'play','indices':indices,'cards':[s.hand[i].code for i in indices],'score_certified':False},calc,'normal_zero_score_attempt'
        if calc.get('score_limitations'):
            # The separate training runner may try a model/reference action
            # without a certified score, but never calls it a guaranteed win.
            # The user's current-Blind runner remains fail-closed.
            self.log('uncertain_score',limitations=calc['score_limitations'])
            if self.calls<self.model_budget:
                self.calls+=1
                decision=self.client.decide(s.ai_payload(),calc)
                c=next(c for c in calc['candidates'] if c['id']==decision.recommendations[0].candidate_id)
                return c,calc,'ai_uncertified'
            return next(c for c in calc['candidates'] if c['action']=='play'),calc,'reference_uncertified'
        direct=local_decision(calc)
        if direct:
            choice=next(c for c in calc['candidates'] if c['id']==direct.recommendations[0].candidate_id)
            return choice,calc,'certified_finish'
        annotate_general_draws(s,calc)
        choice=planned_candidate(calc,s.hands_left)
        decision=None
        # AI acts as strategy reviewer; deterministic finishing and measured
        # one-step score expectation constrain it, rather than trust card rank.
        if self.calls<self.model_budget and (self.actions%4==0 or s.hands_left==1):
            self.calls+=1
            try:
                decision=self.client.decide(s.ai_payload(),calc)
                suggested=next(c for c in calc['candidates'] if c['id']==decision.recommendations[0].candidate_id)
                self.log('ai_review',planned=choice['id'],suggested=suggested['id'],summary=decision.summary)
                # Model may break a statistically close tie, not throw away a
                # certified finish or a materially superior computed score.
                if suggested['action']==choice['action']:
                    def value(c):return (c.get('next_play') or {}).get('expected_best_score',0) if c['action']=='discard' else c.get('expected_score') or 0
                    if value(suggested)>=value(choice)*.95:choice=suggested
            except CopilotError as e:self.log('ai_unavailable',message=str(e))
        return choice,calc,'score_lookahead'

    def run(self):
        from core.single_instance import SingleInstance
        instance=SingleInstance('BalatroCopilotFullRunExecution')
        if instance.existing:
            instance.close();self.events.close()
            raise CopilotError('另一个完整闯关机器人正在运行。')
        try:self.transport.start()
        except Exception:
            instance.close();self.events.close();raise
        thread=threading.Thread(target=self.heartbeat,daemon=True);thread.start()
        try:
            while self.actions<4000 and not self.stop.is_set():
                s=self.stable();r=run_metadata(s)
                if self.runs==0 and s.game_state not in {'MENU','GAME_OVER'}:self.runs=1
                self.max_ante=max(self.max_ante,s.ante or 0)
                if r.get('win_notified') and r.get('won') and r.get('win_overlay') and s.game_state=='ROUND_EVAL' and s.blind.boss and float(s.score)>=float(s.blind.target):
                    self.log('victory',ante=s.ante,seed=r.get('seed'),stake=r.get('stake'),score=s.score,target=s.blind.target)
                    time.sleep(2.5)
                    report={'success':True,'real_game':True,'profile':'BalatroCopilotLab','attempts_this_session':self.runs,'actions_this_session':self.actions,'model_decisions_this_session':self.calls,'ante':s.ante,'won_ante':8,'back':(s.model_extra.get('scoring_rules') or {}).get('back_key'),'seed':r.get('seed'),'seeded':r.get('seeded',False),'stake':r.get('stake'),'boss':s.blind.name,'score':s.score,'target':s.blind.target,'win_notified':True,'win_overlay':True}
                    (self.output/'success.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    (self.output/'victory-state.json').write_text(s.model_dump_json(indent=2),encoding='utf-8')
                    import shutil
                    if (self.root/'copilot-victory.png').exists():shutil.copy2(self.root/'copilot-victory.png',self.output/'victory.png')
                    self.hud.message('stopped',f'BACP_BLOCKS_V1\nA\t已真实通关！\nP\t{float(s.score):,.0f} / {float(s.blind.target):,.0f} 分\nM\t白注 · 第 8 底注最终 Boss\nS\t机器人已停止。证据已保存在本机。')
                    return report
                if r.get('unlock_ready'):action,target='unlock',''
                elif s.game_state in {'MENU','GAME_OVER'}:
                    if self.runs>=self.max_runs:raise CopilotError('达到训练次数上限')
                    if s.game_state=='GAME_OVER':self.log('loss',ante=s.ante,score=s.score,target=s.blind.target,seed=r.get('seed'))
                    self.runs+=1;action,target='start',''
                elif s.game_state=='BLIND_SELECT':action,target='blind',''
                elif s.game_state=='ROUND_EVAL':action,target='cash',''
                elif s.game_state=='SHOP':action,target=self.shop_choice(s)
                elif s.game_state.endswith('_PACK'):
                    pack=r['pack']
                    if s.game_state=='BUFFOON_PACK':
                        eligible=[c for c in pack if c['key'] in RATINGS and len(r['jokers'])<r['joker_limit']]
                        action,target=('take',max(eligible,key=lambda c:RATINGS[c['key']])['id']) if eligible else ('skip','')
                    elif s.game_state=='PLANET_PACK':
                        eligible=[c for c in pack if c['set']=='Planet']
                        played=s.poker_hands or {}
                        if eligible:
                            c=max(eligible,key=lambda c:planet_value(s,c))
                            action,target='take',c['id']
                        else:action,target='skip',''
                    else:action,target='skip',''
                elif s.game_state=='SELECTING_HAND':
                    candidate,calc,method=self.play_choice(s)
                    action=candidate['action']
                    facing=r.get('hand_facing',[])
                    target=[code if not facing or facing[i]=='front' else '??' for i,code in zip(candidate['indices'],candidate['cards'])]
                    self.log('decision',ante=s.ante,round=(s.model_extra or {}).get('round'),blind=s.blind.name,action=action,cards=target,expected=candidate.get('expected_score'),method=method,state=s.model_dump(),calculation=calc)
                    labels='、'.join(card_label(c) if c!='??' else '暗牌' for c in target)
                    verb='打出' if action=='play' else '弃掉'
                    score=candidate.get('expected_score')
                    metric=f'本手验算 {score:,.0f} 分' if candidate.get('score_certified') and score is not None else '比较补牌收益；不编造整轮胜率'
                    note='当前成手直接过关，不额外弃牌。' if method=='certified_finish' else '按当前组合比较实际收益；暗牌不参与身份判断。' if method=='visible_cards_only' else '先看实际分数，再比较出牌与弃牌。'
                    self.hud.message('busy',f'BACP_BLOCKS_V1\nM\t自动实战 · 底注 {s.ante} · {s.blind.name}\nA\t{verb} {labels}\nP\t{metric}\nK\t当前 {s.score}/{s.blind.target} · 剩余 {s.hands_left} 手\nR\t{note}\nF\tEscape 或闯关窗口的停止按钮可取消')
                    fresh=self.fresh()
                    if execution_metadata(fresh).get('signature')!=execution_metadata(s).get('signature'):raise CopilotError('决策期间手牌变化')
                    self.transport.execute(fresh,candidate,self.stop.is_set)
                    self.actions+=1
                    time.sleep(.2)
                    after=self.stable()
                    if after.game_state in {'SELECTING_HAND','ROUND_EVAL','GAME_OVER'}:
                        count_before=s.hands_left if action=='play' else s.discards_left
                        count_after=after.hands_left if action=='play' else after.discards_left
                        if count_after!=count_before-1:raise CopilotError('资源消耗不一致，停止避免重复动作')
                    if action=='play' and candidate.get('score_certified') and after.game_state in {'SELECTING_HAND','ROUND_EVAL','GAME_OVER'}:
                        actual=float(after.score)-float(s.score)
                        self.log('score_check',expected=candidate.get('expected_score'),actual=actual,match=actual==candidate.get('expected_score'),ante=s.ante)
                        if actual!=candidate.get('expected_score'):raise CopilotError('实机计分与计算不一致，需要修复')
                    continue
                else:raise CopilotError('不支持的稳定状态：'+s.game_state)
                self.log('game_action',ante=s.ante,state_name=s.game_state,action=action,target=target)
                self.hud.message('busy',f'自动实战 · 尝试 {self.runs} · 底注 {s.ante}\n{action}\nEscape 停止')
                self.transport.execute_run(s,action,target,self.stop.is_set)
                self.actions+=1
                time.sleep(1.2)
            raise CopilotError('达到安全动作上限')
        finally:
            self.stop.set();self.transport.stop();thread.join(timeout=1)
            self.events.close()
            instance.close()
