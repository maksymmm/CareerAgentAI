"""Dependency-free candidate sandbox UI for the authenticated approval API."""

from __future__ import annotations


def candidate_sandbox_html() -> bytes:
    """Return the static sandbox client without embedding credentials or state."""
    return _HTML.encode("utf-8")


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CareerAgentAI candidate sandbox</title>
  <style>
    :root { color-scheme: light dark; font: 16px/1.45 system-ui, sans-serif; }
    body { margin: auto; max-width: 58rem; padding: 2rem 1rem 5rem; }
    fieldset { border: 1px solid #8888; border-radius: .6rem; margin: 1rem 0; padding: 1rem; }
    label { display: block; margin: .7rem 0; }
    input, textarea, button { box-sizing: border-box; font: inherit; padding: .55rem; width: 100%; }
    textarea { min-height: 7rem; }
    button { cursor: pointer; margin-top: .45rem; }
    .actions { display: grid; gap: .6rem; grid-template-columns: repeat(2, 1fr); }
    .muted { opacity: .75; }
    #result { border-radius: .6rem; min-height: 5rem; overflow: auto; padding: 1rem; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>Candidate sandbox</h1>
  <p class="muted">No real application, message, or calendar action is sent. The bearer token stays in this tab's memory.</p>
  <label>Bearer token <input id="token" type="password" autocomplete="off" required></label>
  <fieldset>
    <legend>Start a sandbox run</legend>
    <label>Run ID <input id="start-run-id" value="candidate-demo-1" required></label>
    <label>Job keywords <input id="keyword" required></label>
    <label>Location <input id="location"></label>
    <label>Candidate profile <textarea id="profile" required></textarea></label>
    <button id="start" type="button">Start run</button>
  </fieldset>
  <fieldset>
    <legend>Inspect and continue</legend>
    <label>Durable run ID <input id="run-id" required></label>
    <div class="actions">
      <button id="status" type="button">Refresh status</button>
      <button id="prompt" type="button">Load approval prompt</button>
      <button id="continue" type="button">Continue recovery</button>
    </div>
  </fieldset>
  <fieldset>
    <legend>Pending candidate decision</legend>
    <p id="action-title">Load a prompt before deciding.</p>
    <pre id="action-details"></pre>
    <div class="actions">
      <button id="approve" type="button" disabled>Approve displayed action</button>
      <button id="decline" type="button" disabled>Decline displayed action</button>
    </div>
  </fieldset>
  <h2>Result</h2>
  <pre id="result" aria-live="polite">Ready.</pre>
  <script>
    'use strict';
    let approvalPrompt = null;
    let decisionInFlight = false;
    let activePromptLoadGeneration = null;
    let recoveryInFlight = false;
    let latestStartRequest = 0;
    let approvalPromptGeneration = 0;
    const byId = (id) => document.getElementById(id);
    const show = (value) => { byId('result').textContent = JSON.stringify(value, null, 2); };
    const runPath = (suffix) => '/v1/candidate/runs/' + encodeURIComponent(byId('run-id').value) + suffix;
    function invalidateApprovalPrompt(message = 'Load the current approval prompt.') {
      approvalPromptGeneration += 1;
      approvalPrompt = null;
      byId('action-title').textContent = message;
      byId('action-details').textContent = '';
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      activePromptLoadGeneration = null;
      byId('prompt').disabled = decisionInFlight || recoveryInFlight;
    }
    function selectRun(runId) {
      byId('run-id').value = runId;
      invalidateApprovalPrompt();
    }
    async function call(path, options = {}) {
      const token = byId('token').value;
      if (!token) throw new Error('Bearer token is required.');
      const response = await fetch(path, {
        ...options,
        headers: {Authorization: 'Bearer ' + token, ...(options.headers || {})},
      });
      const payload = await response.json();
      show({status: response.status, payload});
      if (!response.ok) {
        const error = new Error(payload.message || payload.error || 'Request failed.');
        error.status = response.status;
        throw error;
      }
      return payload;
    }
    async function guarded(operation) {
      try { await operation(); } catch (error) { show({error: String(error.message || error)}); }
    }
    byId('start').addEventListener('click', () => guarded(async () => {
      const requestNumber = ++latestStartRequest;
      const payload = await call('/v1/candidate/runs', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({schema_version: 1, run_id: byId('start-run-id').value,
          keyword: byId('keyword').value, candidate_profile: byId('profile').value,
          location: byId('location').value}),
      });
      if (requestNumber === latestStartRequest) selectRun(payload.run_id);
    }));
    byId('run-id').addEventListener('input', () => {
      latestStartRequest += 1;
      invalidateApprovalPrompt();
    });
    byId('status').addEventListener('click', () => guarded(() => call(runPath('/status'))));
    byId('continue').addEventListener('click', () => guarded(async () => {
      if (decisionInFlight) throw new Error('Wait for the current decision to finish.');
      if (activePromptLoadGeneration !== null) throw new Error('Wait for the approval prompt to finish loading.');
      if (recoveryInFlight) throw new Error('Recovery is already running.');
      recoveryInFlight = true;
      byId('continue').disabled = true;
      byId('prompt').disabled = true;
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      try {
        await call(runPath('/continue'), {method: 'POST'});
        invalidateApprovalPrompt('Recovery finished. Load the current approval prompt.');
      } catch (error) {
        if (error.status) {
          byId('approve').disabled = !approvalPrompt;
          byId('decline').disabled = !approvalPrompt;
        } else {
          invalidateApprovalPrompt('Recovery result is uncertain. Load the current approval prompt.');
        }
        throw error;
      } finally {
        recoveryInFlight = false;
        byId('continue').disabled = false;
        byId('prompt').disabled = false;
      }
    }));
    byId('prompt').addEventListener('click', () => guarded(async () => {
      if (decisionInFlight) throw new Error('Wait for the current decision to finish.');
      if (activePromptLoadGeneration !== null) throw new Error('An approval prompt is already loading.');
      invalidateApprovalPrompt('Loading approval prompt…');
      const promptGeneration = approvalPromptGeneration;
      const promptRunId = byId('run-id').value;
      activePromptLoadGeneration = promptGeneration;
      byId('prompt').disabled = true;
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      try {
        const loadedPrompt = await call('/v1/candidate/runs/' + encodeURIComponent(promptRunId) + '/approval');
        if (promptGeneration !== approvalPromptGeneration || promptRunId !== byId('run-id').value) return;
        approvalPrompt = loadedPrompt;
        byId('action-title').textContent = loadedPrompt.title;
        byId('action-details').textContent = JSON.stringify(loadedPrompt.details, null, 2);
        byId('approve').disabled = false;
        byId('decline').disabled = false;
      } catch (error) {
        if (promptGeneration === approvalPromptGeneration) {
          byId('action-title').textContent = 'Approval prompt unavailable. Load it again.';
        }
        throw error;
      } finally {
        if (activePromptLoadGeneration === promptGeneration) {
          activePromptLoadGeneration = null;
          byId('prompt').disabled = false;
        }
      }
    }));
    async function decide(decision) {
      if (recoveryInFlight) throw new Error('Wait for recovery to finish.');
      if (activePromptLoadGeneration !== null) throw new Error('Wait for the approval prompt to finish loading.');
      if (!approvalPrompt) throw new Error('Load the current prompt first.');
      if (decisionInFlight) throw new Error('A decision is already being submitted.');
      decisionInFlight = true;
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      byId('prompt').disabled = true;
      byId('continue').disabled = true;
      const payload = {schema_version: 1, run_id: approvalPrompt.run_id,
        state_version: approvalPrompt.state_version,
        action_fingerprint: approvalPrompt.action_fingerprint, decision};
      try {
        await call('/v1/candidate/runs/' + encodeURIComponent(payload.run_id) + '/approval', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)});
      } finally {
        decisionInFlight = false;
        approvalPrompt = null;
        byId('action-title').textContent = 'Decision finished. Load the current prompt.';
        byId('action-details').textContent = '';
        byId('prompt').disabled = false;
        byId('continue').disabled = false;
      }
    }
    byId('approve').addEventListener('click', () => guarded(() => decide('approve')));
    byId('decline').addEventListener('click', () => guarded(() => decide('decline')));
  </script>
</body>
</html>
"""
