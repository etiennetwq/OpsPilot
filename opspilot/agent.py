"""Bounded evidence workflow agent, not autonomous remediation."""
import json, os, time
from .ingest import ROOT,DB,ingest,redact
from .tools import LogTools
from .retrieval import Retriever
from .llm import generate

class Agent:
    def __init__(self,db=DB):
        if not db.exists(): ingest(db)
        self.tools=LogTools(db)
        config={'embedding':os.getenv('OPSPILOT_EMBEDDING','lsa'),'model_path':os.getenv('OPSPILOT_EMBEDDING_MODEL')}
        self.logs=Retriever([{'id':f"L{r['line']}",'line':r['line'],'time':r['time'],'level':r['level'],'text':r['message']} for r in self.tools.all()],**config)
        self.docs=Retriever(json.loads((ROOT/'kb/documents.json').read_text(encoding='utf-8')),**config)
    def diagnose(self,question,mode='hybrid',rerank=False,llm=False,k=5,config=None):
        start=time.perf_counter(); question=redact(question)
        logs=self.logs.search(question,mode,k,rerank); docs=self.docs.search(question,mode,3,rerank)
        trace=[{'tool':'retrieve_logs','mode':mode,'ids':[x['id'] for x in logs]},{'tool':'retrieve_docs','ids':[x['id'] for x in docs]}]
        # Require lexical overlap as well as ranking: RRF scores alone are not confidence.
        bm,vec=self.logs.scores(question)
        insufficient=not logs or float(max(bm))<.5 or not docs
        context=[]; stats=[]
        if mode=='hybrid' and not insufficient:
            context=self.tools.context(logs[0]['line']); stats=self.tools.stats()
            trace += [{'tool':'context','line':logs[0]['line'],'radius':2},{'tool':'stats','result':stats}]
        if mode=='keyword' and not insufficient:
            stats=self.tools.stats(); trace.append({'tool':'stats','result':stats})
        usage={'prompt_tokens':0,'completion_tokens':0,'cost_usd':0.0}; error=None
        if insufficient:
            answer={'summary':'证据不足，无法从当前 Apache 样本确认该现象。','hypotheses':[],'next_checks':['提供相应系统、时间窗口及已脱敏日志。'],'citations':[]}
        else:
            answer={'summary':f"日志 {logs[0]['id']} 在 {logs[0]['time']} 记录：{logs[0]['text']}。这是可观察现象，根因尚未确认。",'hypotheses':['需结合版本、配置、访问日志和相邻时间记录验证；单一事件模板不能证明因果关系。'],'next_checks':['只读核对相关配置和组件版本。','对照相邻日志、错误级别计数及文档中的适用版本。',docs[0]['text']],'citations':[x['id'] for x in logs+docs]}
            if llm:
                try: answer,usage=generate(question,logs,docs,config=config)
                except ValueError as e: error=str(e); usage={'prompt_tokens':None,'completion_tokens':None,'cost_usd':None}
        return {'question':question,'mode':mode,'generator':'llm' if llm and not error and not insufficient else 'offline-extractive','embedding':self.logs.embedding,'insufficient_evidence':insufficient,'answer':answer,'logs':logs,'documents':docs,'context':context,'statistics':stats,'trace':trace,'usage':usage,'provider_error':error,'latency_ms':round((time.perf_counter()-start)*1000,3),'warning':'2005 年 Apache/JK2 样本；仅事件证据检索，无确认根因标签。引用 ID 校验不等于语义正确性校验。'}
