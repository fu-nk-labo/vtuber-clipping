"""Single-user loopback app. Models stay local; imports are copied locally."""
from __future__ import annotations
import json
import mimetypes
import re
import secrets
import shutil
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .engine import ValidationError, require, make_proposal, apply_review, export_plan
from .review_html import render_review

MAX_JSON=1024*1024
SAFE_ID=re.compile(r'^[a-f0-9]{32}$')


def json_bytes(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')


def strict_json(data):
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result,'duplicate JSON key')
            result[k]=v
        return result
    def bad(value):raise ValidationError('non-finite JSON number')
    return json.loads(data,object_pairs_hook=pairs,parse_constant=bad)


@dataclass
class Job:
    id: str
    kind: str
    directory: Path
    state: str='running'
    progress: dict=field(default_factory=lambda:{'stage':'starting','progress':0,'message':'開始しています'})
    result: dict|None=None
    error: str|None=None
    cancel_event: threading.Event=field(default_factory=threading.Event)

    def public(self):
        return {'id':self.id,'kind':self.kind,'state':self.state,'progress':self.progress,'result':self.result,'error':self.error}


class AppState:
    def __init__(self,workspace,max_import_bytes=8*1024**3,analyzer=None,renderer=None,downloader=None):
        self.workspace=Path(workspace).resolve();self.workspace.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32)
        self.max_import_bytes=max_import_bytes
        self.assets={};self.jobs={};self.analyses={};self.lock=threading.RLock()
        self.analyzer=analyzer;self.renderer=renderer;self.downloader=downloader

    def asset(self,key,kind=None):
        require(isinstance(key,str) and SAFE_ID.fullmatch(key),'invalid asset ID')
        with self.lock:value=self.assets.get(key)
        require(value is not None and (kind is None or value['kind']==kind),'asset not found')
        return value

    def import_stream(self,stream,name,kind,length):
        require(kind in {'video','transcript'},'unsupported import kind')
        require(isinstance(name,str) and 0<len(name)<=240,'invalid filename')
        name=name.replace('\\','/').split('/')[-1]
        suffix=Path(name).suffix.lower()
        allowed={'.mp4','.m4v','.mov','.mkv','.webm','.avi'} if kind=='video' else {'.srt','.json'}
        require(suffix in allowed,'unsupported file extension')
        require(type(length) is int and 0<length<=min(self.max_import_bytes,8*1024**2 if kind=='transcript' else self.max_import_bytes),'file size exceeds local import limit')
        require(shutil.disk_usage(self.workspace).free>length+64*1024**2,'insufficient local disk space')
        key=uuid.uuid4().hex;folder=self.workspace/('asset-'+key);folder.mkdir()
        path=folder/('source'+suffix)
        remaining=length
        try:
            with path.open('xb') as f:
                while remaining:
                    block=stream.read(min(1024*1024,remaining))
                    require(bool(block),'incomplete upload')
                    f.write(block);remaining-=len(block)
        except Exception:
            # This directory was created by this import only; no existing files removed.
            path.unlink(missing_ok=True);folder.rmdir();raise
        with self.lock:self.assets[key]={'path':path,'kind':kind,'name':name}
        return {'id':key,'kind':kind,'name':name,'size':length}

    def get_job(self,key):
        require(isinstance(key,str) and SAFE_ID.fullmatch(key),'invalid job ID')
        with self.lock:job=self.jobs.get(key)
        require(job is not None,'job not found')
        return job

    def new_job(self,kind,work):
        with self.lock:
            require(not any(j.state in {'running','cancelling'} for j in self.jobs.values()),'another job is running; cancel it and wait before starting')
            key=uuid.uuid4().hex;folder=self.workspace/('job-'+key);folder.mkdir()
            job=Job(key,kind,folder);self.jobs[key]=job
        def execute():
            try:
                result=work(job)
                with self.lock:
                    if job.cancel_event.is_set():job.state='cancelled';job.result=None
                    else:job.result=result;job.state='complete'
            except Exception as error:
                with self.lock:
                    job.state='cancelled' if job.cancel_event.is_set() else 'error'
                    job.error=None if job.state=='cancelled' else str(error)[:1200]
        threading.Thread(target=execute,name='clipbeta-job-'+key,daemon=True).start()
        return job

    def update_progress(self,job,event):
        with self.lock:
            if not job.cancel_event.is_set():job.progress=dict(event)

    def cancel(self,key):
        job=self.get_job(key)
        with self.lock:
            if job.state=='running':job.state='cancelling';job.cancel_event.set()
        return job.public()

    def twitch_list(self,payload):
        from .twitch_intake import parse_twitch_url
        target=parse_twitch_url(payload.get('url'))
        def work(job):
            if self.downloader is None:
                from . import twitch_download as backend
            else:backend=self.downloader
            return backend.list_archives(target['canonical'],progress=lambda e:self.update_progress(job,e),cancelled=job.cancel_event.is_set)
        return self.new_job('archives',work)

    def twitch_acquire(self,payload):
        from .twitch_intake import parse_twitch_url
        target=parse_twitch_url(payload.get('url'))
        require(target['kind']=='vod','Select a finite VOD, not a live channel')
        def work(job):
            if self.downloader is None:
                from . import twitch_download as backend
            else:backend=self.downloader
            result=backend.acquire(target['canonical'],job.directory/'download',progress=lambda e:self.update_progress(job,e),cancelled=job.cancel_event.is_set)
            path=Path(result['path']).resolve()
            require(path.is_file() and path.is_relative_to(job.directory),'invalid acquired media path')
            key=uuid.uuid4().hex
            with self.lock:self.assets[key]={'path':path,'kind':'video','name':'twitch-vod.mp4','provenance':result.get('metadata',{})}
            return {'id':key,'kind':'video','name':'twitch-vod.mp4','size':path.stat().st_size,'provenance':result.get('metadata',{})}
        return self.new_job('download',work)

    def analyze(self,payload):
        require(isinstance(payload,dict),'expected JSON object')
        media=self.asset(payload.get('media_id'),'video')
        config=dict(payload.get('config') or {})
        allowed={'whisper_model_dir','ollama_model','device','compute_type','allow_cpu_fallback','language','glossary','audio_chunk_seconds','context_window_seconds','max_candidates','llm_backend'}
        require(set(config)<=allowed,'unknown analysis setting')
        transcript_id=payload.get('transcript_id')
        if transcript_id:config['transcript_path']=str(self.asset(transcript_id,'transcript')['path'])
        def work(job):
            if self.analyzer is None:
                from .local_ai import analyze
                analyzer=analyze
            else:analyzer=self.analyzer
            result=analyzer(media['path'],job.directory/'analysis',config,lambda e:self.update_progress(job,e),job.cancel_event.is_set)
            require(isinstance(result,dict) and isinstance(result.get('transcript'),list) and isinstance(result.get('candidates'),list),'invalid analysis result')
            proposals={}
            for c in result['candidates']:
                require(isinstance(c.get('id'),str),'candidate ID missing')
                require(c['id'] not in proposals,'duplicate candidate ID')
                proposals[c['id']]=make_proposal(result['transcript'],c['start_ms'],c['end_ms'],result['duration_ms'])
            with self.lock:self.analyses[job.id]={'media':media,'proposals':proposals,'analysis':result}
            return {'candidates':result['candidates'],'duration_ms':result['duration_ms'],'media_id':payload['media_id'],'metadata':result.get('metadata',{})}
        return self.new_job('analysis',work)

    def proposal(self,job_id,candidate_id,selected_range=None):
        job=self.get_job(job_id)
        require(job.state=='complete' and job.kind=='analysis','analysis not complete')
        with self.lock:analysis=self.analyses[job_id]
        require(isinstance(candidate_id,str) and candidate_id in analysis['proposals'],'unknown candidate')
        proposal=analysis['proposals'][candidate_id]
        if selected_range is not None:
            require(isinstance(selected_range,dict) and set(selected_range)=={'start_ms','end_ms'},'invalid selected range')
            start,end=selected_range['start_ms'],selected_range['end_ms']
            require(type(start) is int and type(end) is int and 0<end-start<=60000,'select a range up to 60 seconds')
            proposal=make_proposal(analysis['analysis']['transcript'],start,end,analysis['analysis']['duration_ms'])
        return analysis,proposal

    def render(self,payload):
        analysis,proposal=self.proposal(payload.get('analysis_job_id'),payload.get('candidate_id'),payload.get('range'))
        plan=export_plan(apply_review(proposal,payload.get('review')))
        options=dict(payload.get('options') or {})
        require(set(options)<={'width','height','font_path','bold_font_path','max_clip_seconds'},'unknown render setting')
        def work(job):
            if self.renderer is None:
                from .render import render
                renderer=render
            else:renderer=self.renderer
            path=Path(renderer(plan,analysis['media']['path'],job.directory/'render',options=options,progress=lambda e:self.update_progress(job,e),cancelled=job.cancel_event.is_set)).resolve()
            require(path.is_relative_to(job.directory) and path.is_file(),'renderer returned an invalid output path')
            key=uuid.uuid4().hex
            with self.lock:self.assets[key]={'path':path,'kind':'output','name':'clip.mp4'}
            return {'output_id':key,'plan':plan}
        return self.new_job('render',work)

    def review_page(self,job_id,candidate_id,selected_range=None):
        _,proposal=self.proposal(job_id,candidate_id,selected_range)
        html=render_review(proposal).replace('保存後、CLIで検証・書き出ししてください。','この画面のMP4書き出しボタンでローカル動画を作成できます。')
        bridge='''<script>
const bridgeContext=__BRIDGE_CONTEXT__;
const renderButton=document.createElement('button');renderButton.textContent='この内容でMP4を書き出す';renderButton.style.background='#396cdf';
renderButton.onclick=()=>{const review={schema:p.schema,clip_id:p.clip_id,decisions:p.items.map(({id,status,text,style})=>({id,status,text,style}))};parent.postMessage({type:'clipbeta-render',review,...bridgeContext,range:{start_ms:p.range_start_ms,end_ms:p.range_end_ms}},location.origin)};
document.querySelector('header').append(renderButton);
function addSeekButtons(){[...document.querySelectorAll('article')].forEach((article,index)=>{const seek=document.createElement('button');seek.textContent='動画のこの字幕へ移動';seek.onclick=()=>parent.postMessage({type:'clipbeta-seek',...bridgeContext,range:{start_ms:p.range_start_ms,end_ms:p.range_end_ms},time:(p.range_start_ms+p.items[index].start_ms)/1000},location.origin);article.append(seek)})}const originalRender=render;render=function(){originalRender();addSeekButtons()};addSeekButtons();
</script>'''
        context=json.dumps({'analysis_job_id':job_id,'candidate_id':candidate_id}).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
        return html.replace('</html>',bridge.replace('__BRIDGE_CONTEXT__',context)+'</html>')


