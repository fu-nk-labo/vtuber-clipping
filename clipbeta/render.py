"""Bounded offline FFmpeg preview renderer. No network or editor integration."""
import hashlib
import json
import math
import shutil
import struct
import subprocess
import unicodedata
import wave
from pathlib import Path
from .engine import require, validate_plan, ValidationError, write_new, json_text

FPS=30
RATE=48000
FONT=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
BOLD=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
COLORS={'white':'white','yellow':'0xFFDF58','red':'0xFF7878','cyan':'0x63E4EE','pink':'0xFF8ED1'}


def run(args,cwd=None):
    try:
        result=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    except subprocess.TimeoutExpired as error:
        raise ValidationError('media command exceeded 180-second preview timeout') from error
    if result.returncode:
        raise ValidationError('FFmpeg/ffprobe failed: '+result.stderr.decode(errors='replace')[-1800:])
    return result.stdout


def hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def tools_ready():
    require(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg and ffprobe must already be installed')
    require(FONT.is_file() and BOLD.is_file(),'Preview renderer needs installed Noto Sans CJK regular/bold fonts')


def local_media(path):
    path=Path(path).resolve()
    require(path.is_file() and path.suffix.lower() in {'.mp4','.m4v','.mov','.mkv','.webm','.avi'},'expected an existing local MP4/MOV/MKV/WebM/AVI file; URLs/playlists unsupported')
    return path


def probe(path):
    return json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file','-format_whitelist','mov,matroska,webm,avi','-show_streams','-show_format','-of','json',str(local_media(path))]))


def wrap_caption(text):
    lines=[]
    for line in text.replace('\t',' ').split('\n'):
        chunk='';width=0
        for c in line:
            w=0 if unicodedata.combining(c) else 2
            if width+w>40:lines.append(chunk);chunk='';width=0
            chunk+=c;width+=w
        lines.append(chunk)
    require(len(lines)<=3,'preview supports at most 3 wrapped caption lines (20 characters each, combining marks excluded)')
    return '\n'.join(lines)


def validate_render_plan(plan):
    validate_plan(plan)
    duration=(plan['range_end_ms']-plan['range_start_ms'])/1000
    require(0<duration<=60,'preview supports clips up to 60 seconds')
    require(len(plan['operations'])<=50,'preview supports at most 50 caption operations')
    ordered=sorted(plan['operations'],key=lambda o:o['start_ms'])
    require(all(a['end_ms']<=b['start_ms'] for a,b in zip(ordered,ordered[1:])),'overlapping captions require manual separation before preview')
    for op in ordered:wrap_caption(op['text'])
    return duration


def synth_se(plan,path):
    duration=validate_render_plan(plan)
    values=[0.0]*round(duration*RATE)
    for op in plan['operations']:
        kind=op['style']['se']
        if kind=='none':continue
        start=round(op['start_ms']*RATE/1000)
        length=min(round(.32*RATE),len(values)-start)
        freq={'pop':720,'surprise':1040,'laugh':520}[kind]
        for j in range(length):
            t=j/RATE
            envelope=math.sin(math.pi*j/max(length,1))**2
            pulse=(.5+.5*math.sin(2*math.pi*12*t)) if kind=='laugh' else 1
            values[start+j]+=.2*envelope*pulse*math.sin(2*math.pi*freq*t)
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE)
        f.writeframes(b''.join(struct.pack('<h',round(max(-.9,min(.9,v))*32767)) for v in values))


def caption_runs(plan):
    # Pillow is used only for font metrics; FFmpeg paints literal text files.
    try:
        from PIL import ImageFont
    except ImportError as error:
        raise ValidationError('Word-style preview needs the already-installed Pillow font metrics package') from error
    runs=[]
    for i,op in enumerate(plan['operations']):
        lines=[[]];column=0
        for index,c in enumerate(op['text'].replace('\t',' ')):
            if c=='\n':lines.append([]);column=0;continue
            if column>=20 and not unicodedata.combining(c):lines.append([]);column=0
            if not unicodedata.combining(c):column+=1
            style=next((span for span in op['style'].get('spans',[]) if span['start']<=index<span['end']),op['style'])
            key=(style['color'],style['emphasis'])
            if lines[-1] and lines[-1][-1][0]==key:lines[-1][-1][1]+=c
            else:lines[-1].append([key,c])
        for line_index,line in enumerate(lines):
            metrics=[]
            for (color,emphasis),text in line:
                font=BOLD if emphasis else FONT;size=30 if emphasis else 25
                width=ImageFont.truetype(str(font),size).getlength(text)
                metrics.append((text,color,font,size,width))
            x=(640-sum(m[4] for m in metrics))/2
            require(x>=12,'caption exceeds safe preview width')
            baseline=342-(len(lines)-1-line_index)*36
            for j,(text,color,font,size,width) in enumerate(metrics):
                runs.append({'file':f'run-{i}-{line_index}-{j}.txt','text':text,'font':str(font),'size':size,'color':color,'x':round(x,3),'baseline':baseline,'start':op['start_ms']/1000,'end':op['end_ms']/1000})
                x+=width
    return runs


