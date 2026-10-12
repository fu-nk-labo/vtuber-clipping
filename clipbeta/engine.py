from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import re
from copy import deepcopy
from pathlib import Path
from typing import Protocol

VERSION = 'clipbeta/1'
COLORS = {'white', 'yellow', 'red', 'cyan', 'pink'}
SE = {'none', 'pop', 'surprise', 'laugh'}
ZOOM = {'none', 'punch_in', 'slow_in'}
STATES = {'pending', 'adopted', 'rejected'}


class ValidationError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise ValidationError(message)


def exact_keys(obj, keys, label):
    require(isinstance(obj, dict) and set(obj) == set(keys), f'{label}: invalid fields')


def integer(value, label):
    require(type(value) is int and value >= 0, f'{label}: expected nonnegative integer milliseconds')
    return value


def stable_id(prefix, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return prefix + hashlib.sha256(raw.encode()).hexdigest()[:24]


def text_value(value, label):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 4000, f'{label}: expected 1–4000 text characters')
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), f'{label}: unpaired Unicode surrogate')
    require(not any(ord(c) < 32 and c not in '\n\t' for c in value), f'{label}: control character')
    return value


def read_json(path):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, f'duplicate JSON key: {k}')
            result[k] = v
        return result
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValidationError(f'invalid number: {x}')))
    except (json.JSONDecodeError, UnicodeError) as e:
        raise ValidationError(f'invalid JSON: {e}') from e


def parse_time(value):
    """CLI time: seconds with <=3 decimals, or HH:MM:SS.mmm / SRT comma."""
    require(isinstance(value, str), 'time must be a string')
    m = re.fullmatch(r'(\d+):(\d{2}):(\d{2})[.,](\d{3})', value)
    if m:
        h, minute, sec, ms = map(int, m.groups())
        require(minute < 60 and sec < 60, 'invalid timestamp components')
        return ((h * 60 + minute) * 60 + sec) * 1000 + ms
    require(re.fullmatch(r'\d+(?:\.\d{1,3})?', value) is not None, 'time: use seconds or HH:MM:SS.mmm')
    whole, _, fraction = value.partition('.')
    return int(whole) * 1000 + int(fraction.ljust(3, '0') or '0')


def load_captions(path):
    path = Path(path)
    if path.suffix.lower() == '.json':
        rows = read_json(path)
    elif path.suffix.lower() == '.srt':
        raw = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').strip()
        require(bool(raw), 'empty subtitles')
        rows = []
        for block in re.split(r'\n[ \t]*\n', raw):
            lines = block.splitlines()
            require(len(lines) >= 3 and lines[0].isdigit(), 'SRT: expected index, timing and text')
            m = re.fullmatch(r'(\d+:\d{2}:\d{2},\d{3}) --> (\d+:\d{2}:\d{2},\d{3})', lines[1])
            require(m is not None, 'SRT: malformed timing line')
            rows.append({'start_ms': parse_time(m[1]), 'end_ms': parse_time(m[2]), 'text': '\n'.join(lines[2:])})
    else:
        raise ValidationError('input must be .srt or .json')
    require(isinstance(rows, list) and len(rows) > 0, 'captions must be a nonempty array')
    seen = set()
    clean = []
    for row in rows:
        exact_keys(row, {'start_ms', 'end_ms', 'text'}, 'caption')
        start, end = integer(row['start_ms'], 'start_ms'), integer(row['end_ms'], 'end_ms')
        require(start < end, 'caption start must precede end')
        text = text_value(row['text'], 'caption text')
        key = (start, end, text)
        require(key not in seen, 'duplicate caption')
        seen.add(key)
        clean.append(dict(row))
    return sorted(clean, key=lambda r: (r['start_ms'], r['end_ms'], r['text']))


class SuggestionBackend(Protocol):
    """Pure local contract. A future provider must return validated style choices."""
    name: str
    def suggest(self, text: str) -> tuple[dict, str]: ...


class RuleBackend:
    name = 'rules-v1'
    def suggest(self, text):
        for word,color in [('かわいい','pink'),('可愛い','pink'),('残念','cyan')]:
            if word in text:
                start=text.index(word)
                return {'emphasis':False,'color':'white','se':'none','zoom':'none','spans':[{'start':start,'end':start+len(word),'color':color,'emphasis':True}],'zoom_target':{'x':0.5,'y':0.5}}, 'Literal keyword match; no semantic AI'
        if re.search(r'笑|www|ｗｗ|haha|lol', text, re.I):
            return {'emphasis': True, 'color': 'yellow', 'se': 'laugh', 'zoom': 'none'}, 'Laughter keyword'
        if re.search(r'すご|やば|えっ|[!！]{2,}|wow|amazing', text, re.I):
            return {'emphasis': True, 'color': 'red', 'se': 'surprise', 'zoom': 'punch_in'}, 'Excited reaction keyword'
        if '?' in text or '？' in text:
            return {'emphasis': False, 'color': 'cyan', 'se': 'none', 'zoom': 'slow_in'}, 'Question marker'
        return {'emphasis': False, 'color': 'white', 'se': 'none', 'zoom': 'none'}, 'Neutral; explicit no-op effects'


