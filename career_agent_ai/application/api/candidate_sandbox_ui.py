"""Dependency-free candidate sandbox UI for the authenticated approval API."""

from __future__ import annotations


def candidate_sandbox_html() -> bytes:
    """Return the static sandbox client without embedding credentials or state."""
    return _HTML.encode("utf-8")


_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CareerAgentAI candidate sandbox</title>
  <style>
    :root { color-scheme: light dark; font: 16px/1.45 system-ui, sans-serif; }
    body { box-sizing: border-box; width: 100%; margin: auto; max-width: 58rem; padding: 2rem 1rem 5rem; overflow-wrap: anywhere; }
    fieldset { min-width: 0; border: 1px solid #8888; border-radius: .6rem; margin: 1rem 0; padding: 1rem; }
    pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
    label { display: block; margin: .7rem 0; }
    input, textarea, button { box-sizing: border-box; font: inherit; padding: .55rem; width: 100%; }
    textarea { min-height: 7rem; }
    button { cursor: pointer; margin-top: .45rem; }
    .actions { display: grid; gap: .6rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    @media (max-width: 32rem) { .actions { grid-template-columns: 1fr; } }
    .muted { opacity: .75; }
    .visually-hidden { height: 1px; margin: -1px; overflow: hidden; padding: 0; position: absolute; width: 1px; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
    nav { display: flex; flex-wrap: wrap; gap: .6rem 1rem; margin: 1rem 0; }
    .skip-link { left: 1rem; position: absolute; top: -5rem; }
    .skip-link:focus { top: 1rem; }
    :focus-visible { outline: 3px solid Highlight; outline-offset: 3px; }
    [tabindex="-1"] { scroll-margin-top: 1rem; }
    #result { border-radius: .6rem; min-height: 5rem; overflow: auto; padding: 1rem; white-space: pre-wrap; }
  </style>
</head>
<body>
  <a class="skip-link" href="#dashboard-content">Skip to dashboard content</a>
  <h1 id="candidate-top" tabindex="-1">Candidate sandbox</h1>
  <p class="muted">No real application, message, or calendar action is sent. The bearer token stays in this tab's memory.</p>
  <nav aria-label="Candidate dashboard">
    <a href="#start-panel">Start run</a>
    <a href="#inspect-panel">Inspect runs</a>
    <a href="#decision-panel">Pending decision</a>
    <a href="#overview-title">Run overview</a>
    <a href="#applications-title">My applications</a>
    <a href="#schedule-title">Interviews and events</a>
  </nav>
  <main id="dashboard-content" tabindex="-1">
  <label>Bearer token <input id="token" type="password" autocomplete="off" required></label>
  <button id="clear-session" type="button">Clear this tab's session</button>
  <p class="muted">Clears credentials, profile and displayed data locally. It does not revoke a token or cancel work already accepted by the server.</p>
  <fieldset id="start-panel" tabindex="-1">
    <legend>Start a sandbox run</legend>
    <label>Run ID <input id="start-run-id" value="candidate-demo-1" required></label>
    <label>Job keywords <input id="keyword" required></label>
    <label>Location <input id="location"></label>
    <label>Candidate profile <textarea id="profile" required></textarea></label>
    <button id="start" type="button">Start run</button>
  </fieldset>
  <fieldset id="inspect-panel" tabindex="-1">
    <legend>Inspect and continue</legend>
    <label for="run-phase">Phase</label>
    <select id="run-phase">
      <option value="">All phases</option>
      <option value="search">Search</option><option value="decision">Decision</option>
      <option value="resume">Resume</option><option value="application_prepare">Application prepare</option>
      <option value="application_approval">Application approval</option><option value="application_submit">Application submit</option>
      <option value="application_reconciliation">Application reconciliation</option><option value="message_prepare">Message prepare</option>
      <option value="message_approval">Message approval</option><option value="message_send">Message send</option>
      <option value="message_reconciliation">Message reconciliation</option><option value="track">Track</option>
      <option value="interview_coordination">Interview coordination</option><option value="interview_approval">Interview approval</option>
      <option value="interview_accept">Interview accept</option><option value="interview_reconciliation">Interview reconciliation</option>
      <option value="complete">Complete</option><option value="failed">Failed</option>
    </select>
    <button id="list-runs" type="button">Load my runs</button>
    <ul id="owned-runs" aria-label="My runs" aria-busy="false"></ul>
    <p id="list-state" role="status"></p>
    <button id="next-runs" type="button" disabled>Next page</button>
    <label>Durable run ID <input id="run-id" required></label>
    <div class="actions">
      <button id="status" type="button">Refresh status</button>
      <button id="prompt" type="button">Load approval prompt</button>
      <button id="continue" type="button">Continue recovery</button>
    </div>
  </fieldset>
  <fieldset id="decision-panel" tabindex="-1">
    <legend>Pending candidate decision</legend>
    <p id="action-title">Load a prompt before deciding.</p>
    <pre id="action-details"></pre>
    <div class="actions">
      <button id="approve" type="button" disabled>Approve displayed action</button>
      <button id="decline" type="button" disabled>Decline displayed action</button>
    </div>
  </fieldset>
  <section aria-labelledby="overview-title">
    <h2 id="overview-title" tabindex="-1">Run overview</h2>
    <p id="run-overview" role="status" aria-live="polite">Refresh status to load a snapshot.</p>
    <p class="muted">A snapshot never authorizes an action. Inspect the current approval prompt before deciding.</p>
  </section>
  <section aria-labelledby="applications-title">
    <h2 id="applications-title" tabindex="-1">My applications</h2>
    <label for="application-status">Status</label>
    <select id="application-status">
      <option value="">All statuses</option>
      <option value="saved">Saved</option>
      <option value="applied">Applied</option>
      <option value="interview">Interview</option>
      <option value="offer">Offer</option>
      <option value="rejected">Rejected</option>
      <option value="withdrawn">Withdrawn</option>
    </select>
    <button id="list-applications" type="button">Load my applications</button>
    <ul id="owned-applications" aria-label="My applications" aria-busy="false"></ul>
    <p id="applications-state" role="status"></p>
    <button id="next-applications" type="button" disabled>Next application page</button>
    <h3 id="application-detail-title">Application detail</h3>
    <p id="application-detail-state" role="status"></p>
    <pre id="application-detail" role="region" aria-labelledby="application-detail-title" aria-busy="false"></pre>
    <button id="load-application-history" type="button" disabled>Load application history</button>
    <p id="application-history-state" role="status"></p>
    <h3 id="application-history-title" class="visually-hidden">Application history</h3>
    <pre id="application-history" role="region" aria-labelledby="application-history-title" aria-busy="false"></pre>
    <p class="muted">Read-only tracker snapshots; this list cannot approve or submit applications.</p>
  </section>
  <section aria-labelledby="schedule-title">
    <h2 id="schedule-title" tabindex="-1">My interviews and events</h2>
    <label for="event-status">Status</label>
    <select id="event-status">
      <option value="">All statuses</option>
      <option value="proposed">Proposed</option><option value="accepted">Accepted</option>
      <option value="declined">Declined</option><option value="reschedule_requested">Reschedule requested</option>
      <option value="cancelled">Cancelled</option>
    </select>
    <button id="list-events" type="button">Load my events</button>
    <ul id="owned-events" aria-label="My events" aria-busy="false"></ul>
    <p id="events-state" role="status"></p>
    <button id="next-events" type="button" disabled>Next event page</button>
    <h3 id="event-detail-title">Event detail</h3>
    <p id="event-detail-state" role="status"></p>
    <pre id="event-detail" role="region" aria-labelledby="event-detail-title" aria-busy="false"></pre>
    <p class="muted">Read-only schedule. No accept, decline, reschedule or calendar actions.</p>
  </section>
  <h2 id="result-title">Result</h2>
  <pre id="result" role="region" aria-labelledby="result-title" aria-live="polite">Ready.</pre>
  <a href="#candidate-top">Back to dashboard navigation</a>
  </main>
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
    const pageSummary = (count, singular, plural, hasMore, instruction = '') =>
      count + ' ' + (count === 1 ? singular : plural) + ' on this page. ' +
      (hasMore ? 'More available.' : 'End of list.') + (instruction ? ' ' + instruction : '');
    let eventsGeneration = 0;
    let eventDetailGeneration = 0;
    let eventDetailTrigger = null;
    let nextEventCursor = null;
    const eventId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
    const eventFields = ['event_id', 'employer_name', 'event_type', 'location', 'status', 'state_version',
      'start_at', 'end_at', 'local_date', 'local_start_time', 'local_end_date', 'local_end_time',
      'timezone_name', 'utc_offset', 'end_utc_offset'];
    function eventSnapshot(item) {
      const text = (value, max) => typeof value === 'string' && value.length > 0 && Array.from(value).length <= max;
      const date = value => {
        if (!text(value, 10) || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
        const [year, month, day] = value.split('-').map(Number);
        const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
        const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
      };
      const time = value => text(value, 15) && [8, 15].includes(value.length) && /^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{6})?$/.test(value);
      const offset = value => text(value, 9) && [6, 9].includes(value.length) && /^[+-](?:[01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(value);
      const instant = value => {
        if (!text(value, 40)) return false;
        const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d{6})?)([+-]\d{2}:\d{2}(?::\d{2})?)$/.exec(value);
        return !!match && match[0] === value && date(match[1]) && time(match[2]) && offset(match[3]);
      };
      const epoch = (day, clock, zoneOffset) => {
        // Date supplies exact whole seconds only; BigInt retains every microsecond.
        const [year, month, dayOfMonth] = day.split('-').map(Number);
        const [wholeTime, fraction = '000000'] = clock.split('.');
        const utc = new Date(0);
        utc.setUTCFullYear(year, month - 1, dayOfMonth);
        utc.setUTCHours(...wholeTime.split(':').map(Number), 0);
        const [hours, minutes, seconds = 0] = zoneOffset.slice(1).split(':').map(Number);
        const offsetSeconds = (hours * 3600 + minutes * 60 + seconds) * (zoneOffset[0] === '-' ? -1 : 1);
        return BigInt(utc.getTime()) * 1000n + BigInt(fraction) - BigInt(offsetSeconds) * 1000000n;
      };
      const instantEpoch = value => {
        const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d{6})?)([+-].+)$/.exec(value);
        return epoch(match[1], match[2], match[3]);
      };
      if (!item || !eventId(item.event_id) || !text(item.employer_name, 500) || !text(item.location, 2000) ||
          !['interview', 'trial_day', 'phone_call', 'video_call', 'other'].includes(item.event_type) ||
          !['proposed', 'accepted', 'declined', 'reschedule_requested', 'cancelled'].includes(item.status) ||
          !Number.isSafeInteger(item.state_version) || item.state_version < 1 || !instant(item.start_at) ||
          !date(item.local_date) || !time(item.local_start_time) || !text(item.timezone_name, 200) || !offset(item.utc_offset) ||
          !(item.end_at === null ? [item.local_end_date, item.local_end_time, item.end_utc_offset].every(value => value === null) :
            instant(item.end_at) &&
            date(item.local_end_date) && time(item.local_end_time) && offset(item.end_utc_offset))) {
        throw new Error('Invalid event snapshot.');
      }
      const start = instantEpoch(item.start_at);
      if (epoch(item.local_date, item.local_start_time, item.utc_offset) !== start ||
          (item.end_at !== null && (instantEpoch(item.end_at) <= start ||
            epoch(item.local_end_date, item.local_end_time, item.end_utc_offset) !== instantEpoch(item.end_at)))) {
        throw new Error('Inconsistent event timestamps.');
      }
      // Browser and server timezone databases may differ historically. Fail closed
      // on disagreement instead of displaying an unverified zone label.
      if (/^[+-]/.test(item.timezone_name)) throw new Error('Invalid event timezone.');
      const zone = new Intl.DateTimeFormat('en-GB-u-ca-iso8601-nu-latn', {
        timeZone: item.timezone_name, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
      const zoneMatches = (micros, localDate, localTime) => {
        const remainder = ((micros % 1000000n) + 1000000n) % 1000000n;
        const wholeSecond = new Date(Number((micros - remainder) / 1000n));
        const parts = Object.fromEntries(zone.formatToParts(wholeSecond).map(part => [part.type, part.value]));
        return [parts.year.padStart(4, '0'), parts.month, parts.day].join('-') === localDate &&
          [parts.hour, parts.minute, parts.second].join(':') === localTime.split('.')[0];
      };
      if (!zoneMatches(start, item.local_date, item.local_start_time) ||
          (item.end_at !== null && !zoneMatches(instantEpoch(item.end_at), item.local_end_date, item.local_end_time))) {
        throw new Error('Event timezone data does not match this browser.');
      }
      return Object.fromEntries(eventFields.map(key => [key, item[key]]));
    }
    function clearEventDetail() {
      if (eventDetailTrigger && eventDetailTrigger.isConnected) eventDetailTrigger.disabled = false;
      eventDetailTrigger = null;
      eventDetailGeneration += 1;
      byId('event-detail').setAttribute('aria-busy', 'false');
      byId('event-detail').textContent = '';
      byId('event-detail-state').textContent = '';
    }
    function clearEvents() {
      clearEventDetail(); eventsGeneration += 1; nextEventCursor = null;
      byId('list-events').disabled = false;
      byId('owned-events').setAttribute('aria-busy', 'false');
      byId('owned-events').replaceChildren(); byId('events-state').textContent = '';
      byId('next-events').disabled = true;
    }
    async function loadEventDetail(identifier, trigger) {
      clearEventDetail();
      const generation = eventDetailGeneration;
      eventDetailTrigger = trigger;
      trigger.disabled = true;
      byId('event-detail').setAttribute('aria-busy', 'true');
      byId('event-detail-state').textContent = 'Loading event…';
      try {
        const detail = await call('/v1/candidate/schedule-event?event_id=' + encodeURIComponent(identifier), {},
          'Event detail request failed.');
        if (generation !== eventDetailGeneration) return;
        if (!detail || detail.schema_version !== 1 || !detail.event || detail.event.event_id !== identifier) throw new Error('Invalid event detail.');
        byId('event-detail').textContent = JSON.stringify(eventSnapshot(detail.event), null, 2);
        byId('event-detail-state').textContent = 'Read-only event detail.';
      } catch (error) {
        if (generation === eventDetailGeneration) {
          clearEventDetail(); byId('event-detail-state').textContent = 'Select the event again to retry.';
        }
        throw error;
      } finally {
        if (generation === eventDetailGeneration) {
          if (eventDetailTrigger && eventDetailTrigger.isConnected) eventDetailTrigger.disabled = false;
          eventDetailTrigger = null;
          byId('event-detail').setAttribute('aria-busy', 'false');
        }
      }
    }
    async function loadEvents(after = null) {
      clearEvents();
      const generation = eventsGeneration;
      const selectedStatus = byId('event-status').value;
      byId('list-events').disabled = true;
      byId('owned-events').setAttribute('aria-busy', 'true');
      byId('events-state').textContent = 'Loading events…';
      try {
        const page = await call('/v1/candidate/schedule-events?limit=25' +
          (selectedStatus === '' ? '' : '&status=' + encodeURIComponent(selectedStatus)) +
          (after === null ? '' : '&after=' + encodeURIComponent(after)), {},
          'Schedule events request failed.');
        if (generation !== eventsGeneration) return;
        if (!page || page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || eventId(page.next_after))) throw new Error('Invalid event page.');
        const items = page.items.map(eventSnapshot);
        if (!items.every(item => selectedStatus === '' || item.status === selectedStatus)) throw new Error('Invalid event status filter.');
        if (!items.every((item, index) => item.event_id > (index ? items[index - 1].event_id : (after || ''))) ||
            (page.next_after !== null && (items.length !== 25 || page.next_after !== items.at(-1).event_id))) throw new Error('Invalid event cursor.');
        for (const [index, item] of items.entries()) {
          const row = document.createElement('li');
          row.textContent = item.employer_name + ' — ' + item.event_type + ' — ' + item.status + ' — ' +
            item.local_date + ' ' + item.local_start_time + ' ' + item.utc_offset + ' (' + item.timezone_name + ')';
          const button = document.createElement('button');
          button.type = 'button'; button.textContent = 'View event';
          button.setAttribute('aria-label', 'View event ' + (index + 1) + ': ' + item.event_id);
          button.addEventListener('click', () => {
            if (generation === eventsGeneration) guarded(() => loadEventDetail(item.event_id, button));
          });
          row.append(button); byId('owned-events').append(row);
        }
        nextEventCursor = page.next_after; byId('next-events').disabled = nextEventCursor === null;
        byId('events-state').textContent = pageSummary(
          items.length, 'event', 'events', nextEventCursor !== null, 'Read-only snapshots.');
      } catch (error) {
        if (generation === eventsGeneration) {
          clearEvents(); nextEventCursor = after; byId('next-events').disabled = after === null;
          byId('events-state').textContent = 'Load or retry the event page.';
        }
        throw error;
      } finally {
        if (generation === eventsGeneration) {
          byId('list-events').disabled = false;
          byId('owned-events').setAttribute('aria-busy', 'false');
        }
      }
    }
    byId('list-events').addEventListener('click', () => guarded(() => loadEvents()));
    byId('event-status').addEventListener('change', clearEvents);
    byId('next-events').addEventListener('click', () => guarded(() => {
      if (nextEventCursor !== null) return loadEvents(nextEventCursor);
    }));
    byId('token').addEventListener('input', clearEvents);
    const clearOverview = () => { byId('run-overview').textContent = 'Refresh status to load a snapshot.'; };
    const show = (value) => { clearOverview(); byId('result').textContent = JSON.stringify(value, null, 2); };
    const runPath = (suffix) => '/v1/candidate/runs/' + encodeURIComponent(byId('run-id').value) + suffix;
    let applicationsGeneration = 0;
    let nextApplicationCursor = null;
    let applicationDetailGeneration = 0;
    let applicationDetailTrigger = null;
    let selectedApplicationId = null;
    let applicationHistoryGeneration = 0;
    function clearApplicationHistory() {
      applicationHistoryGeneration += 1;
      byId('application-history').setAttribute('aria-busy', 'false');
      byId('application-history').textContent = '';
      byId('application-history-state').textContent = '';
    }
    function clearApplicationDetail() {
      if (applicationDetailTrigger && applicationDetailTrigger.isConnected) applicationDetailTrigger.disabled = false;
      applicationDetailTrigger = null;
      clearApplicationHistory();
      selectedApplicationId = null;
      byId('load-application-history').disabled = true;
      applicationDetailGeneration += 1;
      byId('application-detail').setAttribute('aria-busy', 'false');
      byId('application-detail').textContent = '';
      byId('application-detail-state').textContent = '';
    }
    async function loadApplicationDetail(applicationId, trigger) {
      clearApplicationDetail();
      const generation = applicationDetailGeneration;
      applicationDetailTrigger = trigger;
      trigger.disabled = true;
      byId('application-detail').setAttribute('aria-busy', 'true');
      byId('application-detail-state').textContent = 'Loading application…';
      try {
        const detail = await call('/v1/candidate/application?application_id=' + encodeURIComponent(applicationId), {},
          'Application detail request failed.');
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
      } finally {
        if (generation === applicationDetailGeneration) {
          if (applicationDetailTrigger && applicationDetailTrigger.isConnected) applicationDetailTrigger.disabled = false;
          applicationDetailTrigger = null;
          byId('application-detail').setAttribute('aria-busy', 'false');
        }
      }
    }
    async function loadApplicationHistory() {
      const applicationId = selectedApplicationId;
      if (applicationId === null) return;
      clearApplicationHistory();
      const generation = applicationHistoryGeneration;
      byId('load-application-history').disabled = true;
      byId('application-history').setAttribute('aria-busy', 'true');
      byId('application-history-state').textContent = 'Loading history…';
      try {
        const history = await call('/v1/candidate/application/history?application_id=' + encodeURIComponent(applicationId), {},
          'Application history request failed.');
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
          byId('load-application-history').disabled = false;
          byId('application-history-state').textContent = 'Load history again to retry.';
        }
        throw error;
      } finally {
        if (generation === applicationHistoryGeneration) {
          byId('load-application-history').disabled = false;
          byId('application-history').setAttribute('aria-busy', 'false');
        }
      }
    }
    byId('load-application-history').addEventListener('click', () => guarded(loadApplicationHistory));
    function clearApplications() {
      clearApplicationDetail();
      applicationsGeneration += 1;
      nextApplicationCursor = null;
      byId('list-applications').disabled = false;
      byId('owned-applications').setAttribute('aria-busy', 'false');
      byId('owned-applications').replaceChildren();
      byId('applications-state').textContent = '';
      byId('next-applications').disabled = true;
    }
    async function loadApplications(after = null) {
      clearApplications();
      const generation = applicationsGeneration;
      const selectedStatus = byId('application-status').value;
      byId('list-applications').disabled = true;
      byId('owned-applications').setAttribute('aria-busy', 'true');
      byId('applications-state').textContent = 'Loading…';
      try {
        const page = await call('/v1/candidate/applications?limit=25' +
          (selectedStatus === '' ? '' : '&status=' + encodeURIComponent(selectedStatus)) +
          (after === null ? '' : '&after=' + encodeURIComponent(after)), {},
          'Applications request failed.');
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
              statuses.includes(item.status) &&
              (selectedStatus === '' || item.status === selectedStatus) &&
              Number.isSafeInteger(item.state_version) && item.state_version >= 1)) {
          throw new Error('Invalid application page.');
        }
        for (const [index, item] of page.items.entries()) {
          const row = document.createElement('li');
          row.textContent = (item.job_title || item.job_id) + ' — ' + item.company_id +
            ' — ' + item.status + ' — version ' + item.state_version + ' — ' + item.application_id;
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'View application';
          // The ordinal remains unique even when an accessibility tree folds
          // distinct whitespace sequences inside otherwise-valid opaque IDs.
          button.setAttribute('aria-label', 'View application ' + (index + 1) + ': ' + item.application_id);
          button.addEventListener('click', () => {
            if (generation === applicationsGeneration) guarded(() => loadApplicationDetail(item.application_id, button));
          });
          row.append(button);
          byId('owned-applications').append(row);
        }
        nextApplicationCursor = page.next_after;
        byId('next-applications').disabled = nextApplicationCursor === null;
        byId('applications-state').textContent = pageSummary(
          page.items.length, 'application', 'applications', nextApplicationCursor !== null,
          'Read-only snapshots.');
      } catch (error) {
        if (generation === applicationsGeneration) {
          clearApplications();
          nextApplicationCursor = after;
          byId('next-applications').disabled = after === null;
          byId('applications-state').textContent = 'Load or retry the application page.';
        }
        throw error;
      } finally {
        if (generation === applicationsGeneration) {
          byId('list-applications').disabled = false;
          byId('owned-applications').setAttribute('aria-busy', 'false');
        }
      }
    }
    byId('list-applications').addEventListener('click', () => guarded(() => loadApplications()));
    byId('next-applications').addEventListener('click', () => guarded(() => {
      if (nextApplicationCursor !== null) return loadApplications(nextApplicationCursor);
    }));
    byId('application-status').addEventListener('change', clearApplications);
    byId('token').addEventListener('input', clearApplications);
    function clearRunList() {
      listGeneration += 1;
      nextRunCursor = null;
      byId('list-runs').disabled = false;
      byId('owned-runs').setAttribute('aria-busy', 'false');
      byId('owned-runs').replaceChildren();
      byId('list-state').textContent = '';
      byId('next-runs').disabled = true;
    }
    async function loadRunList(after = null) {
      clearRunList();
      const generation = listGeneration;
      const selectedPhase = byId('run-phase').value;
      byId('list-runs').disabled = true;
      byId('owned-runs').setAttribute('aria-busy', 'true');
      byId('list-state').textContent = 'Loading…';
      try {
        const page = await call('/v1/candidate/run-statuses?limit=25' +
          (selectedPhase === '' ? '' : '&phase=' + encodeURIComponent(selectedPhase)) +
          (after === null ? '' : '&after=' + encodeURIComponent(after)), {},
          'Run statuses request failed.');
        if (generation !== listGeneration) return;
        const validId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
        const phases = ['search', 'decision', 'resume', 'application_prepare', 'application_approval',
          'application_submit', 'application_reconciliation', 'message_prepare', 'message_approval',
          'message_send', 'message_reconciliation', 'track', 'interview_coordination',
          'interview_approval', 'interview_accept', 'interview_reconciliation', 'complete', 'failed'];
        if (page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || validId(page.next_after)) ||
            !page.items.every(item => item && validId(item.run_id) && phases.includes(item.phase) &&
              (selectedPhase === '' || item.phase === selectedPhase))) {
          throw new Error('Invalid run page.');
        }
        if (!page.items.every((item, index) => item.run_id >
              (index ? page.items[index - 1].run_id : (after || ''))) ||
            (page.next_after !== null && (page.items.length !== 25 ||
              page.next_after !== page.items.at(-1).run_id))) {
          throw new Error('Invalid run cursor.');
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
        byId('list-state').textContent = pageSummary(
          page.items.length, 'run', 'runs', nextRunCursor !== null,
          page.items.length ? 'Select a run to inspect.' : '');
      } catch (error) {
        if (generation === listGeneration) {
          clearRunList();
          nextRunCursor = after;
          byId('next-runs').disabled = after === null;
          byId('list-state').textContent = after === null ? 'Load my runs to retry.' : 'Next page failed. Retry Next page.';
        }
        throw error;
      } finally {
        if (generation === listGeneration) {
          byId('list-runs').disabled = false;
          byId('owned-runs').setAttribute('aria-busy', 'false');
        }
      }
    }
    byId('list-runs').addEventListener('click', () => guarded(() => loadRunList()));
    byId('next-runs').addEventListener('click', () => guarded(() => {
      if (nextRunCursor !== null) return loadRunList(nextRunCursor);
    }));
    byId('run-phase').addEventListener('change', clearRunList);
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
    async function call(path, options = {}, safeError = null) {
      clearOverview();
      const renderRaw = safeError === null;
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
          const error = new Error(renderRaw ? (payload.message || payload.error || 'Request failed.') : safeError);
          error.status = response.status;
          error.code = payload.error;
          throw error;
        }
        return payload;
      } catch (error) {
        if (!renderRaw) error = new Error(safeError);
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
    byId('clear-session').addEventListener('click', () => {
      for (const id of ['token', 'profile', 'keyword', 'location', 'run-id', 'start-run-id', 'run-phase', 'application-status', 'event-status']) byId(id).value = '';
      clearEvents(); clearApplications(); clearRunList(); invalidateInputContext();
      byId('result').textContent = 'Session cleared from this tab. Enter credentials again to continue.';
    });
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
        const loadedPrompt = await call('/v1/candidate/runs/' + encodeURIComponent(promptRunId) + '/approval', {},
          'Approval prompt request failed.');
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
