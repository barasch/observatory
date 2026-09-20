import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';
import {encode,emptyFeedback,encryptCredential} from '../site_src/feedback-core.js';
const html=readFileSync(new URL('../site/index.html',import.meta.url),'utf8');
const fixture=`<article data-tile="test-item" data-category="law"><script type="application/json" class="item-metadata">{"id":"test-item","title":"Test","category":"law","tags":["law"]}</script><div class="tile-body"><button class="tile-toggle" aria-expanded="false">Test</button><p class="tile-summary">Summary</p><div class="tile-detail" hidden>Detail</div></div><div class="feedback-controls" hidden><button data-vote="up" aria-pressed="false">Up</button><button data-vote="down" aria-pressed="false">Down</button><button data-dismiss aria-pressed="false">Dismiss</button><button data-why hidden>Why?</button><form class="reason-form" hidden><textarea></textarea><button type="submit">Save</button><button type="button" data-cancel-reason>Cancel</button></form></div></article>`;
const until=async check=>{const start=Date.now();while(!check()){if(Date.now()-start>4000)throw new Error('Timed out');await new Promise(r=>setTimeout(r,10));}};

test('UI unlock, reversal, neutral dismissal, private explanation and failed save',async()=>{
 const original=globalThis.fetch,passphrase='a sufficiently long unique passphrase';
 const record=await encryptCredential('test-token',passphrase,'barasch/observatory-private');
 const files=new Map([['barasch/observatory/feedback-auth.json',{sha:'one',value:record}],['barasch/observatory-private/feedback.json',{sha:'two',value:emptyFeedback()}]]);
 let rejectWrite=false;const writes=[];
 globalThis.fetch=async(url,options={})=>{
  const [,repo,path]=new URL(url).pathname.match(/^\/repos\/([^/]+\/[^/]+)(?:\/contents\/(.+))?$/);
  if(!path)return new Response(JSON.stringify({private:true,visibility:'private',permissions:{push:true}}));
  if(options.method==='PUT'){
   if(rejectWrite)return new Response('{}',{status:503});
   const data=JSON.parse(options.body),value=JSON.parse(Buffer.from(data.content,'base64').toString());
   files.set(repo+'/'+path,{value,sha:'next'});writes.push(repo+'/'+path);
   return new Response(JSON.stringify({content:{sha:'next'}}));
  }
  const file=files.get(repo+'/'+path);
  return file?new Response(JSON.stringify({sha:file.sha,content:encode(JSON.stringify(file.value))})):new Response('{}',{status:404});
 };
 const dom=new JSDOM(html,{url:'https://barasch.github.io/observatory/'});
 for(const name of ['window','document','location'])globalThis[name]=dom.window[name];
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new dom.window.Event('close'));};
 document.querySelector('[data-topic="law"] .tile-grid').innerHTML=fixture;
 try {
  await import('../site_src/edition.js?ui');
  const tile=document.querySelector('[data-tile]');
  assert.equal(tile.querySelector('.feedback-controls').hidden,true);
  tile.querySelector('.tile-summary').click();assert.equal(tile.querySelector('.tile-detail').hidden,false);
  document.querySelector('#unlock').click();
  await until(()=>!document.querySelector('#unlock-form button').disabled);
  document.querySelector('#unlock-passphrase').value=passphrase;
  document.querySelector('#unlock-form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  await until(()=>!document.querySelector('#lock').hidden);
  assert.equal(tile.querySelector('.feedback-controls').hidden,false);
  tile.querySelector('[data-vote=up]').click();await until(()=>tile.querySelector('[data-vote=up]').getAttribute('aria-pressed')==='true');
  tile.querySelector('[data-why]').click();tile.querySelector('textarea').value='Specific reasoning';
  tile.querySelector('.reason-form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  await until(()=>tile.querySelector('.reason-form').hidden);
  assert.equal(files.get('barasch/observatory-private/feedback.json').value.items['test-item'].explanation,'Specific reasoning');
  tile.querySelector('[data-dismiss]').click();await until(()=>tile.parentNode.id==='dismissed-tiles');
  assert.equal(document.querySelector('#dismissed-section').open,false);
  assert.equal(files.get('barasch/observatory-private/feedback.json').value.items['test-item'].vote,'up');
  tile.querySelector('[data-dismiss]').click();await until(()=>tile.closest('[data-topic]'));
  tile.querySelector('[data-vote=up]').click();await until(()=>tile.querySelector('[data-vote=up]').getAttribute('aria-pressed')==='false');
  assert.equal(files.get('barasch/observatory-private/feedback.json').value.items['test-item'].vote,null);
  rejectWrite=true;tile.querySelector('[data-vote=down]').click();await until(()=>document.querySelector('#feedback-status').classList.contains('error'));
  assert.equal(tile.querySelector('[data-vote=down]').getAttribute('aria-pressed'),'false');
  assert.ok(writes.every(path=>path.startsWith('barasch/observatory-private/')));
  assert.equal(window.localStorage.length,0);assert.equal(window.sessionStorage.length,0);
 }finally{globalThis.fetch=original;dom.window.close();}
});
