// Executes generated intake HTML JavaScript against a DOM double (not a browser).
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element{constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.style={};this.value='';this.disabled=false}append(...x){this.children.push(...x)}replaceChildren(){this.children=[]}click(){if(!this.disabled&&this.onclick)this.onclick()}}
const roots=Object.fromEntries(['url','scenario','load','reset','status','error','content'].map(id=>[id,new Element('div')]));
roots.url.value='https://twitch.tv/demo';roots.scenario.value='normal';
let timers=[],blobs=[],historyCallback;
const c={document:{getElementById:id=>roots[id],createElement:t=>new Element(t)},window:{addEventListener:(name,fn)=>historyCallback=fn},
setTimeout:fn=>{timers.push(fn)},Blob:class{constructor(parts){blobs.push(parts.join(''))}},URL:{createObjectURL:()=> 'blob:mock',revokeObjectURL:()=>{}}};
const html=fs.readFileSync('samples/intake-demo.html','utf8');
for(const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInNewContext(m[1],c);
function nodes(root=roots.content){return [root,...root.children.flatMap(nodes)]}
function click(text){const n=nodes().find(x=>x.tag==='button'&&x.textContent===text);assert(n,'missing button '+text);n.click()}
function drain(){const pending=timers;timers=[];pending.forEach(fn=>fn())}
roots.load.click();assert.equal(nodes().filter(x=>x.tag==='article').length,3);
click('選択して模擬解析');assert.match(roots.status.textContent,/模擬解析中/);
click('キャンセル');drain();assert.match(roots.status.textContent,/キャンセル/);
click('同じ対象で再開');drain();assert.match(roots.status.textContent,/候補レビュー/);
click('採用');click('字幕レビューHTMLを保存');assert.match(blobs.at(-1),/Clip Beta • Offline review/);
click('元の提案JSONを保存');const p=JSON.parse(blobs.at(-1));assert.equal(p.kind,'proposal');assert(p.items.every(i=>i.status==='pending'));
click('アーカイブ選択に戻る');click('選択して模擬解析');historyCallback();drain();assert.match(roots.status.textContent,/URL入力/);
roots.url.value='https://www.twitch.tv/videos/123';roots.load.click();assert.equal(nodes().filter(x=>x.tag==='article').length,1);
click('このVODで模擬解析を開始');roots.url.value='https://evil.test/x';roots.load.click();drain();assert.match(roots.status.textContent,/エラー/);
console.log('PASS: generated intake UI render, selection, cancel/resume, review downloads, back/history, direct VOD, stale invalid URL');
