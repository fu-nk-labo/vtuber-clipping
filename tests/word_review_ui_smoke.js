const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element{constructor(tag){this.tag=tag;this.children=[];this.style={};this.textContent='';this.value=''}append(...x){this.children.push(...x)}replaceChildren(){this.children=[]}setAttribute(){}click(){if(this.onclick)this.onclick()}}
const roots=Object.fromEntries(['cards','count','adopt','reject','save'].map(x=>[x,new Element('div')]));let downloaded,count=0;
const c={document:{getElementById:id=>roots[id],createElement:t=>new Element(t)},Blob:class{constructor(parts){downloaded=JSON.parse(parts.join(''));count++}},URL:{createObjectURL:()=> 'blob:mock',revokeObjectURL:()=>{}},setTimeout:fn=>fn()};
const html=fs.readFileSync('samples/word-styles/review.html','utf8');vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1],c);
roots.adopt.click();roots.save.click();assert.equal(downloaded.decisions[0].style.spans[0].color,'pink');
const card=roots.cards.children[0],input=card.children.find(x=>x.tag==='textarea');
input.value='😀この子かわいいね';input.oninput();
input.selectionStart=5;input.selectionEnd=9;
card.children.find(x=>x.tag==='button'&&x.textContent==='選択語をピンク').click();
const inputs=card.children.flatMap(x=>x.children||[]).filter(x=>x.tag==='input'&&x.type==='number');
inputs[0].value='0.85';inputs[0].oninput();inputs[1].value='0.25';inputs[1].oninput();roots.save.click();
const style=downloaded.decisions[0].style;assert.equal(style.spans[0].start,4);assert.equal(style.spans[0].end,8);assert.equal(style.zoom_target.x,.85);
const before=count;inputs[0].value='2';inputs[0].oninput();roots.save.click();assert.equal(count,before);
inputs[0].value='0.5';inputs[0].oninput();input.value='字幕変更';input.oninput();roots.save.click();assert.equal(downloaded.decisions[0].style.spans.length,0);
console.log('PASS: word-review style serialization, UTF-16 selection to code points, target edits, invalid coordinates blocked, text edit clears spans');
