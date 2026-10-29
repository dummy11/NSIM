// Run the actual report JavaScript without allowing timers, network or file loads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function setup(data) {
  const elements = new Map();
  class Element {
    constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this.text=''; }
    set textContent(value) { this.text=String(value); this.children=[]; }
    get textContent() { return this.text + this.children.map(x=>x.textContent).join(''); }
    set innerHTML(value) { throw new Error('Never render data as HTML'); }
    appendChild(child) { assert.notEqual(child.tagName,'script'); this.children.push(child); return child; }
    replaceChildren(...children) { this.text=''; this.children=children.flatMap(x=>x.tagName==='fragment'?x.children:[x]); }
    setAttribute(key,value) { this.attributes[key]=value; }
  }
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id,new Element('div')); return elements.get(id); },
    createElement: tag=>new Element(tag), createDocumentFragment: ()=>new Element('fragment')
  };
  const forbidden=()=>{ throw new Error('Static report must not poll or connect'); };
  const context=vm.createContext({NSIM_INITIAL:data,document,console,setTimeout:forbidden,setInterval:forbidden,
    fetch:forbidden,EventSource:forbidden,WebSocket:forbidden});
  vm.runInContext(input.client,context);
  return {elements,text:id=>elements.get(id).textContent};
}
const rows=Array.from({length:10000},(_,i)=>({...input.complete.rows[0],id:String(i),case:'case_'+i}));
const jobs=Array.from({length:210},(_,i)=>({job_id:String(i),phase:'simulate',status:'DONE',terminal:true}));
const ui=setup({...input.complete,page_size:100,rows,jobs});
assert.equal(ui.elements.get('cases').children.length,100);
assert(ui.text('cases').includes('<img src=x onerror=alert(1)>'));
assert.equal(ui.elements.get('jobs').children.length,100);
ui.elements.get('cases-page').onchange({target:{value:'100'}});
assert(ui.text('cases').includes('case_9999'));
ui.elements.get('cases-prev').onclick();
assert(ui.text('cases').includes('case_9800'));
ui.elements.get('cases-next').onclick();
assert(ui.text('cases').includes('case_9999'));
ui.elements.get('jobs-page').onchange({target:{value:'999'}});
assert.equal(ui.elements.get('jobs').children.length,10);
assert.equal(ui.text('phase'),'运行结束');
assert.equal(ui.text('verdict'),'FAIL');
const empty=setup({...input.complete,rows:[],jobs:[]});
assert.equal(empty.elements.get('cases').children.length,0);
assert(empty.elements.get('cases-next').disabled);
console.log('Report static pagination passed; no polling or network.');
