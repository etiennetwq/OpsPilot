"""Live comparison via loopback API; credentials stay in the serving process."""
import argparse,csv,hashlib,json,platform,statistics,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUFFIX=' Reply concisely in English: summary under 200 characters, at most two short hypotheses and two short next checks. Do not reproduce whole log records.'

def main():
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8766');a=p.parse_args()
    if not a.base.startswith('http://127.0.0.1:'):raise ValueError('Only local API is supported')
    config=json.load(urllib.request.urlopen(a.base+'/config',timeout=10))
    if not config['configured']:raise SystemExit('Configure your key on the local /setup page first. Do not pass it to this script.')
    tasks=json.loads((ROOT/'eval/agent_tasks.json').read_text(encoding='utf-8'))
    with (ROOT/'data/source/Apache_Apache_2k.log_structured.csv').open(newline='') as f: labels={f"L{r['LineId']}":r['EventId'] for r in csv.DictReader(f)}
    docrels=json.loads((ROOT/'eval/doc_relevance.json').read_text(encoding='utf-8'));results=[]
    def score(task,r,mode):
        refs=set(r['answer']['citations']);allowed={x['id'] for x in r['logs']+r['documents']}
        allowed.update(x['id'] for x in r.get('statistics',[]) if 'id' in x)
        gold={i for i,e in labels.items() if e==task.get('event')};gold_docs=set(docrels.get(task.get('event'),[]))
        relevant=sum(x['id'] in gold for x in r['logs'][:5]);mrr=next((1/(i+1) for i,x in enumerate(r['logs'][:5]) if x['id'] in gold),0)
        ok=not r['insufficient_evidence']
        if task.get('negative'):ok=r['insufficient_evidence'] and (mode!='agent' or r['status']=='abstained')
        else:
            if gold:ok=ok and bool(refs&gold) and bool(refs&gold_docs)
            if task.get('needs_context'):ok=ok and bool(r['context'])
            if task.get('stats_scope'):
                expected=2000 if task['stats_scope']=='all' else sum(e=='E4' for e in labels.values())
                if mode=='agent':ok=ok and any(x['total']==expected and x['id'] in refs and (x['filters']['contains']=='' if expected==2000 else 'directory' in x['filters']['contains'].lower()) for x in r['statistics'])
                else:ok=ok and sum(x.get('count',0) for x in r['statistics'])==expected
        if mode=='agent' and r['status'] not in ('completed','abstained'):ok=False
        if mode in ('vector','hybrid') and not task.get('negative') and r['generator']!='llm':ok=False
        return {'task_success':bool(ok),'hit_at_5':int(relevant>0) if gold else None,'precision_at_5':relevant/5 if gold else None,'recall_at_5':relevant/len(gold) if gold else None,'mrr_at_5':mrr if gold else None,'doc_hit_at_3':int(any(x['id'] in gold_docs for x in r['documents'][:3])) if gold else None,'citation_relevance':sum(i in gold or i in gold_docs or (i.startswith('S:') and bool(task.get('stats_scope'))) for i in refs)/len(refs) if refs else None,'citation_validity':len(refs&allowed)/len(refs) if refs else None,'tool_rejections':sum(t.get('status')=='rejected' for t in r['trace'])}
    for mode in ('keyword','vector','hybrid','agent'):
        for task in tasks:
            req=urllib.request.Request(a.base+'/diagnose',data=json.dumps({'question':task['question']+SUFFIX,'mode':mode,'llm':mode in ('vector','hybrid')}).encode(),headers={'Content-Type':'application/json'})
            r=json.load(urllib.request.urlopen(req,timeout=120));item={'mode':mode,'task':task['id'],'score':score(task,r,mode),'result':r};results.append(item)
            # Checkpoint every result so interruptions and failures stay visible.
            (ROOT/'reports/live_agent_comparison.json').write_text(json.dumps({'in_progress':True,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
            print(mode,task['id'],r.get('status','fixed'),item['score']['task_success'],r['latency_ms'],flush=True)
    summary=[]
    for mode in ('keyword','vector','hybrid','agent'):
        rows=[x for x in results if x['mode']==mode];t=sorted(x['result']['latency_ms'] for x in rows)
        def metric(k):
            v=[x['score'][k] for x in rows if x['score'][k] is not None];return round(statistics.mean(v),6) if v else None
        def tokens(k):
            v=[x['result']['usage'].get(k) for x in rows];return sum(v) if all(isinstance(x,int) for x in v) else None
        summary.append({'mode':mode,'tasks':len(rows),'successes':sum(x['score']['task_success'] for x in rows),'hit_at_5':metric('hit_at_5'),'precision_at_5':metric('precision_at_5'),'recall_at_5':metric('recall_at_5'),'mrr_at_5':metric('mrr_at_5'),'doc_hit_at_3':metric('doc_hit_at_3'),'citation_relevance':metric('citation_relevance'),'citation_validity':metric('citation_validity'),'p50_ms':statistics.median(t),'p95_ms':t[int(.95*(len(t)-1))],'prompt_tokens':tokens('prompt_tokens'),'completion_tokens':tokens('completion_tokens'),'tool_rejections':sum(x['score']['tool_rejections'] for x in rows),'cost_usd':None})
    report={'in_progress':False,'timestamp_utc':datetime.now(timezone.utc).isoformat(),'model_config':config,'python':platform.python_version(),'tasks_sha256':hashlib.sha256((ROOT/'eval/agent_tasks.json').read_bytes()).hexdigest(),'summary':summary,'results':results,'limits':['Six author-written behavior tasks, one run per system/task, no independent holdout or statistical significance.','All model systems use the same configured model and question suffix; context sizes and number of API requests differ. Keyword is offline; vector/hybrid use fixed retrieval then actual LLM; agent uses model-selected tools.','No root-cause labels. Success means labeled event/document citations plus requested context/statistic evidence, or correct abstention. It does not measure incident resolution or natural-language entailment.','Agent retrieved evidence order is first-seen accumulated tool output, not a single globally ranked list; its Hit/P/R/MRR metrics are descriptive and not a fair ranking superiority claim.','Provider-returned prompt/completion tokens are measured; no configured billing price, so dollar cost is unknown. Cache and time-dependent prices prevent equating tokens with a flat exact charge.','Only loopback HTTP, no load testing. Latency includes provider round trips. Earlier exploratory failures are retained separately in live_agent_smoke.json.']}
    (ROOT/'reports/live_agent_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 真实 DeepSeek 工具调用对照评测','',f"UTC: {report['timestamp_utc']}；模型：{config['model']}；每种方法6题、每题1次。",'', '|方法|任务成功|Hit@5|MRR@5|P50 ms|P95 ms|输入 Token|输出 Token|工具拒绝|','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in summary:lines.append('|'+ '|'.join(str(s[k]) for k in ('mode','successes','hit_at_5','mrr_at_5','p50_ms','p95_ms','prompt_tokens','completion_tokens','tool_rejections'))+'|')
    lines+=['','## 任务定义与限制','']+['- '+x for x in report['limits']]+['','all_counts 真值为样本2000行；directory_count_context 真值由上游 E4 计数得到32行。日志相关性用上游 EventId，文档相关性用人工主题映射。失败不隐藏，逐题工具轨迹及结果见 JSON。','费用为未知，不能写成零费用；本报告中的原始 Token 才是实际观测值。','同为6题的比较是有意覆盖工具行为的小样本演示，不能据此推断通用Agent优于固定工作流。']
    (ROOT/'reports/live_agent_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
