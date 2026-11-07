"""Mechanical stage-one discovery with semantic route-health reporting.

The collector reads compact listings only.  It does not inspect document bodies,
make substantive judgments, or treat an editorial referrer as evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from lxml import html as lxml_html

import weekly


ADAPTERS = {
    'feed', 'atom', 'arxiv', 'crossref', 'federal_register', 'biorxiv',
    'biorxiv_pubs', 'clinicaltrials', 'world_bank_wds', 'nyc_ibo', 'html_links',
    'bls_calendar',
}
REQUIRED_STREAM_FIELDS = {
    'id', 'publisher', 'name', 'category', 'source_type', 'endpoint',
    'adapter', 'homepage', 'scope', 'status', 'date_semantics',
    'alternative_routes', 'provenance', 'last_check',
}
REQUIRED_REFERRER_FIELDS = {'id', 'name', 'endpoint', 'status', 'provenance'}
REFERRER_ADAPTERS={'feed','pith','x_account'}
ADMISSION_MODES={'all','signals'}
SIGNAL_WEIGHTS={
    'external-referral':3, 'cross-route':2, 'early-citation':2,
    'journal-linked':2, 'precedential':2, 'en-banc':2,
    'phase-3':2, 'phase-4':2, 'large-enrollment':2,
    'research-metadata':1, 'fda-regulated':1, 'data-monitoring-committee':1,
    'multi-site':1,
}


@dataclass
class ParseResult:
    records: list[dict] = field(default_factory=list)
    recognized: bool = True
    complete: bool = True
    window_covered: bool = False
    seen: int = 0
    dated: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


def validate_registry(registry):
    if registry.get('schema_version') not in (1, 2):
        raise ValueError('Unsupported registry schema')
    streams = registry.get('streams')
    if not isinstance(streams, list) or not streams:
        raise ValueError('Registry needs streams')
    ids = set()
    for stream in streams:
        missing = REQUIRED_STREAM_FIELDS - stream.keys()
        if missing: raise ValueError(f"{stream.get('id','stream')}: missing {sorted(missing)}")
        if stream['id'] in ids: raise ValueError('Duplicate route id: '+stream['id'])
        ids.add(stream['id'])
        if stream['adapter'] not in ADAPTERS:
            raise ValueError(f"{stream['id']}: unknown adapter {stream['adapter']}")
        if stream['category'] not in weekly.CATEGORIES:
            raise ValueError(f"{stream['id']}: unknown category")
        admission=stream.get('admission',{'mode':'all'})
        if admission.get('mode') not in ADMISSION_MODES:
            raise ValueError(f"{stream['id']}: unknown admission mode")
        if admission.get('mode')=='signals' and int(admission.get('min_score',0))<1:
            raise ValueError(f"{stream['id']}: signal admission needs a positive min_score")
        weekly.canonical(materialize_endpoint(stream['endpoint'], '2026-01-01', '2026-01-08'))
    referrer_ids=set()
    for referrer in registry.get('referrers', []):
        missing=REQUIRED_REFERRER_FIELDS-referrer.keys()
        if missing: raise ValueError(f"{referrer.get('id','referrer')}: missing {sorted(missing)}")
        if referrer['id'] in ids or referrer['id'] in referrer_ids:
            raise ValueError('Duplicate discovery id: '+referrer['id'])
        referrer_ids.add(referrer['id'])
        adapter=referrer.get('adapter','feed')
        if adapter not in REFERRER_ADAPTERS:
            raise ValueError(f"{referrer['id']}: unknown referrer adapter {adapter}")
        if adapter=='x_account' and not referrer.get('handle'):
            raise ValueError(f"{referrer['id']}: X account needs a handle")
        weekly.canonical(referrer['endpoint'])
    return registry


def materialize_endpoint(template, start, end):
    last_day=(date.fromisoformat(end)-timedelta(days=1)).isoformat()
    return (template.replace('{start}',start).replace('{end}',end).replace('{last_day}',last_day)
      .replace('{start_compact}',start.replace('-','')).replace('{last_day_compact}',last_day.replace('-','')))


def parse_date(value):
    if not value: return None
    if isinstance(value, dict):
        parts=value.get('date-parts') or value.get('date_parts')
        if parts and parts[0]:
            values=list(parts[0])+[1,1]
            try: return date(*map(int,values[:3])).isoformat()
            except (TypeError,ValueError): return None
    text=' '.join(str(value).split())
    match=re.search(r'(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)',text)
    if match:
        try: return date.fromisoformat(match.group(0)).isoformat()
        except ValueError: pass
    try: return parsedate_to_datetime(text).date().isoformat()
    except (TypeError,ValueError,OverflowError): pass
    match=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b',text)
    if match:
        try: return datetime.strptime(match.group(0),'%B %d, %Y').date().isoformat()
        except ValueError: pass
    return None


def in_window(value, start, end):
    return bool(value and start <= value < end)


def compact_record(stream, *, title, url, published=None, themes=(), work_id=None,
                   version_id=None, date_status='listed', date_basis=None, signals=()):
    record={
        'title':' '.join(str(title).split()), 'date':published,
        'source':stream['publisher'], 'url':url, 'themes':list(themes)[:5],
        'route':stream['id'], 'category':stream['category'],
        'date_provenance':stream['endpoint'], 'date_status':date_status,
        'date_precision':'day' if published else 'unknown',
        'date_basis':date_basis or stream['date_semantics'],
        'source_type':stream['source_type'], 'admission_signals':sorted(set(signals)),
    }
    if work_id: record['work_id']=work_id
    if version_id: record['version_id']=version_id
    return record


def coverage_from_dates(result, start, end, exact=False):
    dates=[r.get('date') for r in result.records if r.get('date')]
    result.dated=len(dates)
    if exact:
        result.window_covered=result.complete
    elif dates:
        last_day=(date.fromisoformat(end)-timedelta(days=1)).isoformat()
        result.window_covered=min(dates) <= start and max(dates) >= last_day
    return result


def parse_feed(raw, stream, start, end):
    root=ET.fromstring(raw)
    root_name=root.tag.split('}')[-1].lower()
    if root_name not in ('rss','feed','rdf'):
        return ParseResult(recognized=False,complete=False,seen=0,
          notes=[f'expected RSS or Atom, received {root_name or "unknown root"}'])
    records=weekly.feed_records(raw,stream)
    if stream['id']=='second-circuit-opinions':
        entries=[e for e in root.iter() if e.tag.split('}')[-1] in ('entry','item')]
        for record,entry in zip(records,entries):
            terms=[(c.get('term') or c.text or '').lower() for c in entry if c.tag.split('}')[-1]=='category']
            text=' '.join(''.join(c.itertext()) for c in entry if c.tag.split('}')[-1] in ('summary','description'))
            signals=[]
            if any(term in ('published','precedential') for term in terms): signals.append('precedential')
            if re.search(r'\ben banc\b',text,re.I): signals.append('en-banc')
            record['admission_signals']=signals
    result=ParseResult(records=records,seen=len(records))
    if not records:
        result.notes.append('recognized feed contained no usable linked entries')
    return coverage_from_dates(result,start,end,stream.get('window_filtered',False))


BLS_MAJOR_RELEASES={
    'Employment Situation':'empsit',
    'Consumer Price Index':'cpi',
    'Producer Price Index':'ppi',
    'U.S. Import and Export Price Indexes':'ximpim',
    'Employment Cost Index':'eci',
    'Productivity and Costs':'prod2',
    'Job Openings and Labor Turnover Survey':'jolts',
    'Real Earnings':'realer',
}


def parse_bls_calendar(raw, stream, start, end):
    """Keep only major scheduled BLS releases; exact release pages are verified later."""
    text=raw.decode('utf-8','replace') if isinstance(raw,bytes) else str(raw)
    # RFC 5545 continuation lines are folded onto the preceding line.
    text=re.sub(r'\r?\n[ \t]', '', text)
    records=[]; seen=0
    for block in text.split('BEGIN:VEVENT')[1:]:
        summary_match=re.search(r'(?m)^SUMMARY:(.+)$',block)
        date_match=re.search(r'(?m)^DTSTART[^:]*:(\d{8})',block)
        if not summary_match or not date_match: continue
        seen+=1; title=summary_match.group(1).strip().replace(r'\,',',')
        slug=BLS_MAJOR_RELEASES.get(title)
        published=f'{date_match.group(1)[:4]}-{date_match.group(1)[4:6]}-{date_match.group(1)[6:8]}'
        if not slug or not in_window(published,start,end): continue
        records.append(compact_record(stream,title=title,
          url=f'https://www.bls.gov/news.release/{slug}.toc.htm',published=published,
          themes=['major economic release'],work_id=f'bls:{slug}:{published}',
          date_status='provisional',
          date_basis='official BLS scheduled release date; verify actual release before selection'))
    result=ParseResult(records=records,seen=seen,complete=True)
    result.notes.append(f'{len(records)} major scheduled releases retained')
    return coverage_from_dates(result,start,end,exact=True)


def parse_crossref(raw, stream, start, end):
    payload=json.loads(raw)
    message=payload.get('message',{})
    items=message.get('items',[])
    records=[]
    contains=stream.get('doi_contains','').lower()
    for item in items:
        doi=str(item.get('DOI','')).lower()
        if contains and contains not in doi: continue
        title=(item.get('title') or [''])[0]
        published=parse_date(item.get('created'))
        if not title or not doi: continue
        signals=[]
        if item.get('abstract') and len(item.get('reference') or [])>=5: signals.append('research-metadata')
        try:
            if int(item.get('is-referenced-by-count',0))>0: signals.append('early-citation')
        except (TypeError,ValueError): pass
        records.append(compact_record(stream,title=title,url='https://doi.org/'+doi,
          published=published,themes=item.get('subject',[])[:5],work_id='doi:'+doi,
          date_status='provisional',date_basis='Crossref DOI registration date; publisher first release requires verification',
          signals=signals))
    total=int(message.get('total-results',len(items)))
    result=ParseResult(records=records,seen=len(items),complete=total<=len(items),truncated=total>len(items))
    if result.truncated: result.notes.append(f'Crossref returned {len(items)} of {total}')
    if contains: result.notes.append(f'kept {len(records)} records matching DOI fragment {contains}')
    return coverage_from_dates(result,start,end,exact=True)


def parse_arxiv(raw, stream, start, end):
    root=ET.fromstring(raw)
    records=[]
    for entry in (e for e in root.iter() if e.tag.split('}')[-1]=='entry'):
        children=list(entry)
        def value(name):
            return next((''.join(c.itertext()).strip() for c in children if c.tag.split('}')[-1]==name),'')
        title=value('title'); published=parse_date(value('published')); ident=value('id')
        link=next((c.get('href','') for c in children if c.tag.split('}')[-1]=='link' and c.get('rel')=='alternate'),'')
        if link.startswith('http://'): link='https://'+link[7:]
        arxiv_id=re.sub(r'^.*/','',ident); base_id=re.sub(r'v\d+$','',arxiv_id)
        version=re.search(r'v(\d+)$',arxiv_id)
        categories=[c.get('term','') for c in children if c.tag.split('}')[-1]=='category'][:5]
        signals=[]
        if value('journal_ref') or value('doi'): signals.append('journal-linked')
        if title and link:
            records.append(compact_record(stream,title=title,url=link,published=published,
              themes=categories,work_id='arxiv:'+base_id,
              version_id=version.group(1) if version else None,signals=signals))
    def number(name,default):
        value=next((e.text for e in root.iter() if e.tag.split('}')[-1]==name),None)
        try: return int(value)
        except (TypeError,ValueError): return default
    total=number('totalResults',len(records)); start_index=number('startIndex',0)
    complete=start_index+len(records)>=total
    result=ParseResult(records=records,seen=len(records),complete=complete,truncated=not complete)
    if not complete: result.notes.append(f'arXiv returned {len(records)} of {total} date-bounded matches')
    return coverage_from_dates(result,start,end,exact=True)


def parse_federal_register(raw, stream, start, end):
    payload=json.loads(raw); items=payload.get('results',[]); records=[]
    for item in items:
        published=parse_date(item.get('publication_date'))
        url=item.get('html_url') or item.get('pdf_url')
        if item.get('title') and url:
            records.append(compact_record(stream,title=item['title'],url=url,published=published,
              themes=[a.get('name','') for a in (item.get('agencies') or [])[:5]],
              work_id='federal-register:'+str(item.get('document_number',''))))
    count=int(payload.get('count',len(items)))
    result=ParseResult(records=records,seen=len(items),complete=count<=len(items),truncated=count>len(items))
    if result.truncated: result.notes.append(f'API returned {len(items)} of {count}')
    return coverage_from_dates(result,start,end,exact=stream.get('window_filtered',False))


def parse_biorxiv(raw, stream, start, end):
    payload=json.loads(raw); items=payload.get('collection',[]); records=[]; revisions=0
    for item in items:
        doi=str(item.get('doi','')).lower(); title=item.get('title'); published=parse_date(item.get('date'))
        version=str(item.get('version',''))
        if version and version!='1':
            revisions+=1; continue
        # The detail API's ``published`` value is a status label (for example,
        # PUBLISHAHEADOFPRINT), not evidence of an identified journal article.
        signals=[]
        if title and doi:
            records.append(compact_record(stream,title=title,url='https://doi.org/'+doi,
              published=published,themes=[item.get('category','')],work_id='doi:'+doi,
              version_id=version or None,signals=signals))
    messages=payload.get('messages') or []
    total=None
    if messages:
        for key in ('total','total_count'):
            try: total=int(messages[0][key]); break
            except (KeyError,TypeError,ValueError): pass
    complete=total is None or total<=len(items)
    result=ParseResult(records=records,seen=len(items),complete=complete,truncated=not complete)
    if not complete: result.notes.append(f'API returned {len(items)} of {total}')
    if revisions: result.notes.append(f'excluded {revisions} revisions pending original-upvote matching')
    return coverage_from_dates(result,start,end,exact=True)


def parse_biorxiv_pubs(raw, stream, start, end):
    """Keep only preprints first posted in-window that gained a journal link in-window."""
    payload=json.loads(raw); items=payload.get('collection',[]); records=[]
    for item in items:
        doi=str(item.get('preprint_doi') or item.get('biorxiv_doi') or '').lower()
        title=item.get('preprint_title'); first_release=parse_date(item.get('preprint_date'))
        if not in_window(first_release,start,end) or not title or not doi: continue
        records.append(compact_record(stream,title=title,url='https://doi.org/'+doi,
          published=first_release,themes=[item.get('preprint_category','')],work_id='doi:'+doi,
          signals=['journal-linked'],date_basis='bioRxiv/medRxiv first-post date; journal linkage is an admission signal'))
    messages=payload.get('messages') or []
    try: total=int(messages[0].get('total',len(items)))
    except (IndexError,TypeError,ValueError): total=len(items)
    complete=total<=len(items)
    result=ParseResult(records=records,seen=len(items),complete=complete,truncated=not complete)
    if not complete: result.notes.append(f'published-link API returned {len(items)} of {total}')
    result.notes.append(f'{len(records)} linked preprints also had first release inside the window')
    return coverage_from_dates(result,start,end,exact=True)


def _nested(obj, *keys):
    for key in keys:
        if not isinstance(obj,dict): return None
        obj=obj.get(key)
    return obj


def parse_clinicaltrials(raw, stream, start, end):
    payload=json.loads(raw); items=payload.get('studies',[]); records=[]
    for item in items:
        protocol=item.get('protocolSection',{})
        ident=protocol.get('identificationModule',{})
        nct=ident.get('nctId'); title=ident.get('briefTitle') or ident.get('officialTitle')
        results=item.get('resultsSection',{})
        dates=results.get('resultsFirstPostDate') or results.get('resultsFirstPostDateStruct') or {}
        if isinstance(dates,dict): dates=dates.get('date')
        published=parse_date(dates)
        if not published:
            published=parse_date(_nested(protocol,'statusModule','resultsFirstPostDateStruct','date'))
        design=protocol.get('designModule',{}); phases=design.get('phases') or []
        enrollment=_nested(design,'enrollmentInfo','count') or 0
        oversight=protocol.get('oversightModule',{}); locations=protocol.get('contactsLocationsModule',{}).get('locations') or []
        signals=[]
        if 'PHASE3' in phases: signals.append('phase-3')
        if 'PHASE4' in phases: signals.append('phase-4')
        try:
            threshold=500 if design.get('studyType')=='INTERVENTIONAL' else 5000
            if int(enrollment)>=threshold: signals.append('large-enrollment')
        except (TypeError,ValueError): pass
        if oversight.get('isFdaRegulatedDrug') or oversight.get('isFdaRegulatedDevice'): signals.append('fda-regulated')
        if oversight.get('oversightHasDmc'): signals.append('data-monitoring-committee')
        if len(locations)>=10: signals.append('multi-site')
        if nct and title:
            records.append(compact_record(stream,title=title,
              url='https://clinicaltrials.gov/study/'+nct,published=published,
              themes=(protocol.get('conditionsModule',{}).get('conditions') or [])[:5],
              work_id='nct:'+nct.lower(),signals=signals))
    truncated=bool(payload.get('nextPageToken'))
    result=ParseResult(records=records,seen=len(items),complete=not truncated,truncated=truncated)
    if truncated: result.notes.append('nextPageToken present')
    return coverage_from_dates(result,start,end,exact=True)


def parse_world_bank(raw, stream, start, end):
    payload=json.loads(raw); documents=payload.get('documents',{})
    items=[v for k,v in documents.items() if k!='facets' and isinstance(v,dict)]
    records=[]
    for item in items:
        title=item.get('display_title') or item.get('title'); published=parse_date(item.get('docdt'))
        url=item.get('pdfurl') or item.get('url')
        if isinstance(url,dict): url=url.get('url')
        if url and str(url).startswith('http://'): url='https://'+str(url)[7:]
        if title and url:
            records.append(compact_record(stream,title=title,url=url,published=published,
              themes=[item.get('docty','')],work_id='world-bank:'+str(item.get('id') or item.get('docna') or url)))
    total=int(payload.get('total',len(items)))
    result=ParseResult(records=records,seen=len(items),complete=total<=len(items),truncated=total>len(items))
    return coverage_from_dates(result,start,end,exact=False)


def parse_nyc_ibo(raw, stream, start, end):
    payload=json.loads(raw); items=payload.get('results',[]); records=[]
    for item in items:
        values={v.get('name'):(v.get('value') or []) for v in item.get('values',[]) if isinstance(v,dict)}
        def first(name):
            value=values.get(name,[])
            return value[0] if isinstance(value,list) and value else None
        title=first('Title') or item.get('name'); published=parse_date(first('Publication Date'))
        path=first('Content Link URL') or item.get('url')
        if path and not str(path).startswith('http'): path=urljoin(stream['homepage'],str(path))
        topics=first('Topics') or ''
        if title and path:
            records.append(compact_record(stream,title=title,url=path,published=published,
              themes=[x for x in re.split(r'\[t\]|,',topics) if x],
              work_id='nyc-ibo:'+str(item.get('id') or path)))
    total=payload.get('total') or payload.get('count')
    complete=total is None or int(total)<=len(items)
    result=ParseResult(records=records,seen=len(items),complete=complete,truncated=not complete)
    if total is None: result.notes.append('API did not state a total; completeness inferred from returned page')
    return coverage_from_dates(result,start,end,exact=False)


def parse_html_links(raw, stream, start, end):
    doc=lxml_html.fromstring(raw)
    records=[]; seen=0
    allow=re.compile(stream.get('link_pattern','.*'),re.I)
    for anchor in doc.xpath('//a[@href]'):
        href=urljoin(stream['endpoint'],anchor.get('href'))
        title=' '.join(anchor.text_content().split())
        if not href.startswith('https://') or not allow.search(href) or len(title)<5: continue
        seen+=1; node=anchor; context=title; published=None
        for _ in range(4):
            node=node.getparent()
            if node is None: break
            context=' '.join(node.text_content().split())[:800]
            published=parse_date(context)
            if published: break
        else: published=None
        if published:
            records.append(compact_record(stream,title=title,url=href,published=published))
    result=ParseResult(records=records,seen=seen,complete=bool(stream.get('html_complete',False)))
    if not result.complete: result.notes.append('HTML index parsed, but pagination/window completeness is not established')
    if seen and not records: result.notes.append('links matched, but no nearby day-level dates were found')
    return coverage_from_dates(result,start,end,exact=False)


PARSERS={
    'feed':parse_feed, 'atom':parse_feed, 'arxiv':parse_arxiv, 'crossref':parse_crossref,
    'federal_register':parse_federal_register, 'biorxiv':parse_biorxiv,
    'biorxiv_pubs':parse_biorxiv_pubs,
    'clinicaltrials':parse_clinicaltrials, 'world_bank_wds':parse_world_bank,
    'nyc_ibo':parse_nyc_ibo, 'html_links':parse_html_links,
    'bls_calendar':parse_bls_calendar,
}


class LinkExtractor(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href: self.links.append(href)


PRIMARY_POINTER_HOSTS=(
    'doi.org','arxiv.org','biorxiv.org','medrxiv.org','nber.org','ssrn.com','osf.io',
    'science.org','pnas.org','nature.com','cell.com','thelancet.com','nejm.org','jamanetwork.com',
    'sciencedirect.com','springer.com','wiley.com','tandfonline.com','sagepub.com','jstor.org',
    'worldbank.org','imf.org','oecd.org','un.org','courtlistener.com','documentcloud.org',
)


def likely_primary_pointer(url):
    parts=urlsplit(url); host=(parts.hostname or '').lower(); path=parts.path.lower()
    return (host.endswith(('.gov','.mil','.int','.edu')) or
      any(host==domain or host.endswith('.'+domain) for domain in PRIMARY_POINTER_HOSTS) or
      path.endswith(('.pdf','.csv','.xlsx','.zip')))


def identity_keys(value):
    """Return mechanical identifiers suitable for referral-to-inventory matching."""
    if isinstance(value,dict):
        raw=value.get('url',''); work_id=str(value.get('work_id','')).lower()
    else:
        raw=str(value); work_id=''
    decoded=unquote(raw).lower(); keys=set()
    try: keys.add('url:'+weekly.canonical(raw))
    except ValueError: pass
    doi=re.search(r'10\.\d{4,9}/[^\s?#]+',decoded)
    if work_id.startswith('doi:'): keys.add(work_id)
    if doi: keys.add('doi:'+doi.group(0).rstrip(').,;'))
    arxiv=re.search(r'(?:arxiv:|/abs/|/pdf/)(\d{4}\.\d{4,5})(?:v\d+)?',decoded)
    if work_id.startswith('arxiv:'): keys.add(re.sub(r'v\d+$','',work_id))
    if arxiv: keys.add('arxiv:'+arxiv.group(1))
    nct=re.search(r'\bnct\d{8}\b',decoded+' '+work_id)
    if nct: keys.add('nct:'+nct.group(0))
    return keys


def apply_discovery_signals(run_directory):
    with weekly.transaction(run_directory) as state:
        pointers=[]
        for referral in state.get('referrals',[]):
            pointers.append((identity_keys(referral['target_url']),referral['referrer_id']))
        for record in state.get('inventory',[]):
            signals=set(record.get('admission_signals',[])); found=set(record.get('found_by',[]))
            if record.get('also_found_by'): signals.add('cross-route')
            keys=identity_keys(record)
            for target_keys,referrer_id in pointers:
                if keys & target_keys:
                    signals.add('external-referral'); found.add(referrer_id)
            record['admission_signals']=sorted(signals)[:12]
            if found: record['found_by']=sorted(found)[:8]


def parse_arxiv_referral_metadata(raw, referrers_by_id, endpoint):
    """Resolve referred arXiv IDs to first-release metadata; retain no abstract."""
    root=ET.fromstring(raw); records=[]
    for entry in (e for e in root.iter() if e.tag.split('}')[-1]=='entry'):
        children=list(entry)
        def value(name):
            return next((''.join(c.itertext()).strip() for c in children if c.tag.split('}')[-1]==name),'')
        ident=value('id'); arxiv_id=re.sub(r'v\d+$','',re.sub(r'^.*/','',ident))
        if arxiv_id not in referrers_by_id: continue
        title=value('title'); published=parse_date(value('published'))
        categories=[c.get('term','') for c in children if c.tag.split('}')[-1]=='category'][:5]
        category='economy' if any(c=='econ.GN' or c.startswith(('econ.','q-fin.')) for c in categories) else 'science'
        version=re.search(r'v(\d+)$',re.sub(r'^.*/','',ident)); referrers=sorted(referrers_by_id[arxiv_id])
        if title and published:
            stream={'id':referrers[0],'publisher':'Open research repository','category':category,
              'source_type':'Research preprint','endpoint':endpoint,
              'date_semantics':'arXiv first-submission date resolved from an independent referral'}
            record=compact_record(stream,title=title,url='https://arxiv.org/abs/'+arxiv_id,
              published=published,themes=categories,work_id='arxiv:'+arxiv_id,
              version_id=version.group(1) if version else None,signals=['external-referral'])
            record['found_by']=referrers; records.append(record)
    return records


def parse_ny_opinion_referral_metadata(raw, referrers, endpoint):
    """Resolve one official New York opinion without retaining opinion text."""
    doc=lxml_html.fromstring(raw)
    title=' '.join(doc.xpath('string(//div[contains(@class,"case-info")]/h1)').split())
    info=[' '.join(p.text_content().split()) for p in doc.xpath('//div[contains(@class,"case-info")]/p')]
    slip=next((value for value in info if re.fullmatch(r'20\d{2} NY Slip Op \d{5}',value)),None)
    published=next((parse_date(value) for value in info if parse_date(value)),None)
    court=next((value for value in info if value.startswith(('Appellate Division','Court of Appeals'))),None)
    if not title or not slip or not published: return []
    slip_id=slip.lower().replace(' ny slip op ','-').replace(' ','')
    stream={'id':sorted(referrers)[0],'publisher':'New York State Unified Court System',
      'category':'law','source_type':'Court opinion','endpoint':endpoint,
      'date_semantics':'official decision date resolved from an independent referral'}
    record=compact_record(stream,title=title,url=endpoint,published=published,
      themes=[court] if court else [],work_id='ny-slip-op:'+slip_id,
      signals=['external-referral'])
    record['found_by']=sorted(referrers)
    return [record]


def resolve_referral_metadata(run_directory):
    """Resolve supported unmatched primary pointers using the metadata allowance."""
    state=json.loads((Path(run_directory)/'run.json').read_text())
    existing=set()
    for record in state.get('inventory',[]): existing.update(identity_keys(record))
    pending={}; pending_ny={}
    for referral in state.get('referrals',[]):
        keys=identity_keys(referral['target_url'])
        if keys & existing: continue
        arxiv_id=next((key[6:] for key in keys if key.startswith('arxiv:')),None)
        if arxiv_id: pending.setdefault(arxiv_id,set()).add(referral['referrer_id'])
        parts=urlsplit(referral['target_url'])
        if (parts.hostname or '').lower() in ('nycourts.gov','www.nycourts.gov') and re.fullmatch(
            r'/reporter/(?:current/)?3dseries/20\d{2}/20\d{2}_\d{5}\.s?html?',parts.path,re.I):
            pending_ny.setdefault(referral['target_url'],set()).add(referral['referrer_id'])
    resolved=[]
    ids=sorted(pending)
    for offset in range(0,len(ids),50):
        batch=ids[offset:offset+50]
        endpoint='https://export.arxiv.org/api/query?'+urlencode({
          'id_list':','.join(batch),'start':0,'max_results':len(batch)})
        cache=weekly.fetch(run_directory,'metadata',endpoint)
        resolved.extend(parse_arxiv_referral_metadata(cache.read_bytes(),pending,endpoint))
    for endpoint,referrers in sorted(pending_ny.items()):
        cache=weekly.fetch(run_directory,'metadata',endpoint)
        resolved.extend(parse_ny_opinion_referral_metadata(cache.read_bytes(),referrers,endpoint))
    weekly.add_inventory(run_directory,resolved)
    return {'requested':len(ids)+len(pending_ny),'resolved':len(resolved)}


def admit_inventory(run_directory, registry):
    policies={s['id']:s.get('admission',{'mode':'all'}) for s in registry['streams']}
    policies.update({r['id']:{'mode':'signals','min_score':3} for r in registry.get('referrers',[])})
    selected=[]; route_counts={}; admitted_by_route={}; excluded_by_reason={}
    state=json.loads((Path(run_directory)/'run.json').read_text())
    if state.get('candidates'):
        raise ValueError('Candidate admission already started')
    for record in state.get('inventory',[]):
        route_counts[record['route']]=route_counts.get(record['route'],0)+1
        signals=set(record.get('admission_signals',[]))
        if record.get('also_found_by'): signals.add('cross-route')
        score=sum(SIGNAL_WEIGHTS.get(signal,0) for signal in signals)
        route_ids=[record['route']]+record.get('also_found_by',[])
        route_policies=[policies.get(route,{'mode':'all'}) for route in route_ids]
        admit=any(p.get('mode','all')=='all' for p in route_policies)
        if not admit:
            thresholds=[int(p.get('min_score',1)) for p in route_policies if p.get('mode')=='signals']
            admit=bool(thresholds and score>=min(thresholds))
        if admit:
            candidate=dict(record); candidate['admission_signals']=sorted(signals); candidate['admission_score']=score
            selected.append(candidate); admitted_by_route[record['route']]=admitted_by_route.get(record['route'],0)+1
        else:
            excluded_by_reason['insufficient-metadata-or-activity-signal']=excluded_by_reason.get('insufficient-metadata-or-activity-signal',0)+1
    summary={'status':'selecting','inventory':len(state.get('inventory',[])),'eligible':len(selected),
      'by_route':route_counts,'admitted_by_route':admitted_by_route,'excluded_by_reason':excluded_by_reason,
      'signal_weights':SIGNAL_WEIGHTS}
    if len(selected)>1000:
        summary['status']='overflow'
        with weekly.transaction(run_directory) as current:
            current['admission']=summary; current['coverage']['admission_overflow']=len(selected)
            current['coverage']['complete']=False; current['coverage']['shortfall']='technical'
        return summary
    with weekly.transaction(run_directory) as current: current['admission']=summary
    weekly.add_candidates(run_directory,selected)
    with weekly.transaction(run_directory) as current:
        current['admission']['status']='complete'; current['admission']['admitted']=len(current['candidates'])
    return summary


def parse_referrer_feed(raw, referrer, start, end):
    root=ET.fromstring(raw)
    def local(element): return element.tag.split('}')[-1]
    referrals=[]; posts_seen=0; dated=0; post_records=[]; skipped=[]
    own=urlsplit(referrer['endpoint']).hostname
    exclude=re.compile(referrer.get('exclude_title_pattern',r'$.'))
    per_post=int(referrer.get('max_links_per_post',100)); total_limit=int(referrer.get('max_referrals',250))
    for entry in root.iter():
        if local(entry) not in ('item','entry'): continue
        children=list(entry)
        def value(names):
            return next((''.join(c.itertext()).strip() for c in children if local(c) in names),'')
        post_title=value({'title'}); post_url=value({'link'})
        if not post_url:
            post_url=next((c.get('href','') for c in children if local(c)=='link' and c.get('href')),'')
        published=parse_date(value({'published','pubDate','date','updated'}))
        posts_seen+=1
        if published:
            dated+=1; post_records.append({'date':published})
        if not post_title or not post_url or not in_window(published,start,end): continue
        if exclude.search(post_title):
            skipped.append('excluded post: '+post_title[:80]); continue
        body='\n'.join(''.join(c.itertext()) for c in children if local(c) in ('description','summary','content','encoded'))
        parser=LinkExtractor(); parser.feed(body)
        if len(parser.links)>per_post:
            skipped.append(f'skipped link-heavy post ({len(parser.links)} links): {post_title[:60]}'); continue
        for href in parser.links:
            target=urljoin(post_url,href)
            parts=urlsplit(target)
            if (parts.scheme!='https' or not parts.hostname or parts.hostname==own or
                not likely_primary_pointer(target)): continue
            try: target=weekly.canonical(target); post=weekly.canonical(post_url)
            except ValueError: continue
            referrals.append({'referrer_id':referrer['id'],'post_title':' '.join(post_title.split())[:300],
              'post_date':published,'post_url':post,'target_url':target})
            if len(referrals)>=total_limit: break
        if len(referrals)>=total_limit:
            skipped.append(f'referral cap reached ({total_limit})'); break
    result=ParseResult(records=post_records,seen=posts_seen,dated=dated,complete=True)
    coverage_from_dates(result,start,end,exact=False)
    result.notes.extend(skipped[:10])
    result.notes.append(f'{len(referrals)} likely-primary outbound pointers retained')
    if not referrals: result.notes.append('no outbound HTTPS primary-document pointers found in in-window posts')
    return referrals,result


def parse_pith_referrer(raw, referrer, start, end):
    """Extract arXiv pointers from Pith's ranked public surface, not its review prose."""
    doc=lxml_html.fromstring(raw); referrals=[]; post_records=[]; seen=set(); dated=0
    for article in doc.xpath('//article'):
        link=next((a for a in article.xpath('.//a[@href]')
          if re.fullmatch(r'/paper/(\d{4}\.\d{4,5})',a.get('href',''))),None)
        if link is None: continue
        arxiv_id=re.fullmatch(r'/paper/(\d{4}\.\d{4,5})',link.get('href')).group(1)
        if arxiv_id in seen: continue
        seen.add(arxiv_id); text=' '.join(article.text_content().split())
        match=re.search(r'(?:Pith\s+)?reviewed\s+(20\d{2}-\d{2}-\d{2})',text,re.I)
        reviewed=parse_date(match.group(1)) if match else None
        if reviewed: dated+=1; post_records.append({'date':reviewed})
        if not in_window(reviewed,start,end): continue
        title=' '.join(link.text_content().split()) or arxiv_id
        referrals.append({'referrer_id':referrer['id'],'post_title':title[:300],
          'post_date':reviewed,'post_url':urljoin(referrer['endpoint'],f'/paper/{arxiv_id}'),
          'target_url':f'https://arxiv.org/abs/{arxiv_id}'})
    result=ParseResult(records=post_records,seen=len(seen),dated=dated,complete=True)
    coverage_from_dates(result,start,end,exact=False)
    result.notes.append(f'{len(referrals)} in-window ranked arXiv pointers retained; review text ignored')
    return referrals,result


