"""Produce a self-contained mock intake UI using the real local caption engine."""
import json
from pathlib import Path
from .engine import make_proposal
from .review_html import render_review


def render_flow(captions):
    candidates=[]
    for index,(start,end,title) in enumerate([(1000,5000,'驚きのリアクション'),(5000,11000,'質問から笑いへ'),(0,14000,'会話全体')]):
        proposal=make_proposal(captions,start,end,15000)
        candidates.append({'id':'candidate-'+str(index+1),'title':'MOCK: '+title,'status':'pending','start_ms':start,'end_ms':end,'proposal':proposal,'review_html':render_review(proposal)})
    def safe(value):
        return json.dumps(value,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    script=(Path(__file__).parent/'web/flow.js').read_text()
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Clip Beta 0.2 | URLから切り抜き候補へ</title>
<style>body{font:16px system-ui;background:#0d1425;color:#f0f4ff;max-width:1050px;margin:40px auto;padding:0 24px}h1{font-size:36px;line-height:1.3}h2{font-size:24px}.tag{color:#c6adff;letter-spacing:2px}.notice{border:1px solid #70598f;background:#29213c;border-radius:12px;padding:16px}p,small{color:#b8c7e1;line-height:1.7}input,select{box-sizing:border-box;padding:14px;border:1px solid #526687;border-radius:8px;background:#1b2942;color:#fff;font:inherit}input{width:100%;margin:12px 0}button{padding:12px 18px;margin:6px;border:1px solid #657eb0;border-radius:8px;background:#304975;color:#fff;font:inherit;cursor:pointer}button.primary{background:#7048c9}button:disabled{opacity:.45;cursor:default}article{padding:20px;border:1px solid #384b70;border-radius:14px;margin:14px 0;background:#18233b}#status{font-weight:bold;margin:24px 0}.bar{height:12px;background:#213659;border-radius:8px;overflow:hidden}.fill{height:100%;background:#ae8aff}#error{color:#ffb1b1}textarea{width:100%;min-height:100px}a{color:#c6adff}</style>
<p class="tag">CLIP BETA / OFFLINE PROTOTYPE 0.2</p><h1>TwitchのURLから、<br>確認できる切り抜き候補へ</h1>
<p class="notice">MOCK・オフライン体験です。URLの検証だけを行い、Twitchには接続しません。表示するアーカイブ、解析進捗、見どころ候補は合成サンプルです。動画取得・音声認識・課金はありません。</p>
<label for="url">チャンネルまたはVODのURL</label><input id="url" value="https://www.twitch.tv/demo_vtuber" autocomplete="off" spellcheck="false">
<label for="scenario">動作確認用</label><select id="scenario"><option value="normal">通常の模擬データ</option><option value="empty">一覧0件</option><option value="error">一覧エラー</option></select>
<button id="load" class="primary">模擬アーカイブを表示</button><button id="reset">最初から</button>
<div id="status" aria-live="polite"></div><p id="error" role="alert"></p><main id="content"></main>
<script>''' + script + '''</script><script>
'use strict';
const candidates='''+safe(candidates)+''';
const flow=new ClipFlow.Flow();
const byId=id=>document.getElementById(id);
function el(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n}
function button(text,fn){const b=el('button',text);b.onclick=fn;return b}
function save(name,value,type='application/json'){const content=typeof value==='string'?value:JSON.stringify(value,null,2);const url=URL.createObjectURL(new Blob([content],{type}));const a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function start(id){let epoch;try{epoch=flow.select(id)}catch(e){byId('error').textContent=e.message;return}render();
// Repeated starts return the same epoch. Duplicate callbacks are harmless because
// complete/tick require a current analyzing state and matching epoch.
for(const [delay,value] of [[400,20],[850,55],[1300,85]])setTimeout(()=>{if(flow.tick(epoch,value))render()},delay);
setTimeout(()=>{if(flow.complete(epoch,candidates))render()},1800)}
function render(){
const names={input:'1 / URL入力',select:'2 / アーカイブ選択',analyzing:'3 / 模擬解析中',review:'4 / 候補レビュー',empty:'アーカイブ0件（模擬）',error:'エラー',cancelled:'解析をキャンセルしました'};
byId('status').textContent=names[flow.state];byId('error').textContent=flow.error;const area=byId('content');area.replaceChildren();
if(flow.state==='select'){
area.append(el('h2',flow.target.kind==='vod'?'このVODを対象にしますか？':'アーカイブを選ぶと模擬解析が始まります'));
for(const archive of flow.archives){const card=el('article');card.append(el('h2',archive.title),el('p','15秒の合成字幕データ / 実際の配信情報ではありません'),button(flow.target.kind==='vod'?'このVODで模擬解析を開始':'選択して模擬解析',()=>start(archive.id)));area.append(card)}}
if(flow.state==='analyzing'){
area.append(el('h2','模擬解析 '+flow.progress+'%'),el('p','状態遷移デモ: 合成字幕を読み込む → 固定の候補を準備する。実際の動画は読み取っていません。'));
const bar=el('div');bar.className='bar';const fill=el('div');fill.className='fill';fill.style.width=flow.progress+'%';bar.append(fill);area.append(bar,button('キャンセル',()=>{flow.cancel();render()}),button('選択に戻る',()=>{flow.back();render()}))}
if(flow.state==='cancelled')area.append(button('同じ対象で再開',()=>start(flow.selected)),button('選択に戻る',()=>{flow.back();render()}));
if(flow.state==='review'){
area.append(el('p','固定の合成候補です。採用した候補の字幕レビューHTMLを保存し、そこで文言・色・SE・ズームを確認できます。候補の採用は字幕全件の採用を意味しません。'));
for(const c of flow.candidates){const card=el('article');card.append(el('h2',c.title),el('p',(c.start_ms/1000)+'–'+(c.end_ms/1000)+'秒 / '+c.status));
card.append(button('採用',()=>{flow.decide(c.id,'adopted');render()}),button('不採用',()=>{flow.decide(c.id,'rejected');render()}));
if(c.status==='adopted')card.append(button('字幕レビューHTMLを保存',()=>save(c.id+'-review.html',c.review_html,'text/html')),button('元の提案JSONを保存',()=>save(c.id+'-proposal.json',c.proposal)));
area.append(card)}
const download=button('候補の選択結果を保存',()=>save('mock-candidate-selections.json',{schema:'clipbeta-intake/1',mock:true,target:flow.target,archive_id:flow.selected,candidates:flow.candidates.map(({id,status,start_ms,end_ms})=>({id,status,start_ms,end_ms}))}));download.disabled=flow.candidates.some(c=>c.status==='pending');area.append(download,button('アーカイブ選択に戻る',()=>{flow.back();render()}))}
if(flow.state==='empty'||flow.state==='error')area.append(el('p','URLまたは動作確認用シナリオを変更して、もう一度表示してください。'));
}
byId('load').onclick=()=>{flow.submit(byId('url').value,byId('scenario').value);render()};
byId('reset').onclick=()=>{flow.reset();render()};
// A browser-history traversal must never revive an old asynchronous completion.
window.addEventListener('popstate',()=>{flow.reset();render()});
render();
</script></html>'''
