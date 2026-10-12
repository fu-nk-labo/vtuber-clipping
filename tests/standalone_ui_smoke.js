// Dependency-free DOM-double QA. This does not substitute for a real browser run.
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {execFileSync}=require('child_process');
const path=require('path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'clipbeta/web/standalone.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];

class Element{
  constructor(tag='div'){
    this.tag=tag;this.children=[];this.style={};this.value='';this.files=[];
    this.checked=false;this.disabled=false;this.textContent='';this.currentTime=0;
    this.contentWindow={};this.classes=new Set();
    this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),contains:x=>this.classes.has(x)};
  }
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(...nodes){this.children=[...nodes]}
  removeAttribute(name){delete this[name]}
  setAttribute(name,value){this[name]=value}
  scrollIntoView(){}
  pause(){this.paused=true;this.pauseCount=(this.pauseCount||0)+1}
  load(){this.loadCount=(this.loadCount||0)+1}
  click(){if(!this.disabled&&this.onclick)return this.onclick()}
}
const response=(value,ok=true)=>({ok,json:async()=>value});
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve()};
const sampleResult={media_id:'media-video',duration_ms:4000,candidates:[
  {id:'first',title:'<img src=x onerror=alert(1)>',reason:'literal <script>',start_ms:0,end_ms:1500,evidence:[{quote:'literal evidence'}]},
  {id:'second',title:'Second',reason:'Another synthetic scene',start_ms:2000,end_ms:3500,evidence:[]},
]};
const progress={stage:'testing',progress:.5,message:'Synthetic progress'};
const archiveResult={target:{kind:'channel',canonical:'https://www.twitch.tv/synthetic'},archives:[
  {id:'12345',url:'https://www.twitch.tv/videos/12345',title:'<img src=x> synthetic VOD',duration_ms:4000,is_live:false},
  {id:'live',url:'https://www.twitch.tv/synthetic',title:'Live must stay disabled',duration_ms:4000,is_live:true},
]};

function app(current=null){
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
  const defaults={device:'cpu',language:'ja',model:'qwen3:4b',backend:'ollama',resolution:'1280x720'};
  for(const [id,value] of Object.entries(defaults))elements[id].value=value;
  for(const id of ['previewSection','reviewSection','outputSection'])elements[id].classList.add('hidden');
  const timers=[],calls=[],blobs=[],created=[],handlers={},jobs=new Map();
  const server={analysisState:'complete',renderState:'complete',archivesState:'complete',downloadState:'complete',
    holdUploads:false,pendingUploads:[],holdJobStarts:false,pendingJobStarts:[],current,
    archivesResult:archiveResult,archiveError:null,downloadError:null};
  if(current)jobs.set(current.id,current);
  let analyses=0,renders=0,archives=0,downloads=0;
  const context={
    document:{getElementById:id=>elements[id],createElement:tag=>{const e=new Element(tag);created.push(e);return e}},
    window:{addEventListener:(name,handler)=>handlers[name]=handler},
    location:{origin:'http://127.0.0.1:12345'},
    setTimeout:callback=>{timers.push(callback);return timers.length},
    Blob:class{constructor(parts){blobs.push(parts.join(''))}},
    URL:{createObjectURL:()=> 'blob:synthetic',revokeObjectURL:()=>{}},
    fetch:async(url,options={})=>{
      assert(url.startsWith('/'),'The app must not fetch an external URL');
      assert.equal(options.headers['X-Clipbeta-Token'],'__SESSION_TOKEN__');
      const body=options.headers['Content-Type']==='application/json'?JSON.parse(options.body):options.body;
      calls.push({url,options,body});
      if(url==='/api/current')return response({job:server.current});
      if(url==='/api/twitch'||url==='/api/twitch/acquire'){
        const listing=url==='/api/twitch';
        const id=listing?'archives-'+(++archives):'download-'+(++downloads);
        const error=listing?server.archiveError:server.downloadError;
        jobs.set(id,{id,kind:listing?'archives':'download',state:error?'error':listing?server.archivesState:server.downloadState,
          progress,error,result:listing?server.archivesResult:{id:'acquired-video',kind:'video',name:'synthetic-vod.mp4'}});
        if(server.holdJobStarts)await new Promise(resolve=>server.pendingJobStarts.push(resolve));
        return response({id});
      }
      if(url.startsWith('/api/import?')){
        const kind=new URLSearchParams(url.split('?')[1]).get('kind');
        if(server.holdUploads)await new Promise(resolve=>server.pendingUploads.push(resolve));
        return response({id:'media-'+kind});
      }
      if(url==='/api/analyze'){
        const id='analysis-'+(++analyses);
        jobs.set(id,{id,kind:'analysis',state:server.analysisState,progress,result:sampleResult});
        return response({id});
      }
      if(url==='/api/render'){
        const id='render-'+(++renders);
        jobs.set(id,{id,kind:'render',state:server.renderState,progress,
          result:{output_id:'output-'+renders,plan:{kind:'synthetic-plan',candidate:body.candidate_id}}});
        return response({id});
      }
      if(url.startsWith('/api/status?')){
        const id=new URLSearchParams(url.split('?')[1]).get('id');
        assert(jobs.has(id),'Unknown status job');return response(jobs.get(id));
      }
      if(url==='/api/cancel'){const job=jobs.get(body.id);job.state='cancelled';return response(job)}
      throw Error('Unexpected mocked route '+url);
    },
  };
  vm.createContext(context);vm.runInContext(source,context);
  const harness={elements,server,jobs,calls,blobs,created,timers,context,
    read:expression=>vm.runInContext(expression,context),
    message:(data,changes={})=>handlers.message({origin:context.location.origin,source:elements.review.contentWindow,
      data:{analysis_job_id:vm.runInContext('analysisJob',context),candidate_id:vm.runInContext('selectedCandidate',context),
        range:vm.runInContext('selectedRange',context),...data},...changes}),
    tick:async()=>{const pending=timers.splice(0);pending.forEach(fn=>fn());await settle()},
    candidate:index=>elements.candidates.children[index].children.find(child=>child.tag==='button'),
    archive:index=>elements.archives.children[index].children.find(child=>child.tag==='button'),
    counts:()=>({analyses,renders,archives,downloads}),
  };
  return harness;
}

