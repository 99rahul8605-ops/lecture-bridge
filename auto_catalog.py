"""Metadata-only batch/subject/chapter/lecture catalog and Telegram selection queue.

Does not fetch, decrypt, download, or upload video media. The actual lecture
catalog is ingested from the user's normally loaded website UI/API responses.
"""
from __future__ import annotations
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ID_RE = re.compile(r'^[a-f0-9]{24}$', re.I)
DB_PATH = Path(os.getenv('AUTO_CATALOG_DB', './chapter_data/auto_catalog.sqlite3'))
STATES = {'pending', 'awaiting_export', 'delivered', 'failed'}


def _id(value):
    return str(value or '').lower() if ID_RE.fullmatch(str(value or '')) else ''


def _clean(value, size=200):
    value = str(value or '').replace('\x00', '').strip()
    return re.sub(r'\s+', ' ', value)[:size]


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS batches (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, api_id TEXT DEFAULT '',
              updated INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subjects (
              batch_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
              chapters INTEGER DEFAULT 0, lectures INTEGER DEFAULT 0,
              position INTEGER DEFAULT 0, updated INTEGER NOT NULL,
              PRIMARY KEY(batch_id,id)
            );
            CREATE TABLE IF NOT EXISTS chapters (
              batch_id TEXT NOT NULL, subject_id TEXT NOT NULL,
              id TEXT NOT NULL, name TEXT NOT NULL,
              reported INTEGER DEFAULT 0, position INTEGER DEFAULT 0,
              updated INTEGER NOT NULL,
              PRIMARY KEY(batch_id,subject_id,id)
            );
            CREATE TABLE IF NOT EXISTS lectures (
              video_id TEXT PRIMARY KEY, content_id TEXT DEFAULT '',
              batch_id TEXT NOT NULL, subject_id TEXT NOT NULL,
              chapter_id TEXT NOT NULL, title TEXT NOT NULL,
              status TEXT DEFAULT '', video_status TEXT DEFAULT '',
              lecture_date TEXT DEFAULT '', duration TEXT DEFAULT '',
              updated INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lectures_chapter
              ON lectures(batch_id,subject_id,chapter_id);
            CREATE TABLE IF NOT EXISTS sessions (
              uid INTEGER PRIMARY KEY, batch_id TEXT DEFAULT '',
              subject_id TEXT DEFAULT '', chapter_id TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS queue (
              uid INTEGER NOT NULL, video_id TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', updated INTEGER NOT NULL,
              PRIMARY KEY(uid,video_id)
            );
            CREATE TABLE IF NOT EXISTS worker_control (
              uid INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS worker_active (
              uid INTEGER PRIMARY KEY, browser_id TEXT NOT NULL,
              video_id TEXT NOT NULL, lease_until INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS worker_attempts (
              uid INTEGER NOT NULL, video_id TEXT NOT NULL, attempts INTEGER DEFAULT 0,
              last_error TEXT DEFAULT '', PRIMARY KEY(uid,video_id)
            );
        ''')


def _ensure_parent(db, batch_id):
    db.execute('INSERT OR IGNORE INTO batches(id,name,updated) VALUES(?,?,?)',
               (batch_id, 'Unlabelled batch ' + batch_id[:8], int(time.time())))


def ingest(body):
    """Accept only public-facing catalog metadata. Media URLs are ignored."""
    if not isinstance(body, dict):
        raise ValueError('Invalid payload')
    kind = body.get('kind')
    if kind not in ('batches', 'subjects', 'chapters', 'lectures'):
        raise ValueError('Unknown catalog type')
    items = body.get('items')
    if not isinstance(items, list) or len(items) > 250:
        raise ValueError('items must be a list with max 250 entries')
    batch_id = _id(body.get('batchId'))
    subject_id = _id(body.get('subjectId'))
    chapter_id = _id(body.get('chapterId'))
    api_id = _id(body.get('apiBatchId'))
    if kind != 'batches' and not batch_id:
        raise ValueError('Valid batchId needed')
    if kind in ('chapters','lectures') and not subject_id:
        raise ValueError('Valid subjectId needed')
    if kind == 'lectures' and not chapter_id:
        raise ValueError('Valid chapterId needed')
    now = int(time.time())
    count = 0
    with _db() as db:
        if batch_id:
            _ensure_parent(db, batch_id)
            if api_id:
                db.execute('UPDATE batches SET api_id=?,updated=? WHERE id=?', (api_id,now,batch_id))
        if kind == 'batches':
            for item in items:
                if not isinstance(item,dict): continue
                ident,name = _id(item.get('id')), _clean(item.get('name'),160)
                if not ident or not name: continue
                db.execute('INSERT INTO batches(id,name,updated) VALUES(?,?,?) '
                           'ON CONFLICT(id) DO UPDATE SET name=excluded.name,updated=excluded.updated',
                           (ident,name,now))
                count+=1
        elif kind == 'subjects':
            for pos,item in enumerate(items):
                if not isinstance(item,dict): continue
                ident,name = _id(item.get('id')),_clean(item.get('name'),160)
                if not ident or not name: continue
                db.execute('INSERT INTO subjects(batch_id,id,name,chapters,lectures,position,updated) '
                           'VALUES(?,?,?,?,?,?,?) ON CONFLICT(batch_id,id) DO UPDATE SET '
                           'name=excluded.name,chapters=excluded.chapters,lectures=excluded.lectures,'
                           'position=excluded.position,updated=excluded.updated',
                           (batch_id,ident,name,_nonneg(item.get('chapters')),_nonneg(item.get('lectures')),pos,now))
                count+=1
        elif kind == 'chapters':
            for pos,item in enumerate(items):
                if not isinstance(item,dict): continue
                ident,name = _id(item.get('id')),_clean(item.get('name'),160)
                if not ident or not name: continue
                db.execute('INSERT INTO chapters(batch_id,subject_id,id,name,reported,position,updated) '
                           'VALUES(?,?,?,?,?,?,?) ON CONFLICT(batch_id,subject_id,id) DO UPDATE SET '
                           'name=excluded.name,reported=excluded.reported,position=excluded.position,updated=excluded.updated',
                           (batch_id,subject_id,ident,name,_nonneg(item.get('lectures')),pos,now))
                count+=1
        elif kind == 'lectures':
            # The nested data source includes DRM URLs; deliberately not persisted.
            for item in items:
                if not isinstance(item,dict): continue
                vid=_id(item.get('videoId'))
                title=_clean(item.get('title'),220)
                if not vid or not title: continue
                status=_clean(item.get('status'),40).upper()
                vstatus=_clean(item.get('videoStatus'),40)
                db.execute('''INSERT INTO lectures
                           (video_id,content_id,batch_id,subject_id,chapter_id,title,
                            status,video_status,lecture_date,duration,updated)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(video_id) DO UPDATE SET
                            content_id=excluded.content_id,batch_id=excluded.batch_id,
                            subject_id=excluded.subject_id,chapter_id=excluded.chapter_id,
                            title=excluded.title,
                            status=CASE WHEN excluded.status<>'' THEN excluded.status ELSE lectures.status END,
                            video_status=CASE WHEN excluded.video_status<>'' THEN excluded.video_status ELSE lectures.video_status END,
                            lecture_date=CASE WHEN excluded.lecture_date<>'' THEN excluded.lecture_date ELSE lectures.lecture_date END,
                            duration=CASE WHEN excluded.duration<>'' THEN excluded.duration ELSE lectures.duration END,
                            updated=excluded.updated''',
                           (vid,_id(item.get('contentId')),batch_id,subject_id,chapter_id,
                            title,status,vstatus,_clean(item.get('date'),35),_clean(item.get('duration'),20),now))
                count+=1
    return {'saved':count,'kind':kind}


def _nonneg(val):
    try:
        return min(100000, max(0,int(val or 0)))
    except (ValueError,TypeError):
        return 0


def _session(db,uid):
    db.execute('INSERT OR IGNORE INTO sessions(uid) VALUES(?)',(uid,))
    return db.execute('SELECT * FROM sessions WHERE uid=?',(uid,)).fetchone()


def _nth(rows,arg,field='id'):
    value=str(arg or '').strip()
    if value.isdigit():
        n=int(value)
        if 1<=n<=len(rows): return rows[n-1][field]
    ident=_id(value)
    if ident and any(r[field]==ident for r in rows): return ident
    return ''


def _numbers(spec,n):
    s=str(spec or '').strip().lower()
    if s in ('all','sab','new'): return list(range(1,n+1))
    if re.fullmatch(r'\d+',s):
        count=int(s)
        if 1<=count<=n:return list(range(1,count+1))
        raise ValueError(f'1 se {n} tak count bhejo')
    selected=set()
    for part in re.split(r'[\s,]+',s):
        if not part: continue
        if re.fullmatch(r'\d+[-:]\d+',part):
            a,b=(int(v) for v in re.split('[-:]',part))
            if a>b or a<1 or b>n: raise ValueError(f'Invalid range {part}')
            selected.update(range(a,b+1))
        elif re.fullmatch(r'\d+',part):selected.add(int(part))
        else:raise ValueError('Example: 5 / 1-3 / 1,4,7 / all')
    if not selected or min(selected)<1 or max(selected)>n:
        raise ValueError(f'Index 1 se {n} tak hone chahiye')
    return sorted(selected)


def _list(rows, heading, hint, *, name_field='name', extra=None):
    if not rows:return f'📭 {heading}: koi synced item nahi. Website Chrome mein relevant page kholo.'
    lines=[f'📚 {heading} ({len(rows)})']
    for i,r in enumerate(rows[:25],1):
        suffix=extra(r) if extra else ''
        lines.append(f'{i}. {_clean(r[name_field],95)}{suffix}')
    if len(rows)>25:lines.append(f'… aur {len(rows)-25} items (first 25 shown)')
    lines.append(hint)
    return '\n'.join(lines)


def _lectures(db,s):
    # API delivers newest-first; date ascending gives natural lecture sequence.
    return db.execute('''SELECT * FROM lectures WHERE batch_id=? AND subject_id=? AND chapter_id=?
                       ORDER BY lecture_date ASC,video_id ASC''',
                      (s['batch_id'],s['subject_id'],s['chapter_id'])).fetchall()


def command(uid,cmd,arg=''):
    uid=int(uid)
    if uid<=0: raise ValueError('Invalid Telegram user')
    cmd=_clean(cmd,40).lstrip('/').lower()
    arg=_clean(arg,120)
    with _db() as db:
        s=_session(db,uid)
        if cmd=='syncstatus':
            counts=[db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('batches','subjects','chapters','lectures')]
            return ('📡 Catalog: '+ ' | '.join(f'{t}={n}' for t,n in zip(('batches','subjects','chapters','lectures'),counts))+'\n'
                    'Browser website pages/API normally load hone par data sync hota hai.')
        if cmd=='batches':
            rows=db.execute('SELECT * FROM batches ORDER BY updated DESC,name ASC').fetchall()
            return _list(rows,'Batches','/batch 1 = choose')
        if cmd=='batch':
            rows=db.execute('SELECT * FROM batches ORDER BY updated DESC,name ASC').fetchall()
            ident=_nth(rows,arg)
            if not ident:return '❌ /batches dekho, phir /batch 1 bhejo.'
            db.execute("UPDATE sessions SET batch_id=?,subject_id='',chapter_id='' WHERE uid=?",(ident,uid))
            return '✅ Batch selected. Ab /subjects bhejo.'
        if cmd=='subjects':
            if not s['batch_id']:return 'Pehle /batches aur /batch 1 bhejo.'
            rows=db.execute('SELECT * FROM subjects WHERE batch_id=? ORDER BY position,name',(s['batch_id'],)).fetchall()
            return _list(rows,'Subjects','/subject 1 = choose',extra=lambda r:f' ({r["chapters"]} chapters, {r["lectures"]} lectures)')
        if cmd=='subject':
            if not s['batch_id']:return 'Pehle /batch 1 bhejo.'
            rows=db.execute('SELECT * FROM subjects WHERE batch_id=? ORDER BY position,name',(s['batch_id'],)).fetchall()
            ident=_nth(rows,arg)
            if not ident:return '❌ /subjects dekho, phir /subject 1 bhejo.'
            db.execute("UPDATE sessions SET subject_id=?,chapter_id='' WHERE uid=?",(ident,uid))
            return '✅ Subject selected. Ab /autochapters bhejo.'
        if cmd=='autochapters':
            if not s['subject_id']:return 'Pehle /subjects aur /subject 1 bhejo.'
            rows=db.execute('SELECT * FROM chapters WHERE batch_id=? AND subject_id=? ORDER BY position,name',
                            (s['batch_id'],s['subject_id'])).fetchall()
            return _list(rows,'Chapters','/autochapter 1 = choose',extra=lambda r:f' ({r["reported"]} reported)')
        if cmd=='autochapter':
            rows=db.execute('SELECT * FROM chapters WHERE batch_id=? AND subject_id=? ORDER BY position,name',
                            (s['batch_id'],s['subject_id'])).fetchall()
            ident=_nth(rows,arg)
            if not ident:return '❌ /autochapters dekho, phir /autochapter 1 bhejo.'
            db.execute('UPDATE sessions SET chapter_id=? WHERE uid=?',(ident,uid))
            return '✅ Chapter selected. Ab /lectures ya /add all bhejo.'
        if cmd=='lectures':
            if not s['chapter_id']:return 'Pehle /autochapter 1 bhejo.'
            rows=_lectures(db,s)
            return _list(rows,'Lectures','/add 5, /add 1-3, /add all. Ready items hi queue honge.',
                         name_field='title',extra=lambda r: ' ✅' if r['status']=='COMPLETED' and r['video_status'].lower()=='ready' else ' ⏳')
        if cmd=='add':
            if not s['chapter_id']:return 'Pehle /autochapter 1 bhejo.'
            rows=_lectures(db,s)
            if not rows:return '📭 Lecture data nahi mila. Website par chapter kholo aur sync hone do.'
            try:chosen=_numbers(arg,len(rows))
            except ValueError as exc:return '❌ '+str(exc)
            added=skipped=scheduled=0
            now=int(time.time())
            for n in chosen:
                r=rows[n-1]
                if r['status']!='COMPLETED' or r['video_status'].lower()!='ready':
                    scheduled+=1;continue
                cursor=db.execute("INSERT OR IGNORE INTO queue(uid,video_id,status,updated) VALUES(?,?,'pending',?)",
                                  (uid,r['video_id'],now))
                if cursor.rowcount:added+=1
                else:skipped+=1
            return (f'✅ Added {added} | already queued/delivered {skipped} | not Ready {scheduled}\n'
                    'Ab /queueauto ya doosra chapter select karo. /done = queue prepare.')
        if cmd=='autopending':
            return 'yes' if db.execute("SELECT 1 FROM queue WHERE uid=? AND status='pending' LIMIT 1",(uid,)).fetchone() else 'no'
        if cmd=='autostart':
            db.execute('INSERT INTO worker_control(uid,enabled) VALUES(?,1) '
                       'ON CONFLICT(uid) DO UPDATE SET enabled=1',(uid,))
            return '▶️ Worker queue ON. Chrome par Tampermonkey Start Worker once karo.'
        if cmd=='autostop':
            db.execute('INSERT INTO worker_control(uid,enabled) VALUES(?,0) '
                       'ON CONFLICT(uid) DO UPDATE SET enabled=0',(uid,))
            return '⏸ Auto queue paused: naya lecture assign nahi hoga. Current upload may finish.'
        if cmd=='autoretry':
            n=db.execute("UPDATE queue SET status='awaiting_export',updated=? WHERE uid=? AND status='failed'",
                         (int(time.time()),uid)).rowcount
            db.execute('DELETE FROM worker_attempts WHERE uid=?',(uid,))
            return f'🔁 {n} failed lectures requeued. /autostart for worker.'
        if cmd=='autostatus':
            enabled=db.execute('SELECT enabled FROM worker_control WHERE uid=?',(uid,)).fetchone()
            job=db.execute('SELECT a.video_id, l.title, a.lease_until FROM worker_active a '
                           'LEFT JOIN lectures l ON l.video_id=a.video_id WHERE a.uid=?',(uid,)).fetchone()
            status=('ON' if enabled and enabled['enabled'] else 'OFF')
            return f'🤖 Auto worker: {status}' + (f' | Working: {_clean(job["title"],70)}' if job else ' | Idle')
        if cmd=='queueauto':
            rows=db.execute('''SELECT q.status,l.title FROM queue q
                               LEFT JOIN lectures l ON l.video_id=q.video_id
                               WHERE q.uid=? ORDER BY q.updated,l.lecture_date''',(uid,)).fetchall()
            if not rows:return '📭 Auto queue empty. /add all se lectures select karo.'
            counts={k:sum(r['status']==k for r in rows) for k in STATES}
            lines=[f'📦 Auto queue {len(rows)} | pending {counts["pending"]} | waiting {counts["awaiting_export"]} | delivered {counts["delivered"]} | failed {counts["failed"]}']
            lines += [f'{i}. {_clean(r["title"] or "Unknown",65)} [{r["status"]}]' for i,r in enumerate(rows[:20],1)]
            return '\n'.join(lines)+'\n/autostatus • /autostop • /autostart • /autoretry'
        if cmd=='autoclear':
            db.execute('DELETE FROM worker_active WHERE uid=?',(uid,))
            c=db.execute("DELETE FROM queue WHERE uid=? AND status!='delivered'",(uid,))
            return f'🧹 {c.rowcount} pending/waiting items removed. Delivered history safe.'
        if cmd=='done':
            c=db.execute("UPDATE queue SET status='awaiting_export',updated=? WHERE uid=? AND status='pending'",
                         (int(time.time()),uid))
            still=db.execute("SELECT count(*) FROM queue WHERE uid=? AND status='awaiting_export'",(uid,)).fetchone()[0]
            db.execute('INSERT INTO worker_control(uid,enabled) VALUES(?,1) '
                       'ON CONFLICT(uid) DO UPDATE SET enabled=1',(uid,))
            return (f'📦 {c.rowcount} newly prepared; {still} waiting export.\n'
                    '▶ Auto queue ON. Chrome mein Auto Catalog Tampermonkey > Start queue worker '
                    'ek baar ON karo. Existing authorized uploader har lecture par trigger hoga; '
                    'sirf real Telegram success pe delivered mark hoga.')
        return 'Unknown auto command'


def has_pending(uid):
    with _db() as db:
        return db.execute("SELECT 1 FROM queue WHERE uid=? AND status='pending' LIMIT 1",(int(uid),)).fetchone() is not None


def mark_delivered(video_id):
    """Call only *after* an authorized file has really reached Telegram."""
    ident=_id(video_id)
    if not ident:raise ValueError('Bad video ID')
    with _db() as db:
        cur=db.execute("UPDATE queue SET status='delivered',updated=? WHERE video_id=? AND status='awaiting_export'",
                       (int(time.time()),ident))
    return cur.rowcount


# Queue worker only orchestrates existing, separate and permission-checked uploader.
# No media URLs, CDN credentials, license requests or media payloads handled here.
_WORKER_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{8,64}$')
_WORKER_LEASE_SECONDS = 180
_MAX_WORKER_ATTEMPTS = 2


def _worker_owner():
    owner = os.environ.get('OWNER_ID','').strip()
    if not owner.isdecimal() or int(owner) <= 0:
        raise ValueError('OWNER_ID must be a positive integer')
    return int(owner)


def _browser_id(value):
    b = str(value or '')
    if not _WORKER_ID_RE.fullmatch(b):
        raise ValueError('Invalid browser identifier')
    return b


def _assigned_lecture(db, video_id):
    r = db.execute('SELECT batch_id,subject_id,chapter_id,video_id,title FROM lectures WHERE video_id=?',
                   (video_id,)).fetchone()
    if not r:return None
    return dict(r)


def worker_next(browser_id):
    'Atomically lease exactly one queued lecture to one open worker tab.'
    browser_id=_browser_id(browser_id)
    uid=_worker_owner()
    now=int(time.time())
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        enabled=db.execute('SELECT enabled FROM worker_control WHERE uid=?',(uid,)).fetchone()
        active=db.execute('SELECT * FROM worker_active WHERE uid=?',(uid,)).fetchone()
        # Existing upload is allowed to finish even if /autostop disabled new jobs.
        if active and active['lease_until']>now:
            state=db.execute('SELECT status FROM queue WHERE uid=? AND video_id=?',
                             (uid,active['video_id'])).fetchone()
            if state and state['status']=='awaiting_export':
                if active['browser_id']==browser_id:
                    details=_assigned_lecture(db,active['video_id'])
                    if details:return {'state':'assigned','lecture':details}
                return {'state':'busy'}
        if active:
            db.execute('DELETE FROM worker_active WHERE uid=?',(uid,))
        if not enabled or not enabled['enabled']:
            return {'state':'paused'}
        while True:
            r=db.execute('''SELECT q.video_id FROM queue q WHERE q.uid=? AND q.status='awaiting_export'
                            ORDER BY q.rowid ASC LIMIT 1''',(uid,)).fetchone()
            if not r:return {'state':'empty'}
            video_id=r['video_id']
            lecture=_assigned_lecture(db,video_id)
            prior=db.execute('SELECT attempts FROM worker_attempts WHERE uid=? AND video_id=?',
                             (uid,video_id)).fetchone()
            if not lecture or (prior and prior['attempts']>=_MAX_WORKER_ATTEMPTS):
                db.execute("UPDATE queue SET status='failed',updated=? WHERE uid=? AND video_id=?",
                           (now,uid,video_id))
                continue
            db.execute('''INSERT INTO worker_attempts(uid,video_id,attempts) VALUES(?,?,1)
                          ON CONFLICT(uid,video_id) DO UPDATE SET attempts=attempts+1''',
                       (uid,video_id))
            db.execute('INSERT INTO worker_active(uid,browser_id,video_id,lease_until) VALUES(?,?,?,?)',
                       (uid,browser_id,video_id,now+_WORKER_LEASE_SECONDS))
            return {'state':'assigned','lecture':lecture}


def worker_heartbeat(browser_id,video_id):
    browser_id=_browser_id(browser_id)
    ident=_id(video_id)
    if not ident:raise ValueError('Invalid video ID')
    uid=_worker_owner()
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute('''SELECT q.status FROM worker_active a JOIN queue q
                        ON a.uid=q.uid AND a.video_id=q.video_id
                        WHERE a.uid=? AND a.browser_id=? AND a.video_id=?''',
                     (uid,browser_id,ident)).fetchone()
        if not r or r['status']!='awaiting_export':return {'active':False}
        db.execute('UPDATE worker_active SET lease_until=? WHERE uid=?',
                   (int(time.time())+_WORKER_LEASE_SECONDS,uid))
        return {'active':True}


def worker_finish(browser_id,video_id,outcome,error=''):
    browser_id=_browser_id(browser_id)
    ident=_id(video_id)
    if not ident:raise ValueError('Invalid video ID')
    if outcome not in ('delivered','failed'):raise ValueError('Invalid worker outcome')
    uid=_worker_owner()
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute('SELECT * FROM worker_active WHERE uid=? AND browser_id=? AND video_id=?',
                     (uid,browser_id,ident)).fetchone()
        if not r:return {'updated':0,'state':'not_assigned'}
        status='delivered' if outcome=='delivered' else 'failed'
        changed=db.execute("UPDATE queue SET status=?,updated=? WHERE uid=? AND video_id=? AND status='awaiting_export'",
                           (status,int(time.time()),uid,ident)).rowcount
        if status=='failed':
            db.execute('UPDATE worker_attempts SET last_error=? WHERE uid=? AND video_id=?',
                       (_clean(error,250),uid,ident))
        db.execute('DELETE FROM worker_active WHERE uid=?',(uid,))
        return {'updated':changed,'state':status}
