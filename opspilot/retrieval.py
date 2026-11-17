"""BM25 + normalized dense vectors with reciprocal rank fusion."""
import math, re
from collections import Counter
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize

def tokens(text): return re.findall(r'[a-z0-9_]+',text.lower())

class Retriever:
    def __init__(self,items,embedding='lsa',model_path=None):
        self.items=items; self.text=[x['text'] for x in items]
        self.tf=[Counter(tokens(s)) for s in self.text]
        self.length=np.array([sum(c.values()) for c in self.tf]); self.avg=max(float(self.length.mean()),1)
        self.df=Counter(t for c in self.tf for t in c)
        self.embedding=embedding
        if embedding=='sentence-transformer':
            from sentence_transformers import SentenceTransformer
            self.encoder=SentenceTransformer(model_path,local_files_only=True)
            self.vectors=self.encoder.encode(self.text,normalize_embeddings=True)
        elif embedding=='lsa':
            self.encoder=TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),sublinear_tf=True)
            mat=self.encoder.fit_transform(self.text)
            self.svd=TruncatedSVD(n_components=min(64,mat.shape[0]-1,mat.shape[1]-1),random_state=42)
            self.vectors=normalize(self.svd.fit_transform(mat))
        else: raise ValueError('Unknown embedding')
    def scores(self,q):
        bm=np.zeros(len(self.items)); N=len(bm)
        for t in tokens(q):
            freq=np.array([c[t] for c in self.tf]); df=self.df[t]
            bm+=math.log(1+(N-df+.5)/(df+.5))*freq*2.5/(freq+1.5*(.25+.75*self.length/self.avg))
        if self.embedding=='lsa': v=normalize(self.svd.transform(self.encoder.transform([q])))
        else: v=self.encoder.encode([q],normalize_embeddings=True)
        return bm,np.maximum(self.vectors@v[0],0)
    def search(self,q,mode='hybrid',k=5,rerank=False):
        if mode not in ('keyword','vector','hybrid'): raise ValueError('Unknown mode')
        bm,vec=self.scores(q)
        def ranks(a): return sorted(range(len(a)),key=lambda i:(-float(a[i]),i))
        if mode=='keyword': scores=bm
        elif mode=='vector': scores=vec
        else:
            scores=np.zeros(len(bm))
            for a in (bm,vec):
                for rank,i in enumerate(ranks(a),1):
                    if a[i]>1e-9: scores[i]+=1/(60+rank)
        ids=[i for i in ranks(scores) if scores[i]>1e-9][:max(k,20) if rerank else k]
        if rerank:
            # Optional local lexical reranker, explicitly not a neural cross-encoder.
            qt=set(tokens(q))
            ids.sort(key=lambda i:(-len(qt.intersection(self.tf[i]))/max(len(qt),1),-scores[i],i))
        return [dict(self.items[i],score=float(scores[i])) for i in ids[:k]]
