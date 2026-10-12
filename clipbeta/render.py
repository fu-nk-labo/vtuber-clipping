"""Bounded offline FFmpeg preview renderer. No network or editor integration."""
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unicodedata
import wave
from pathlib import Path
from .engine import require, validate_plan, ValidationError, write_new, json_text

FPS=30
RATE=48000
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
BOLD=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
COLORS={'white':'white','yellow':'0xFFDF58','red':'0xFF7878','cyan':'0x63E4EE','pink':'0xFF8ED1'}
RESOLUTIONS={(640,360),(1280,720),(1920,1080)}


class RenderCancelled(ValidationError):
    """A requested local render was stopped without publishing a finished MP4."""


def check_cancelled(cancelled):
    if cancelled is not None and (cancelled() if callable(cancelled) else cancelled.is_set()):
        raise RenderCancelled('render cancelled')


def render_options(options=None):
    """Validate rendering configuration without installing or downloading anything."""
    require(options is None or isinstance(options,dict),'render options must be an object')
    options=dict(options or {})
    allowed={'width','height','ffmpeg_path','ffprobe_path','font_path','bold_font_path','max_clip_seconds'}
    require(not set(options)-allowed,'unknown render option')
    options.setdefault('width',640);options.setdefault('height',360)
    require(type(options['width']) is int and type(options['height']) is int and
            (options['width'],options['height']) in RESOLUTIONS,
            'render size must be 640x360, 1280x720, or 1920x1080')
    options.setdefault('max_clip_seconds',60)
    maximum=options['max_clip_seconds']
    require(type(maximum) in (int,float) and math.isfinite(maximum) and 0<maximum<=300,
            'max_clip_seconds must be greater than zero and at most 300')
    for key in allowed-{'width','height','max_clip_seconds'}:
        if options.get(key) is None:
            options[key]=os.environ.get('CLIPBETA_'+key.upper()) or None
        require(options[key] is None or isinstance(options[key],(str,os.PathLike)),f'{key} must be a local path')
        require(options[key] is None or bool(str(options[key]).strip()),f'{key} must not be empty')
    return options


def resolve_executable(name,configured=None):
    """Resolve a single executable; paths with spaces remain one argv element."""
    value=os.path.expanduser(str(configured)) if configured is not None else name
    resolved=shutil.which(value)
    require(resolved is not None,f'{name} must already be installed; configure {name}_path if it is not on PATH')
    require(Path(resolved).suffix.lower() not in {'.bat','.cmd'},
            f'{name}_path must select a native executable, not a shell wrapper')
    return str(Path(resolved).resolve())


def font_directories():
    roots=[Path('/usr/share/fonts/opentype/noto'),Path('/usr/share/fonts/truetype/noto'),
           Path.home()/'.local/share/fonts',Path('/Library/Fonts'),Path.home()/'Library/Fonts']
    if os.environ.get('WINDIR'):
        roots.append(Path(os.environ['WINDIR'])/'Fonts')
    elif os.name=='nt':
        roots.append(Path('C:/Windows/Fonts'))
    if os.environ.get('LOCALAPPDATA'):
        roots.append(Path(os.environ['LOCALAPPDATA'])/'Microsoft/Windows/Fonts')
    return roots


def discover_fonts(options=None):
    options=render_options(options)
    variable=['NotoSansJP[wght].ttf','NotoSansJP-VariableFont_wght.ttf','NotoSansJP-VF.ttf']
    def choose(key,bold):
        if options[key] is not None:
            path=Path(options[key]).expanduser()
            require(path.is_file(),f'{key} must point to an existing font file')
            return path.resolve()
        weight='Bold' if bold else 'Regular'
        names=[f'NotoSansCJK-{weight}.ttc',f'NotoSansCJKjp-{weight}.otf',
               f'NotoSansJP-{weight}.ttf',f'NotoSansJP-{weight}.otf']+variable
        paths=[BOLD if bold else FONT]
        paths.extend(root/name for root in font_directories() for name in names)
        for path in paths:
            if path.is_file():return path.resolve()
        raise ValidationError(f'Install Noto Sans CJK / Noto Sans JP fonts, or configure {key}')
    return choose('font_path',False),choose('bold_font_path',True)


def _stop_process(process):
    if process.poll() is not None:return
    try:process.terminate()
    except OSError:
        # A short-lived process can exit between poll() and terminate().
        if process.poll() is None:raise
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill();process.wait()


