import json, hashlib
import httpx, pytest
from fastapi.testclient import TestClient
from opspilot.agent import Agent
from opspilot.api import app
from opspilot.tool_agent import ToolAgent, ModelConfig, definitions

@pytest.fixture(scope='module')
def agent(): return Agent()

def call(name,args,id='1'):
    return {'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
def finish(citations,answerable=True):
    return {'answerable':answerable,'summary':'Observed sample evidence; root cause unknown.','hypotheses':[],'next_checks':[],'citations':citations}

def scripted(agent,batches,**kwargs):
    requests=[]
    def handler(req):
        data=json.loads(req.content);requests.append(data)
        batch=batches[min(len(requests)-1,len(batches)-1)]
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':batch}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r=ToolAgent(agent,ModelConfig('unit-test-secret','test','https://api.deepseek.com',1,2),client=client,**kwargs).run('Investigate Directory index forbidden')
    return r,requests

def test_definitions_are_read_only():
    assert {t['function']['name'] for t in definitions()}=={'search_logs','search_docs','get_context','log_stats','finish_investigation'}
    assert all(not t['function']['parameters'].get('additionalProperties',True) for t in definitions())

def test_model_selects_then_adapts_to_actual_result(agent):
    requests=[]
    def handler(req):
        d=json.loads(req.content);requests.append(d)
        if len(requests)==1:batch=[call('search_logs',{'query':'Directory index forbidden','k':2},'a'),call('search_docs',{'query':'directory index forbidden','k':2},'b')]
        elif len(requests)==2:
            results=[json.loads(x['content']) for x in d['messages'] if x['role']=='tool'];line=results[0]['items'][0]['line']
            batch=[call('get_context',{'line':line,'radius':1},'c')]
        else:
            results=[json.loads(x['content']) for x in d['messages'] if x['role']=='tool']
            batch=[call('finish_investigation',finish([results[0]['items'][0]['id'],results[1]['items'][0]['id']]),'d')]
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':batch}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r=ToolAgent(agent,ModelConfig('test-secret','test'),c).run('q')
    assert r['status']=='completed' and r['usage']['api_calls']==3
    assert [t['tool'] for t in r['trace']]==['search_logs','search_docs','get_context','finish_investigation']
    assert requests[0]['tool_choice']=='auto' and 'thinking' in requests[0]
    assert r['usage']['total_tokens']==360

@pytest.mark.parametrize('name,args',[
 ('execute_shell',{'command':'whoami'}),
 ('get_context',{'line':0,'radius':1}),
 ('get_context',{'line':'132','radius':1}),
 ('get_context',{'line':True,'radius':1}),
 ('search_logs',{'query':'x','k':1000}),
 ('search_docs',{'query':'x','k':1,'path':'/etc/passwd'}),
 ('log_stats',{'contains':'','level':None,'start':20,'end':1})])
def test_reject_invalid_tools_and_arguments(agent,name,args):
    before=hashlib.sha256(agent.tools.db.read_bytes()).hexdigest()
    r,_=scripted(agent,[[call(name,args)]],max_rounds=1)
    assert r['status']=='round_limit' and r['trace'][0]['status']=='rejected'
    assert hashlib.sha256(agent.tools.db.read_bytes()).hexdigest()==before

def test_filtered_count_and_grounded_finish(agent):
    a={'contains':'Directory index forbidden','level':None,'start':1,'end':2000}
    r,requests=scripted(agent,[[call('log_stats',a,'a')],[call('finish_investigation',finish(['S:1']),'b')]])
    assert r['status']=='completed' and r['statistics'][0]['total']==32
    assert r['usage']['cost_usd']==pytest.approx(.00028)
    assert '32' in requests[1]['messages'][-1]['content']

@pytest.mark.parametrize('refs',[['L999999','D:fake'],[],['L132']])
def test_finish_rejects_unseen_or_incomplete_citations(agent,refs):
    r,_=scripted(agent,[[call('search_logs',{'query':'Directory index forbidden','k':2},'a')],[call('finish_investigation',finish(refs),'b')]],max_rounds=2)
    assert r['status']=='round_limit' and r['trace'][-1]['status']=='rejected'

def test_abstention(agent):
    r,_=scripted(agent,[[call('search_logs',{'query':'Kubernetes OOMKilled','k':1},'a')],[call('finish_investigation',finish([],False),'b')]])
    assert r['status']=='abstained' and r['answer']['citations']==[]

def test_duplicate_call_feedback(agent):
    c=call('search_logs',{'query':'workerEnv','k':1},'a');c2=call('search_logs',{'query':'workerEnv','k':1},'b')
    r,_=scripted(agent,[[c],[c2]],max_rounds=2)
    assert r['trace'][1]['status']=='rejected'

def test_duplicate_wire_id_stops(agent):
    r,_=scripted(agent,[[call('search_logs',{'query':'x','k':1},'a')]],max_rounds=3)
    assert r['status']=='protocol_error' and len(r['trace'])==1

def test_round_budget(agent):
    batches=[[call('search_logs',{'query':str(i),'k':1},str(i))] for i in range(10)]
    r,_=scripted(agent,batches,max_rounds=2)
    assert r['status']=='round_limit' and r['usage']['api_calls']==2

def test_call_budget(agent):
    r,_=scripted(agent,[[call('search_logs',{'query':str(i),'k':1},str(i)) for i in range(5)]],max_calls=2)
    assert r['status']=='tool_limit' and len(r['trace'])==2

def test_token_budget(agent):
    r,_=scripted(agent,[[call('search_logs',{'query':'x','k':1})]],max_tokens=100)
    assert r['status']=='token_limit' and r['usage']['api_calls']==1

def test_timeout_budget_before_request(agent):
    r,requests=scripted(agent,[[call('search_logs',{'query':'x','k':1})]],timeout=0)
    assert r['status']=='time_limit' and not requests

def test_provider_error_withheld(agent):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401,text='unit-test-secret'))) as c:
        r=ToolAgent(agent,ModelConfig('unit-test-secret','test'),c).run('q')
    assert r['status']=='provider_error' and r['usage']['total_tokens'] is None
    assert 'unit-test-secret' not in json.dumps(r)

