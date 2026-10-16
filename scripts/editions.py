"""Validated public editions. Private feedback never enters this build."""
from __future__ import annotations
import html
import json
import re
import shutil
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

CATEGORIES = {'law': 'Courts & Law', 'economy': 'Economy & Finance',
              'government': 'Government & Public Services', 'science': 'Science & Research'}
EDITION_KEYS = {'schema_version', 'date', 'prepared_at', 'items', 'coverage_note', 'window'}
ITEM_KEYS = {'id', 'category', 'title', 'publisher', 'published', 'geography', 'summary',
             'detail', 'source_url', 'document_url', 'source_type', 'tags', 'preview_url', 'revision_of'}
REQUIRED = ITEM_KEYS - {'detail', 'preview_url', 'revision_of'}
ROOT = Path(__file__).resolve().parents[1]

def esc(value):
    return html.escape(str(value), quote=True)

def https_url(value):
    parsed = urlparse(value)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password

def validate_edition(edition):
    if not isinstance(edition, dict) or set(edition) - EDITION_KEYS:
        raise ValueError('Edition has unknown fields. Private selection notes must never be published.')
    if edition.get('schema_version') not in (1, 2):
        raise ValueError('schema_version must be 1 or 2')
    weekly = edition['schema_version'] == 2
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', edition.get('date', '')):
        raise ValueError('date must be YYYY-MM-DD')
    date.fromisoformat(edition['date'])
    prepared = datetime.fromisoformat(edition['prepared_at'].replace('Z', '+00:00'))
    if prepared.tzinfo is None:
        raise ValueError('prepared_at must include a timezone')
    if prepared.astimezone(ZoneInfo('America/New_York')).date().isoformat() != edition['date']:
        raise ValueError('Edition date must be its preparation date in New York')
    if weekly:
        window = edition.get('window')
        if not isinstance(window, dict) or set(window) != {'start', 'end'}:
            raise ValueError('Weekly edition requires window start/end dates')
        start, end = date.fromisoformat(window['start']), date.fromisoformat(window['end'])
        if not start < end <= date.fromisoformat(edition['date']):
            raise ValueError('Window must precede preparation, start inclusive/end exclusive')
    elif 'window' in edition:
        raise ValueError('Historical daily editions cannot specify a weekly window')
    if not isinstance(edition.get('items'), list) or not 1 <= len(edition['items']) <= (20 if weekly else 15):
        raise ValueError('Edition exceeds its item limit')
    seen_ids, seen_urls = set(), set()
    for item in edition['items']:
        if not isinstance(item, dict) or set(item) - ITEM_KEYS or not REQUIRED <= set(item):
            raise ValueError('Item contains unknown or missing fields')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,100}', item['id']) or item['id'] in seen_ids:
            raise ValueError('Each item needs a unique, stable lowercase id')
        seen_ids.add(item['id'])
        if item['category'] not in CATEGORIES:
            raise ValueError('Unknown category')
        for field in ('title', 'publisher', 'published', 'geography', 'summary', 'source_type'):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f'{field} must be nonempty text')
        length_limits = {'title': 220, 'publisher': 180, 'geography': 100, 'source_type': 100}
        for field, limit in length_limits.items():
            if len(item[field]) > limit:
                raise ValueError(f'{field} exceeds {limit} characters')
        if not 30 <= len(item['summary'].split()) <= 120:
            raise ValueError('Summary must be 30–120 words (target 60–90)')
        published = date.fromisoformat(item['published'])
        if weekly and not start <= published < end:
            raise ValueError('Release date is outside the weekly window')
        if 'revision_of' in item:
            rev = item['revision_of']
            if not weekly or not isinstance(rev, dict) or set(rev) != {'item_id', 'edition_date', 'changes'}:
                raise ValueError('Revision requires item_id, edition_date and changes')
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,100}', rev['item_id']) or rev['item_id'] == item['id']:
                raise ValueError('Revision needs a distinct original item id')
            if date.fromisoformat(rev['edition_date']) >= date.fromisoformat(edition['date']):
                raise ValueError('Original edition must precede this edition')
            if not isinstance(rev['changes'], str) or not 1 <= len(rev['changes'].split()) <= 60:
                raise ValueError('State substantive revision changes in 1–60 words')
        if published > date.fromisoformat(edition['date']):
            raise ValueError('Publication date cannot be after the edition date')
        for field in ('source_url', 'document_url'):
            if not isinstance(item[field], str) or not https_url(item[field]):
                raise ValueError(f'{field} must be an HTTPS URL without credentials')
        canonical = item['document_url'].split('#')[0].rstrip('/')
        if canonical in seen_urls:
            raise ValueError('Duplicate primary document')
        seen_urls.add(canonical)
        if 'preview_url' in item and (not isinstance(item['preview_url'], str) or not https_url(item['preview_url'])):
            raise ValueError('preview_url must be HTTPS; omit it when no real preview exists')
        if not isinstance(item['tags'], list) or not 1 <= len(item['tags']) <= 8 or not all(isinstance(t, str) and 0 < len(t) <= 80 for t in item['tags']):
            raise ValueError('tags must contain 1–8 short factual topic labels')
        if 'detail' in item and (not isinstance(item['detail'], str) or len(item['detail'].split()) > 120):
            raise ValueError('detail must be plain text of no more than 120 words')
    if 'coverage_note' in edition and (
        not isinstance(edition['coverage_note'], str)
        or len(edition['coverage_note'].split()) > 100
    ):
        raise ValueError('coverage_note must be plain text of no more than 100 words')
    return edition