def run(args,cwd=None,*,cancelled=None,progress=None,duration=None,timeout=180):
    """Run argv without a shell, draining both streams without pipe deadlocks.

    With FFmpeg's ``-progress pipe:1``, progress receives a fraction from 0 to 1.
    Temporary output streams also permit cancellation while FFmpeg is silent.
    """
    check_cancelled(cancelled)
    with tempfile.TemporaryDirectory(prefix='clipbeta-process-') as scratch, \
            (Path(scratch)/'stdout').open('w+b') as stdout, \
            (Path(scratch)/'stderr').open('w+b') as stderr, \
            (Path(scratch)/'stdout').open('rb') as reader:
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        try:process=subprocess.Popen(args,cwd=cwd,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,shell=False,
                                     creationflags=flags)
        except OSError as error:raise ValidationError(f'could not start media command: {error}') from error
        started=time.monotonic();offset=0;pending=b'';last=0.0
        def read_progress():
            nonlocal offset,pending,last
            if progress is None or not duration:return
            reader.seek(offset);pending+=reader.read();offset=reader.tell()
            lines=pending.split(b'\n');pending=lines.pop()
            for line in lines:
                key,_,value=line.partition(b'=')
                if key==b'out_time_us':
                    try:fraction=max(last,min(1.0,int(value)/(duration*1_000_000)))
                    except ValueError:continue
                    last=fraction;progress(fraction)
        try:
            while process.poll() is None:
                check_cancelled(cancelled)
                if time.monotonic()-started>timeout:
                    raise ValidationError(f'media command exceeded {timeout:g}-second preview timeout')
                read_progress();time.sleep(.1)
            check_cancelled(cancelled);read_progress()
            stderr.seek(0)
            if process.returncode:
                raise ValidationError('FFmpeg/ffprobe failed: '+stderr.read().decode(errors='replace')[-1800:])
            stdout.seek(0);return stdout.read()
        finally:_stop_process(process)


def hash_file(path,cancelled=None):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            check_cancelled(cancelled);digest.update(chunk)
    return digest.hexdigest()


def tools_ready(options=None):
    options=render_options(options)
    ffmpeg=resolve_executable('ffmpeg',options['ffmpeg_path'])
    ffprobe=resolve_executable('ffprobe',options['ffprobe_path'])
    regular,bold=discover_fonts(options)
    return {'ffmpeg':ffmpeg,'ffprobe':ffprobe,'font':regular,'bold':bold}


def local_media(path):
    path=Path(path).resolve()
    require(path.is_file() and path.suffix.lower() in {'.mp4','.m4v','.mov','.mkv','.webm','.avi'},'expected an existing local MP4/MOV/MKV/WebM/AVI file; URLs/playlists unsupported')
    return path


def probe(path,options=None,cancelled=None):
    options=render_options(options)
    executable=resolve_executable('ffprobe',options['ffprobe_path'])
    return json.loads(run([executable,'-v','error','-protocol_whitelist','file','-format_whitelist','mov,matroska,webm,avi','-show_streams','-show_format','-of','json',str(local_media(path))],cancelled=cancelled))


# A deliberately small line-start rule, not morphological segmentation or a
# complete Japanese typography engine. ASCII-only text keeps its old wrapping.
JAPANESE_NO_START=frozenset('、。，．・：；？！ーｰ〜～…‥々ヽヾゝゞ'
                          '）］｝」』】〕〉》〙〗〟’”»'
                          'ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ'
                          'ｧｨｩｪｫｯｬｭｮㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ')


def caption_lines(text):
    """Return wrapped lines of original (code-point index, character) pairs.

    Back up an automatic break instead of overflowing the 20-character budget
    when its next character cannot start a Japanese line. Explicit newlines are
    preserved, including a user-requested punctuation/small-kana line start.
    """
    paragraphs=[[]]
    for index,char in enumerate(text):
        if char=='\n':paragraphs.append([])
        else:paragraphs[-1].append((index,' ' if char=='\t' else char))
    lines=[]
    for paragraph in paragraphs:
        if not paragraph:lines.append([]);continue
        japanese=any('\u3040'<=c<='\u30ff' or '\u3400'<=c<='\u9fff' or '\uff61'<=c<='\uff9f' for _,c in paragraph)
        groups=[]
        for pair in paragraph:
            char=pair[1]
            # Keep marks and joined sequences with their preceding character;
            # retain the existing non-combining-code-point width accounting.
            attached=unicodedata.category(char).startswith('M') or char=='\u200d'
            if groups and (attached or groups[-1][-1][1]=='\u200d'):groups[-1].append(pair)
            else:groups.append([pair])
        widths=[sum(not unicodedata.combining(c) for _,c in group) for group in groups]
        require(all(width<=20 for width in widths),'caption character group exceeds the 20-character line limit')
        def prohibited(group):
            char=group[0][1]
            return (char in JAPANESE_NO_START or unicodedata.category(char).startswith('M') or char=='\u200d'
                    or japanese and (char in ',.!?:;)]}' or unicodedata.category(char) in {'Pe','Pf'}))
        start=0
        while start<len(groups):
            end=start;width=0
            while end<len(groups) and width+widths[end]<=20:
                width+=widths[end];end+=1
            if end<len(groups):
                while end>start and prohibited(groups[end]):end-=1
            require(end>start,'cannot wrap Japanese caption within 20 characters; add an explicit newline or shorten it')
            lines.append([pair for group in groups[start:end] for pair in group]);start=end
    require(len(lines)<=3,'preview supports at most 3 wrapped caption lines (20 characters each, combining marks excluded)')
    return lines


