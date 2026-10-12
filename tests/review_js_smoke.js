// Dependency-free JS behavior test using a minimal DOM double, not a browser.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.style={};this.textContent='';this.value='';}
  append(...nodes){this.children.push(...nodes)}
  replaceChildren(){this.children=[]}
  setAttribute(){}
  click(){if(this.onclick)this.onclick()}
}
const roots=Object.fromEntries(['cards','count','adopt','reject','save'].map(x=>[x,new Element('div')]));
let downloaded;
const context={document:{getElementById:id=>roots[id],createElement:tag=>new Element(tag)},
 Blob:class{constructor(parts){downloaded=JSON.parse(parts.join(''))}},
 URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},setTimeout:fn=>fn()};
const html=fs.readFileSync('samples/review.html','utf8');
vm.runInNewContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
assert.equal(roots.cards.children.length,5);
assert.match(roots.count.textContent,/5/);
roots.adopt.click();roots.adopt.click();assert.match(roots.count.textContent,/0/);
const card=roots.cards.children[0];const input=card.children.find(x=>x.tag==='textarea');
input.value='</script><img src=x onerror=alert(1)>';input.oninput();
assert.equal(card.children.at(-1).textContent,input.value);
roots.save.click();assert.equal(downloaded.decisions[0].text,input.value);
assert(downloaded.decisions.every(x=>x.status==='adopted'));
roots.reject.click();roots.save.click();assert(downloaded.decisions.every(x=>x.status==='rejected'));
console.log('PASS: JS DOM-double render, repeat-adopt, unsafe text edit, download payload, reject');