REFERRER_PARSERS={'feed':parse_referrer_feed,'pith':parse_pith_referrer}


def health_record(endpoint, adapter, result=None, error=None):
    if error:
        return {'reachable':False,'parsed':False,'fresh':False,'complete':False,
          'window_covered':False,'records_seen':0,'records_dated':0,'records_in_window':0,
          'truncated':False,'endpoint':endpoint,'adapter':adapter,'error':error}
    in_count=sum(bool(r.get('date')) for r in result.records)
    return {'reachable':True,'parsed':result.recognized,'fresh':in_count>0,
      'complete':result.complete,'window_covered':result.window_covered,
      'records_seen':result.seen,'records_dated':result.dated,'records_in_window':in_count,
      'truncated':result.truncated,'endpoint':endpoint,'adapter':adapter,'notes':result.notes}


PAGINATED_ADAPTERS={'arxiv','biorxiv_pubs','clinicaltrials'}


def replace_query(url, **updates):
    parts=urlsplit(url); query=dict(parse_qsl(parts.query,keep_blank_values=True))
    query.update({key:str(value) for key,value in updates.items()})
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))


def next_page_endpoint(adapter, raw, endpoint):
    """Return the source-declared next page without ranking or sampling records."""
    if adapter=='clinicaltrials':
        token=json.loads(raw).get('nextPageToken')
        return replace_query(endpoint,pageToken=token) if token else None
    if adapter=='biorxiv_pubs':
        payload=json.loads(raw); items=payload.get('collection',[])
        try:
            total=int((payload.get('messages') or [{}])[0].get('total',len(items)))
            cursor=int(endpoint.rstrip('/').rsplit('/',1)[1])
        except (TypeError,ValueError,IndexError):
            return None
        if not items or cursor+len(items)>=total: return None
        return endpoint.rstrip('/').rsplit('/',1)[0]+'/'+str(cursor+len(items))
    if adapter=='arxiv':
        root=ET.fromstring(raw)
        def number(name, default):
            value=next((e.text for e in root.iter() if e.tag.split('}')[-1]==name),None)
            try: return int(value)
            except (TypeError,ValueError): return default
        entries=sum(e.tag.split('}')[-1]=='entry' for e in root.iter())
        start_index=number('startIndex',0); total=number('totalResults',entries)
        if not entries or start_index+entries>=total: return None
        return replace_query(endpoint,start=start_index+entries)
    return None