def wrap_caption(text):
    return '\n'.join(''.join(char for _,char in line) for line in caption_lines(text))


def validate_render_plan(plan,options=None):
    validate_plan(plan)
    options=render_options(options)
    duration=(plan['range_end_ms']-plan['range_start_ms'])/1000
    maximum=options['max_clip_seconds']
    require(0<duration<=maximum,f'preview supports clips up to {maximum:g} seconds')
    require(len(plan['operations'])<=50,'preview supports at most 50 caption operations')
    ordered=sorted(plan['operations'],key=lambda o:o['start_ms'])
    require(all(a['end_ms']<=b['start_ms'] for a,b in zip(ordered,ordered[1:])),'overlapping captions require manual separation before preview')
    for op in ordered:wrap_caption(op['text'])
    return duration


def synth_se(plan,path,options=None,cancelled=None):
    duration=validate_render_plan(plan,options)
    frames=round(duration*RATE);events=[]
    for op in plan['operations']:
        kind=op['style']['se']
        if kind=='none':continue
        start=round(op['start_ms']*RATE/1000)
        length=min(round(.32*RATE),frames-start)
        freq={'pop':720,'surprise':1040,'laugh':520}[kind]
        events.append((start,length,freq,kind))
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE)
        # Bound working memory even when the optional five-minute cap is used.
        for offset in range(0,frames,RATE):
            check_cancelled(cancelled);values=[0.0]*min(RATE,frames-offset)
            for start,length,freq,kind in events:
                for sample in range(max(offset,start),min(offset+len(values),start+length)):
                    j=sample-start;t=j/RATE
                    envelope=math.sin(math.pi*j/max(length,1))**2
                    pulse=(.5+.5*math.sin(2*math.pi*12*t)) if kind=='laugh' else 1
                    values[sample-offset]+=.2*envelope*pulse*math.sin(2*math.pi*freq*t)
            f.writeframes(b''.join(struct.pack('<h',round(max(-.9,min(.9,v))*32767)) for v in values))


def caption_runs(plan,options=None,fonts=None):
    # Pillow is used only for font metrics; FFmpeg paints literal text files.
    try:
        from PIL import ImageFont
    except ImportError as error:
        raise ValidationError('Word-style preview needs the already-installed Pillow font metrics package') from error
    options=render_options(options);regular,bold=fonts or discover_fonts(options)
    scale=options['width']/640;runs=[]
    for i,op in enumerate(plan['operations']):
        lines=[]
        for source_line in caption_lines(op['text']):
            line=[]
            for index,c in source_line:
                style=next((span for span in op['style'].get('spans',[]) if span['start']<=index<span['end']),op['style'])
                key=(style['color'],style['emphasis'])
                if line and line[-1][0]==key:line[-1][1]+=c
                else:line.append([key,c])
            lines.append(line)
        for line_index,line in enumerate(lines):
            metrics=[]
            for (color,emphasis),text in line:
                font=bold if emphasis else regular;size=round((30 if emphasis else 25)*scale)
                try:width=ImageFont.truetype(str(font),size).getlength(text)
                except OSError as error:
                    raise ValidationError('Could not read the configured font; select a valid Noto TTF, TTC, or OTF file') from error
                metrics.append((text,color,font,size,width))
            x=(options['width']-sum(m[4] for m in metrics))/2
            require(x>=12*scale,'caption exceeds safe preview width')
            baseline=round((342-(len(lines)-1-line_index)*36)*scale)
            for j,(text,color,font,size,width) in enumerate(metrics):
                runs.append({'file':f'run-{i}-{line_index}-{j}.txt','text':text,'font':str(font),'size':size,'color':color,'x':round(x,3),'baseline':baseline,'start':op['start_ms']/1000,'end':op['end_ms']/1000})
                x+=width
    return runs