def validate_style(style, text=None):
    required={'emphasis','color','se','zoom'}
    require(isinstance(style,dict) and required <= set(style) <= required|{'spans','zoom_target'}, 'style: invalid fields')
    require(type(style['emphasis']) is bool, 'emphasis must be boolean')
    require(isinstance(style['color'], str) and style['color'] in COLORS, 'unknown color preset')
    require(isinstance(style['se'], str) and style['se'] in SE, 'unknown SE preset')
    require(isinstance(style['zoom'], str) and style['zoom'] in ZOOM, 'unknown zoom preset')
    target=style.get('zoom_target',{'x':0.5,'y':0.5})
    exact_keys(target,{'x','y'},'zoom target')
    require(all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in target.values()),'zoom target coordinates must be finite numbers in [0,1]')
    spans=style.get('spans',[])
    require(isinstance(spans,list) and len(spans)<=32,'spans must be an array with at most 32 entries')
    previous=0
    for span in spans:
        exact_keys(span,{'start','end','color','emphasis'},'span')
        a,b=integer(span['start'],'span start'),integer(span['end'],'span end')
        require(previous<=a<b,'spans must be sorted and nonoverlapping')
        previous=b
        require(isinstance(span['color'],str) and span['color'] in COLORS and type(span['emphasis']) is bool,'invalid span style')
        if text is not None:
            require(b<=len(text),'span exceeds text length in Unicode code points')
            for boundary in (a,b):
                if 0<boundary<len(text):
                    require(not unicodedata.combining(text[boundary]) and text[boundary] not in '\ufe0f\ufe0e\u200d' and text[boundary-1]!='\u200d','span splits a combining/ZWJ sequence')



def make_proposal(captions, start, end, duration, backend=None):
    integer(start, 'start'); integer(end, 'end'); integer(duration, 'duration')
    require(0 <= start < end <= duration, 'range must satisfy 0 <= start < end <= duration')
    require(all(r['end_ms'] <= duration for r in captions), 'caption exceeds declared media duration')
    backend = backend or RuleBackend()
    source = stable_id('src_', captions)
    clip = stable_id('clip_', [source, start, end])
    items = []
    for row in captions:
        a, b = max(start, row['start_ms']), min(end, row['end_ms'])
        if a >= b:
            continue
        style, reason = backend.suggest(row['text'])
        validate_style(style, row['text'])
        items.append({'id': stable_id('cue_', [clip, row]), 'source_start_ms': row['start_ms'],
                      'source_end_ms': row['end_ms'], 'start_ms': a-start, 'end_ms': b-start,
                      'original_text': row['text'], 'text': row['text'], 'style': style,
                      'reason': reason, 'status': 'pending'})
    require(items, 'no captions overlap selected range')
    result = {'schema': VERSION, 'kind': 'proposal', 'source_id': source, 'clip_id': clip,
              'backend': backend.name, 'media_duration_ms': duration, 'range_start_ms': start,
              'range_end_ms': end, 'items': items}
    validate_proposal(result)
    return result


def validate_proposal(p):
    exact_keys(p, {'schema', 'kind', 'source_id', 'clip_id', 'backend', 'media_duration_ms', 'range_start_ms', 'range_end_ms', 'items'}, 'proposal')
    require(p['schema'] == VERSION and p['kind'] == 'proposal', 'unsupported proposal schema')
    require(isinstance(p['source_id'], str) and re.fullmatch(r'src_[0-9a-f]{24}', p['source_id']), 'invalid source ID')
    text_value(p['backend'], 'backend')
    s, e, d = (integer(p[k], k) for k in ['range_start_ms', 'range_end_ms', 'media_duration_ms'])
    require(s < e <= d, 'invalid clip range')
    require(p['clip_id'] == stable_id('clip_', [p['source_id'], s, e]), 'clip ID mismatch')
    require(isinstance(p['items'], list) and p['items'], 'empty items')
    seen = set()
    for item in p['items']:
        exact_keys(item, {'id', 'source_start_ms', 'source_end_ms', 'start_ms', 'end_ms', 'original_text', 'text', 'style', 'reason', 'status'}, 'item')
        a, b, x, y = (integer(item[k], k) for k in ['source_start_ms', 'source_end_ms', 'start_ms', 'end_ms'])
        require(a < b <= d and x < y <= e-s and x == max(a,s)-s and y == min(b,e)-s, 'item time bounds mismatch')
        text_value(item['text'], 'edited text'); text_value(item['original_text'], 'original text'); text_value(item['reason'], 'reason')
        expected = stable_id('cue_', [p['clip_id'], {'start_ms': a, 'end_ms': b, 'text': item['original_text']}])
        require(item['id'] == expected and expected not in seen, 'invalid or duplicate cue ID')
        seen.add(expected)
        require(isinstance(item['status'], str) and item['status'] in STATES, 'invalid review status')
        validate_style(item['style'],item['text'])
    return p


