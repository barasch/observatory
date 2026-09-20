import {PUBLIC_REPO,GitHub,encryptCredential,decryptCredential,newPassphrase,emptyFeedback,validateFeedback} from './feedback-core.js';
const $ = selector => document.querySelector(selector);
const tiles = [...document.querySelectorAll('[data-tile]')];
const homes = new Map(tiles.map(tile=>[tile.dataset.tile,tile.parentNode]));
let session = null, feedback=emptyFeedback(), busy=false;
const dialog = $('#account-dialog');
function status(message,error=false) {
  const node=$('#feedback-status') || $('#account-status');
  node.hidden=!message; node.textContent=message; node.classList.toggle('error',error);
}
function accountStatus(message) { $('#account-status').textContent=message; }
function setBusy(value) {
  busy=value;
  for (const button of document.querySelectorAll('.feedback-controls button, #lock, #preferences, #unlock, #account-dialog button, #account-dialog input')) button.disabled=value;
}
function redraw() {
  document.body.classList.toggle('unlocked',Boolean(session));
  $('#unlock').hidden=Boolean(session);
  $('#lock').hidden=!session;
  $('#preferences').hidden=!session;
  for(const tile of tiles) {
    const state=session?feedback.items[tile.dataset.tile]:null;
    tile.querySelector('.feedback-controls').hidden=!session;
    tile.querySelectorAll('[data-vote]').forEach(b=>b.setAttribute('aria-pressed',String(state?.vote === b.dataset.vote)));
    const dismiss=tile.querySelector('[data-dismiss]');
    dismiss.setAttribute('aria-pressed',String(Boolean(state?.dismissed)));
    dismiss.textContent=state?.dismissed?'Restore':'Dismiss';
    tile.querySelector('[data-why]').hidden=!state?.vote;
    if(!session || !state?.vote) {
      tile.querySelector('.reason-form').hidden=true;
      tile.querySelector('textarea').value='';
    }
    const destination=state?.dismissed?$('#dismissed-tiles'):homes.get(tile.dataset.tile);
    destination.append(tile);
  }
  for (const section of document.querySelectorAll('[data-topic]')) {
    const count=section.querySelectorAll('[data-tile]').length;
    section.querySelector('.topic-count').textContent=count;
    const empty=section.querySelector('.category-empty');
    empty.hidden=count>0;
    empty.textContent=session?'No remaining selections in this category.':'No selections in this edition.';
  }
  const dismissed=$('#dismissed-section');
  if(dismissed) {
    const count=$('#dismissed-tiles').children.length;
    dismissed.hidden=!session || count===0;
    $('#dismissed-count').textContent=count;
  }
}
async function save(tile,patch) {
  if(!session || busy) return;
  setBusy(true);status('Saving privately…');
  try {
    const metadata=JSON.parse(tile.querySelector('.item-metadata').textContent);
    feedback=await session.api.saveFeedback(session.repo,tile.dataset.tile,patch,metadata);
    tile.querySelector('.reason-form').hidden=true;
    redraw(); status('Saved privately.');
  } catch(error) {status(`${error.message} Your change was not saved; please retry.`,true);}
  finally {setBusy(false);}
}
for(const tile of tiles) {
  const toggle=tile.querySelector('.tile-toggle');
  const detail=tile.querySelector('.tile-detail');
  const expand=()=>{detail.hidden=!detail.hidden;toggle.setAttribute('aria-expanded',String(!detail.hidden));};
  toggle.addEventListener('click',expand);
  tile.addEventListener('click',event=>{
    if(event.target.closest('a,button,input,textarea,form,.feedback-controls')) return;
    if(window.getSelection()?.toString()) return;
    expand();
  });
  for(const button of tile.querySelectorAll('[data-vote]')) button.addEventListener('click',()=>{
    const prior=feedback.items[tile.dataset.tile]?.vote;
    save(tile,{vote:prior===button.dataset.vote?null:button.dataset.vote,explanation:''});
  });
  tile.querySelector('[data-dismiss]').addEventListener('click',()=>{
    const restoring=Boolean(feedback.items[tile.dataset.tile]?.dismissed);
    save(tile,{dismissed:!restoring}).then(()=>{
      if(!restoring && feedback.items[tile.dataset.tile]?.dismissed) $('#dismissed-section').querySelector('summary').focus();
    });
  });
  tile.querySelector('[data-why]').addEventListener('click',()=>{
    const form=tile.querySelector('.reason-form'); form.hidden=!form.hidden;
    form.querySelector('textarea').value=feedback.items[tile.dataset.tile]?.explanation || '';
    if(!form.hidden) form.querySelector('textarea').focus();
  });
  tile.querySelector('[data-cancel-reason]').addEventListener('click',()=>tile.querySelector('.reason-form').hidden=true);
  tile.querySelector('.reason-form').addEventListener('submit',event=>{
    event.preventDefault();save(tile,{explanation:tile.querySelector('textarea').value.trim()});
  });
}
function showView(name) {
  for(const view of dialog.querySelectorAll('[data-account-view]')) view.hidden=view.dataset.accountView!==name;
  accountStatus('');
}
async function openAccount() {
  showView('unlock');dialog.showModal();$('#unlock-passphrase').focus();
  accountStatus('Checking setup…');setBusy(true);
  try {
    const file=await new GitHub().read(PUBLIC_REPO,'feedback-auth.json',true);
    showView(file?'unlock':'setup');
    if(!file) {$('#setup-passphrase').value=newPassphrase();$('#setup-repository').focus();}
    else $('#unlock-passphrase').focus();
  } catch(error) {accountStatus(error.message);}
  finally {setBusy(false);}
}
$('#unlock').addEventListener('click',openAccount);
$('#close-account').addEventListener('click',()=>{if(!busy)dialog.close();});
dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
dialog.addEventListener('close',()=>{
  $('#setup-token').value='';$('#setup-passphrase').value='';$('#unlock-passphrase').value='';$('#preference-notes').value='';
});
$('#show-setup').addEventListener('click',()=>{showView('setup');$('#setup-passphrase').value=newPassphrase();});
$('#show-unlock').addEventListener('click',()=>showView('unlock'));
$('#generate-passphrase').addEventListener('click',()=>$('#setup-passphrase').value=newPassphrase());
$('#unlock-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;setBusy(true);accountStatus('Unlocking…');
  try {
    const file=await new GitHub().read(PUBLIC_REPO,'feedback-auth.json');
    const credential=await decryptCredential(file.value,$('#unlock-passphrase').value);
    const api=new GitHub(credential.token);await api.assertPrivate(credential.repo);
    const saved=await api.read(credential.repo,'feedback.json',true);
    feedback=validateFeedback(saved?.value || emptyFeedback());
    session={api,repo:credential.repo};dialog.close();redraw();status('Private feedback unlocked.');
  } catch(error) {accountStatus(error.message);}
  finally {$('#unlock-passphrase').value='';setBusy(false);}
});
$('#setup-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;setBusy(true);accountStatus('Checking repository access…');
  try {
    const api=new GitHub($('#setup-token').value.trim());
    const repo=$('#setup-repository').value.trim();
    const metadata=await api.assertPrivate(repo);
    if(metadata.default_branch!=='main')throw new Error('Initialize the private repository with a README on a main branch.');
    const publicMetadata=await api.request(PUBLIC_REPO);
    if(!publicMetadata.permissions?.push)throw new Error('The token also needs Contents: read and write for barasch/observatory.');
    const prior=await api.read(PUBLIC_REPO,'feedback-auth.json',true);
    if(prior && prior.value.feedback_repository!==repo)throw new Error('Existing feedback uses a different repository. Keep that repository to preserve your feedback.');
    const record=await encryptCredential(api.token,$('#setup-passphrase').value,repo);
    let file=await api.read(repo,'feedback.json',true);
    if(!file) {
      await api.write(repo,'feedback.json',emptyFeedback(),undefined,'Initialize private Observatory feedback');
      file=await api.read(repo,'feedback.json');
    }
    feedback=validateFeedback(file.value);
    await api.write(PUBLIC_REPO,'feedback-auth.json',record,prior?.sha,'Configure encrypted Observatory access');
    session={api,repo};dialog.close();redraw();status('Private feedback is ready. Connect this private repository to ChatGPT before starting the nightly task.');
  } catch(error) {accountStatus(error.message);}
  finally {$('#setup-token').value='';setBusy(false);}
});
$('#lock').addEventListener('click',()=>{
  if(busy)return;
  session=null;feedback=emptyFeedback();redraw();status('');
  // Reload also discards the decrypted credential and private data in memory.
  location.reload();
});
$('#preferences').addEventListener('click',async()=>{
  if(!session || busy)return;
  showView('preferences');dialog.showModal();setBusy(true);accountStatus('Loading private preferences…');
  try {
    await session.api.assertPrivate(session.repo);
    const file=await session.api.read(session.repo,'preferences.json',true);
    $('#preference-notes').value=file?.value?.selection_notes || '';
    $('#preferences-form').dataset.sha=file?.sha || '';
    accountStatus('');
  }catch(error){accountStatus(error.message);}
  finally{setBusy(false);}
});
$('#preferences-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!session || busy)return;setBusy(true);accountStatus('Saving privately…');
  try {
    await session.api.assertPrivate(session.repo);
    await session.api.write(session.repo,'preferences.json',{schema_version:1,selection_notes:$('#preference-notes').value.trim(),updated_at:new Date().toISOString()},$('#preferences-form').dataset.sha || undefined,'Save private Observatory preferences');
    dialog.close();status('Preferences saved privately.');
  }catch(error){accountStatus([409,422].includes(error.status)?'Preferences changed on another device. Copy your text, reopen Preferences, and reconcile the changes.':error.message);}
  finally{setBusy(false);}
});
window.addEventListener('beforeunload',event=>{if(busy){event.preventDefault();event.returnValue='';}});
redraw();
