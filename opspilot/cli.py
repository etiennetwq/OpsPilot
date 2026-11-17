import argparse,json
from .ingest import ingest

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('ingest'); q=sub.add_parser('diagnose'); q.add_argument('question'); q.add_argument('--mode',choices=['keyword','vector','hybrid'],default='hybrid'); q.add_argument('--llm',action='store_true'); q.add_argument('--rerank',action='store_true')
    sub.add_parser('evaluate'); sub.add_parser('serve')
    args=p.parse_args()
    if args.command=='ingest': print(json.dumps(ingest(),indent=2))
    elif args.command=='diagnose':
        from .agent import Agent
        print(json.dumps(Agent().diagnose(args.question,args.mode,args.rerank,args.llm),ensure_ascii=False,indent=2))
    elif args.command=='evaluate':
        from .evaluate import evaluate
        evaluate()
    else:
        import uvicorn
        uvicorn.run('opspilot.api:app',host='127.0.0.1',port=8765)
if __name__=='__main__': main()
