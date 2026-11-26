import sqlite3, json, uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parent / 'data' / 'gray.sqlite'
ACTIVE = {'queued', 'running'}
MAX_QUEUE = 10

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
          input_json TEXT NOT NULL, input_hash TEXT NOT NULL, prompt_version TEXT NOT NULL,
          budget_json TEXT NOT NULL, error_code TEXT, error_message TEXT, retryable INTEGER NOT NULL DEFAULT 0,
          cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          started_at TEXT, completed_at TEXT, result_json TEXT
        );
        CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status,created_at);
        CREATE TABLE IF NOT EXISTS stages (
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, stage TEXT NOT NULL,
          status TEXT NOT NULL, output_json TEXT, validation_json TEXT, duration_ms INTEGER,
          created_at TEXT NOT NULL, PRIMARY KEY(job_id,stage)
        );
        CREATE TABLE IF NOT EXISTS documents (
          job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
          document_id TEXT NOT NULL, metadata_json TEXT NOT NULL, text TEXT NOT NULL,
          PRIMARY KEY(job_id,document_id)
        );
        ''')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute("UPDATE jobs SET status='interrupted',stage='interrupted',updated_at=? WHERE status='running'", (now(),))

def create_job(data, input_hash):
    timestamp=now(); job_id=str(uuid.uuid4())
    with connect() as db:
        existing=db.execute("SELECT id,status,stage,created_at,updated_at FROM jobs WHERE input_hash=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",(input_hash,)).fetchone()
        if existing: return dict(existing),False
        count=db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
        if count>=MAX_QUEUE: raise OverflowError('대기 중인 분석이 많습니다. 완료 후 다시 맡겨 주세요.')
        db.execute("INSERT INTO jobs(id,status,stage,input_json,input_hash,prompt_version,budget_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(job_id,'queued','queued',json.dumps(data,ensure_ascii=False),input_hash,'harness-v1',json.dumps({'llm':3,'search':4,'article_extract':1,'documents':8},ensure_ascii=False),timestamp,timestamp))
    return get_job(job_id),True

def claim_next():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row: return None
        timestamp=now(); db.execute("UPDATE jobs SET status='running',stage='article',started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='queued'",(timestamp,timestamp,row['id']))
        return get_job(row['id'])

def get_job(job_id, include_result=True):
    with connect() as db:
        row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:return None
        result=dict(row); result['input']=json.loads(result.pop('input_json')); result['budget']=json.loads(result.pop('budget_json'))
        raw=result.pop('result_json'); result['result']=json.loads(raw) if raw and include_result else None
        return result

def list_jobs(limit=20):
    with connect() as db:
        rows=db.execute('SELECT id,status,stage,created_at,updated_at,completed_at,error_code,error_message,retryable,result_json FROM jobs ORDER BY created_at DESC LIMIT ?', (min(max(limit,1),50),)).fetchall()
        items=[]
        for row in rows:
            item=dict(row); raw=item.pop('result_json'); item['title']=json.loads(raw).get('title') if raw else None; items.append(item)
        return items

def checkpoint(job_id,stage,output,validation=None,duration_ms=None):
    timestamp=now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        job=db.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job: raise KeyError(job_id)
        if job['cancel_requested']: raise InterruptedError('cancelled')
        db.execute('INSERT OR REPLACE INTO stages(job_id,stage,status,output_json,validation_json,duration_ms,created_at) VALUES(?,?,?,?,?,?,?)',(job_id,stage,'completed',json.dumps(output,ensure_ascii=False),json.dumps(validation,ensure_ascii=False) if validation is not None else None,duration_ms,timestamp))
        db.execute('UPDATE jobs SET stage=?,updated_at=? WHERE id=?',(stage,timestamp,job_id))

def saved_stage(job_id,stage):
    with connect() as db:
        row=db.execute("SELECT output_json FROM stages WHERE job_id=? AND stage=? AND status='completed'",(job_id,stage)).fetchone()
        return json.loads(row[0]) if row else None

def load_documents(job_id):
    with connect() as db:
        rows=db.execute('SELECT metadata_json,text FROM documents WHERE job_id=? ORDER BY document_id',(job_id,)).fetchall()
        return [{**json.loads(row['metadata_json']),'text':row['text']} for row in rows]

def save_documents(job_id, documents):
    with connect() as db:
        db.executemany('INSERT OR REPLACE INTO documents(job_id,document_id,metadata_json,text) VALUES(?,?,?,?)',[(job_id,d['id'],json.dumps({k:v for k,v in d.items() if k not in ('text','content')},ensure_ascii=False),d.get('text',d.get('content',''))) for d in documents])

def finish(job_id,result):
    with connect() as db:
        db.execute("UPDATE jobs SET status='completed',stage='completed',result_json=?,completed_at=?,updated_at=?,error_code=NULL,error_message=NULL WHERE id=? AND cancel_requested=0",(json.dumps(result,ensure_ascii=False),now(),now(),job_id))

def finish_cancel(job_id):
    with connect() as db: db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',updated_at=? WHERE id=?",(now(),job_id))

def fail(job_id,code,message,retryable=False):
    with connect() as db: db.execute("UPDATE jobs SET status='failed',stage='failed',error_code=?,error_message=?,retryable=?,updated_at=? WHERE id=?",(code,message,int(retryable),now(),job_id))

def request_cancel(job_id):
    with connect() as db:
        row=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:return 'missing'
        if row['status']=='queued': db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',updated_at=? WHERE id=?",(now(),job_id)); return 'cancelled'
        if row['status']=='running': db.execute('UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?',(now(),job_id)); return 'requested'
        return 'conflict'

def resume(job_id):
    with connect() as db:
        row=db.execute("SELECT status,retryable FROM jobs WHERE id=?",(job_id,)).fetchone()
        if not row:return 'missing'
        if row['status'] not in ('failed','interrupted') or (row['status']=='failed' and not row['retryable']): return 'conflict'
        db.execute("UPDATE jobs SET status='queued',stage='queued',cancel_requested=0,error_code=NULL,error_message=NULL,updated_at=? WHERE id=?",(now(),job_id)); return 'queued'

def delete_job(job_id):
    with connect() as db:
        row=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:return 'missing'
        if row['status']=='running':return 'conflict'
        db.execute('PRAGMA foreign_keys=ON'); db.execute('DELETE FROM jobs WHERE id=?',(job_id,)); return 'deleted'
