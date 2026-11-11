# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Independent blind-review repository for the fixed RealPDF30 benchmark.

The dataset is frozen by SHA-256. Reviewer A and B annotations are immutable
and invisible to the other reviewer. A real third role adjudicates disagreements.
No LLM-generated material is ever treated as a human attestation.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone
from contextlib import closing
import re
from pathlib import Path

VALID_ROLES=('A','B','adjudicator','auditor')
EVIDENCE_LABELS=('valid_experiment','non_experiment','insufficient_evidence','unreadable')
ANSWER_LABELS=('answerable','unanswerable','insufficient_evidence')
METHOD_LABELS=('comparable','not_comparable','insufficient_evidence')
CONFLICT_LABELS=('confirmed_conflict','apparent_conflict_due_to_settings','no_conflict','insufficient_evidence')
SCHEMA_VERSION='realpdf30_blind_review_v1'


def _json(x):
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def _hash_bytes(content):
    return hashlib.sha256(content).hexdigest()


def _sha(x):
    return _hash_bytes(x.encode('utf-8'))


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _load_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as fp:
        return list(csv.DictReader(fp))


def _load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def load_frozen_dataset(root:Path):
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    paper_map={row['paper_id']:row for row in manifest['papers']}
    if len(paper_map)!=30:
        raise ValueError('The evaluation manifest must contain exactly 30 distinct papers')
    sources=['manifest.json','annotations/candidate_evidence.csv','tasks/single_and_cross_paper_qa.jsonl']
    digest=_sha(_json({name:_hash_bytes((root/name).read_bytes()) for name in sources}))
    tasks=[]
    candidate_rows=_load_csv(root/sources[1])
    reference_pages={row['candidate_id']:int(row['page']) for row in candidate_rows}
    for row in candidate_rows:
        paper=paper_map[row['paper_id']]
        if row['pdf_sha256']!=paper['sha256']:
            raise ValueError('Candidate PDF SHA-256 is inconsistent with frozen manifest')
        payload={'kind':'evidence','id':row['candidate_id'],'paper_a':row['paper_id'],
                 'paper_b':'','page':int(row['page']),'split':row.get('split',''),
                 'title':paper['title'],'question':'核查候选实验数值、指标及其对应方法和工况',
                 'candidate':row,'pdf_sha256':row['pdf_sha256']}
        tasks.append(('E:'+row['candidate_id'],'evidence',payload))
    lookup={'single_paper_evidence':'question','cross_paper_method_comparison':'method',
            'potential_conflict_detection':'conflict'}
    for row in _load_jsonl(root/sources[2]):
        kind=lookup[row['task_type']]
        if row['paper_a'] not in paper_map or (row.get('paper_b') and row['paper_b'] not in paper_map):
            raise ValueError('Benchmark question has an unknown paper')
        payload={'kind':kind,'id':row['question_id'],'paper_a':row['paper_a'],
                 'paper_b':row.get('paper_b') or '','page':0,'question':row['question'],
                 'split':row.get('split',''),'candidate':row,
                 'title':paper_map[row['paper_a']]['title'],
                 'suggested_page_a':reference_pages.get(row.get('anchor_a'),0),
                 'suggested_page_b':reference_pages.get(row.get('anchor_b'),0),
                 'pdf_sha256':paper_map[row['paper_a']]['sha256']}
        tasks.append(('Q:'+row['question_id'],kind,payload))
    if len(tasks)!=256 or len(set(t[0] for t in tasks))!=256:
        raise ValueError(f'RealPDF30 must contain 256 unique tasks; found {len(tasks)}')
    return manifest, digest, tasks