def video_filter(plan,options=None,runs=None):
    options=render_options(options);width=options['width'];height=options['height'];scale=width/640
    z='1';tx='0.5';ty='0.5'
    for op in reversed(plan['operations']):
        mode=op['style']['zoom']
        if mode=='none':continue
        a,b=op['start_ms']/1000,op['end_ms']/1000
        condition=f'gte(on/{FPS},{a})*lt(on/{FPS},{b})'
        value='1.18' if mode=='punch_in' else f'1+0.12*(on/{FPS}-{a})/({b-a})'
        z=f'if({condition},{value},{z})'
        target=op['style'].get('zoom_target',{'x':.5,'y':.5})
        tx=f"if({condition},{target['x']},{tx})";ty=f"if({condition},{target['y']},{ty})"
    chain=f"fps={FPS},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,zoompan=z='{z}':x='max(0,min(iw-iw/zoom,iw*({tx})-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*({ty})-ih/zoom/2))':d=1:s={width}x{height}:fps={FPS}"
    chain+=f",drawbox=x=0:y={round(250*scale)}:w={width}:h={round(110*scale)}:color=black@0.72:t=fill"
    for run in caption_runs(plan,options) if runs is None else runs:
        chain+=f",drawtext=fontfile='{run['font']}':textfile={run['file']}:expansion=none:fontcolor={COLORS[run['color']]}:fontsize={run['size']}:borderw={round(2*scale)}:bordercolor=black:x={run['x']}:y={run['baseline']}-ascent:enable='gte(t,{run['start']})*lt(t,{run['end']})'"
    return chain