def apply_review(proposal, review):
    validate_proposal(proposal)
    exact_keys(review, {'schema', 'clip_id', 'decisions'}, 'review')
    require(review['schema'] == VERSION and review['clip_id'] == proposal['clip_id'], 'review targets another clip/schema')
    require(isinstance(review['decisions'], list), 'decisions must be array')
    result = deepcopy(proposal)
    items = {i['id']: i for i in result['items']}
    seen = set()
    for decision in review['decisions']:
        exact_keys(decision, {'id', 'status', 'text', 'style'}, 'decision')
        key = decision['id']
        require(isinstance(key, str) and key in items and key not in seen, 'unknown or duplicate decision ID')
        seen.add(key)
        require(isinstance(decision['status'], str) and decision['status'] in STATES, 'invalid decision status')
        text_value(decision['text'], 'edited text'); validate_style(decision['style'],decision['text'])
        items[key].update(deepcopy(decision))
    return validate_proposal(result)


def export_plan(proposal):
    validate_proposal(proposal)
    require(all(i['status'] != 'pending' for i in proposal['items']), 'review all items before export')
    operations = []
    for i in proposal['items']:
        if i['status'] == 'adopted':
            operations.append({'id': i['id'], 'start_ms': i['start_ms'], 'end_ms': i['end_ms'], 'text': i['text'], 'style': deepcopy(i['style'])})
    p = {'schema': VERSION, 'kind': 'edit-plan', 'clip_id': proposal['clip_id'], 'source_id': proposal['source_id'],
         'range_start_ms': proposal['range_start_ms'], 'range_end_ms': proposal['range_end_ms'],
         'timebase': 'clip-relative-ms', 'operations': operations}
    p['revision_id'] = stable_id('rev_', p)
    return validate_plan(p)


def validate_plan(p):
    exact_keys(p, {'schema', 'kind', 'clip_id', 'source_id', 'range_start_ms', 'range_end_ms', 'timebase', 'operations', 'revision_id'}, 'plan')
    require(p['schema'] == VERSION and p['kind'] == 'edit-plan' and p['timebase'] == 'clip-relative-ms', 'unsupported plan schema')
    require(isinstance(p['source_id'], str) and re.fullmatch(r'src_[0-9a-f]{24}', p['source_id']), 'invalid source ID')
    s, e = integer(p['range_start_ms'], 'range start'), integer(p['range_end_ms'], 'range end')
    require(s < e and p['clip_id'] == stable_id('clip_', [p['source_id'], s, e]), 'invalid clip range or ID')
    require(isinstance(p['operations'], list), 'operations must be array')
    seen = set()
    for op in p['operations']:
        exact_keys(op, {'id', 'start_ms', 'end_ms', 'text', 'style'}, 'operation')
        key = op['id']
        require(isinstance(key, str) and re.fullmatch(r'cue_[0-9a-f]{24}', key) and key not in seen, 'duplicate/invalid operation ID')
        seen.add(key)
        a, b = integer(op['start_ms'], 'operation start'), integer(op['end_ms'], 'operation end')
        require(a < b <= e-s, 'operation outside clip bounds')
        text_value(op['text'], 'operation text'); validate_style(op['style'],op['text'])
    require(p['revision_id'] == stable_id('rev_', {k:v for k,v in p.items() if k != 'revision_id'}), 'revision digest mismatch')
    return p


def reconcile(plan, existing=None):
    """Pure adapter preview: stable-ID upserts, never duplicated. NOT media application."""
    validate_plan(plan)
    if existing is not None:
        validate_plan(existing)
        require(existing['clip_id'] == plan['clip_id'], 'cannot reconcile different clips')
    old = {o['id']: o for o in (existing or {'operations': []})['operations']}
    new = {o['id']: o for o in plan['operations']}
    return {'kind': 'adapter-preview-only', 'clip_id': plan['clip_id'],
            'create': [k for k in new if k not in old],
            'update': [k for k in new if k in old and new[k] != old[k]],
            'unchanged': [k for k in new if k in old and new[k] == old[k]],
            'remove_managed': [k for k in old if k not in new],
            'note': 'No media or editor was modified. A future adapter must scope IDs to its managed layer.'}


def write_new(path, content):
    """Exclusive create: never overwrite inputs or previous outputs."""
    with Path(path).open('x', encoding='utf-8') as f:
        f.write(content)


def json_text(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