def video_filter(plan):
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
    chain=f"fps={FPS},scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1,zoompan=z='{z}':x='max(0,min(iw-iw/zoom,iw*({tx})-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*({ty})-ih/zoom/2))':d=1:s=640x360:fps={FPS}"
    chain+=",drawbox=x=0:y=250:w=640:h=110:color=black@0.72:t=fill"
    for run in caption_runs(plan):
        chain+=f",drawtext=fontfile='{run['font']}':textfile={run['file']}:expansion=none:fontcolor={COLORS[run['color']]}:fontsize={run['size']}:borderw=2:bordercolor=black:x={run['x']}:y={run['baseline']}-ascent:enable='gte(t,{run['start']})*lt(t,{run['end']})'"
    return chain


def render(plan,media,out_dir):
    tools_ready();duration=validate_render_plan(plan);media=local_media(media)
    info=probe(media);streams=info['streams'];videos=[s for s in streams if s['codec_type']=='video']
    require(videos,'media has no video stream')
    require(videos[0]['width']*videos[0]['height']<=3840*2160,'preview input limited to 4K')
    actual=float(info['format']['duration'])
    require(math.isfinite(actual) and plan['range_end_ms']/1000<=actual+.001,'plan exceeds actual media duration')
    runs=caption_runs(plan)
    out=Path(out_dir).resolve();out.mkdir(exist_ok=False)
    write_new(out/'edit-plan.json',json_text(plan))
    for i,op in enumerate(plan['operations']):write_new(out/f'caption-{i}.txt',wrap_caption(op['text']))
    for run_spec in runs:write_new(out/run_spec['file'],run_spec['text'])
    synth_se(plan,out/'synthetic-se.wav')
    vf=video_filter(plan);write_new(out/'video-filter.txt',vf+'\n')
    audio=any(s['codec_type']=='audio' for s in streams)
    graph=f'[0:v:0]setpts=PTS-STARTPTS,{vf}[v];'
    if audio:graph+=f'[0:a:0]asetpts=PTS-STARTPTS,apad,atrim=duration={duration}[base];[base][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=0,atrim=duration={duration}[a]'
    else:graph+=f'[1:a]atrim=duration={duration}[a]'
    command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-protocol_whitelist','file','-format_whitelist','mov,matroska,webm,avi','-ss',str(plan['range_start_ms']/1000),'-t',str(duration),'-i',str(media),'-i','synthetic-se.wav','-filter_complex',graph,'-map','[v]','-map','[a]','-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1','-c:a','aac','-b:a','128k','-ar',str(RATE),'-map_metadata','-1','-fflags','+bitexact','-flags:v','+bitexact','-flags:a','+bitexact','-movflags','+faststart','preview.mp4']
    write_new(out/'command.json',json_text(command))
    run(command,cwd=out)
    result=probe(out/'preview.mp4')
    require(abs(float(result['format']['duration'])-duration)<=1/FPS+.03,'rendered duration outside tolerance')
    manifest={'renderer':'clipbeta-ffmpeg-preview/2','synthetic_se':True,'source_sha256':hash_file(media),'plan_revision':plan['revision_id'],'duration_seconds':duration,'fps':FPS,'width':640,'height':360,'video_sha256':hashlib.sha256((out/'preview.mp4').read_bytes()).hexdigest(),'preset_semantics':{'punch_in':1.18,'slow_in':'linear 1.0 to 1.12 over caption interval','se':'0.32-second generated tones, not licensed sound assets','caption':'Unicode code-point spans; literal text runs, burned-in with bottom band','zoom_target':'normalized source-frame point; crop clamped inside frame'},'ffmpeg_version':run(['ffmpeg','-version']).decode().splitlines()[0]}
    write_new(out/'manifest.json',json_text(manifest));write_new(out/'probe.json',json_text(result))
    return out/'preview.mp4'


def synthetic_source(path):
    tools_ready();path=Path(path).resolve()
    require(not path.exists(),'source output already exists')
    vf=f"drawbox=x=0:y=0:w=640:h=42:color=black:t=fill,drawtext=fontfile='{FONT}':text='SYNTHETIC LOCAL DEMO - NO TWITCH MEDIA':expansion=none:fontcolor=white:fontsize=20:x=(w-text_w)/2:y=10"
    run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=15','-f','lavfi','-i','anullsrc=r=48000:cl=mono','-vf',vf,'-t','15','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1','-c:a','aac','-map_metadata','-1',str(path)])
    return path
