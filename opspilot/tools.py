"""Fixed, parameterized SELECT tools. No shell, user SQL or arbitrary paths."""
import sqlite3
from .ingest import DB

class LogTools:
    def __init__(self,db=DB): self.db=db
    def _select(self,sql,args=()):
        with sqlite3.connect(self.db.resolve().as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row
            c.execute('PRAGMA query_only=ON')
            return [dict(x) for x in c.execute(sql,args)]
    def search(self,contains='',level=None,limit=20,start=1,end=2000):
        if not 1<=limit<=100 or not 1<=start<=end<=2000: raise ValueError('Invalid bounds')
        if level not in (None,'error','notice','warn','info','debug'): raise ValueError('Invalid level')
        # Escape LIKE metacharacters so user input is a literal substring.
        q=contains.replace('!','!!').replace('%','!%').replace('_','!_')
        return self._select("SELECT * FROM logs WHERE message LIKE ? ESCAPE '!' AND (? IS NULL OR level=?) AND line BETWEEN ? AND ? ORDER BY line LIMIT ?",('%'+q+'%',level,level,start,end,limit))
    def context(self,line,radius=2):
        if not 1<=line<=2000 or not 0<=radius<=5: raise ValueError('Invalid context bounds')
        return self._select('SELECT * FROM logs WHERE line BETWEEN ? AND ? ORDER BY line',(max(1,line-radius),line+radius))
    def stats(self):
        return self._select('SELECT level,COUNT(*) AS count FROM logs GROUP BY level ORDER BY level')
    def all(self): return self._select('SELECT * FROM logs ORDER BY line')