def test_missing_usage_is_unknown(agent):
    def handler(req):return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[call('finish_investigation',finish([],False))]}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:r=ToolAgent(agent,ModelConfig('test-secret','test'),c).run('q')
    assert r['usage']['prompt_tokens'] is None and r['usage']['cost_usd'] is None

def test_missing_config_no_network(agent):
    r=ToolAgent(agent,ModelConfig('','')).run('q')
    assert r['status']=='configuration_required' and r['usage']['api_calls']==0

def test_json_injection_is_literal(agent):
    r,_=scripted(agent,[[call('log_stats',{'contains':"'; DROP TABLE logs; --",'level':None,'start':1,'end':2000},'a')],[call('finish_investigation',finish(['S:1']),'b')]])
    assert r['statistics'][0]['total']==0 and len(agent.tools.all())==2000

def test_config_is_memory_only_and_not_echoed():
    with TestClient(app) as c:
        key='unit-test-local-secret'
        r=c.post('/config',json={'api_key':key,'model':'test'})
        assert r.status_code==200 and key not in r.text
        assert key not in c.get('/config').text
        assert c.get('/config').headers['cache-control']=='no-store'
        bad=c.post('/config',json={'api_key':key,'model':'bad model'})
        assert bad.status_code==422 and key not in bad.text
        assert c.post('/config',headers={'origin':'https://malicious.invalid'},json={'api_key':key,'model':'test'}).status_code==403
        c.delete('/config');assert not c.get('/config').json()['configured']
        assert c.post('/diagnose',json={'question':'test','mode':'agent'}).json()['status']=='configuration_required'


def test_truncated_final_json_is_rejected_then_recovered(agent):
    broken={'id':'b','type':'function','function':{'name':'finish_investigation','arguments':'{"answerable":true,"summary":"cut'}}
    r,reqs=scripted(agent,[[call('log_stats',{'contains':'','level':None,'start':1,'end':2000},'a')],[broken],[call('finish_investigation',finish(['S:1']),'c')]])
    assert r['status']=='completed' and r['trace'][1]['status']=='rejected'
    assert r['trace'][1]['result']['issues'][0]['type']=='json_invalid'
    assert 'json_invalid' in reqs[2]['messages'][-1]['content']

def test_fixed_generator_uses_same_memory_config(monkeypatch):
    from opspilot.llm import generate
    monkeypatch.setenv('OPSPILOT_API_KEY','wrong-other-environment')
    def handler(req):
        assert req.headers['Authorization']=='Bearer local-memory-test-secret'
        assert str(req.url)=='https://api.deepseek.com/chat/completions'
        assert json.loads(req.content)['thinking']=={'type':'disabled'}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'Observation','hypotheses':[],'next_checks':[],'citations':['L1','D:test']})}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        answer,usage=generate('q',[{'id':'L1'}],[{'id':'D:test'}],c,config=ModelConfig('local-memory-test-secret','test'))
    assert usage['prompt_tokens']==100 and usage['cost_usd'] is None
