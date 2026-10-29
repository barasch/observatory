"""Mechanical stage-one discovery with semantic route-health reporting.

The collector reads compact listings only.  It does not inspect document bodies,
score candidates, or treat an editorial referrer as evidence.
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
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from lxml import html as lxml_html

import weekly


ADAPTERS = {
    'feed', 'atom', 'arxiv', 'crossref', 'federal_register', 'biorxiv',
    'clinicaltrials', 'world_bank_wds', 'nyc_ibo', 'html_links',
}
REQUIRED_STREAM_FIELDS = {
    'id', 'publisher', 'name', 'category', 'source_type', 'endpoint',
    'adapter', 'homepage', 'scope', 'status', 'date_semantics',
    'alternative_routes', 'provenance', 'last_check',
}
REQUIRED_REFERRER_FIELDS = {'id', 'name', 'endpoint', 'status', 'provenance'}


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
        weekly.canonical(materialize_endpoint(stream['endpoint'], '2026-01-01', '2026-01-08'))
    referrer_ids=set()
    for referrer in registry.get('referrers', []):
        missing=REQUIRED_REFERRER_FIELDS-referrer.keys()
        if missing: raise ValueError(f"{referrer.get('id','referrer')}: missing {sorted(missing)}")
        if referrer['id'] in ids or referrer['id'] in referrer_ids:
            raise ValueError('Duplicate discovery id: '+referrer['id'])
        referrer_ids.add(referrer['id'])
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
                   version_id=None, date_status='listed', date_basis=None):
    record={
        'title':' '.join(str(title).split()), 'date':published,
        'source':stream['publisher'], 'url':url, 'themes':list(themes)[:5],
        'route':stream['id'], 'category':stream['category'],
        'date_provenance':stream['endpoint'], 'date_status':date_status,
        'date_precision':'day' if published else 'unknown',
        'date_basis':date_basis or stream['date_semantics'],
        'source_type':stream['source_type'],
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
    result=ParseResult(records=records,seen=len(records))
    if not records:
        result.notes.append('recognized feed contained no usable linked entries')
    return coverage_from_dates(result,start,end,stream.get('window_filtered',False))


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
        records.append(compact_record(stream,title=title,url='https://doi.org/'+doi,
          published=published,themes=item.get('subject',[])[:5],work_id='doi:'+doi,
          date_status='provisional',date_basis='Crossref DOI registration date; publisher first release requires verification'))
    total=int(message.get('total-results',len(items)))
    result=ParseResult(records=records,seen=len(items),complete=total<=len(items),truncated=total>len(items))
    if result.truncated: result.notes.append(f'Crossref returned {len(items)} of {total}')
    if contains: result.notes.append(f'kept {len(records)} records matching DOI fragment {contains}')
    return coverage_from_dates(result,start,end,exact=True)


def parse_arxiv(raw, stream, start, end):
    root=ET.fromstring(raw)
    records=weekly.feed_records(raw,stream)
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
    payload=json.loads(raw); items=payload.get('collection',[]); records=[]
    for item in items:
        doi=str(item.get('doi','')).lower(); title=item.get('title'); published=parse_date(item.get('date'))
        if title and doi:
            records.append(compact_record(stream,title=title,url='https://doi.org/'+doi,
              published=published,themes=[item.get('category','')],work_id='doi:'+doi,
              version_id=str(item.get('version','')) or None))
    messages=payload.get('messages') or []
    total=None
    if messages:
        for key in ('total','total_count'):
            try: total=int(messages[0][key]); break
            except (KeyError,TypeError,ValueError): pass
    complete=total is None or total<=len(items)
    result=ParseResult(records=records,seen=len(items),complete=complete,truncated=not complete)
    if not complete: result.notes.append(f'API returned {len(items)} of {total}')
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
        if nct and title:
            records.append(compact_record(stream,title=title,
              url='https://clinicaltrials.gov/study/'+nct,published=published,
              themes=(protocol.get('conditionsModule',{}).get('conditions') or [])[:5],
              work_id='nct:'+nct.lower()))
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
    'clinicaltrials':parse_clinicaltrials, 'world_bank_wds':parse_world_bank,
    'nyc_ibo':parse_nyc_ibo, 'html_links':parse_html_links,
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


def collect(run_directory, registry, route_ids=None, include_referrers=True):
    validate_registry(registry)
    state=json.loads((Path(run_directory)/'run.json').read_text())
    start,end=state['window']['start'],state['window']['end']
    chosen=set(route_ids or [])
    for original in registry['streams']:
        if original['status'] not in ('active','provisional'): continue
        if chosen and original['id'] not in chosen: continue
        stream=dict(original); endpoint=materialize_endpoint(stream['endpoint'],start,end); stream['endpoint']=endpoint
        try:
            cache=weekly.fetch(run_directory,'registry',endpoint)
        except Exception as exc:
            weekly.record_route_health(run_directory,stream['id'],health_record(endpoint,stream['adapter'],error=type(exc).__name__+': '+str(exc)[:300]))
            continue
        try:
            raw=cache.read_bytes()
            result=PARSERS[stream['adapter']](raw,stream,start,end)
            window_records=[r for r in result.records if in_window(r.get('date'),start,end)]
            result.records=window_records
            weekly.add_candidates(run_directory,window_records)
            weekly.record_route_health(run_directory,stream['id'],health_record(endpoint,stream['adapter'],result))
        except Exception as exc:
            weekly.record_route_health(run_directory,stream['id'],{'reachable':True,'parsed':False,
              'fresh':False,'complete':False,'window_covered':False,'records_seen':0,
              'records_dated':0,'records_in_window':0,'truncated':False,'endpoint':endpoint,
              'adapter':stream['adapter'],'error':type(exc).__name__+': '+str(exc)[:300]})
    if include_referrers:
        for referrer in registry.get('referrers',[]):
            if referrer['status'] not in ('active','provisional'): continue
            if chosen and referrer['id'] not in chosen: continue
            endpoint=referrer['endpoint']
            try:
                cache=weekly.fetch(run_directory,'search',endpoint)
            except Exception as exc:
                weekly.record_route_health(run_directory,referrer['id'],health_record(endpoint,'referrer_feed',error=type(exc).__name__+': '+str(exc)[:300]))
                continue
            try:
                referrals,result=parse_referrer_feed(cache.read_bytes(),referrer,start,end)
                weekly.add_referrals(run_directory,referrals)
                result.records=[r for r in result.records if in_window(r.get('date'),start,end)]
                weekly.record_route_health(run_directory,referrer['id'],health_record(endpoint,'referrer_feed',result))
            except Exception as exc:
                weekly.record_route_health(run_directory,referrer['id'],{'reachable':True,'parsed':False,
                  'fresh':False,'complete':False,'window_covered':False,'records_seen':0,
                  'records_dated':0,'records_in_window':0,'truncated':False,'endpoint':endpoint,
                  'adapter':'referrer_feed','error':type(exc).__name__+': '+str(exc)[:300]})
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