def merge_page_results(pages, complete, notes=()):
    result=ParseResult(
      records=[record for page in pages for record in page.records],
      recognized=all(page.recognized for page in pages), complete=complete,
      seen=sum(page.seen for page in pages), dated=sum(page.dated for page in pages),
      truncated=not complete,
      notes=[note for page in pages for note in page.notes]+list(notes),
    )
    return result


def collect(run_directory, registry, route_ids=None, include_referrers=True):
    validate_registry(registry)
    state=json.loads((Path(run_directory)/'run.json').read_text())
    start,end=state['window']['start'],state['window']['end']
    chosen=set(route_ids or [])
    last_arxiv_request=0.0
    for original in registry['streams']:
        if original['status'] not in ('active','provisional'): continue
        if chosen and original['id'] not in chosen: continue
        stream=dict(original); endpoint=materialize_endpoint(stream['endpoint'],start,end); stream['endpoint']=endpoint
        pages=[]; page_endpoint=endpoint; visited=set(); page_failure=None
        while page_endpoint:
            if page_endpoint in visited or len(visited)>=100:
                page_failure='pagination cycle or 100-page technical safety limit reached'; break
            visited.add(page_endpoint)
            if stream['adapter']=='arxiv':
                delay=3.1-(time.monotonic()-last_arxiv_request)
                if delay>0: time.sleep(delay)
                last_arxiv_request=time.monotonic()
            try:
                bucket='registry' if not pages else 'metadata'
                cache=weekly.fetch(run_directory,bucket,page_endpoint)
                raw=cache.read_bytes()
                page=PARSERS[stream['adapter']](raw,stream,start,end)
                pages.append(page)
                page_endpoint=next_page_endpoint(stream['adapter'],raw,page_endpoint)
            except Exception as exc:
                page_failure=type(exc).__name__+': '+str(exc)[:300]; break
            if stream['adapter'] not in PAGINATED_ADAPTERS: break
        if not pages:
            weekly.record_route_health(run_directory,stream['id'],health_record(endpoint,stream['adapter'],error=page_failure or 'no response'))
            continue
        complete=not page_failure and (stream['adapter'] in PAGINATED_ADAPTERS or pages[-1].complete)
        notes=[f'pagination stopped: {page_failure}'] if page_failure else []
        result=merge_page_results(pages,complete,notes)
        result=coverage_from_dates(result,start,end,exact=stream.get('window_filtered',False))
        window_records=[r for r in result.records if in_window(r.get('date'),start,end)]
        result.records=window_records
        weekly.add_inventory(run_directory,window_records)
        weekly.record_route_health(run_directory,stream['id'],health_record(endpoint,stream['adapter'],result))
    if include_referrers:
        for referrer in registry.get('referrers',[]):
            if referrer['status'] not in ('active','provisional'): continue
            if chosen and referrer['id'] not in chosen: continue
            endpoint=referrer['endpoint']; adapter=referrer.get('adapter','feed')
            try:
                cache=weekly.fetch(run_directory,'search',endpoint)
            except Exception as exc:
                weekly.record_route_health(run_directory,referrer['id'],health_record(endpoint,'referrer_'+adapter,error=type(exc).__name__+': '+str(exc)[:300]))
                continue
            try:
                referrals,result=REFERRER_PARSERS[adapter](cache.read_bytes(),referrer,start,end)
                weekly.add_referrals(run_directory,referrals)
                result.records=[r for r in result.records if in_window(r.get('date'),start,end)]
                weekly.record_route_health(run_directory,referrer['id'],health_record(endpoint,'referrer_'+adapter,result))
            except Exception as exc:
                weekly.record_route_health(run_directory,referrer['id'],{'reachable':True,'parsed':False,
                  'fresh':False,'complete':False,'window_covered':False,'records_seen':0,
                  'records_dated':0,'records_in_window':0,'truncated':False,'endpoint':endpoint,
                  'adapter':'referrer_'+adapter,'error':type(exc).__name__+': '+str(exc)[:300]})
    resolve_referral_metadata(run_directory)
    apply_discovery_signals(run_directory)
    admit_inventory(run_directory,registry)
    return weekly.audit(run_directory)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate','collect'])
    parser.add_argument('registry'); parser.add_argument('--run'); parser.add_argument('--route',action='append')
    parser.add_argument('--no-referrers',action='store_true')
    args=parser.parse_args(); registry=json.loads(Path(args.registry).read_text())
    if args.command=='validate': result={'valid':bool(validate_registry(registry)),'streams':len(registry['streams']),'referrers':len(registry.get('referrers',[]))}
    else:
        if not args.run: parser.error('--run is required for collect')
        result=collect(args.run,registry,args.route,not args.no_referrers)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
