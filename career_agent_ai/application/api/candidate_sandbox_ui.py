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
    <button id="list-runs" type="button">Load my runs</button>
    <ul id="owned-runs" aria-label="My runs"></ul>
    <p id="list-state" role="status"></p>
    <button id="next-runs" type="button" disabled>Next page</button>
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
  <section aria-labelledby="overview-title">
    <h2 id="overview-title">Run overview</h2>
    <p id="run-overview" role="status" aria-live="polite">Refresh status to load a snapshot.</p>
    <p class="muted">A snapshot never authorizes an action. Inspect the current approval prompt before deciding.</p>
  </section>
  <section aria-labelledby="applications-title">
    <h2 id="applications-title">My applications</h2>
    <button id="list-applications" type="button">Load my applications</button>
    <ul id="owned-applications" aria-label="My applications"></ul>
    <p id="applications-state" role="status"></p>
    <button id="next-applications" type="button" disabled>Next application page</button>
    <h3>Application detail</h3>
    <p id="application-detail-state" role="status"></p>
    <pre id="application-detail"></pre>
    <button id="load-application-history" type="button" disabled>Load application history</button>
    <p id="application-history-state" role="status"></p>
    <pre id="application-history"></pre>
    <p class="muted">Read-only tracker snapshots; this list cannot approve or submit applications.</p>
  </section>
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
    let latestResultRequest = 0;
    let inputContextGeneration = 0;
    let listGeneration = 0;
    let nextRunCursor = null;
    const byId = (id) => document.getElementById(id);
    const clearOverview = () => { byId('run-overview').textContent = 'Refresh status to load a snapshot.'; };
    const show = (value) => { clearOverview(); byId('result').textContent = JSON.stringify(value, null, 2); };
    const runPath = (suffix) => '/v1/candidate/runs/' + encodeURIComponent(byId('run-id').value) + suffix;
    let applicationsGeneration = 0;
    let nextApplicationCursor = null;
    let applicationDetailGeneration = 0;
    let selectedApplicationId = null;
    let applicationHistoryGeneration = 0;
    function clearApplicationHistory() {
      applicationHistoryGeneration += 1;
      byId('application-history').textContent = '';
      byId('application-history-state').textContent = '';
    }
    function clearApplicationDetail() {
      clearApplicationHistory();
      selectedApplicationId = null;
      byId('load-application-history').disabled = true;
      applicationDetailGeneration += 1;
      byId('application-detail').textContent = '';
      byId('application-detail-state').textContent = '';
    }
    async function loadApplicationDetail(applicationId) {
      clearApplicationDetail();
      const generation = applicationDetailGeneration;
      byId('application-detail-state').textContent = 'Loading application…';
      try {
        const detail = await call('/v1/candidate/application?application_id=' + encodeURIComponent(applicationId), {}, false);
        if (generation !== applicationDetailGeneration) return;
        const item = detail && detail.application;
        if (detail.schema_version !== 1 || !item || item.application_id !== applicationId ||
            typeof item.job_id !== 'string' || !item.job_id || Array.from(item.job_id).length > 200 ||
            typeof item.job_title !== 'string' || Array.from(item.job_title).length > 500 ||
            typeof item.company_id !== 'string' || Array.from(item.company_id).length > 200 ||
            !['saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'].includes(item.status) ||
            !Number.isSafeInteger(item.state_version) || item.state_version < 1 ||
            !['created_at', 'updated_at'].every(key => typeof item[key] === 'string' &&
              item[key].length <= 64 && Number.isFinite(Date.parse(item[key])))) {
          throw new Error('Invalid application detail.');
        }
        const snapshot = {};
        for (const key of ['application_id', 'job_id', 'job_title', 'company_id', 'status', 'state_version', 'created_at', 'updated_at']) snapshot[key] = item[key];
        byId('application-detail').textContent = JSON.stringify(snapshot, null, 2);
        byId('application-detail-state').textContent = 'Read-only application detail.';
        selectedApplicationId = applicationId;
        byId('load-application-history').disabled = false;
      } catch (error) {
        if (generation === applicationDetailGeneration) {
          clearApplicationDetail();
          byId('application-detail-state').textContent = 'Select the application again to retry.';
        }
        throw error;
      }
    }
    async function loadApplicationHistory() {
      const applicationId = selectedApplicationId;
      if (applicationId === null) return;
      clearApplicationHistory();
      const generation = applicationHistoryGeneration;
      byId('application-history-state').textContent = 'Loading history…';
      try {
        const history = await call('/v1/candidate/application/history?application_id=' + encodeURIComponent(applicationId), {}, false);
        if (generation !== applicationHistoryGeneration) return;
        const statuses = ['saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'];
        const transitions = {saved: ['applied', 'withdrawn'], applied: ['interview', 'rejected', 'withdrawn'],
          interview: ['offer', 'rejected', 'withdrawn'], offer: ['withdrawn'], rejected: [], withdrawn: []};
        if (!history || history.schema_version !== 1 || history.application_id !== applicationId ||
            !Number.isSafeInteger(history.state_version) || history.state_version < 1 ||
            !Array.isArray(history.items) || history.items.length > 4 ||
            history.items.length !== history.state_version - 1 || !history.items.every((item, index) =>
              item && item.sequence === index && statuses.includes(item.from_status) && statuses.includes(item.to_status) &&
              transitions[item.from_status].includes(item.to_status) &&
              typeof item.occurred_at === 'string' && item.occurred_at.length <= 64 && Number.isFinite(Date.parse(item.occurred_at)) &&
              (index === 0 || (item.from_status === history.items[index - 1].to_status &&
                Date.parse(item.occurred_at) >= Date.parse(history.items[index - 1].occurred_at))))) {
          throw new Error('Invalid application history.');
        }
        const snapshot = {schema_version: 1, application_id: applicationId, state_version: history.state_version,
          items: history.items.map(item => ({sequence: item.sequence, from_status: item.from_status,
            to_status: item.to_status, occurred_at: item.occurred_at}))};
        byId('application-history').textContent = JSON.stringify(snapshot, null, 2);
        byId('application-history-state').textContent = history.items.length ? 'Read-only application history.' : 'No status transitions yet.';
      } catch (error) {
        if (generation === applicationHistoryGeneration) {
          clearApplicationHistory();
          byId('application-history-state').textContent = 'Load history again to retry.';
        }
        throw error;
      }
    }
    byId('load-application-history').addEventListener('click', () => guarded(loadApplicationHistory));
    function clearApplications() {
      clearApplicationDetail();
      applicationsGeneration += 1;
      nextApplicationCursor = null;
      byId('owned-applications').replaceChildren();
      byId('applications-state').textContent = '';
      byId('next-applications').disabled = true;
    }
    async function loadApplications(after = null) {
      clearApplications();
      const generation = applicationsGeneration;
      byId('applications-state').textContent = 'Loading…';
      try {
        const page = await call('/v1/candidate/applications?limit=25' +
          (after === null ? '' : '&after=' + encodeURIComponent(after)));
        if (generation !== applicationsGeneration) return;
        // Canonical whitespace is validated by the server (Python strip differs
        // from JavaScript trim, notably for U+FEFF). Treat returned IDs as opaque.
        const identifier = value => typeof value === 'string' &&
          Array.from(value).length >= 1 && Array.from(value).length <= 200;
        const statuses = ['saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'];
        if (!page || page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || identifier(page.next_after)) || !page.items.every(item =>
              item && identifier(item.application_id) && identifier(item.job_id) &&
              typeof item.job_title === 'string' && Array.from(item.job_title).length <= 500 &&
              typeof item.company_id === 'string' && Array.from(item.company_id).length <= 200 &&
              statuses.includes(item.status) && Number.isSafeInteger(item.state_version) && item.state_version >= 1)) {
          throw new Error('Invalid application page.');
        }
        for (const item of page.items) {
          const row = document.createElement('li');
          row.textContent = (item.job_title || item.job_id) + ' — ' + item.company_id +
            ' — ' + item.status + ' — version ' + item.state_version + ' — ' + item.application_id;
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'View application';
          button.addEventListener('click', () => {
            if (generation === applicationsGeneration) guarded(() => loadApplicationDetail(item.application_id));
          });
          row.append(button);
          byId('owned-applications').append(row);
        }
        nextApplicationCursor = page.next_after;
        byId('next-applications').disabled = nextApplicationCursor === null;
        byId('applications-state').textContent = page.items.length ? 'Read-only application snapshots.' : 'No applications on this page.';
      } catch (error) {
        if (generation === applicationsGeneration) {
          clearApplications();
          nextApplicationCursor = after;
          byId('next-applications').disabled = after === null;
          byId('applications-state').textContent = 'Load or retry the application page.';
        }
        throw error;
      }
    }
    byId('list-applications').addEventListener('click', () => guarded(() => loadApplications()));
    byId('next-applications').addEventListener('click', () => guarded(() => {
      if (nextApplicationCursor !== null) return loadApplications(nextApplicationCursor);
    }));
    byId('token').addEventListener('input', clearApplications);
    function clearRunList() {
      listGeneration += 1;
      nextRunCursor = null;
      byId('owned-runs').replaceChildren();
      byId('list-state').textContent = '';
      byId('next-runs').disabled = true;
    }
    async function loadRunList(after = null) {
      clearRunList();
      const generation = listGeneration;
      byId('list-state').textContent = 'Loading…';
      try {
        const page = await call('/v1/candidate/run-statuses?limit=25' +
          (after === null ? '' : '&after=' + encodeURIComponent(after)));
        if (generation !== listGeneration) return;
        const validId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
        if (page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || validId(page.next_after)) ||
            !page.items.every(item => item && validId(item.run_id) && typeof item.phase === 'string')) {
          throw new Error('Invalid run page.');
        }
        for (const item of page.items) {
          const row = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = item.run_id + ' — ' + item.phase.replaceAll('_', ' ');
          button.addEventListener('click', () => {
            if (generation !== listGeneration) return;
            latestStartRequest += 1;
            selectRun(item.run_id);
            byId('result').textContent = 'Run selected. Refresh status or load its current prompt.';
          });
          row.append(button); byId('owned-runs').append(row);
        }
        nextRunCursor = page.next_after;
        byId('next-runs').disabled = nextRunCursor === null;
        byId('list-state').textContent = page.items.length ? 'Select a run to inspect it.' : 'No runs on this page.';
      } catch (error) {
        if (generation === listGeneration) {
          clearRunList();
          nextRunCursor = after;
          byId('next-runs').disabled = after === null;
          byId('list-state').textContent = after === null ? 'Load my runs to retry.' : 'Next page failed. Retry Next page.';
        }
        throw error;
      }
    }
    byId('list-runs').addEventListener('click', () => guarded(() => loadRunList()));
    byId('next-runs').addEventListener('click', () => guarded(() => {
      if (nextRunCursor !== null) return loadRunList(nextRunCursor);
    }));
    byId('token').addEventListener('input', clearRunList);
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
      clearOverview();
      inputContextGeneration += 1;
      latestResultRequest += 1;
      byId('run-id').value = runId;
      invalidateApprovalPrompt();
    }
    async function call(path, options = {}, renderRaw = true) {
      clearOverview();
      const resultRequest = ++latestResultRequest;
      const token = byId('token').value;
      const contextGeneration = inputContextGeneration;
      if (!token) throw new Error('Bearer token is required.');
      try {
        const response = await fetch(path, {
          ...options,
          headers: {Authorization: 'Bearer ' + token, ...(options.headers || {})},
        });
        const payload = await response.json();
        if (contextGeneration !== inputContextGeneration) {
          throw new Error('Inputs changed; reload the current state.');
        }
        if (renderRaw && resultRequest === latestResultRequest) show({status: response.status, payload});
        if (!response.ok) {
          const error = new Error(renderRaw ? (payload.message || payload.error || 'Request failed.') : 'Application detail request failed.');
          error.status = response.status;
          error.code = payload.error;
          throw error;
        }
        return payload;
      } catch (error) {
        if (!renderRaw) error = new Error('Application detail request failed.');
        error.resultRequest = resultRequest;
        throw error;
      }
    }
    async function guarded(operation) {
      try { await operation(); } catch (error) {
        if (error.resultRequest === undefined || error.resultRequest === latestResultRequest) {
          show({error: String(error.message || error)});
        }
      }
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
    function invalidateInputContext() {
      clearOverview();
      inputContextGeneration += 1;
      latestResultRequest += 1;
      latestStartRequest += 1;
      invalidateApprovalPrompt();
      byId('result').textContent = 'Inputs changed. Reload the current state.';
    }
    byId('run-id').addEventListener('input', invalidateInputContext);
    byId('token').addEventListener('input', invalidateInputContext);
    byId('status').addEventListener('click', () => guarded(async () => {
      clearOverview();
      const runId = byId('run-id').value;
      const requestNumber = latestResultRequest + 1;
      const snapshot = await call(runPath('/status'));
      if (requestNumber !== latestResultRequest) return;
      if (snapshot.schema_version !== 1 || snapshot.run_id !== runId ||
          !Number.isSafeInteger(snapshot.state_version) || snapshot.state_version < 0 ||
          typeof snapshot.phase !== 'string' || typeof snapshot.waiting_for_human !== 'boolean' ||
          typeof snapshot.terminal !== 'boolean') throw new Error('Invalid status snapshot.');
      const state = snapshot.terminal ? 'Finished' : snapshot.waiting_for_human ? 'Your decision is needed' : 'In progress';
      byId('run-overview').textContent = state + ' — ' + snapshot.phase.replaceAll('_', ' ') +
        ' (version ' + snapshot.state_version + '). Run: ' + snapshot.run_id;
    }));
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
        if (error.code === 'recovery_unavailable') {
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
        const loadedPrompt = await call('/v1/candidate/runs/' + encodeURIComponent(promptRunId) + '/approval', {}, false);
        if (promptGeneration !== approvalPromptGeneration || promptRunId !== byId('run-id').value) return;
        const fields = ['schema_version', 'run_id', 'state_version', 'action_kind', 'title',
          'details', 'action_fingerprint', 'allowed_decisions'];
        if (!loadedPrompt || typeof loadedPrompt !== 'object' || Array.isArray(loadedPrompt) ||
            Object.keys(loadedPrompt).length !== fields.length || !fields.every(key => Object.hasOwn(loadedPrompt, key)) ||
            loadedPrompt.schema_version !== 1 || loadedPrompt.run_id !== promptRunId ||
            !Number.isSafeInteger(loadedPrompt.state_version) || loadedPrompt.state_version < 0 ||
            !['approve_application', 'approve_message', 'approve_interview'].includes(loadedPrompt.action_kind) ||
            typeof loadedPrompt.title !== 'string' || !loadedPrompt.title.trim() ||
            !loadedPrompt.details || typeof loadedPrompt.details !== 'object' || Array.isArray(loadedPrompt.details) ||
            typeof loadedPrompt.action_fingerprint !== 'string' || !/^[0-9a-f]{64}$/.test(loadedPrompt.action_fingerprint) ||
            !Array.isArray(loadedPrompt.allowed_decisions) || loadedPrompt.allowed_decisions.length !== 2 ||
            !['approve', 'decline'].every(value => loadedPrompt.allowed_decisions.includes(value))) {
          throw new Error('Invalid approval prompt. Load it again.');
        }
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
