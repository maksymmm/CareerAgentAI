"""Static, no-execution operator approval client for the sandbox runtime."""


def operator_sandbox_html() -> bytes:
    """Return an unpersonalized client; credentials and prompts are never embedded."""
    return _HTML.encode("utf-8")


_HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareerAgentAI operator sandbox</title>
<style>body{font:16px/1.5 system-ui;max-width:54rem;margin:auto;padding:2rem}label{display:block;margin:1rem 0}input,button{font:inherit;padding:.6rem}input{display:block;width:95%}button{margin:.3rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eee;padding:1rem}</style>
<h1>Operator sandbox</h1><p>Dry-run only. Recording approval never executes an operation.
Status is not permission to retry. Token stays in this tab; reload clears it.</p>
<label>Bearer token<input id="token" type="password" autocomplete="off"></label>
<label>Operation ID<input id="operation" autocomplete="off"></label>
<button id="status" type="button">Refresh status</button>
<button id="load" type="button">Load approval prompt</button>
<h2>Exact approval prompt</h2><pre id="prompt">No prompt loaded.</pre>
<button id="approve" type="button" disabled>Approve dry-run intent</button>
<button id="decline" type="button" disabled>Decline</button>
<button id="clear" type="button">Clear credentials</button>
<pre id="result" role="status" aria-live="polite"></pre>
<script>
'use strict';
const el=id=>document.getElementById(id);
let prompt=null, busy=false;
function invalidate(){prompt=null;el('prompt').textContent='No prompt loaded.';buttons();}
function buttons(){for(const id of ['status','load','clear'])el(id).disabled=busy;
 for(const id of ['approve','decline'])el(id).disabled=busy||!prompt;}
for(const id of ['token','operation'])el(id).addEventListener('input',()=>{invalidate();el('result').textContent='';});
el('clear').onclick=()=>{el('token').value='';invalidate();el('result').textContent='Credentials cleared.';};
async function perform(kind){
 if(busy)return;
 const token=el('token').value, operation=el('operation').value;
 const saved=prompt;
 invalidate();busy=true;buttons();
 try{
  if(!token||!operation)throw new Error('Token and operation ID are required.');
  const decision=kind==='approve'||kind==='decline';
  if(decision&&(!saved||saved.operation!==operation||saved.token!==token))throw new Error('Reload the approval prompt.');
  const route=kind==='status'?'status':'approval';
  const options={method:decision?'POST':'GET',cache:'no-store',credentials:'omit',redirect:'error',
   headers:{Authorization:'Bearer '+token}};
  if(decision){options.headers['Content-Type']='application/json';options.body=JSON.stringify({schema_version:1,
   action_fingerprint:saved.value.action_fingerprint,decision:kind});}
  const response=await fetch('/v1/release/promotions/'+encodeURIComponent(operation)+'/'+route,options);
  if(!response.ok)throw new Error('Request failed (HTTP '+response.status+'). Reload the prompt before deciding.');
  const value=await response.json();
  if(token!==el('token').value||operation!==el('operation').value)throw new Error('Inputs changed; reload the prompt.');
  if(kind==='load'){
   if(value.operation_id!==operation||!(/^[0-9a-f]{64}$/).test(value.action_fingerprint)||
    !Array.isArray(value.allowed_decisions)||!['approve','decline'].every(x=>value.allowed_decisions.includes(x)))throw new Error('Invalid approval prompt.');
   prompt={operation,token,value};el('prompt').textContent=JSON.stringify(value,null,2);
  }
  el('result').textContent=JSON.stringify(value,null,2);
 }catch(error){invalidate();el('result').textContent=error.message;}
 finally{busy=false;buttons();}
}
for(const id of ['status','load','approve','decline'])el(id).onclick=()=>perform(id);
</script></html>"""