def connect(db_file):
    db=sqlite3.connect(db_file,timeout=20,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=20000')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    return db


DDL='''
CREATE TABLE IF NOT EXISTS benchmark_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviewers(
 username TEXT PRIMARY KEY,role TEXT NOT NULL UNIQUE,
 password_salt TEXT NOT NULL,password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
 task_id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,corpus_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS submissions(
 task_id TEXT NOT NULL,reviewer TEXT NOT NULL CHECK(reviewer IN ('A','B')),
 label_json TEXT NOT NULL,label_sha256 TEXT NOT NULL,submitted_at TEXT NOT NULL,
 PRIMARY KEY(task_id,reviewer),FOREIGN KEY(task_id) REFERENCES tasks(task_id));
CREATE TABLE IF NOT EXISTS adjudications(
 task_id TEXT PRIMARY KEY,adjudicator TEXT NOT NULL,
 label_json TEXT NOT NULL,label_sha256 TEXT NOT NULL,decided_at TEXT NOT NULL,
 FOREIGN KEY(task_id) REFERENCES tasks(task_id));
CREATE TABLE IF NOT EXISTS audit(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,
 task_id TEXT NOT NULL DEFAULT '',occurred_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,previous_hash TEXT NOT NULL,entry_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_tasks_update BEFORE UPDATE ON tasks
 BEGIN SELECT RAISE(ABORT,'review task is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_tasks_delete BEFORE DELETE ON tasks
 BEGIN SELECT RAISE(ABORT,'review task is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_submission_update BEFORE UPDATE ON submissions
 BEGIN SELECT RAISE(ABORT,'review submission is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_submission_delete BEFORE DELETE ON submissions
 BEGIN SELECT RAISE(ABORT,'review submission is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_adjudication_update BEFORE UPDATE ON adjudications
 BEGIN SELECT RAISE(ABORT,'review adjudication is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_adjudication_delete BEFORE DELETE ON adjudications
 BEGIN SELECT RAISE(ABORT,'review adjudication is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit
 BEGIN SELECT RAISE(ABORT,'review audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit
 BEGIN SELECT RAISE(ABORT,'review audit is immutable'); END;
'''


def password_digest(password,salt=None):
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode('utf-8'),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return salt.hex(),digest.hex()


def verify_password(db,username,password):
    if username not in VALID_ROLES or not isinstance(password,str) or len(password)>128:
        return False
    row=db.execute('SELECT * FROM reviewers WHERE username=?',(username,)).fetchone()
    if row is None:return False
    _,given=password_digest(password,bytes.fromhex(row['password_salt']))
    return hmac.compare_digest(given,row['password_hash'])


def audit(db,actor,action,task_id,payload):
    last=db.execute('SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
    prior=last[0] if last else '0'*64
    timestamp=_utc()
    payload_hash=_sha(_json(payload))
    signature=_sha(_json([prior,actor,action,task_id,timestamp,payload_hash]))
    db.execute('''INSERT INTO audit(actor,action,task_id,occurred_at,payload_sha256,previous_hash,entry_hash)
                  VALUES (?,?,?,?,?,?,?)''',
               (actor,action,task_id,timestamp,payload_hash,prior,signature))


def check_audit(db):
    prior='0'*64
    count=0
    for row in db.execute('SELECT * FROM audit ORDER BY seq'):
        if row['previous_hash']!=prior:
            raise ValueError('Audit chain predecessor mismatch')
        expected=_sha(_json([prior,row['actor'],row['action'],row['task_id'],
                             row['occurred_at'],row['payload_sha256']]))
        if expected!=row['entry_hash']:
            raise ValueError('Audit chain hash mismatch')
        prior=expected;count+=1
    return {'audit_entries':count,'audit_head':prior}


def initialize(db_file,dataset):
    db_file=Path(db_file).resolve()
    if db_file.exists():
        with closing(connect(db_file)) as db:
            try:
                if db.execute('SELECT 1 FROM reviewers LIMIT 1').fetchone():
                    raise RuntimeError('Review database already initialized; refusing to rotate reviewer identities')
            except sqlite3.OperationalError:
                pass
    manifest,digest,tasks=load_frozen_dataset(Path(dataset))
    db_file.parent.mkdir(parents=True,exist_ok=True)
    if db_file.is_symlink():raise ValueError('Review DB path cannot be a symlink')
    if __import__('os').name!='nt':
        import os
        os.chmod(db_file.parent,0o700)
    credentials={name:secrets.token_urlsafe(18) for name in VALID_ROLES}
    with closing(connect(db_file)) as db:
        db.executescript(DDL)
        db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]:
                raise RuntimeError('Review DB is already populated')
            db.execute('INSERT INTO benchmark_meta VALUES (?,?)',('corpus_digest',digest))
            db.execute('INSERT INTO benchmark_meta VALUES (?,?)',('schema_version',SCHEMA_VERSION))
            db.execute('INSERT INTO benchmark_meta VALUES (?,?)',('created_at',_utc()))
            for username,pw in credentials.items():
                salt,hashed=password_digest(pw)
                db.execute('INSERT INTO reviewers VALUES (?,?,?,?)',
                           (username,username,salt,hashed))
            for task_id,kind,payload in tasks:
                raw=_json(payload)
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',
                           (task_id,kind,raw,_sha(raw),digest))
            audit(db,'system','initialize','',{'corpus_sha256':digest,'tasks':len(tasks)})
            db.commit()
        except Exception:
            db.rollback();raise
    if __import__('os').name!='nt':
        import os
        os.chmod(db_file,0o600)
    return {'database':str(db_file),'corpus_digest':digest,'task_count':len(tasks),
            'credentials_one_time':credentials}


