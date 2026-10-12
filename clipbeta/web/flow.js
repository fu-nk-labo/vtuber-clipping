/* Pure offline state machine. No fetch, sockets, tokens, storage, or external links. */
(function(root){
'use strict';
const reserved=new Set(['videos','directory','downloads','settings','subscriptions','inventory','wallet','login','signup','search','p','jobs','turbo','products','store']);
function parseTwitchURL(raw){
 if(typeof raw!=='string'||raw.length>2048||/[\u0000-\u0020\u007f\\%]/.test(raw))throw Error('HTTPSのTwitchチャンネルまたはVOD URLを入力してください');
 const match=raw.match(/^https:\/\/(www\.|m\.)?twitch\.tv(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i);
 if(!match)throw Error('許可されるホストは twitch.tv / www.twitch.tv / m.twitch.tv のみです');
 const path=match[2]||'/';let found;
 if((found=path.match(/^\/videos\/([1-9][0-9]{0,19})\/?$/)))return {kind:'vod',id:found[1],canonical:'https://www.twitch.tv/videos/'+found[1]};
 if((found=path.match(/^\/([a-zA-Z0-9_]{1,25})(?:\/videos)?\/?$/))&&!reserved.has(found[1].toLowerCase()))return {kind:'channel',login:found[1].toLowerCase(),canonical:'https://www.twitch.tv/'+found[1].toLowerCase()};
 throw Error('対応形式: https://www.twitch.tv/チャンネル または /videos/数字ID');
}
function mockArchives(target,scenario='normal'){
 if(scenario==='empty')return [];
 if(scenario==='error')throw Error('模擬エラー: 一覧を取得できませんでした（実通信なし）');
 if(target.kind==='vod')return [{id:'mock-vod-'+target.id,title:'MOCK: 指定VOD '+target.id+'（存在未確認）',duration_ms:15000}];
 return ['ゲーム実況','雑談','コラボ'].map((name,i)=>({id:'mock-archive-'+(i+1),title:'MOCK: '+target.login+' / '+name+' 合成アーカイブ',duration_ms:15000}));
}
class Flow {
 constructor(){this.epoch=0;this.state='input';this.target=null;this.archives=[];this.selected=null;this.candidates=[];this.progress=0;this.error=''}
 invalidate(){this.epoch++;this.candidates=[];this.progress=0}
 submit(raw,scenario='normal'){
  this.invalidate();this.selected=null;this.archives=[];this.error='';this.target=null;
  try{this.target=parseTwitchURL(raw);this.archives=mockArchives(this.target,scenario);this.state=this.archives.length?'select':'empty'}
  catch(e){this.state='error';this.error=e.message}
  return this.state;
 }
 select(id){
  if(!['select','analyzing','review','cancelled'].includes(this.state))throw Error('選択できるアーカイブがありません');
  const archive=this.archives.find(x=>x.id===id);if(!archive)throw Error('一覧にないアーカイブです');
  if(this.selected===id&&['analyzing','review'].includes(this.state))return this.epoch;
  this.invalidate();this.selected=id;this.state='analyzing';return this.epoch;
 }
 tick(epoch,progress){
  if(this.state!=='analyzing'||epoch!==this.epoch)return false;
  if(!Number.isInteger(progress)||progress<0||progress>99)throw Error('不正な進行率');
  if(progress<this.progress)return false;
  this.progress=progress;return true;
 }
 complete(epoch,candidates){
  if(this.state!=='analyzing'||epoch!==this.epoch)return false;
  if(!Array.isArray(candidates)||!candidates.length)throw Error('候補がありません');
  this.candidates=JSON.parse(JSON.stringify(candidates));this.progress=100;this.state='review';return true;
 }
 fail(epoch,message){if(this.state!=='analyzing'||epoch!==this.epoch)return false;this.invalidate();this.state='error';this.error=String(message);return true}
 cancel(){if(this.state==='analyzing'){this.invalidate();this.state='cancelled'}return this.state}
 back(){this.invalidate();this.selected=null;this.state=this.archives.length?'select':'input';this.error='';return this.state}
 reset(){this.invalidate();this.state='input';this.archives=[];this.selected=null;this.target=null;this.error=''}
 decide(id,status){if(this.state!=='review')throw Error('レビュー待ちではありません');if(!['pending','adopted','rejected'].includes(status))throw Error('不正な選択');const c=this.candidates.find(x=>x.id===id);if(!c)throw Error('不明な候補');c.status=status}
}
const api={parseTwitchURL,mockArchives,Flow};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ClipFlow=api;
})(typeof globalThis!=='undefined'?globalThis:this);
