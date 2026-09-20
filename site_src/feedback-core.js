// Credentials live only in memory after unlock. No browser storage is used.
export const PUBLIC_REPO = 'barasch/observatory';
export const ITERATIONS = 600000;
const prefix = 'observatory-feedback-v1:';
const bytes64 = bytes => btoa(Array.from(bytes, b => String.fromCharCode(b)).join(''));
const from64 = text => Uint8Array.from(atob(text.replace(/\s/g, '')), c => c.charCodeAt(0));
export const encode = text => bytes64(new TextEncoder().encode(text));
export const decode = text => new TextDecoder().decode(from64(text));
export const newPassphrase = () => bytes64(crypto.getRandomValues(new Uint8Array(24)));
async function keyFor(passphrase, salt) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2', salt:from64(salt), iterations:ITERATIONS, hash:'SHA-256'}, material, {name:'AES-GCM', length:256}, false, ['encrypt','decrypt']);
}
export function validateRepository(repo) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo) || repo.toLowerCase() === PUBLIC_REPO) throw new Error('Choose a separate private repository.');
  return repo;
}
export async function encryptCredential(token, passphrase, repo) {
  validateRepository(repo);
  if (passphrase.length < 24) throw new Error('Use the generated passphrase, or at least 24 characters.');
  const salt = bytes64(crypto.getRandomValues(new Uint8Array(16)));
  const key = await keyFor(passphrase, salt);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cipher = await crypto.subtle.encrypt({name:'AES-GCM',iv}, key, new TextEncoder().encode(prefix+JSON.stringify({token,repo,branch:'main'})));
  return {version:1,salt,iterations:ITERATIONS,iv:bytes64(iv),data:bytes64(new Uint8Array(cipher)),feedback_repository:repo,feedback_branch:'main'};
}
export async function decryptCredential(record, passphrase) {
  if (record?.version !== 1 || record.iterations !== ITERATIONS) throw new Error('Unsupported credential format.');
  let text;
  try {
    const key = await keyFor(passphrase,record.salt);
    text = new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:from64(record.iv)},key,from64(record.data)));
  } catch { throw new Error('Incorrect passphrase or damaged credential.'); }
  if (!text.startsWith(prefix)) throw new Error('Invalid credential.');
  const value = JSON.parse(text.slice(prefix.length));
  validateRepository(value.repo);
  if (value.repo !== record.feedback_repository || value.branch !== record.feedback_branch || value.branch !== 'main') throw new Error('Credential repository mismatch.');
  return value;
}
export const emptyFeedback = () => ({schema_version:1,items:{}});
export function validateFeedback(value) {
  if (value?.schema_version !== 1 || !value.items || typeof value.items !== 'object' || Array.isArray(value.items)) throw new Error('Unsupported private feedback format.');
  for (const [id,item] of Object.entries(value.items)) {
    if (!/^[a-z0-9][a-z0-9-]{2,100}$/.test(id) || !item || ![null,'up','down'].includes(item.vote) || typeof item.dismissed !== 'boolean' || typeof item.explanation !== 'string') throw new Error('Invalid private feedback item.');
  }
  return value;
}
export function patchFeedback(value, id, patch, metadata, now = new Date().toISOString()) {
  validateFeedback(value);
  if (!/^[a-z0-9][a-z0-9-]{2,100}$/.test(id)) throw new Error('Invalid item identifier.');
  const old = value.items[id] || {vote:null,dismissed:false,explanation:''};
  const next = {...value, items:{...value.items,[id]:{...old,...patch,metadata,updated_at:now}}};
  return validateFeedback(next);
}
// The scheduler should use only these explicit votes, never dismissals or clicks.
export function preferenceEvidence(value) {
  return Object.entries(validateFeedback(value).items).filter(([,x]) => x.vote === 'up' || x.vote === 'down').map(([id,x]) => ({id,vote:x.vote,explanation:x.explanation,metadata:x.metadata}));
}
export class GitHub {
  constructor(token = '') { this.token = token; }
  async request(repo, path='', options={}) {
    const response = await fetch(`https://api.github.com/repos/${repo}${path}`, {
      method:options.method || 'GET',cache:'no-store',
      headers:{Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28',...(this.token?{Authorization:`Bearer ${this.token}`} : {}),...(options.body?{'Content-Type':'application/json'}:{})},
      ...(options.body?{body:JSON.stringify(options.body)}:{})
    });
    if (response.status === 404 && options.allowMissing) return null;
    if (!response.ok) {
      const error = new Error(response.status === 401 ? 'GitHub access expired. Replace the token in Setup.' : response.status === 404 ? 'Repository unavailable. Check its name and the token’s repository access.' : `GitHub could not save or load this change (${response.status}).`);
      error.status=response.status; throw error;
    }
    return response.json();
  }
  async assertPrivate(repo) {
    validateRepository(repo);
    const metadata = await this.request(repo);
    if (metadata.private !== true || metadata.visibility !== 'private') throw new Error('Feedback storage must be private. Nothing was written.');
    if (metadata.permissions?.push !== true) throw new Error('The token needs Contents: read and write for the private repository.');
    return metadata;
  }
  async read(repo,path,allowMissing=false) {
    const record = await this.request(repo,`/contents/${path}?ref=main`,{allowMissing});
    if (!record) return null;
    let content=record.content;
    if (!content && record.sha) content=(await this.request(repo,`/git/blobs/${record.sha}`)).content;
    return {sha:record.sha,value:JSON.parse(decode(content))};
  }
  async write(repo,path,value,sha,message) {
    return this.request(repo,`/contents/${path}`,{method:'PUT',body:{message,branch:'main',content:encode(JSON.stringify(value,null,2)+'\n'),...(sha?{sha}:{})}});
  }
  async saveFeedback(repo,id,patch,metadata) {
    // Check privacy before every write, including a retry after a concurrent edit.
    for (let attempt=0;attempt<4;attempt++) {
      await this.assertPrivate(repo);
      const file = await this.read(repo,'feedback.json',true);
      const next = patchFeedback(file?.value || emptyFeedback(),id,patch,metadata);
      try { await this.write(repo,'feedback.json',next,file?.sha,'Save private Observatory feedback'); return next; }
      catch(error) { if (![409,422].includes(error.status) || attempt === 3) throw error; }
    }
  }
}