class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version='ClipBetaLocal'
        def log_message(self,*args):pass
        @property
        def origin(self):return f'http://127.0.0.1:{self.server.server_port}'
        def authorized(self,write=False):
            require(self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}','invalid Host')
            require(not self.headers.get('Origin') or self.headers['Origin']==self.origin,'cross-origin request rejected')
            query=parse_qs(urlsplit(self.path).query)
            supplied=self.headers.get('X-Clipbeta-Token') or query.get('token',[''])[0]
            require(isinstance(supplied,str) and supplied.isascii() and secrets.compare_digest(supplied,state.token),'invalid session token')
            require(not self.headers.get('Transfer-Encoding'),'chunked requests unsupported')
        def reply(self,status,data,content_type='application/json; charset=utf-8'):
            if isinstance(data,(dict,list)):data=json_bytes(data)
            if isinstance(data,str):data=data.encode()
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            try:
                self.authorized();url=urlsplit(self.path);q=parse_qs(url.query)
                one=lambda k:q.get(k,[''])[0]
                if url.path=='/':
                    page=(Path(__file__).parent/'web/standalone.html').read_text(encoding='utf-8').replace('__SESSION_TOKEN__',state.token)
                    self.reply(200,page,'text/html; charset=utf-8')
                elif url.path=='/api/current':
                    with state.lock:current=next((j.public() for j in state.jobs.values() if j.state in {'running','cancelling'}),None)
                    self.reply(200,{'job':current})
                elif url.path=='/api/status':
                    with state.lock:value=state.get_job(one('id')).public()
                    self.reply(200,value)
                elif url.path=='/api/review':
                    selected_range={'start_ms':int(one('start_ms')),'end_ms':int(one('end_ms'))} if one('start_ms') or one('end_ms') else None
                    self.reply(200,state.review_page(one('job'),one('candidate'),selected_range),'text/html; charset=utf-8')
                elif url.path=='/media':self.serve_media(state.asset(one('id')),one('download')=='1')
                else:self.reply(404,{'error':'not found'})
            except (ValidationError,ValueError,KeyError) as e:self.reply(400,{'error':str(e)})
            except (BrokenPipeError,ConnectionResetError):pass
        def serve_media(self,asset,download):
            require(asset['kind'] in {'video','output'},'not a media asset')
            path=asset['path'];size=path.stat().st_size;start=0;end=size-1;status=200
            range_header=self.headers.get('Range')
            if range_header:
                m=re.fullmatch(r'bytes=(\d+)-(\d*)',range_header)
                require(m is not None,'unsupported byte range')
                start=int(m[1]);end=min(int(m[2]) if m[2] else size-1,size-1)
                if start>end or start>=size:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return
                status=206
            self.send_response(status);self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            if download:self.send_header('Content-Disposition','attachment; filename="clip.mp4"')
            self.end_headers()
            with path.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    block=f.read(min(1024*1024,remaining))
                    if not block:break
                    self.wfile.write(block);remaining-=len(block)
        def do_POST(self):
            try:
                self.authorized(write=True);url=urlsplit(self.path);q=parse_qs(url.query)
                length=int(self.headers.get('Content-Length','0'))
                if url.path=='/api/import':
                    self.connection.settimeout(120)
                    self.reply(201,state.import_stream(self.rfile,q.get('name',[''])[0],q.get('kind',[''])[0],length));return
                require(0<length<=MAX_JSON,'invalid JSON body length')
                require(self.headers.get('Content-Type','').split(';')[0]=='application/json','expected application/json')
                self.connection.settimeout(15)
                payload=strict_json(self.rfile.read(length));require(isinstance(payload,dict),'expected JSON object')
                if url.path=='/api/twitch':
                    self.reply(202,{'id':state.twitch_list(payload).id})
                elif url.path=='/api/twitch/acquire':self.reply(202,{'id':state.twitch_acquire(payload).id})
                elif url.path=='/api/analyze':self.reply(202,{'id':state.analyze(payload).id})
                elif url.path=='/api/render':self.reply(202,{'id':state.render(payload).id})
                elif url.path=='/api/cancel':self.reply(200,state.cancel(payload.get('id')))
                else:self.reply(404,{'error':'not found'})
            except (ValidationError,ValueError,KeyError,TypeError,OSError) as e:self.reply(400,{'error':str(e)[:1200]})
    return Handler


def serve(workspace,port=0,open_browser=True):
    require(type(port) is int and 0<=port<=65535,'port must be between 0 and 65535')
    state=AppState(workspace)
    server=LocalServer(('127.0.0.1',port),make_handler(state))
    url=f'http://127.0.0.1:{server.server_port}/?token={state.token}'
    print('ClipBeta local app: '+url)
    print('Local imports remain in: '+str(state.workspace))
    if open_browser:webbrowser.open(url)
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:
        for job in state.jobs.values():job.cancel_event.set()
        server.server_close()