def load_editions():
    editions = []
    for path in sorted((ROOT / 'data/editions').glob('*.json'), reverse=True):
        edition = validate_edition(json.loads(path.read_text()))
        if path.stem != edition['date']:
            raise ValueError(f'{path.name}: filename must match edition date')
        editions.append(edition)
    known = {e['date']: {i['id'] for i in e['items']} for e in editions}
    windows = []
    for e in editions:
        if e.get('window'):
            w = e['window']
            if any(w['start'] < end and start < w['end'] for start, end in windows):
                raise ValueError('Weekly publication windows overlap')
            windows.append((w['start'], w['end']))
        for item in e['items']:
            rev = item.get('revision_of')
            if rev and rev['item_id'] not in known.get(rev['edition_date'], set()):
                raise ValueError('Revision refers to an unknown original item')
    return editions


def editorial_index(editions):
    """Return the compact 30-day input used by the next editorial run."""
    counts = Counter(
        item['category']
        for edition in editions
        for item in edition['items']
    )
    return {
        'schema_version': 1,
        'window_start': editions[-1]['date'] if editions else None,
        'through': editions[0]['date'] if editions else None,
        'category_counts': {category: counts.get(category, 0) for category in CATEGORIES},
        'items': [
            {
                'date': edition['date'],
                'id': item['id'],
                'title': item['title'],
                'document_url': item['document_url'],
            }
            for edition in editions
            for item in edition['items']
        ],
    }

def tile(item):
    ident = esc(item['id'])
    preview = ''
    if item.get('preview_url'):
        preview = f'<a class="document-preview" href="{esc(item["document_url"])}" target="_blank" rel="noopener noreferrer" aria-label="Open original document: {esc(item["title"])}"><img src="{esc(item["preview_url"])}" alt="Document preview" loading="lazy" referrerpolicy="no-referrer"></a>'
    # Static text is escaped. Feedback metadata uses a JSON text node, never HTML.
    meta = json.dumps({k: item[k] for k in ('id','category','title','publisher','document_url','source_type','tags')}, ensure_ascii=True).replace('<', '\\u003c')
    detail = f'<p>{esc(item["detail"])}</p>' if item.get('detail') else ''
    if item.get('revision_of'):
        rev = item['revision_of']
        detail += f'<p>Revision of <a href="https://barasch.github.io/observatory/editions/{esc(rev["edition_date"])}/#item-{esc(rev["item_id"])}">earlier publication</a>: {esc(rev["changes"])}</p>'
    return f'''<article class="source-tile" id="item-{ident}" data-tile="{ident}" data-category="{esc(item['category'])}">
      <script type="application/json" class="item-metadata">{meta}</script>
      {preview}
      <div class="tile-body">
        <p class="tile-meta">{esc(item['publisher'])} <span>· {esc(item['published'])}</span></p>
        <h3><button class="tile-toggle" type="button" aria-expanded="false" aria-controls="detail-{ident}">{esc(item['title'])}</button></h3>
        <p class="tile-summary">{esc(item['summary'])}</p>
        <p class="tile-labels">{esc(item['geography'])} <span>· {esc(item['source_type'])}</span></p>
        <div class="tile-detail" id="detail-{ident}" hidden>{detail}
          <p class="source-links"><a href="{esc(item['document_url'])}" target="_blank" rel="noopener noreferrer">Original document ↗</a><a href="{esc(item['source_url'])}" target="_blank" rel="noopener noreferrer">Publisher’s page ↗</a></p>
        </div>
      </div>
      <div class="feedback-controls" hidden>
        <button type="button" data-vote="up" aria-pressed="false" aria-label="More like this" title="More like this">👍</button>
        <button type="button" data-vote="down" aria-pressed="false" aria-label="Fewer like this" title="Fewer like this">👎</button>
        <button type="button" data-dismiss aria-pressed="false">Dismiss</button>
        <button type="button" data-why hidden>Why?</button>
        <form class="reason-form" hidden><label>Optional explanation<textarea maxlength="2000" rows="2"></textarea></label><button type="submit">Save explanation</button><button type="button" data-cancel-reason>Cancel</button></form>
      </div>
    </article>'''

