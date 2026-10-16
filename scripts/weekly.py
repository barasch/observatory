"""Private, resumable weekly editorial work. No model calls or publication writes.

All network work is reserved before execution; failed work retains its charge.
Use a directory OUTSIDE the public checkout for run files and source caches.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {'registry':40, 'search':32, 'metadata':16, 'assess':100, 'verify':60, 'checks':20}
STAGES = ['collect', 'screen', 'assess', 'verify', 'ready']
CATEGORIES = {'law', 'economy', 'government', 'science'}

def now(): return datetime.now(timezone.utc).isoformat()
def words(text): return len(text.split())
def bounded(text, limit): return ' '.join(str(text).split()[:limit])
def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.write('\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def private_path(path):
    path = Path(path).resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('Run data must be outside the public checkout')
    return path

@contextmanager
def transaction(directory):
    directory = private_path(directory); directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / 'run.json'
        state = json.loads(path.read_text())
        yield state
        state['updated_at'] = now()
        write_json(path, state)

def init(directory, start, end, pilot=True, registry=None):
    directory = private_path(directory); directory.mkdir(parents=True, exist_ok=True)
    if date.fromisoformat(start) >= date.fromisoformat(end): raise ValueError('Empty window')
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (directory / 'run.json').exists(): raise ValueError('Run already exists; resume it')
        state = {'schema_version':1,'protocol':'weekly-v1','window':{'start':start,'end':end},
          'run_id':start+'_'+end, 'pilot':pilot,'stage':'collect','created_at':now(),
          'limits':LIMITS, 'actions':[], 'candidates':[], 'excluded':[], 'assessments':{},
          'verifications':{}, 'exposures':[], 'coverage':{},'validation_attempts':0,
          'publication':{'state':'unpublished'}, 'registry_snapshot':registry or [],
          'screening_words':0, 'checkpoints':[]}
        write_json(directory / 'run.json', state)
    return state['run_id']

def reserve(directory, bucket, target, work_id=None):
    with transaction(directory) as s:
        if bucket not in LIMITS: raise ValueError('Unknown allowance')
        allowed = {'collect':{'registry','search','metadata','checks'},'screen':{'checks'},
          'assess':{'assess','checks'},'verify':{'verify','checks'},'ready':{'checks'}}
        if bucket not in allowed[s['stage']]: raise ValueError('Action not permitted in this stage')
        if sum(a['bucket']==bucket for a in s['actions']) >= s['limits'][bucket]:
            raise ValueError('Exhausted '+bucket+' allowance')
        if bucket in ('assess','verify'):
            selected = {c['id'] for c in s['candidates'] if c.get('decision')=='advance'}
            if work_id not in selected: raise ValueError('Work was not screened in')
            if bucket=='verify' and work_id not in s['assessments']: raise ValueError('Work not assessed')
            distinct = {a['work_id'] for a in s['actions'] if a['bucket']==bucket}
            if work_id not in distinct and len(distinct)>= (50 if bucket=='assess' else 25):
                raise ValueError('Distinct-work allowance exceeded')
        ticket = len(s['actions'])+1
        s['actions'].append({'ticket':ticket,'bucket':bucket,'target':target,
          'work_id':work_id,'status':'reserved','at':now()})
    return ticket

def finish(directory, ticket, status, **metadata):
    with transaction(directory) as s:
        a = s['actions'][ticket-1]
        if a['status']!='reserved': raise ValueError('Ticket already completed')
        a.update(status=status, finished_at=now(), **metadata)

def canonical(url):
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password:
        raise ValueError('Expected HTTPS URL without credentials')
    query=[(k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ('fbclid','gclid')]
    return urlunsplit((p.scheme,p.netloc.lower(),p.path.rstrip('/'),urlencode(query),''))

def add_candidates(directory, records):
    with transaction(directory) as s:
        if s['stage']!='collect': raise ValueError('Collection is closed')
        known={c['work_id']:c for c in s['candidates']}
        for raw in records:
            # Whitelist: accidental abstract/body/profile fields never reach screening.
            c={k:raw[k] for k in ('title','date','source','themes','url','route','category','date_provenance',
                 'work_id','version_id','revision_of','first_release','source_type') if k in raw}
            for field in ('title','source','url','route','category'):
                if not c.get(field): raise ValueError('Missing '+field)
            if c['category'] not in CATEGORIES: raise ValueError('Unknown category')
            c['url']=canonical(c['url'])
            c['work_id']=c.get('work_id') or c['url']
            c['id']=hashlib.sha256((c['work_id']+'|'+str(c.get('version_id',''))).encode()).hexdigest()[:16]
            if c.get('date'):
                date.fromisoformat(c['date'])
                if not s['window']['start'] <= c['date'] < s['window']['end']:
                    s['excluded'].append({'id':c['id'],'route':c['route'],'reason':'outside-window'}); continue
            else: c['date']=None
            if c['work_id'] in known:
                previous=known[c['work_id']]
                previous.setdefault('also_found_by',[]).append(c['route']); continue
            if len(s['candidates'])>=1000:
                s['coverage']['record_cap_reached']=True
                s['excluded'].append({'id':c['id'],'route':c['route'],'reason':'record-cap'}); continue
            c['title']=bounded(c['title'],30); c['source']=bounded(c['source'],8)
            c['themes']=bounded(' '.join(c.get('themes',[])) if isinstance(c.get('themes'),list) else c.get('themes',''),10)
            c['screen_text']=bounded(f"{c['title']} | {c['date'] or 'date unknown'} | {c['source']} | {c['themes']}",50)
            c['date_status']='provisional' if not c.get('date_provenance') else 'listed'
            s['candidates'].append(c); known[c['work_id']]=c

def transition(directory, target):
    with transaction(directory) as s:
        if STAGES.index(target)!=STAGES.index(s['stage'])+1: raise ValueError('Invalid stage transition')
        if any(a['status']=='reserved' for a in s['actions']): raise ValueError('Resolve pending actions before proceeding')
        if target=='screen':
            if not s['candidates']: raise ValueError('Empty candidate pool')
            s['screening_words']=sum(words(c['screen_text']) for c in s['candidates'])
        if target=='assess':
            if any('decision' not in c for c in s['candidates']): raise ValueError('Screen every candidate')
            if not 1<=sum(c['decision']=='advance' for c in s['candidates'])<=50: raise ValueError('Need 1–50 selected works')
        if target=='verify':
            selected={c['id'] for c in s['candidates'] if c.get('decision')=='advance'}
            if not selected <= s['assessments'].keys(): raise ValueError('Record every assessment, including failures')
        if target=='ready':
            if not s['verifications']: raise ValueError('No verification records')
        s['checkpoints'].append({'stage':s['stage'],'at':now(),'actions':len(s['actions'])})
        s['stage']=target

def screen(directory, decisions):
    with transaction(directory) as s:
        if s['stage']!='screen': raise ValueError('Not screening')
        ids={c['id'] for c in s['candidates']}
        if set(decisions)!=ids: raise ValueError('Supply exactly one decision for each candidate')
        codes={'advance','lower-relevance','routine','duplicate','uncertain'}
        if sum(d['decision']=='advance' for d in decisions.values())>50: raise ValueError('At most 50 advance')
        for c in s['candidates']:
            d=decisions[c['id']]
            if d['decision'] not in codes: raise ValueError('Unknown decision')
            c['decision']=d['decision']
            if d['decision']=='advance':
                if not d.get('reason'): raise ValueError('Advancement needs a short reason')
                c['reason']=bounded(d['reason'],30)

def expose(directory, bucket, work_id, text, locator):
    if bucket not in ('assess','verify'): raise ValueError('Excerpts only in reading stages')
    with transaction(directory) as s:
        if s['stage']!=bucket: raise ValueError('Wrong reading stage')
        if not any(a['work_id']==work_id and a['bucket']==bucket and a['status']=='ok' for a in s['actions']):
            raise ValueError('No successful charged retrieval for work')
        prior=[e for e in s['exposures'] if e['bucket']==bucket]
        remaining=min((600 if bucket=='assess' else 1500)-sum(e['words'] for e in prior if e['work_id']==work_id),
                      30000-sum(e['words'] for e in prior))
        if remaining<=0: raise ValueError('Text allowance exhausted')
        excerpt=bounded(text,remaining)
        s['exposures'].append({'bucket':bucket,'work_id':work_id,'words':words(excerpt),'locator':locator,'at':now()})
        # Short excerpt stored privately for audit; no full text in manifest.
        key=len(s['exposures'])
        write_json(private_path(directory)/'excerpts'/f'{key:04}.json',{'work_id':work_id,'locator':locator,'text':excerpt})
    return excerpt

def assessment(directory, work_id, record, verify=False):
    with transaction(directory) as s:
        stage='verify' if verify else 'assess'
        if s['stage']!=stage: raise ValueError('Wrong stage')
        selected={c['id'] for c in s['candidates'] if c.get('decision')=='advance'}
        if work_id not in selected: raise ValueError('Unknown selected work')
        if verify and work_id not in s['assessments']: raise ValueError('Not assessed')
        required={'status','reason'} if record.get('status')=='access-failed' else (
          {'status','evidence','scope','finding','limitations','source_characteristics','decision'} if not verify else
          {'status','passages','release_date','first_release_date','document_url','claims_checked'})
        if not required <= record.keys(): raise ValueError('Incomplete assessment record')
        if record.get('status')!='access-failed' and not any(e['bucket']==stage and e['work_id']==work_id for e in s['exposures']):
            raise ValueError('Cannot assess without inspected text')
        if words(json.dumps(record))>350: raise ValueError('Assessment is too long')
        s['verifications' if verify else 'assessments'][work_id]=record

class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'): self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip(): self.parts.append(data.strip())
def plain(raw):
    p=Text(); p.feed(raw); return '\n'.join(p.parts)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): return None

def fetch(directory,bucket,url,work_id=None):
    """Charged redirects, bounded bytes, explicit failure; returns private cache path."""
    opener=urllib.request.build_opener(NoRedirect)
    for _ in range(4):
        canonical(url)
        ticket=reserve(directory,bucket,url,work_id)
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'ObservatoryResearch/1.0 (primary-source reading)'})
            with opener.open(req,timeout=25) as res:
                data=res.read(12*1024*1024+1)
                if len(data)>12*1024*1024: raise ValueError('Response exceeds 12 MiB')
                content_type=res.headers.get('content-type','')
            cache=private_path(directory)/'cache'/f'{ticket:04}.bin'
            cache.parent.mkdir(exist_ok=True); cache.write_bytes(data)
            finish(directory,ticket,'ok',bytes=len(data),content_type=content_type,cache=cache.name)
            return cache
        except urllib.error.HTTPError as e:
            finish(directory,ticket,'failed',error=f'HTTP {e.code}')
            if e.code in (301,302,303,307,308) and e.headers.get('Location'):
                url=urljoin(url,e.headers['Location']); continue
            raise
        except Exception as e:
            finish(directory,ticket,'failed',error=type(e).__name__+': '+str(e)[:300]); raise
    raise ValueError('Too many redirects')

def extract(path):
    raw=Path(path).read_bytes()
    if raw.startswith(b'%PDF'):
        from pypdf import PdfReader
        return '\n'.join(f'[Page {i+1}]\n{p.extract_text() or ""}' for i,p in enumerate(PdfReader(io.BytesIO(raw)).pages))
    return plain(raw.decode('utf-8',errors='replace'))

def feed_records(raw,source):
    root=ET.fromstring(raw)
    def tag(e): return e.tag.split('}')[-1]
    def value(e,names):
        return next((''.join(c.itertext()).strip() for c in e if tag(c) in names),'')
    def listed_date(rawdate, description=''):
        for value_ in (rawdate, plain(description)):
            if not value_: continue
            match=re.search(r'(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)',value_)
            if match:
                try: return date.fromisoformat(match.group(0)).isoformat()
                except ValueError: pass
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(value_).date().isoformat()
            except (ValueError,TypeError,OverflowError): pass
            match=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b',value_)
            if match:
                try: return datetime.strptime(match.group(0),'%B %d, %Y').date().isoformat()
                except ValueError: pass
        return None
    records=[]
    for e in root.iter():
        if tag(e) not in ('entry','item'): continue
        title=value(e,{'title'}); link=value(e,{'link'})
        if not link:
            link=next((c.attrib.get('href','') for c in e if tag(c)=='link' and c.attrib.get('rel','alternate')=='alternate'),'')
        rawdate=value(e,{'published','pubDate','date','updated'})
        published=listed_date(rawdate,value(e,{'description','summary'}))
        themes=[c.attrib.get('term') or (c.text or '') for c in e if tag(c)=='category'][:5]
        if title and link.startswith('https://'):
            records.append({'title':plain(title),'date':published,'source':source['publisher'],'url':link,
              'themes':themes,'route':source['id'],'category':source['category'],'date_provenance':source['endpoint'],
              'source_type':source['source_type']})
    return records

def audit(directory):
    s=json.loads((private_path(directory)/'run.json').read_text())
    return {'run_id':s['run_id'],'stage':s['stage'],'pilot':s['pilot'],'window':s['window'],
      'actions':dict(Counter(a['bucket'] for a in s['actions'])),'failed_actions':sum(a['status']=='failed' for a in s['actions']),
      'pending_actions':sum(a['status']=='reserved' for a in s['actions']),
      'candidates':len(s['candidates']),'advanced':sum(c.get('decision')=='advance' for c in s['candidates']),
      'assessed':len(s['assessments']),'verified':len(s['verifications']),
      'screening_words':s['screening_words'],'reading_words':dict(Counter({b:sum(e['words'] for e in s['exposures'] if e['bucket']==b) for b in ('assess','verify')})),
      'by_category':dict(Counter(c['category'] for c in s['candidates'])),
      'by_source':dict(Counter(c['source'] for c in s['candidates'])),
      'by_route':dict(Counter(c['route'] for c in s['candidates'])), 'coverage':s['coverage']}

def publication_gate(directory,edition,feedback):
    from editions import validate_edition
    with transaction(directory) as s:
        if s['stage']!='ready': raise ValueError('Not ready')
        if s['validation_attempts']>=2: raise ValueError('Validation attempts exhausted')
        s['validation_attempts']+=1
    validate_edition(edition)
    s=json.loads((private_path(directory)/'run.json').read_text())
    if edition.get('window')!=s['window']: raise ValueError('Wrong window')
    for item in edition['items']:
        matches=[v for v in s['verifications'].values() if v.get('document_url')==item['document_url'] and v.get('status')=='verified']
        if not matches: raise ValueError('Missing verified primary document')
        v=matches[0]
        if v['release_date']!=item['published']: raise ValueError('Unverified release date')
        if item.get('revision_of'):
            if feedback.get('items',{}).get(item['revision_of']['item_id'],{}).get('vote')!='up': raise ValueError('Revision needs current original upvote')
        elif v['first_release_date']!=item['published']: raise ValueError('Not first release')
    holds=[]
    if s['pilot']: holds.append('pilot')
    if not s['coverage'].get('complete'):
        holds.append('technical-coverage' if s['coverage'].get('shortfall')=='technical' else 'coverage-incomplete')
    elif s['coverage'].get('shortfall')=='technical':
        holds.append('technical-coverage')
    return {'valid':True,'may_publish':not holds,'items':len(edition['items']),'hold_reasons':holds}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('run'); p.add_argument('command',choices=['init','audit','advance','add','screen','fetch','reserve','finish','excerpt','assess','verify'])
    p.add_argument('--start');p.add_argument('--end');p.add_argument('--input');p.add_argument('--stage');p.add_argument('--bucket');p.add_argument('--url');p.add_argument('--id');p.add_argument('--ticket',type=int);p.add_argument('--status',default='ok');p.add_argument('--offset',type=int,default=0);p.add_argument('--words',type=int,default=600)
    a=p.parse_args()
    if a.command=='init': result=init(a.run,a.start,a.end)
    elif a.command=='audit': result=audit(a.run)
    elif a.command=='advance': result=transition(a.run,a.stage)
    elif a.command in ('add','screen'): result=(add_candidates if a.command=='add' else screen)(a.run,json.loads(Path(a.input).read_text()))
    elif a.command=='reserve': result=reserve(a.run,a.bucket,a.url,a.id)
    elif a.command=='finish': result=finish(a.run,a.ticket,a.status)
    elif a.command=='fetch': result=str(fetch(a.run,a.bucket,a.url,a.id))
    elif a.command=='excerpt':
        text=extract(a.input).split();result=expose(a.run,a.bucket,a.id,' '.join(text[a.offset:a.offset+a.words]),f'{Path(a.input).name} words {a.offset}:{a.offset+a.words}')
    else: result=assessment(a.run,a.id,json.loads(Path(a.input).read_text()),a.command=='verify')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