def verify_frozen_corpus(db,dataset):
    _,digest,_=load_frozen_dataset(Path(dataset))
    saved=db.execute("SELECT value FROM benchmark_meta WHERE key='corpus_digest'").fetchone()
    if saved is None or saved[0]!=digest:
        raise ValueError('Frozen dataset changed; refusing to use existing blind reviews')
    return digest


def verified_pdf_path(dataset,paper_id,*,pdf_dir=None):
    root=Path(dataset).resolve()
    paper_map={p['paper_id']:p for p in json.loads((root/'manifest.json').read_text(encoding='utf-8'))['papers']}
    if paper_id not in paper_map:
        raise ValueError('Unknown paper')
    row=paper_map[paper_id]
    # Only the path from the fixed, trusted manifest is accepted.
    folder=Path(pdf_dir).resolve() if pdf_dir is not None else (root/'pdfs').resolve()
    name=Path(row['pdf_file']).name
    path=(folder/name).resolve()
    if not path.is_relative_to(folder) or path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f'PDF unavailable; download official version {row["paper_id"]}{row["version"]}')
    if path.stat().st_size!=row['pdf_bytes'] or _hash_bytes(path.read_bytes())!=row['sha256']:
        raise ValueError('Original PDF SHA-256/size mismatch; review is blocked')
    return path,row


def _norm_label(value):
    return '' if value is None else unicodedata.normalize('NFKC',str(value)).strip()


