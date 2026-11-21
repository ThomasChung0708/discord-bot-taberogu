/* In-memory DOM contract tests: no browser, packages, real DB or network. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function node(tag='div') {
  return {tagName:tag.toUpperCase(),value:'',files:[],hidden:false,disabled:false,children:[],dataset:{},attrs:{},handlers:{},
    classList:{add(){},remove(){},toggle(){}},
    addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(key,value){this.attrs[key]=value;},removeAttribute(key){delete this.attrs[key];},
    focus(){},setCustomValidity(value){this.validationMessage=value;},reportValidity(){return !this.validationMessage;},
    prepend(...children){this.children.unshift(...children);},
    showModal(){this.open=true;},
    querySelector(){return this.submit;}
  };
}
async function testAdmin() {
  const nodes=new Map();
  for(const match of fs.readFileSync('templates/admin.html','utf8').matchAll(/\bid="([^"]+)"/g))nodes.set(match[1],node());
  for(const element of nodes.values()){let value='';Object.defineProperty(element,'value',{get:()=>value,set:next=>{value=String(next);}});}
  const get=id=>{assert(nodes.has(id),`Unknown node ${id}`);return nodes.get(id);};
  const submit=node('button');get('editorForm').submit=submit;
  const inputIds=['name','categoryInput','areaInput','recommendedBy','keywordsInput','lunchBudget','dinnerBudget','lunchMin','lunchMax','dinnerMin','dinnerMax','businessHours','tabelogUrl','googleMapsUrl','imageUrl','imageFile','comments','newComment'];
  get('editorForm').elements=[...inputIds.map(get),submit];
  const legacy='  legacy\nsecond line\n\n  ';
  const first={id:1,name:'喫茶',category:'咖啡',area:'京都',comments:legacy,tags:['喫茶'],keywords:['喫茶'],comment_items:[{comment:'formal',created_by:'Admin',created_at:'2026-11-21'}],lunch_budget_min:0};
  const shops=[first,{...first,id:2,name:'麺屋'}];
  const requests=[];const confirmations=[];let allowDiscard=false;let pendingPut=null;
  const N={$:get,t:(zh)=>zh,el:(tag,cls,text)=>Object.assign(node(tag),{className:cls,textContent:text}),
    fillSelect(){},comments(){},status(target,text){target.textContent=text;},safeImageUrl:()=>'',safeUrl:()=>'',debounce:fn=>fn,
    json:(method,payload)=>({method,body:JSON.stringify(payload)}),
    async request(url,options={}) {
      requests.push({url,options});
      if(options.method==='PUT'){
        const payload=JSON.parse(options.body);
        await new Promise(resolve=>{pendingPut=resolve;});Object.assign(first,payload);return structuredClone(first);
      }
      if(url.startsWith('/api/admin/restaurants?'))return {restaurants:structuredClone(shops),areas:['京都'],categories:['咖啡']};
      if(url.startsWith('/api/admin/restaurants/'))return structuredClone(shops.find(shop=>String(shop.id)===url.split('/').pop()));
      throw new Error(`Unexpected request ${url}`);
    }
  };
  const workbench=node();
  const document={querySelector:()=>workbench,querySelectorAll:()=>get('restaurantList').children,addEventListener(){},createTextNode:text=>({textContent:text})};
  const context={URLSearchParams,Notebook:N,document,window:{addEventListener(){}},location:{replace(){}},confirm:text=>{confirmations.push(text);return allowDiscard;},console};
  vm.runInNewContext(fs.readFileSync('static/js/admin.js','utf8'),context);
  const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  async function fire(target,event,extra={}){for(const fn of target.handlers[event]||[])fn({target,preventDefault(){},...extra});await settle();}
  await settle();await fire(get('restaurantList').children[0],'click');
  assert.equal(get('name').value,'喫茶');assert.equal(get('lunchMin').value,'0','Zero budget survives');
  get('newComment').value='unfinished note';get('imageFile').files=[{name:'draft.png'}];
  await fire(get('restaurantList').children[1],'click');
  assert.equal(confirmations.length,1,'Switching with a note/file draft must ask');assert.equal(get('name').value,'喫茶','Cancel preserves current editor');
  get('name').value='Updated';await fire(get('editorForm'),'input',{target:get('name')});
  await fire(get('editorForm'),'submit',{submitter:submit});
  assert(pendingPut,'PUT starts');assert(get('name').disabled&&get('newComment').disabled,'Pending mutation locks fields');
  const put=requests.find(row=>row.options.method==='PUT');const payload=JSON.parse(put.options.body);
  assert(!Object.hasOwn(payload,'comments'),'Editor PUT omits legacy comments');assert.equal(payload.name,'Updated');assert.equal(payload.lunch_budget_min,0);
  pendingPut();await settle();
  assert.equal(get('newComment').value,'unfinished note','Saving fields preserves comment draft');assert.equal(get('imageFile').files.length,1,'Saving fields preserves selected file');
  assert(!get('name').disabled,'Controls restored after success');assert.equal(first.comment_items.length,1,'Save preserves formal comments');
  allowDiscard=true;await fire(get('restaurantList').children[1],'click');assert.equal(get('name').value,'麺屋');assert.equal(get('newComment').value,'');
  get('lunchMin').value='2000';get('lunchMax').value='1000';const count=requests.filter(row=>row.options.method==='PUT').length;
  await fire(get('editorForm'),'submit',{submitter:submit});assert.equal(requests.filter(row=>row.options.method==='PUT').length,count,'Invalid budget range never submits');
  assert(get('lunchMax').validationMessage,'Invalid range explains correction');
  await fire(get('restaurantList').children[0],'click');assert.equal(get('lunchMax').validationMessage,'','Switching shops clears stale validity');
  console.log('PASS admin: draft guards, omitted legacy payload, zero budget, slow-save lock, draft retention and range validation');
}

function sharedContext(nodes=new Map()) {
  const document={documentElement:{},body:{dataset:{page:'public'}},
    getElementById:id=>{assert(nodes.has(id),`Unknown node ${id}`);return nodes.get(id);},
    querySelectorAll:()=>[],addEventListener(){},createElement:node,createTextNode:text=>({textContent:text})};
  const context={document,window:{},URL,URLSearchParams,setTimeout,clearTimeout,AbortController,
    location:{href:'http://fixture/',search:'',pathname:'/'},history:{replaceState(){}},console};
  vm.runInNewContext(fs.readFileSync('static/js/notebook.js','utf8'),context);
  context.Notebook=context.window.Notebook;
  return context;
}
const text=node=>[node.textContent||'',...(node.children||[]).map(text)].join('');
async function testShared() {
  const context=sharedContext(), N=context.Notebook;
  for(const url of ['javascript:alert(1)','data:text/html,x','//evil.test','https://','']) {
    const result=N.link('店家連結',url,true);
    assert.equal(result.tagName,'SPAN','Rejected URLs render as plain text');
    assert.equal(result.textContent,'店家連結');
    assert(!('href' in result));assert(!('target' in result));
  }
  for(const url of ['https://example.com/shop','/?shop=1','/map?shop=1']) {
    const result=N.link('Shop',url,true);
    assert.equal(result.tagName,'A');assert.equal(result.href,url);
    assert.equal(result.target,'_blank');assert.equal(result.rel,'noopener noreferrer');
  }
  for(const status of [502,503,200]) {
    context.fetch=async()=>new Response('<html>upstream error</html>',{status});
    await assert.rejects(N.request('/api/restaurants'),error=>{
      assert.equal(error.status,status);
      assert(error.message.includes(`(${status})`),'Invalid JSON shows the response status');
      assert(!error.message.includes('網路'),'An HTTP response is not a network failure');
      return true;
    });
  }
  context.fetch=async()=>new Response(JSON.stringify({detail:'請修正資料'}),{status:400});
  await assert.rejects(N.request('/api/restaurants'),error=>error.status===400&&error.message==='請修正資料');
  context.fetch=async()=>new Response('null',{status:500});
  await assert.rejects(N.request('/api/restaurants'),error=>error.status===500&&error.message.includes('(500)'));
  context.fetch=async()=>new Response(JSON.stringify({restaurants:[]}));
  assert.equal(JSON.stringify(await N.request('/api/restaurants')),'{"restaurants":[]}');
  vm.runInNewContext('fetch=async()=>{throw new TypeError("offline");}',context);
  await assert.rejects(N.request('/api/restaurants'),error=>error.message.includes('網路'));
  vm.runInNewContext('fetch=async()=>{const error=new Error();error.name="AbortError";throw error;}',context);
  await assert.rejects(N.request('/api/restaurants'),error=>error.message.includes('網路'));
  let redirected='';context.location.pathname='/admin';context.location.replace=url=>{redirected=url;};
  context.fetch=async()=>new Response('<html>expired</html>',{status:401});
  await assert.rejects(N.request('/api/admin/restaurants'),error=>error.message.includes('登入已過期'));
  assert.equal(redirected,'/admin');
  console.log('PASS shared: unsafe links become text, safe links survive, HTTP/JSON errors show status, API details and network/session handling');
}
async function testPublic() {
  const nodes=new Map();
  for(const match of fs.readFileSync('templates/public.html','utf8').matchAll(/\bid="([^"]+)"/g))nodes.set(match[1],node());
  const context=sharedContext(nodes), N=context.Notebook, get=N.$;
  const metadata=node();
  const comment={comment:'Note body',created_by:'Private creator',author_label:'friend-ABCDEF',created_at:'2026-11-21'};
  for(const author of ['Private creator','Admin','Google Sheet']) {
    N.comments(metadata,[{...comment,created_by:author}]);
    const meta=metadata.children[0].children[0];
    assert.equal(meta.children.length,1,'Public comment metadata contains only a date');
    assert.equal(meta.children[0].tagName,'TIME');
    assert.equal(text(meta),'2026-11-21');
    assert.equal(meta.children[0].dateTime,'2026-11-21');
  }
  N.comments(metadata,[comment],true);
  assert.equal(text(metadata.children[0].children[0]),'Private creator · 2026-11-21','Admin retains creator');
  const shops=[{id:1,name:'No budgets'},{id:2,name:'Lunch',lunch_budget_text:'￥1,000'},
    {id:3,name:'Dinner',dinner_budget_text:'￥2,000'},
    {id:4,name:'Both',lunch_budget_text:'￥3,000',dinner_budget_text:'￥4,000'}];
  N.request=async url=>{
    if(url.startsWith('/api/restaurants?'))return {restaurants:shops,areas:[],categories:[]};
    if(url.endsWith('/comments'))return {comments:[comment]};
    return shops.find(shop=>String(shop.id)===url.split('/').pop());
  };
  vm.runInNewContext(fs.readFileSync('static/js/public.js','utf8'),context);
  const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
  await settle();
  const rows=get('grid').children;
  const prices=rows.map(row=>row.children[2]);
  assert.equal(prices[0].children.length,1,'Missing budgets use one quiet line');
  assert.equal(prices[0].children[0].className,'muted');
  assert.equal(text(prices[0]),'預算未記錄');
  assert.equal(text(prices[1]),'午餐￥1,000晚餐未記錄');
  assert.equal(text(prices[2]),'午餐未記錄晚餐￥2,000');
  assert.equal(text(prices[3]),'午餐￥3,000晚餐￥4,000');
  rows[0].handlers.click[0]();await settle();
  const facts=get('detailContent').children.find(child=>child.tagName==='DL');
  assert.equal(facts.children.length,3,'Missing detail budgets share one fact with hours and recommender');
  assert.equal(text(facts.children[0]),'預算未記錄');
  console.log('PASS public: date-only comments, admin creators, single missing-budget line, recorded budgets and detail');
}
(async()=>{await testAdmin();await testShared();await testPublic();})().catch(error=>{console.error(error);process.exitCode=1;});

