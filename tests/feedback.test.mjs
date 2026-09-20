import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
if (!globalThis.crypto) globalThis.crypto=webcrypto;
import {encryptCredential,decryptCredential,patchFeedback,emptyFeedback,preferenceEvidence,GitHub,encode} from '../site_src/feedback-core.js';

test('encrypted credential unlocks on another device, rejects wrong passphrase and altered repository',async()=>{
  const token='test-token-never-publish-plaintext';
  const record=await encryptCredential(token,'a long unique test passphrase of 40 chars','owner/private');
  assert.ok(!JSON.stringify(record).includes(token));
  assert.deepEqual(await decryptCredential(record,'a long unique test passphrase of 40 chars'),{token,repo:'owner/private',branch:'main'});
  await assert.rejects(decryptCredential(record,'wrong password'),/Incorrect passphrase/);
  await assert.rejects(decryptCredential({...record,feedback_repository:'attacker/repo'},'a long unique test passphrase of 40 chars'),/mismatch/);
});

test('dismissal is neutral, votes reverse, and dismissing a voted item does not erase its vote',()=>{
  let state=patchFeedback(emptyFeedback(),'test-item',{dismissed:true},{});
  assert.deepEqual(preferenceEvidence(state),[]);
  state=patchFeedback(state,'test-item',{vote:'up',explanation:'Useful method'},{});
  assert.equal(preferenceEvidence(state)[0].vote,'up');
  state=patchFeedback(state,'test-item',{dismissed:false},{});
  assert.equal(preferenceEvidence(state).length,1);
  state=patchFeedback(state,'test-item',{vote:'down'},{});
  assert.equal(preferenceEvidence(state)[0].vote,'down');
  state=patchFeedback(state,'test-item',{vote:null},{});
  assert.deepEqual(preferenceEvidence(state),[]);
});

test('private-store guard refuses public repositories before reading or writing feedback',async()=>{
  const calls=[];const api=new GitHub('secret');
  api.request=async(repo,path='',options={})=>{calls.push({repo,path,options});return{private:false,visibility:'public',permissions:{push:true}};};
  await assert.rejects(api.saveFeedback('owner/public','test-item',{vote:'up'},{}),/must be private/);
  assert.equal(calls.length,1);assert.equal(calls[0].path,'');
});

test('concurrent feedback saves preserve unrelated changes and merge only the requested field',async()=>{
  const api=new GitHub('secret');let writes=0;
  let remote=patchFeedback(emptyFeedback(),'test-item',{vote:'up'},{});
  api.assertPrivate=async()=>{};
  api.read=async()=>({sha:'current-'+writes,value:structuredClone(remote)});
  api.write=async(repo,path,value)=>{
    if(writes++===0){
      remote=patchFeedback(remote,'other-item',{vote:'down'},{});
      remote=patchFeedback(remote,'test-item',{explanation:'Changed on phone'},{});
      throw Object.assign(new Error('conflict'),{status:409});
    }
    remote=value;
  };
  const saved=await api.saveFeedback('owner/private','test-item',{dismissed:true},{});
  assert.equal(saved.items['other-item'].vote,'down');
  assert.equal(saved.items['test-item'].explanation,'Changed on phone');
  assert.equal(saved.items['test-item'].vote,'up');
  assert.equal(saved.items['test-item'].dismissed,true);
});

test('GitHub transport sends credentials only in the authorization header and writes only to selected private repo',async()=>{
  const original=globalThis.fetch;const calls=[];
  globalThis.fetch=async(url,options)=>{
    calls.push({url,options});
    const body=options.method==='PUT'?{}:url.includes('/contents/')?{sha:'old',content:encode(JSON.stringify(emptyFeedback()))}:{private:true,visibility:'private',permissions:{push:true}};
    return new Response(JSON.stringify(body),{status:200});
  };
  try {
    await new GitHub('sensitive-token').saveFeedback('owner/private','test-item',{vote:'up'},{});
    for(const call of calls){assert.ok(!call.url.includes('sensitive-token'));assert.equal(call.options.headers.Authorization,'Bearer sensitive-token');}
    const writes=calls.filter(c=>c.options.method==='PUT');
    assert.equal(writes.length,1);assert.match(writes[0].url,/repos\/owner\/private\/contents\/feedback.json$/);
  }finally{globalThis.fetch=original;}
});