def validate_label(kind,payload,data):
    if type(data) is not dict:
        raise ValueError('Annotation must be an object')
    result={'label':_norm_label(data.get('label')),
            'reason':_norm_label(data.get('reason')),
            'citation_page':data.get('citation_page',0),
            'citation_quote':_norm_label(data.get('citation_quote')),
            'citation_b_page':data.get('citation_b_page',0),
            'citation_b_quote':_norm_label(data.get('citation_b_quote')),
            'answer':_norm_label(data.get('answer')),
            'metric':_norm_label(data.get('metric')),
            'value':_norm_label(data.get('value')),
            'method':_norm_label(data.get('method')),
            'dataset':_norm_label(data.get('dataset')),
            'setting':_norm_label(data.get('setting')),
            'unit':_norm_label(data.get('unit')),
            'baseline':_norm_label(data.get('baseline')),
            'same_dataset':_norm_label(data.get('same_dataset')),
            'same_metric':_norm_label(data.get('same_metric')),
            'same_setting':_norm_label(data.get('same_setting')),
            'rubric_score':data.get('rubric_score',0),
            'contradiction_label':_norm_label(data.get('contradiction_label'))}
    accepted={'evidence':EVIDENCE_LABELS,'question':ANSWER_LABELS,
              'method':METHOD_LABELS,'conflict':CONFLICT_LABELS}[kind]
    if result['label'] not in accepted:
        raise ValueError('Invalid scientific review label')
    if type(result['citation_page']) is not int or not 0<=result['citation_page']<=500:
        raise ValueError('Physical PDF page number is invalid')
    if not 12<=len(result['reason'])<=1600:
        raise ValueError('The reviewer must provide a 12-1600 character rationale')
    if len(result['answer'])>2000 or len(result['citation_quote'])>1800:
        raise ValueError('Review entry exceeds configured limits')
    if result['label'] not in ('unreadable','insufficient_evidence','unanswerable'):
        if result['citation_page']<1 or len(result['citation_quote'])<8:
            raise ValueError('An affirmative judgement needs a PDF page and 8+ characters of supporting quotation')
    if kind=='evidence' and result['label']=='valid_experiment' and (not result['metric'] or not result['value']):
        raise ValueError('A valid experiment requires the metric and reported value')
    if kind=='question' and (type(result['rubric_score']) is not int or result['rubric_score'] not in (0,1,2,3)):
        raise ValueError('Question quality rubric must be 0-3')
    if kind in ('method','conflict'):
        if kind=='method' and result['contradiction_label'] not in CONFLICT_LABELS:
            raise ValueError('Every pair requires a conflict judgement, even when conditions are not comparable')
        if any(result[name] not in ('yes','no','unknown') for name in ('same_dataset','same_metric','same_setting')):
            raise ValueError('Pair comparisons require same-dataset, metric and setting decisions')
        if result['label'] not in ('insufficient_evidence',) and payload.get('paper_b'):
            if type(result['citation_b_page']) is not int or result['citation_b_page']<1 or len(result['citation_b_quote'])<8:
                raise ValueError('Cross-paper decisions must quote a second verified PDF page')
    if kind!='evidence' and result['label'] in ('answerable','comparable','confirmed_conflict') and len(result['answer'])<6:
        raise ValueError('Provide the supported scientific answer')
    if any(len(result[k])>240 for k in ('metric','method','dataset','setting','unit','baseline','value')):
        raise ValueError('Experiment field is too long')
    return result



def verify_quoted_pdf(dataset,paper_id,page,quote,*,pdf_dir=None):
    """Confirm original bytes, physical page number, and text on that page.

    PDF layout extraction sometimes rearranges columns; accept conservative
    token overlap when a continuous substring cannot be recovered.
    """
    path,meta=verified_pdf_path(dataset,paper_id,pdf_dir=pdf_dir)
    if not quote:return None
    if type(page) is not int or not 1<=page<=meta['pages']:
        raise ValueError('PDF citation page outside the frozen document')
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('PyMuPDF is required to verify PDF quotations') from exc
    with fitz.open(path) as document:
        source=document[page-1].get_text('text',sort=True)
    norm=lambda text:re.sub(r'[^\w\d]+','',text.casefold())
    needle=norm(quote)
    original=norm(source)
    if len(needle)<8:
        raise ValueError('PDF quotation is too short to verify')
    if needle in original:
        return {'page':page,'method':'continuous','pdf_sha256':meta['sha256']}
    # Check that most significant words are present; allow two-column wraps.
    words=[x.casefold() for x in re.findall(r'[A-Za-z][A-Za-z0-9+-]{2,}',quote)]
    tokens=set(re.findall(r'[A-Za-z][A-Za-z0-9+-]{2,}',source.casefold()))
    overlap=sum(word in tokens for word in words)
    if len(words)<5 or overlap/max(len(words),1)<.82:
        raise ValueError('PDF quote does not match the cited physical page')
    return {'page':page,'method':'layout_token_match','pdf_sha256':meta['sha256']}