def render(edition):
    if edition:
        heading = date.fromisoformat(edition['date']).strftime('%A, %B %-d, %Y')
        stamp = datetime.fromisoformat(edition['prepared_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/New_York')).strftime('%-I:%M %p %Z')
        intro = f'<time datetime="{esc(edition["date"])}">{esc(heading)}</time><p class="edition-caption">{len(edition["items"])} primary sources · Prepared {esc(stamp)}</p>'
        if edition.get('window'):
            window = edition['window']
            last = date.fromisoformat(window['end']) - timedelta(days=1)
            intro += f'<p class="edition-window">Coverage: {esc(window["start"])} – {esc(last.isoformat())} · New York dates</p>'
        if edition.get('coverage_note'):
            intro += f'<p class="coverage-note">{esc(edition["coverage_note"])}</p>'
    else:
        intro = '<p class="edition-caption">The first curated edition has not been published yet.</p><p class="coverage-note">Weekly editions will appear here after the pilot is approved. <a href="archive/collected.html">Browse earlier collected records</a>.</p>'
    sections = []
    for key, label in CATEGORIES.items():
        items = [item for item in (edition or {}).get('items', []) if item['category'] == key]
        sections.append(f'<section class="topic-section" data-topic="{key}" aria-labelledby="heading-{key}"><header class="topic-header"><h2 id="heading-{key}">{esc(label)}</h2><span class="topic-count">{len(items)}</span></header><div class="tile-grid">{"".join(map(tile, items))}</div><p class="category-empty"{ " hidden" if items else ""}>No selections in this edition.</p></section>')
    return f'''<div data-edition><header class="edition-heading"><h1>Observatory</h1>{intro}</header>
    <p id="feedback-status" role="status" aria-live="polite" hidden></p>
    {''.join(sections)}
    <details id="dismissed-section" hidden><summary>Dismissed <span id="dismissed-count">0</span></summary><div class="tile-grid" id="dismissed-tiles"></div></details></div>'''

def overwrite_site(site, page):
    editions = load_editions()
    today = datetime.now(ZoneInfo('America/New_York')).date()
    if any(date.fromisoformat(e['date']) > today for e in editions):
        raise ValueError('Cannot publish future editions')
    cutoff = today - timedelta(days=29)
    retained = [e for e in editions if date.fromisoformat(e['date']) >= cutoff]
    # Keep the last successful edition visible even during a long outage.
    latest = editions[0] if editions else None
    if (site / 'archive/index.html').exists():
        shutil.copy2(site / 'archive/index.html', site / 'archive/collected.html')
    page('index.html', 'Observatory', 'A weekly selection of primary sources.', render(latest), 'home')
    archive_links = ''.join(f'<li><a href="../editions/{e["date"]}/">{esc(e["date"])}</a><span>{len(e["items"])} sources</span></li>' for e in retained)
    page('archive/index.html', 'Archive', 'Editions from the past 30 days.', f'<article class="edition-heading"><h1>Archive</h1><p>Editions from the past 30 days.</p><ul class="edition-archive">{archive_links}</ul><p><a href="collected.html">Earlier collected records</a></p></article>', 'archive')
    linked = {i['revision_of']['edition_date'] for e in retained for i in e['items'] if i.get('revision_of')}
    pages = [e for e in editions if e in retained or e is latest or e['date'] in linked]
    for edition in pages:
        page(f'editions/{edition["date"]}/index.html', edition['date'], 'A weekly selection of primary sources.', render(edition), 'archive')
    for filename in ('feedback-core.js', 'edition.js', 'edition.css'):
        shutil.copy2(ROOT / 'site_src' / filename, site / 'assets' / filename)
    # Explicit allowlist: never copy configuration directories or feedback files.
    auth = ROOT / 'feedback-auth.json'
    if auth.exists():
        from feedback_auth import validate_auth
        record = validate_auth(json.loads(auth.read_text()))
        (site / 'feedback-auth.json').write_text(json.dumps(record))
    (site / 'data/editions.json').write_text(json.dumps(retained, ensure_ascii=False))
    (site / 'data/latest.json').write_text(json.dumps(latest, ensure_ascii=False))
    (site / 'data/editorial-index.json').write_text(
        json.dumps(editorial_index(retained), ensure_ascii=False, separators=(',', ':'))
    )
    entries = []
    for edition in retained:
        for item in edition['items']:
            entries.append(f'<item><guid isPermaLink="false">{esc(item["id"])}</guid><title>{esc(item["title"])}</title><link>{esc(item["document_url"])}</link><description>{esc(item["summary"])}</description></item>')
    (site / 'feed.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Observatory</title><link>https://barasch.github.io/observatory/</link><description>Weekly primary sources</description>'+''.join(entries)+'</channel></rss>')
    return sum(len(e['items']) for e in retained)

if __name__ == '__main__':
    import sys
    for filename in sys.argv[1:]:
        validate_edition(json.loads(Path(filename).read_text()))
        print(f'Valid edition: {filename}')