async function analyzedApp(){
  const h=app();await settle();
  h.elements.videoFile.files=[{name:'synthetic.mp4'}];
  h.elements.transcriptFile.files=[{name:'synthetic.srt'}];
  await h.elements.analyze.onclick();return h;
}

let count=0;
async function test(name,run){await run();count++;console.log('PASS '+name)}

async function main(){
  await test('analysis warnings remain literal and visible even without candidates',async()=>{
    const h=app();await settle();
    h.context.warningResult={...sampleResult,candidates:[],metadata:{warnings:['No sentence boundary <img src=x>']}};
    h.read("displayAnalysis(warningResult,'warn-job')");
    assert.equal(h.elements.analysisWarnings.textContent,'No sentence boundary <img src=x>');
    assert.equal(h.elements.candidates.children.length,0);
    assert(!h.created.some(el=>el.tag==='img'));
  });

  await test('initial reconnect and analysis keep imports local and blank model nullable',async()=>{
    const h=await analyzedApp();
    assert.equal(h.counts().analyses,1);assert.equal(h.elements.candidates.children.length,2);
    const request=h.calls.find(call=>call.url==='/api/analyze');
    assert.equal(request.body.config.whisper_model_dir,null);
    assert.equal(request.body.transcript_id,'media-transcript');
    assert.equal(request.body.config.compute_type,'int8');
    assert.equal(h.elements.analyze.disabled,false);assert.equal(h.elements.cancel.disabled,true);
    assert(h.elements.source.src.startsWith('/media?'));
    assert.equal(h.elements.candidates.children[0].children[0].textContent,sampleResult.candidates[0].title);
    assert(!h.created.some(el=>el.tag==='img'||el.tag==='script'));
  });
  await test('repeat analysis clicks while importing do not duplicate work',async()=>{
    const h=app();await settle();h.elements.videoFile.files=[{name:'synthetic.mp4'}];
    h.server.holdUploads=true;const first=h.elements.analyze.onclick();await settle();
    await h.elements.analyze.onclick();assert.equal(h.server.pendingUploads.length,1);
    assert(h.elements.videoFile.disabled);assert(h.elements.transcriptFile.disabled);
    h.server.holdUploads=false;h.server.pendingUploads.splice(0).forEach(resolve=>resolve());
    await first;assert.equal(h.counts().analyses,1);
    assert.equal(h.calls.filter(call=>call.url.startsWith('/api/import?')).length,1);
  });
  await test('iframe origin, source, and current selection guard messages',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    await h.message({type:'clipbeta-render',review:{}},{origin:'https://untrusted.example'});
    await h.message({type:'clipbeta-render',review:{}},{source:{}});
    assert.equal(h.counts().renders,0);
    await h.message({type:'clipbeta-seek',time:2.5});assert.equal(h.elements.source.currentTime,2.5);
    await h.message({type:'clipbeta-seek',time:NaN});assert.equal(h.elements.source.currentTime,2.5);
    h.elements.back.click();assert(h.elements.reviewSection.classList.contains('hidden'));
    assert.equal(h.elements.review.src,undefined);
    await h.message({type:'clipbeta-render',review:{}});assert.equal(h.counts().renders,0);
  });
  await test('render completion downloads the matching plan; new selection clears stale output',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    await h.message({type:'clipbeta-render',review:{decisions:[]}});
    assert.equal(h.counts().renders,1);assert(!h.elements.outputSection.classList.contains('hidden'));
    assert(h.elements.download.href.endsWith('&download=1'));
    h.elements.savePlan.click();assert.equal(JSON.parse(h.blobs.at(-1)).candidate,'first');
    h.candidate(1).click();assert(h.elements.outputSection.classList.contains('hidden'));
    assert.equal(h.elements.output.src,undefined);assert.equal(h.elements.download.href,undefined);
    assert.equal(h.read('finalPlan'),null);
    const downloads=h.blobs.length;h.elements.savePlan.click();assert.equal(h.blobs.length,downloads);
  });
  await test('replacement render clears old result; repeated render and back are blocked during work',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    await h.message({type:'clipbeta-render',review:{}});h.server.renderState='running';
    const pending=h.message({type:'clipbeta-render',review:{}});await settle();
    assert(h.elements.outputSection.classList.contains('hidden'));assert.equal(h.read('finalPlan'),null);
    await h.message({type:'clipbeta-render',review:{}});assert.equal(h.counts().renders,2);
    h.elements.back.click();h.candidate(1).click();assert.equal(h.read('selectedCandidate'),'first');
    await h.elements.cancel.onclick();await h.tick();await pending;
    assert.equal(h.jobs.get('render-2').state,'cancelled');assert(!h.elements.analyze.disabled);
    assert(h.elements.cancel.disabled);assert(h.elements.outputSection.classList.contains('hidden'));
    assert.match(h.elements.status.textContent,/中止/);
  });
  await test('both configured font paths reach the local render request unchanged',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    h.elements.font.value="C:\\Noto fonts\\Regular's font.ttf";
    h.elements.boldFont.value='C:\\Noto fonts\\Bold font.ttf';
    await h.message({type:'clipbeta-render',review:{}});
    const options=h.calls.find(call=>call.url==='/api/render').body.options;
    assert.equal(options.font_path,h.elements.font.value);
    assert.equal(options.bold_font_path,h.elements.boldFont.value);
    assert.equal(options.width,1280);assert.equal(options.height,720);
  });
  await test('changing the input unloads hidden source and output playback',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    await h.message({type:'clipbeta-render',review:{}});
    h.elements.source.paused=false;h.elements.output.paused=false;
    const sourceLoads=h.elements.source.loadCount||0,outputLoads=h.elements.output.loadCount||0;
    h.elements.videoFile.onchange();
    for(const id of ['source','output']){assert(h.elements[id].paused);assert.equal(h.elements[id].src,undefined)}
    assert(h.elements.source.loadCount>sourceLoads);assert(h.elements.output.loadCount>outputLoads);
    assert(h.elements.previewSection.classList.contains('hidden'));
    assert(h.elements.outputSection.classList.contains('hidden'));assert.equal(h.read('finalPlan'),null);
  });
  await test('reload reconnects to a running analysis and exposes its completed candidates',async()=>{
    const current={id:'restored-analysis',kind:'analysis',state:'running',progress,result:sampleResult};
    const h=app(current);await settle();assert(h.elements.analyze.disabled);assert(!h.elements.cancel.disabled);
    assert.equal(h.read('activeJob'),'restored-analysis');
    current.state='complete';await h.tick();
    assert.equal(h.read('analysisJob'),'restored-analysis');assert.equal(h.elements.candidates.children.length,2);
    assert(!h.elements.analyze.disabled);assert(h.elements.cancel.disabled);
  });
  await test('reload reconnects to a running render and can cancel it',async()=>{
    const current={id:'restored-render',kind:'render',state:'running',progress,result:{output_id:'out',plan:{}}};
    const h=app(current);await settle();assert.equal(h.read('activeJob'),'restored-render');
    await h.elements.cancel.onclick();await h.tick();
    assert.equal(current.state,'cancelled');assert.equal(h.read('activeJob'),null);
    assert(!h.elements.analyze.disabled);assert(h.elements.outputSection.classList.contains('hidden'));
  });
  await test('reload reconnects to a render and exposes its completed download',async()=>{
    const current={id:'restored-render',kind:'render',state:'running',progress,result:{output_id:'restored-output',plan:{restored:true}}};
    const h=app(current);await settle();current.state='complete';await h.tick();
    assert(!h.elements.outputSection.classList.contains('hidden'));
    assert(h.elements.download.href.includes('restored-output'));assert.equal(h.read('finalPlan.restored'),true);
  });
  await test('range changes reload review, clear old output, and reject stale iframe messages',async()=>{
    const h=await analyzedApp();h.candidate(0).click();
    await h.message({type:'clipbeta-render',review:{}});const oldRange={start_ms:0,end_ms:1500};
    h.elements.rangeStart.value='0.250';h.elements.rangeEnd.value='1.250';h.elements.applyRange.click();
    assert(h.elements.review.src.includes('start_ms=250&end_ms=1250'));
    assert.equal(h.elements.source.currentTime,.25);assert(h.elements.outputSection.classList.contains('hidden'));
    assert.equal(h.read('finalPlan'),null);
    await h.message({type:'clipbeta-render',range:oldRange,review:{}});
    await h.message({type:'clipbeta-render',candidate_id:'old-candidate',review:{}});
    await h.message({type:'clipbeta-render',analysis_job_id:'old-analysis',review:{}});
    assert.equal(h.counts().renders,1);
    await h.message({type:'clipbeta-render',review:{}});
    const request=h.calls.filter(call=>call.url==='/api/render').at(-1);
    assert.deepEqual(request.body.range,{start_ms:250,end_ms:1250});
  });
  await test('unapplied or invalid ranges cannot render or replace the reviewed range',async()=>{
    const h=await analyzedApp();h.candidate(0).click();const reviewURL=h.elements.review.src;
    h.elements.rangeEnd.value='1.400';await h.message({type:'clipbeta-render',review:{}});
    assert.equal(h.counts().renders,0);assert.match(h.elements.status.textContent,/範囲を適用/);
    for(const [start,end] of [['-1','1'],['0','0'],['2','1'],['0','4.001'],['0','Infinity'],['','1']]){
      h.elements.rangeStart.value=start;h.elements.rangeEnd.value=end;h.elements.applyRange.click();
      assert.equal(h.elements.review.src,reviewURL);
    }
    h.read('mediaDurationMs=120000');h.elements.rangeStart.value='0';h.elements.rangeEnd.value='60.001';h.elements.applyRange.click();
    assert.equal(h.elements.review.src,reviewURL);
    h.elements.rangeEnd.value='60';h.elements.applyRange.click();assert(h.elements.review.src.includes('end_ms=60000'));
  });
  await test('Twitch URL lists actual returned choices and chains selected VOD through download and analysis',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';
    h.elements.videoFile.files=[{name:'unrelated-local.mp4'}];h.elements.whisper.value='C:\\Models\\local-whisper';
    await h.elements.checkTwitch.onclick();assert.equal(h.counts().archives,1);
    assert.equal(h.elements.archives.children.length,2);assert.equal(h.archive(1).disabled,true);
    assert.equal(h.elements.archives.children[0].children[0].textContent,archiveResult.archives[0].title);
    assert(!h.created.some(el=>el.tag==='img'));
    await h.archive(0).click();assert.equal(h.counts().downloads,1);assert.equal(h.counts().analyses,1);
    const request=h.calls.find(call=>call.url==='/api/analyze');
    assert.equal(request.body.media_id,'acquired-video');assert.equal(request.body.config.whisper_model_dir,'C:\\Models\\local-whisper');
    assert(!h.calls.some(call=>call.url.startsWith('/api/import?kind=video')));
    assert.equal(h.elements.candidates.children.length,2);
  });
  await test('missing dependency and access denial stop without fake choices or local-file fallback',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';
    h.elements.videoFile.files=[{name:'must-not-run.mp4'}];h.server.archiveError='yt-dlp package is missing';
    await h.elements.checkTwitch.onclick();assert.equal(h.elements.archives.children.length,0);
    assert.match(h.elements.twitchStatus.textContent,/yt-dlp/);assert.match(h.elements.twitchStatus.textContent,/ローカル動画/);
    await h.elements.analyze.onclick();assert.equal(h.counts().analyses,0);
    h.server.archiveError=null;await h.elements.checkTwitch.onclick();h.server.downloadError='Twitch access denied (403)';
    await h.archive(0).click();assert.equal(h.counts().analyses,0);assert.equal(h.read('mediaAsset'),null);
    assert.match(h.elements.twitchStatus.textContent,/403/);assert.equal(h.elements.candidates.children.length,0);
  });
  await test('Twitch listing repeat clicks and cancellation do not create duplicate or stale choices',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';h.server.archivesState='running';
    const pending=h.elements.checkTwitch.onclick();await settle();
    await h.elements.checkTwitch.onclick();assert.equal(h.counts().archives,1);
    assert(h.elements.twitchURL.disabled);await h.elements.cancel.onclick();await h.tick();await pending;
    assert.equal(h.elements.archives.children.length,0);assert.equal(h.counts().downloads,0);
    assert(!h.elements.checkTwitch.disabled);assert(h.elements.cancel.disabled);
  });
  await test('cancel while a job-start request is pending is sent as soon as its ID arrives',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';
    h.server.holdJobStarts=true;h.server.archivesState='running';
    const pending=h.elements.checkTwitch.onclick();await settle();assert.equal(h.read('activeJob'),null);
    await h.elements.cancel.onclick();h.server.pendingJobStarts.splice(0).forEach(resolve=>resolve());await pending;
    assert(h.calls.some(call=>call.url==='/api/cancel'&&call.body.id==='archives-1'));
    assert.equal(h.elements.archives.children.length,0);assert(!h.elements.analyze.disabled);
  });
  await test('cancelled VOD download never starts analysis and can be retried',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';
    await h.elements.checkTwitch.onclick();h.server.downloadState='running';
    const pending=h.archive(0).click();await settle();await h.archive(0).click();
    assert.equal(h.counts().downloads,1);await h.elements.cancel.onclick();await h.tick();await pending;
    assert.equal(h.counts().analyses,0);assert.equal(h.read('mediaAsset'),null);assert(!h.archive(0).disabled);
    h.server.downloadState='complete';await h.archive(0).click();assert.equal(h.counts().downloads,2);assert.equal(h.counts().analyses,1);
  });
  await test('changing Twitch URL invalidates old choices without starting a request',async()=>{
    const h=app();await settle();h.elements.twitchURL.value='https://www.twitch.tv/synthetic';
    await h.elements.checkTwitch.onclick();const requests=h.calls.length,oldChoice=h.archive(0);
    h.elements.twitchURL.value='https://www.twitch.tv/different';h.elements.twitchURL.oninput();
    await oldChoice.onclick();
    assert.equal(h.elements.archives.children.length,0);assert.equal(h.calls.length,requests);
    assert.equal(h.read('selectedCandidate'),null);assert.equal(h.read('mediaAsset'),null);
  });
  await test('old candidate actions cannot select a result from an earlier analysis',async()=>{
    const h=await analyzedApp(),oldChoice=h.candidate(0);
    await h.elements.analyze.onclick();oldChoice.onclick();
    assert.equal(h.counts().analyses,2);assert.equal(h.read('selectedCandidate'),null);
    h.candidate(0).click();assert.equal(h.read('selectedCandidate'),'first');
  });
  await test('reload reconnects to archive listing and exposes selectable finite VODs',async()=>{
    const current={id:'restored-archives',kind:'archives',state:'running',progress,result:archiveResult};
    const h=app(current);await settle();current.state='complete';await h.tick();
    assert.equal(h.elements.archives.children.length,2);assert(!h.archive(0).disabled);assert(h.archive(1).disabled);
    assert.equal(h.counts().downloads,0);assert.equal(h.counts().analyses,0);
  });
  await test('reload-completed download requires explicit analysis with current settings',async()=>{
    const current={id:'restored-download',kind:'download',state:'running',progress,result:{id:'restored-video',kind:'video',name:'restored.mp4'}};
    const h=app(current);await settle();current.state='complete';await h.tick();
    assert.equal(h.counts().analyses,0);assert.equal(h.read('mediaAsset.id'),'restored-video');
    assert.match(h.elements.status.textContent,/解析を開始/);
    h.elements.whisper.value='C:\\Models\\chosen-after-reload';h.elements.model.value='chosen:local';
    await h.elements.analyze.onclick();
    const request=h.calls.find(call=>call.url==='/api/analyze');
    assert.equal(request.body.media_id,'restored-video');assert.equal(request.body.config.ollama_model,'chosen:local');
    assert(!h.calls.some(call=>call.url.startsWith('/api/import?kind=video')));
  });
  await test('generated review bridge survives repeated adopt/reject and serializes current edits',async()=>{
    const python=process.env.PYTHON||'python';
    const page=execFileSync(python,['-c',
      "import tempfile; from clipbeta.standalone import AppState; from clipbeta.engine import make_proposal; " +
      "t=tempfile.TemporaryDirectory(); s=AppState(t.name); p=make_proposal([{'start_ms':0,'end_ms':1000,'text':'synthetic'}],0,1000,1000); " +
      "s.proposal=lambda *_:({},p); print(s.review_page('id','candidate')); t.cleanup()"],{cwd:root,encoding:'utf8'});
    const roots=Object.fromEntries(['cards','count','adopt','reject','save','header'].map(id=>[id,new Element()]));
    const events=[];const context={
      document:{getElementById:id=>roots[id],createElement:tag=>new Element(tag),querySelector:selector=>roots[selector],
        querySelectorAll:selector=>selector==='article'?roots.cards.children:[]},
      parent:{postMessage:(data,origin)=>events.push({data,origin})},location:{origin:'http://127.0.0.1:12345'},
      Blob:class{},URL:{createObjectURL:()=> 'blob:mock',revokeObjectURL:()=>{}},setTimeout:fn=>fn(),
    };
    vm.createContext(context);for(const script of page.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
    const seeks=()=>roots.cards.children.flatMap(card=>card.children).filter(el=>el.textContent==='動画のこの字幕へ移動');
    assert.equal(seeks().length,1);roots.adopt.click();roots.adopt.click();assert.equal(seeks().length,1);
    roots.reject.click();assert.equal(seeks().length,1);seeks()[0].click();
    assert.equal(events[0].data.type,'clipbeta-seek');assert.equal(events[0].data.time,0);
    roots.header.children[0].click();assert.equal(events[1].data.type,'clipbeta-render');
    assert.equal(events[1].data.review.decisions[0].status,'rejected');
    assert.equal(events[1].data.analysis_job_id,'id');assert.equal(events[1].data.candidate_id,'candidate');
    assert.equal(events[1].data.range.start_ms,0);assert.equal(events[1].data.range.end_ms,1000);
    assert.equal(events[1].origin,context.location.origin);
  });
  console.log(count+' standalone DOM-double tests passed (real browser QA remains separate)');
}
const watchdog=setTimeout(()=>{console.error('FAIL: standalone DOM-double test did not finish');process.exit(1)},10000);
main().catch(error=>{console.error(error);process.exitCode=1}).finally(()=>clearTimeout(watchdog));