def _task(db,task_id):
    row=db.execute('SELECT * FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if row is None:raise ValueError('Review task not found')
    payload=json.loads(row['payload'])
    if _sha(row['payload'])!=row['payload_sha256']:
        raise ValueError('Task payload tampering detected')
    return row,payload


def list_tasks(db,role,*,limit=300,kind=None):
    if role not in VALID_ROLES:raise PermissionError('Role denied')
    if kind and kind not in ('evidence','question','method','conflict'):
        raise ValueError('Invalid review task category')
    rows=db.execute('SELECT task_id,kind,payload FROM tasks ORDER BY task_id').fetchall()
    if kind:rows=[r for r in rows if r['kind']==kind]
    items=[]
    for row in rows[:min(limit,256)]:
        first=db.execute('SELECT 1 FROM submissions WHERE task_id=? AND reviewer=?',
                         (row['task_id'],'A')).fetchone() is not None
        second=db.execute('SELECT 1 FROM submissions WHERE task_id=? AND reviewer=?',
                          (row['task_id'],'B')).fetchone() is not None
        decided=db.execute('SELECT 1 FROM adjudications WHERE task_id=?',(row['task_id'],)).fetchone() is not None
        task=json.loads(row['payload'])
        items.append({'id':row['task_id'],'kind':row['kind'],'question':task['question'],
                      'paper_a':task['paper_a'],'paper_b':task['paper_b'],
                      'split':task['split'],
                      # Blind reviewers only see their own progress.
                      'state': ('submitted' if (first if role=='A' else second) else 'pending')
                      if role in ('A','B') else ('resolved' if decided else 'ready' if first and second else 'waiting')})
    return items


def view_task(db,role,task_id):
    if role not in VALID_ROLES:raise PermissionError('Review role denied')
    _,payload=_task(db,task_id)
    # Keep machine-generated candidate answers hidden from blind reviewers.
    if role in ('A','B'):
        payload=dict(payload)
        candidate=payload.get('candidate') or {}
        payload['candidate']={key:candidate[key] for key in ('source_quote','page','anchor_a','anchor_b') if key in candidate}
    result={'task':payload,'task_id':task_id,'role':role}
    if role in ('A','B'):
        submission=db.execute('SELECT label_json,submitted_at FROM submissions WHERE task_id=? AND reviewer=?',
                              (task_id,role)).fetchone()
        result['my_submission']=dict(submission) if submission else None
    else:
        reviews=db.execute('SELECT reviewer,label_json,submitted_at FROM submissions WHERE task_id=?',
                           (task_id,)).fetchall()
        if len(reviews)==2:
            result['reviews']={row['reviewer']:json.loads(row['label_json']) for row in reviews}
        result['ready_for_adjudication']=len(reviews)==2
        if role=='adjudicator':
            decided=db.execute('SELECT label_json FROM adjudications WHERE task_id=?',(task_id,)).fetchone()
            if decided:result['adjudicated_label']=json.loads(decided[0])
    return result


def submit(db,role,task_id,label,*,dataset,pdf_dir=None):
    if role not in ('A','B'):raise PermissionError('Only independent reviewers may submit')
    row,payload=_task(db,task_id)
    normalized=validate_label(row['kind'],payload,label)
    for paper in filter(None,(payload['paper_a'],payload['paper_b'])):
        verified_pdf_path(dataset,paper,pdf_dir=pdf_dir)
    if normalized['citation_quote']:
        verify_quoted_pdf(dataset,payload['paper_a'],normalized['citation_page'],
                          normalized['citation_quote'],pdf_dir=pdf_dir)
    if normalized['citation_b_quote']:
        if not payload['paper_b']:raise ValueError('No second paper for this task')
        verify_quoted_pdf(dataset,payload['paper_b'],normalized['citation_b_page'],
                          normalized['citation_b_quote'],pdf_dir=pdf_dir)
    data=_json(normalized)
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''INSERT INTO submissions VALUES (?,?,?,?,?)''',
                   (task_id,role,data,_sha(data),_utc()))
        audit(db,role,'blind_review_submitted',task_id,{'sha256':_sha(data)})
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        raise ValueError('This reviewer has already submitted an immutable annotation') from None
    except Exception:
        db.rollback();raise
    return {'status':'submitted','task_id':task_id,'reviewer':role,'label_sha256':_sha(data)}


def adjudicate(db,role,task_id,label,*,dataset,pdf_dir=None):
    if role!='adjudicator':raise PermissionError('Adjudicator role required')
    row,payload=_task(db,task_id)
    existing=db.execute('SELECT reviewer,label_json FROM submissions WHERE task_id=?',(task_id,)).fetchall()
    if {r['reviewer'] for r in existing}!={'A','B'}:
        raise ValueError('Two independent completed blind reviews are required before adjudication')
    normalized=validate_label(row['kind'],payload,label)
    for paper in filter(None,(payload['paper_a'],payload['paper_b'])):
        verified_pdf_path(dataset,paper,pdf_dir=pdf_dir)
    if normalized['citation_quote']:
        verify_quoted_pdf(dataset,payload['paper_a'],normalized['citation_page'],
                          normalized['citation_quote'],pdf_dir=pdf_dir)
    if normalized['citation_b_quote']:
        if not payload['paper_b']:raise ValueError('No second paper for this task')
        verify_quoted_pdf(dataset,payload['paper_b'],normalized['citation_b_page'],
                          normalized['citation_b_quote'],pdf_dir=pdf_dir)
    data=_json(normalized)
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('INSERT INTO adjudications VALUES (?,?,?,?,?)',
                   (task_id,role,data,_sha(data),_utc()))
        audit(db,role,'scientific_adjudication',task_id,{'sha256':_sha(data),
            'review_hashes':{r['reviewer']:_sha(r['label_json']) for r in existing}})
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        raise ValueError('Adjudication is immutable; create a formally versioned correction externally') from None
    except Exception:
        db.rollback();raise
    return {'status':'adjudicated','task_id':task_id,'label_sha256':_sha(data)}


def progress(db):
    n=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    a=db.execute("SELECT COUNT(*) FROM submissions WHERE reviewer='A'").fetchone()[0]
    b=db.execute("SELECT COUNT(*) FROM submissions WHERE reviewer='B'").fetchone()[0]
    resolved=db.execute('SELECT COUNT(*) FROM adjudications').fetchone()[0]
    by_type={r['kind']:r['n'] for r in db.execute('SELECT kind,COUNT(*) n FROM tasks GROUP BY kind')}
    return {'tasks':n,'reviewer_A':a,'reviewer_B':b,'adjudicated':resolved,'counts':by_type,
            'official_gold_complete':n==256 and a==n and b==n and resolved==n,
            'scientific_model_accuracy':None}


def export_audit(db,path,*,dataset=None,pdf_dir=None):
    integrity=check_storage_integrity(db)
    result={'schema':SCHEMA_VERSION,'progress':progress(db),'audit':integrity,
            'tasks':[],'is_official_gold':False}
    for row in db.execute('SELECT task_id,kind,payload_sha256 FROM tasks ORDER BY task_id'):
        item={'task_id':row['task_id'],'kind':row['kind'],
              'input_sha256':row['payload_sha256']}
        decided=db.execute('SELECT label_json,label_sha256 FROM adjudications WHERE task_id=?',
                           (row['task_id'],)).fetchone()
        if decided:item['gold']=json.loads(decided[0]);item['gold_sha256']=decided[1]
        result['tasks'].append(item)
    result['is_official_gold']=result['progress']['official_gold_complete']
    valid_pdfs=0
    if dataset:
        corpus=json.loads((Path(dataset)/'manifest.json').read_text(encoding='utf-8'))
        for paper in corpus['papers']:
            try:verified_pdf_path(dataset,paper['paper_id'],pdf_dir=pdf_dir)
            except (FileNotFoundError,ValueError):continue
            valid_pdfs+=1
    result['verified_pdf_count']=valid_pdfs
    if valid_pdfs!=30:result['is_official_gold']=False
    result['scientific_model_accuracy']=None
    result['corpus_sha256']=db.execute("SELECT value FROM benchmark_meta WHERE key='corpus_digest'").fetchone()[0]
    result['snapshot_sha256']=_sha(_json(result))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'path':str(path),'official_gold_complete':result['is_official_gold'],
            'adjudicated':result['progress']['adjudicated'],'verified_pdfs':valid_pdfs,
            'sha256':_hash_bytes(path.read_bytes())}


def check_storage_integrity(db):
    """Validate task bytes and link each annotation to a chained audit event.

    A count-only gate can be bypassed by inserting synthetic rows directly into
    SQLite. An official gold export must have a recorded event for every A/B
    and adjudication, with the exact SHA-256 of that stored label.
    """
    integrity=check_audit(db)
    observed={}
    for event in db.execute('SELECT actor,action,task_id,payload_sha256 FROM audit'):
        key=(event['actor'],event['action'],event['task_id'])
        if key in observed:
            raise ValueError('Duplicated review audit event')
        observed[key]=event['payload_sha256']
    for row in db.execute('SELECT task_id,payload,payload_sha256 FROM tasks'):
        if _sha(row['payload'])!=row['payload_sha256']:
            raise ValueError('Frozen input was modified')
    reviews={}
    for row in db.execute('SELECT task_id,reviewer,label_json,label_sha256 FROM submissions'):
        if _sha(row['label_json'])!=row['label_sha256']:
            raise ValueError('Immutable human review label was modified')
        expected=_sha(_json({'sha256':row['label_sha256']}))
        if observed.get((row['reviewer'],'blind_review_submitted',row['task_id']))!=expected:
            raise ValueError('Human annotation lacks an authentic chained audit event')
        reviews.setdefault(row['task_id'],{})[row['reviewer']]=row['label_sha256']
    for row in db.execute('SELECT task_id,adjudicator,label_json,label_sha256 FROM adjudications'):
        if _sha(row['label_json'])!=row['label_sha256']:
            raise ValueError('Immutable adjudication label was modified')
        pair=reviews.get(row['task_id'],{})
        if set(pair)!={'A','B'}:
            raise ValueError('Adjudication is missing independent A/B review')
        expected=_sha(_json({'sha256':row['label_sha256'],'review_hashes':pair}))
        if observed.get((row['adjudicator'],'scientific_adjudication',row['task_id']))!=expected:
            raise ValueError('Adjudication lacks an authentic chained audit event')
    return integrity


def export_gold_csv(db,dataset,folder,*,pdf_dir=None):
    """Materialize review tables compatible with existing RealPDF30 scorers.

    The output is deliberately withheld until all 256 tasks have independent
    reviews, third-party adjudication, and the 30 PDFs have matching hashes.
    """
    if not progress(db)['official_gold_complete']:
        raise ValueError('Double-blind annotation and adjudication are not complete')
    manifest=json.loads((Path(dataset)/'manifest.json').read_text(encoding='utf-8'))
    for p in manifest['papers']:
        verified_pdf_path(dataset,p['paper_id'],pdf_dir=pdf_dir)
    check_storage_integrity(db)
    destination=Path(folder)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Gold export directory must be empty for immutable reproducibility')
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'annotations').mkdir()
    (destination/'tasks').mkdir()
    def rows(table,task_id,role):
        if role=='gold':
            row=db.execute('SELECT label_json FROM adjudications WHERE task_id=?',(task_id,)).fetchone()
        else:
            row=db.execute('SELECT label_json FROM submissions WHERE task_id=? AND reviewer=?',(task_id,role)).fetchone()
        return json.loads(row[0])
    def save(path,fields,records):
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
            writer.writeheader();writer.writerows(records)
    source={r['task_id']:json.loads(r['payload']) for r in db.execute('SELECT task_id,payload FROM tasks')}
    for role,name in (('A','review_A.csv'),('B','review_B.csv'),('gold','adjudicated_gold.csv')):
        records=[]
        for key,payload in source.items():
            if payload['kind']!='evidence':continue
            v=rows('annotations',key,role)
            candidate=payload['candidate']
            valid=v['label']=='valid_experiment'
            records.append({'candidate_id':payload['id'],'paper_id':payload['paper_a'],
                'pdf_sha256':payload['pdf_sha256'],'page':payload['page'],
                'decision':'accepted' if valid else 'rejected',
                'correct_method':v['method'],'correct_dataset':v['dataset'],
                'correct_metric':v['metric'],'correct_value':v['value'],
                'correct_baseline':v['baseline'],'correct_setting':v['setting'],
                'correct_quote':v['citation_quote'],
                'verified_quote':v['citation_quote'],'verified_page':v['citation_page'],
                'reviewer':role,'notes':v['reason'],
                **({name:'yes' if valid else 'no' for name in ('metric_correct','value_correct','relation_correct','method_correct','dataset_correct')} )})
        fields=['candidate_id','paper_id','pdf_sha256','page','decision','correct_method',
                'correct_dataset','correct_metric','correct_value','correct_baseline','correct_setting',
                'correct_quote','verified_quote','verified_page','reviewer','notes',
                'metric_correct','value_correct','relation_correct','method_correct','dataset_correct']
        save(destination/'annotations'/name,fields,records)
    for role,name in (('A','qa_review_A.csv'),('B','qa_review_B.csv'),('gold','qa_adjudicated.csv')):
        records=[]
        for key,payload in source.items():
            if payload['kind']=='evidence':continue
            v=rows('annotations',key,role)
            records.append({'question_id':payload['id'],'task_type':payload['candidate']['task_type'],
                'paper_a':payload['paper_a'],'paper_b':payload['paper_b'],
                'split':payload['split'],'reviewer':role,'answer_gold':v['answer'] or v['reason'],
                'citation_gold':f"{payload['paper_a']} p.{v['citation_page']}: {v['citation_quote']}",
                'answerable':'yes' if v['label'] in ('answerable','comparable','confirmed_conflict','no_conflict') else 'no',
                'rubric_score':v['rubric_score'],'judgement_reason':v['reason']})
        save(destination/'tasks'/name,['question_id','task_type','paper_a','paper_b','split','reviewer',
             'answer_gold','citation_gold','answerable','rubric_score','judgement_reason'],records)
    pairs=_load_csv(Path(dataset)/'tasks/method_and_conflict_pairs.csv')
    for role,name in (('A','pair_review_A.csv'),('B','pair_review_B.csv'),('gold','pair_adjudicated.csv')):
        records=[]
        for item in pairs:
            pair_id=item['pair_id'];method=rows('annotations','Q:M-'+pair_id,role)
            cf_id='Q:X-'+pair_id
            conflict=rows('annotations',cf_id,role) if cf_id in source else None
            records.append({'pair_id':pair_id,'paper_a':item['paper_a'],'paper_b':item['paper_b'],
                'split':item['split'],'reviewer':role,
                'same_dataset':method['same_dataset'],'same_metric':method['same_metric'],
                'same_setting':method['same_setting'],
                'quantitatively_comparable':'yes' if method['label']=='comparable' else 'no',
                'contradiction_label':conflict['label'] if conflict else method['contradiction_label'],
                'judgement_reason':method['reason'] + (' / '+conflict['reason'] if conflict else '')})
        save(destination/'tasks'/name,['pair_id','paper_a','paper_b','split','reviewer','same_dataset',
             'same_metric','same_setting','quantitatively_comparable','contradiction_label',
             'judgement_reason'],records)
    evidence=export_audit(db,destination/'review_snapshot.json',dataset=dataset,pdf_dir=pdf_dir)
    return {'output':str(destination),'counts':{'evidence':117,'qa':139,'pairs':34},
            'manifest_sha256':evidence['sha256']}
