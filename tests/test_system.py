import hashlib,json,sqlite3
import httpx,pytest
from fastapi.testclient import TestClient
from opspilot.agent import Agent
from opspilot.api import app
from opspilot.ingest import ROOT,ingest,redact,verify
from opspilot.tools import LogTools
from opspilot.llm import generate

@pytest.fixture(scope='session')
def agent():
    ingest(); return Agent()

def test_sources(): assert len(verify()['files'])==6

def test_import_idempotent(tmp_path):
    db=tmp_path/'test.sqlite'; a=ingest(db); b=ingest(db)
    assert a==b and a['rows']==2000 and a['rejected_lines']==[]
    assert len(LogTools(db).all())==2000

def test_labels_not_in_runtime(agent):
    assert all('event' not in r and 'EventId' not in r for r in agent.logs.items)

def test_redaction():
    assert redact('1.2.3.4 a@b.com password=secret token=abc')=='<IP> <EMAIL> password=<SECRET> token=<SECRET>'
    assert '<IPV6>' in redact('2001:db8::1234')

@pytest.mark.parametrize('q',["'; DROP TABLE logs; --",'%',r'_',chr(92)])
def test_literal_sql(agent,q):
    before=hashlib.sha256(agent.tools.db.read_bytes()).hexdigest()
    assert all(q.lower() in row['message'].lower() for row in agent.tools.search(q))
    assert hashlib.sha256(agent.tools.db.read_bytes()).hexdigest()==before

@pytest.mark.parametrize('kwargs',[{'limit':101},{'level':'x'},{'start':10,'end':1}])
def test_query_bounds(agent,kwargs):
    with pytest.raises(ValueError): agent.tools.search(**kwargs)

@pytest.mark.parametrize('mode',['keyword','vector','hybrid'])
def test_evidence(agent,mode):
    r=agent.diagnose('Directory index forbidden by rule',mode)
    assert not r['insufficient_evidence']
    assert 'Directory index forbidden' in r['logs'][0]['text']
    assert set(r['answer']['citations']) <= {e['id'] for e in r['logs']+r['documents']}
    assert r['usage']['cost_usd']==0

def test_abstain(agent):
    r=agent.diagnose('Kubernetes pod OOMKilled memory leak')
    assert r['insufficient_evidence'] and r['answer']['citations']==[]

def test_context_and_stats(agent):
    r=agent.diagnose('mod_jk workerEnv error state')
    assert len(r['trace'])==4 and sum(x['count'] for x in r['statistics'])==2000
    assert len(r['context'])<=5

def test_rerank(agent): assert len(agent.logs.search('workerEnv error',rerank=True))==5

def test_api():
    with TestClient(app) as c:
        assert c.get('/health').json()['rows']==2000
        assert c.get('/').status_code==200
        assert c.get('/logs?limit=101').status_code==422
        assert c.get('/logs?start=100&end=1').status_code==422
        assert c.post('/diagnose',json={'question':'abc','mode':'execute'}).status_code==422
        assert c.post('/diagnose',json={'question':'Directory index forbidden'}).status_code==200
        assert c.post('/diagnose',json={'question':'Directory index forbidden','llm':True}).status_code==409

@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv('OPSPILOT_API_KEY','test-not-a-real-secret'); monkeypatch.setenv('OPSPILOT_MODEL','test')

@pytest.mark.parametrize('citations',[['L999','D:a'],[],['L1']])
def test_reject_fabricated_citations(api_env,citations):
    def handler(req):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'test','hypotheses':[],'next_checks':[],'citations':citations})}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ValueError): generate('q',[{'id':'L1'}],[{'id':'D:a'}],c)

def test_api_usage_accounting(api_env,monkeypatch):
    monkeypatch.setenv('OPSPILOT_INPUT_USD_PER_MILLION','1');monkeypatch.setenv('OPSPILOT_OUTPUT_USD_PER_MILLION','2')
    def handler(req):
        data=json.loads(req.content); assert 'untrusted DATA' in data['messages'][0]['content']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'Hypothesis only','hypotheses':[],'next_checks':[],'citations':['L1','D:a']})}}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        a,u=generate('q',[{'id':'L1'}],[{'id':'D:a'}],c)
    assert u['cost_usd']==pytest.approx(.00014)

def test_provider_failure_does_not_leak(api_env):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='private-provider-body'))) as c:
        with pytest.raises(ValueError,match='withheld'): generate('q',[],[],c)
