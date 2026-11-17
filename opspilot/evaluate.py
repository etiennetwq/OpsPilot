"""Small, disclosed event-evidence benchmark. Never labels guessed root causes."""
import csv,hashlib,json,os,platform,statistics,time
from datetime import datetime,timezone
from .agent import Agent
from .ingest import ROOT,verify

def evaluate():
    start=time.perf_counter(); agent=Agent(); startup=round((time.perf_counter()-start)*1000,3)
    tasks=json.loads((ROOT/'eval/tasks.json').read_text()); docrels=json.loads((ROOT/'eval/doc_relevance.json').read_text())
    with (ROOT/'data/source/Apache_Apache_2k.log_structured.csv').open(newline='') as f:
        labels={f"L{r['LineId']}":r['EventId'] for r in csv.DictReader(f)}
    live=os.getenv('OPSPILOT_EVAL_LIVE')=='1'; rows=[]; examples=[]
    for mode in ('keyword','vector','hybrid'):
        for task in tasks:
            # Three warm runs offline; live costs one request per task/system.
            results=[agent.diagnose(task['question'],mode,llm=live and mode!='keyword') for _ in range(1 if live else 3)]
            r=results[-1]; expected={i for i,e in labels.items() if e==task['event']}; gold_docs=set(docrels.get(task['event'],[]))
            hits=[x['id'] in expected for x in r['logs']]; dh=[x['id'] in gold_docs for x in r['documents']]
            refs=set(r['answer']['citations']); valid={x['id'] for x in r['logs']+r['documents']}
            supported=sum(x in expected or x in gold_docs for x in refs)
            success=(bool(hits and hits[0]) and any(dh) and any(i in refs for i in expected) and bool(refs&gold_docs) and not r['insufficient_evidence']) if task['answerable'] else r['insufficient_evidence']
            if live and mode!='keyword' and task['answerable'] and r['generator']!='llm': success=False
            rows.append({'mode':mode,'task':task['id'],'answerable':task['answerable'],'hit_at_5':int(any(hits)) if expected else None,'precision_at_5':sum(hits)/5 if expected else None,'recall_at_5':sum(hits)/len(expected) if expected else None,'mrr_at_5':next((1/(i+1) for i,h in enumerate(hits) if h),0) if expected else None,'doc_hit_at_3':int(any(dh)) if expected else None,'citation_validity':len(refs&valid)/len(refs) if refs else None,'citation_relevance':supported/len(refs) if refs else None,'task_success':int(success),'abstained':r['insufficient_evidence'],'latency_ms':statistics.median(x['latency_ms'] for x in results),'usage':r['usage'],'provider_error':r['provider_error'],'generator':r['generator']})
            if mode=='hybrid': examples.append({'task':task['id'],'result':r})
    def mean(rs,k):
        a=[r[k] for r in rs if r[k] is not None]; return round(statistics.mean(a),6) if a else None
    summary=[]
    for mode in ('keyword','vector','hybrid'):
        rs=[r for r in rows if r['mode']==mode]; times=sorted(r['latency_ms'] for r in rs)
        s={'mode':mode,**{k:mean(rs,k) for k in ('hit_at_5','precision_at_5','recall_at_5','mrr_at_5','doc_hit_at_3','citation_validity','citation_relevance','task_success')},'p50_ms':statistics.median(times),'p95_ms':times[int(.95*(len(times)-1))],'prompt_tokens':sum(r['usage']['prompt_tokens'] for r in rs) if all(r['usage']['prompt_tokens'] is not None for r in rs) else None,'completion_tokens':sum(r['usage']['completion_tokens'] for r in rs) if all(r['usage']['completion_tokens'] is not None for r in rs) else None,'cost_usd':sum(r['usage']['cost_usd'] for r in rs) if all(r['usage']['cost_usd'] is not None for r in rs) else None,'provider_failures':sum(bool(r['provider_error']) for r in rs)}
        summary.append(s)
    result={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),'platform':platform.platform(),'source_commit':verify()['commit'],'task_sha256':hashlib.sha256((ROOT/'eval/tasks.json').read_bytes()).hexdigest(),'knowledge_sha256':hashlib.sha256((ROOT/'kb/documents.json').read_bytes()).hexdigest(),'embedding':agent.logs.embedding,'live_llm_requested':live,'cold_index_ms':startup,'offline_repeats':3 if not live else None,'summary':summary,'rows':rows,'limits':['12 author-written positive queries across 6 upstream event labels, 3 author-written negative queries. No incident/root-cause labels.','Same corpus is intentionally searched; queries and relevance mappings were authored after inspecting templates. No independent held-out benchmark; no significance or production accuracy claim.','Dense default is local char TF-IDF/SVD, not pretrained neural semantic embeddings.','All offline systems share an extractive answer formatter. Vector+LLM and hybrid+LLM are NOT measured unless live_llm_requested=true and successful API usage is reported.','Task success means top-1 matching event plus relevant document citations, or correct abstention. It is not incident resolution.','Citation validity checks ID existence; relevance is event/document annotation, not natural-language entailment.','Latency excludes startup; no concurrency load test. Costs use provider token usage and operator-supplied prices; failed call cost may be unknown.']}
    prefix='live' if live else 'offline'
    (ROOT/f'reports/{prefix}_results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (ROOT/f'reports/{prefix}_examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# OpsPilot 实测评测报告','',f"运行时间：{result['timestamp_utc']}；Python {result['python']}；向量：{result['embedding']}。",'',f'索引冷启动：{startup} ms；任务：12 正例 + 3 拒答负例。','', '|方法|日志 Hit@5|P@5|Recall@5|MRR@5|文档 Hit@3|任务成功|P50 ms|P95 ms|', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in summary: lines.append('|'+ '|'.join(str(s[k]) for k in ('mode','hit_at_5','precision_at_5','recall_at_5','mrr_at_5','doc_hit_at_3','task_success','p50_ms','p95_ms'))+'|')
    lines+=['','## 结果解释与限制','']+['- '+s for s in result['limits']]+['','离线调用 Token 为 0，API 费用为 $0；这不是付费 LLM 的成本估计。真实 LLM 的质量、延迟、Token 和费用在未调用时均未测量。','小样本及重复模板可产生很高的检索分数，不能据此声称 Hybrid 优于其他方法。保留逐任务失败记录，不选择性隐藏。','', '## 标注依据','', '日志相关性使用上游 structured.csv 的 EventId；运行时仅导入原始日志，标签不参与索引。文档相关性由本项目作者按主题建立，见 eval/doc_relevance.json；需要独立人工复核。', '', 'Recall@5 分母为该事件全部相关日志行，重复事件很多，因此数值可能很低。统计按 query 宏平均；Hit/MRR/P/R 和文档指标只含正例，任务成功含全部 15 例。P95 为排序样本的 floor(0.95*(n-1)) 分位。']
    lines += ['','## Token / Cost','', '|Method|Prompt tokens|Completion tokens|USD|Provider failures|','|---|---:|---:|---:|---:|']
    for s in summary: lines.append('|'+ '|'.join(str(s[k]) for k in ('mode','prompt_tokens','completion_tokens','cost_usd','provider_failures'))+'|')
    if live:
        lines=[line for line in lines if not line.startswith('离线调用 Token')]
        lines+=['','Live mode: inspect generator, provider_failures and usage. Null means unknown; semantic correctness is not measured.']
    (ROOT/f'reports/{prefix}_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': evaluate()
