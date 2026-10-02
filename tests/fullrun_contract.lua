local files,now,count={},100,0
love={timer={getTime=function()return now end},data={hash=function(_,s)return string.rep(string.char(#s%250),32)end},
 filesystem={getIdentity=function()return 'BalatroCopilotLab'end,getInfo=function(p)return files[p] and {size=#files[p]} end,
 read=function(p)return files[p]end,write=function(p,s)files[p]=s;return true end},update=function()end,draw=function()end}
G={STATES={MENU=1,ROUND_EVAL=2,BLIND_SELECT=3},STATE=1,GAME={won=false,round_resets={ante=1},current_round={reroll_cost=5},dollars=20,bankrupt_at=0,STOP_USE=0},CONTROLLER={},I={UIBOX={}},FUNCS={}}
BALATRO_COPILOT_AUTO_V1={session=string.rep('a',64),snapshot=function()return {signature=string.rep('b',64)}end,cancel=function()error('Unexpected executor exception')end}
G.FUNCS.start_run=function()count=count+1 end
G.FUNCS.setup_run=function()end
G.SETTINGS={profile=1};function get_compressed()return nil end
G.FUNCS.cash_out=function(e)assert(e.config.button=='cash_out');count=count+1 end
dofile(__RUN_SOURCE__)
local A=BALATRO_COPILOT_AUTO_V1
local token=string.rep('c',32)
local request=0
local function command(action,edits)
 request=request+1
 local p={'BACP_RUN_COMMAND_V1',string.format('%032x',request),os.time(),token,A.session,A.snapshot().fullrun.signature,action,''}
 for k,v in pairs(edits or {}) do p[k]=v end
 files['balatro_copilot_run_command.txt']=table.concat(p,'\n')..'\n'
 love.update(.1)
 return files['balatro_copilot_run_ack.txt']
end
assert(command('start'):find('|rejected|lease',1,true) and count==0)
files['balatro_copilot_auto_lease.txt']=table.concat({'BACP_AUTO_LEASE_V1',token,os.time(),'1'},'\n')..'\n'
assert(command('eval'):find('|rejected|blocked',1,true) and count==0)
assert(command('start',{[5]=string.rep('f',64)}):find('|rejected|stale',1,true))
assert(command('start'):find('|accepted|invoked',1,true) and count==1)
now=102;love.update(.1)
G.STATE=2;G.round_eval={UIRoot={children={}}}
G.I.UIBOX={{UIRoot={children={{config={button='cash_out'}}}}}}
assert(A.snapshot().fullrun.cash_ready,'Separate attached cash-out UIBox must be found')
assert(command('cash'):find('|accepted|invoked',1,true) and count==2)
now=104;love.update(.1)
G.CONTROLLER.locked=true
G.OVERLAY_MENU={UIRoot={children={{config={button='continue_unlock'}}}},get_UIE_by_ID=function()return nil end}
G.FUNCS.continue_unlock=function(e)assert(e.config.button=='continue_unlock');count=count+1;G.OVERLAY_MENU=nil;G.CONTROLLER.locked=false end
assert(A.snapshot().fullrun.unlock_ready and A.snapshot().fullrun.ready)
assert(command('unlock'):find('|accepted|invoked',1,true) and count==3)
now=106;love.update(.1)
G.STATE=1
love.filesystem.getIdentity=function()return 'Balatro'end
assert(command('start'):find('|rejected|blocked',1,true) and count==3,'Must not erase real run')