def render(plan,media,out_dir,options=None,progress=None,cancelled=None):
    """Render a new local output folder; callbacks run on the calling thread.

    ``progress`` receives {stage, progress, message}, with progress in [0, 1].
    ``cancelled`` may be a boolean-returning callable or threading.Event.
    Configurable paths can also use CLIPBETA_<OPTION_NAME> environment variables.
    """
    def report(stage,value,message):
        check_cancelled(cancelled)
        if progress is not None:progress({'stage':stage,'progress':value,'message':message})
        check_cancelled(cancelled)
    report('preparing',0.0,'Checking local render configuration')
    options=render_options(options);ready=tools_ready(options)
    duration=validate_render_plan(plan,options);media=local_media(media)
    options['ffmpeg_path']=ready['ffmpeg'];options['ffprobe_path']=ready['ffprobe']
    report('probing',.03,'Reading the local media streams')
    info=probe(media,options,cancelled);streams=info['streams'];videos=[s for s in streams if s['codec_type']=='video']
    require(videos,'media has no video stream')
    require(videos[0]['width']*videos[0]['height']<=3840*2160,'preview input limited to 4K')
    actual=float(info['format']['duration'])
    require(math.isfinite(actual) and plan['range_end_ms']/1000<=actual+.001,'plan exceeds actual media duration')
    runs=caption_runs(plan,options,(ready['font'],ready['bold']))
    report('captions',.08,'Preparing captions and generated sound effects')
    out=Path(out_dir).resolve();out.mkdir(exist_ok=False)
    write_new(out/'edit-plan.json',json_text(plan))
    # Only controlled relative filenames are used in filters. In particular,
    # Windows drive colons, spaces, apostrophes, and backslashes never need
    # FFmpeg filter escaping, even when the source font path contains them.
    font_names={str(ready['font']):'font-regular.ttf',str(ready['bold']):'font-bold.ttf'}
    shutil.copyfile(ready['font'],out/'font-regular.ttf')
    shutil.copyfile(ready['bold'],out/'font-bold.ttf')
    for run_spec in runs:run_spec['font']=font_names[run_spec['font']]
    for i,op in enumerate(plan['operations']):write_new(out/f'caption-{i}.txt',wrap_caption(op['text']))
    for run_spec in runs:write_new(out/run_spec['file'],run_spec['text'])
    synth_se(plan,out/'synthetic-se.wav',options,cancelled)
    vf=video_filter(plan,options,runs);write_new(out/'video-filter.txt',vf+'\n')
    audio=any(s['codec_type']=='audio' for s in streams)
    graph=f'[0:v:0]setpts=PTS-STARTPTS,{vf}[v];'
    if audio:graph+=f'[0:a:0]asetpts=PTS-STARTPTS,apad,atrim=duration={duration}[base];[base][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=0,atrim=duration={duration}[a]'
    else:graph+=f'[1:a]atrim=duration={duration}[a]'
    command=[ready['ffmpeg'],'-hide_banner','-loglevel','error','-nostdin','-n','-progress','pipe:1','-nostats','-protocol_whitelist','file','-format_whitelist','mov,matroska,webm,avi','-ss',str(plan['range_start_ms']/1000),'-t',str(duration),'-i',str(media),'-i','synthetic-se.wav','-filter_complex',graph,'-map','[v]','-map','[a]','-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1','-c:a','aac','-b:a','128k','-ar',str(RATE),'-map_metadata','-1','-fflags','+bitexact','-flags:v','+bitexact','-flags:a','+bitexact','-movflags','+faststart','preview.partial.mp4']
    write_new(out/'command.json',json_text(command))
    partial=out/'preview.partial.mp4';finished=out/'preview.mp4';owns_finished=False
    try:
        report('rendering',.15,'Rendering the local MP4')
        run(command,cwd=out,cancelled=cancelled,duration=duration,
            progress=lambda fraction:report('rendering',.15+.75*fraction,'Rendering the local MP4'),
            timeout=max(180,duration*12))
        report('verifying',.92,'Verifying the rendered video')
        result=probe(partial,options,cancelled)
        require(abs(float(result['format']['duration'])-duration)<=1/FPS+.03,'rendered duration outside tolerance')
        output_video=next((s for s in result['streams'] if s['codec_type']=='video'),{})
        require((output_video.get('width'),output_video.get('height'))==(options['width'],options['height']),
                'rendered dimensions do not match the selected preset')
        manifest={'renderer':'clipbeta-ffmpeg-preview/3','synthetic_se':True,'source_sha256':hash_file(media,cancelled),'plan_revision':plan['revision_id'],'duration_seconds':duration,'fps':FPS,'width':options['width'],'height':options['height'],'video_sha256':hash_file(partial,cancelled),'preset_semantics':{'punch_in':1.18,'slow_in':'linear 1.0 to 1.12 over caption interval','se':'0.32-second generated tones, not licensed sound assets','caption':'Unicode code-point spans; literal text runs, burned-in with bottom band','zoom_target':'normalized source-frame point; crop clamped inside frame'},'ffmpeg_version':run([ready['ffmpeg'],'-version'],cancelled=cancelled).decode().splitlines()[0]}
        check_cancelled(cancelled)
        if finished.exists():raise FileExistsError(str(finished))
        # Exclusive creation also protects against a concurrent file appearing
        # after the initial output-folder check, on Windows and POSIX alike.
        with finished.open('xb') as target,partial.open('rb') as source:
            owns_finished=True
            try:
                while True:
                    check_cancelled(cancelled);chunk=source.read(1024*1024)
                    if not chunk:break
                    target.write(chunk)
            except BaseException:
                target.close();finished.unlink(missing_ok=True);raise
        partial.unlink()
        result['format']['filename']=str(finished)
        write_new(out/'manifest.json',json_text(manifest));write_new(out/'probe.json',json_text(result))
        report('complete',1.0,'Local MP4 is ready')
        return finished
    except RenderCancelled:
        partial.unlink(missing_ok=True)
        # A cancellation in the final progress callback still must not expose
        # an output reported as successfully completed.
        if owns_finished:finished.unlink(missing_ok=True)
        raise


def synthetic_source(path,options=None,cancelled=None):
    ready=tools_ready(options);path=Path(path).resolve()
    require(not path.exists(),'source output already exists')
    vf="drawbox=x=0:y=0:w=640:h=42:color=black:t=fill,drawtext=fontfile='font-regular.ttf':text='SYNTHETIC LOCAL DEMO - NO TWITCH MEDIA':expansion=none:fontcolor=white:fontsize=20:x=(w-text_w)/2:y=10"
    with tempfile.TemporaryDirectory(prefix='clipbeta-synthetic-') as scratch:
        shutil.copyfile(ready['font'],Path(scratch)/'font-regular.ttf')
        run([ready['ffmpeg'],'-hide_banner','-loglevel','error','-nostdin','-n','-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=15','-f','lavfi','-i','anullsrc=r=48000:cl=mono','-vf',vf,'-t','15','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1','-c:a','aac','-map_metadata','-1',str(path)],cwd=scratch,cancelled=cancelled)
    return path
