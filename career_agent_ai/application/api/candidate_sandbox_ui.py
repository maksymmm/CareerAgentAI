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
    .attestation input { width: auto; }
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
    <a href="#interview-intents-title">Pending interview actions</a>
  </nav>
  <main id="dashboard-content" tabindex="-1">
  <label>Bearer token <input id="token" type="password" autocomplete="off" required></label>
  <button id="clear-session" type="button">Clear this tab's session</button>
  <p class="muted">Clears credentials, profile and displayed data locally. It does not revoke a token or cancel work already accepted by the server.</p>
  <fieldset id="start-panel" tabindex="-1" aria-busy="false">
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
  <fieldset id="reconciliation-panel" tabindex="-1" aria-busy="false">
    <legend>Application submission reconciliation</legend>
    <p id="reconciliation-state" role="status">Refresh a run in application reconciliation before recording independently verified provider evidence.</p>
    <label>Provider submission ID
      <input id="provider-submission-id" autocomplete="off" maxlength="1000" disabled>
    </label>
    <label class="attestation"><input id="application-reconciliation-verified" type="checkbox" disabled> I independently verified this provider outcome for the displayed run version.</label>
    <div class="actions">
      <button id="confirm-submitted" type="button" disabled>Confirm provider submission</button>
      <button id="confirm-not-submitted" type="button" disabled>Confirm no provider submission</button>
    </div>
    <p class="muted">Use only independently verified provider evidence. This action never performs a blind retry.</p>
  </fieldset>
  <fieldset id="message-reconciliation-panel" tabindex="-1" aria-busy="false">
    <legend>Message delivery reconciliation</legend>
    <p id="message-reconciliation-state" role="status">Refresh a run in message reconciliation before recording the independently verified delivery outcome.</p>
    <label class="attestation"><input id="message-reconciliation-verified" type="checkbox" disabled> I independently verified this delivery outcome for the displayed run version.</label>
    <div class="actions">
      <button id="confirm-delivered" type="button" disabled>Confirm message delivered</button>
      <button id="confirm-not-delivered" type="button" disabled>Confirm no message delivery</button>
    </div>
    <p class="muted">Verify the provider outcome outside this sandbox. This action records evidence and never performs a blind retry.</p>
  </fieldset>
  <fieldset id="interview-reconciliation-panel" tabindex="-1" aria-busy="false">
    <legend>Interview calendar reconciliation</legend>
    <p id="interview-reconciliation-state" role="status">Refresh a run in interview reconciliation before recording independently verified calendar evidence.</p>
    <label>Provider event ID
      <input id="provider-event-id" autocomplete="off" maxlength="1000" disabled>
    </label>
    <label class="attestation"><input id="interview-reconciliation-verified" type="checkbox" disabled> I independently verified this calendar outcome for the displayed run version.</label>
    <div class="actions">
      <button id="confirm-calendar-completed" type="button" disabled>Confirm calendar action</button>
      <button id="confirm-calendar-not-completed" type="button" disabled>Confirm no calendar action</button>
    </div>
    <p class="muted">Use only independently verified calendar-provider evidence. This action never modifies a real calendar or performs a blind retry.</p>
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
    <fieldset id="event-decline-panel" aria-busy="false">
      <legend>Candidate-approved interview decline</legend>
      <p id="event-decline-state" role="status">Select a proposed event and load its current detail before declining.</p>
      <label class="attestation"><input id="confirm-event-decline" type="checkbox" disabled> I explicitly confirm that I want to decline this displayed interview proposal in the sandbox.</label>
      <button id="decline-event" type="button" disabled>Decline selected event</button>
      <label class="attestation"><input id="event-decline-reconciliation-verified" type="checkbox" disabled> I checked the sandbox provider's durable outcome for this exact event version.</label>
      <div class="actions">
        <button id="confirm-event-decline-completed" type="button" disabled>Confirm decline completed</button>
        <button id="confirm-event-decline-not-completed" type="button" disabled>Confirm no decline effect</button>
      </div>
      <p class="muted">An ambiguous result blocks retries until sandbox evidence is reconciled. No real calendar is contacted or changed.</p>
    </fieldset>
    <p class="muted">Schedule snapshots are read-only; the separate decline control requires a fresh event version and explicit confirmation.</p>
  </section>
  <section aria-labelledby="interview-intents-title">
    <h2 id="interview-intents-title" tabindex="-1">Pending interview actions</h2>
    <button id="load-interview-intents" type="button">Find pending interview actions</button>
    <ul id="pending-interview-intents" aria-label="Pending interview actions" aria-busy="false"></ul>
    <p id="interview-intents-state" role="status">Load owner-scoped pending interview actions after signing in.</p>
    <button id="next-interview-intents" type="button" disabled>Next pending action page</button>
    <h3 id="interview-intent-detail-title">Selected interview action</h3>
    <pre id="interview-intent-detail" role="region" aria-labelledby="interview-intent-detail-title"></pre>
    <p class="muted">Only pending sandbox interview actions for this authenticated candidate are listed. Select one to load a fresh run status. Internal operation IDs and provider event IDs are never exposed.</p>
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
    let promptInFlight = false;
    let recoveryInFlight = false;
    let startInFlight = false;
    let statusInFlight = false;
    let reconciliationInFlight = false;
    let reconciliationAvailable = false;
    let reconciliationVersion = null;
    let messageReconciliationAvailable = false;
    let messageReconciliationVersion = null;
    let interviewReconciliationAvailable = false;
    let interviewReconciliationVersion = null;
    let eventDeclineInFlight = false;
    let eventDeclineAvailable = false;
    let eventDeclineVersion = null;
    let eventDeclineReconciliationAvailable = false;
    let eventDeclineReconciliationVersion = null;
    let selectedInterviewIntent = null;
    let interviewIntentsGeneration = 0;
    let nextInterviewIntentCursor = null;
    let interviewIntentsInFlight = false;
    let latestStartRequest = 0;
    let approvalPromptGeneration = 0;
    let latestResultRequest = 0;
    let inputContextGeneration = 0;
    let listGeneration = 0;
    let nextRunCursor = null;
    let selectedRunVersion = null;
    const byId = (id) => document.getElementById(id);
    const pageSummary = (count, singular, plural, hasMore, instruction = '') =>
      count + ' ' + (count === 1 ? singular : plural) + ' on this page. ' +
      (hasMore ? 'More available.' : 'End of list.') + (instruction ? ' ' + instruction : '');
    let eventsGeneration = 0;
    let eventDetailGeneration = 0;
    let eventDetailTrigger = null;
    let nextEventCursor = null;
    let selectedEventId = null;
    let selectedEventVersion = null;
    const eventId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
    const calendarDate = value => {
      if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
      const [year, month, day] = value.split('-').map(Number);
      const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
      const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
      return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
    };
    const localTime = value => typeof value === 'string' &&
      /^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{6})?$/.test(value);
    const utcOffset = value => typeof value === 'string' &&
      /^[+-](?:[01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(value);
    const timezoneName = value => {
      if (typeof value !== 'string' || !value || /^[+-]/.test(value)) return false;
      try { new Intl.DateTimeFormat('en-GB', {timeZone: value}); } catch (error) { return false; }
      return true;
    };
    const localEpoch = (day, clock, zoneOffset) => {
      const [year, month, dayOfMonth] = day.split('-').map(Number);
      const [wholeTime, fraction = '000000'] = clock.split('.');
      const utc = new Date(0);
      utc.setUTCFullYear(year, month - 1, dayOfMonth);
      utc.setUTCHours(...wholeTime.split(':').map(Number), 0);
      const [hours, minutes, seconds = 0] = zoneOffset.slice(1).split(':').map(Number);
      const offsetSeconds = (hours * 3600 + minutes * 60 + seconds) * (zoneOffset[0] === '-' ? -1 : 1);
      return BigInt(utc.getTime()) * 1000n + BigInt(fraction) - BigInt(offsetSeconds) * 1000000n;
    };
    const timezoneMatches = (zoneName, micros, day, clock) => {
      const zone = new Intl.DateTimeFormat('en-GB-u-ca-iso8601-nu-latn', {
        timeZone: zoneName, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
      const remainder = ((micros % 1000000n) + 1000000n) % 1000000n;
      const wholeSecond = new Date(Number((micros - remainder) / 1000n));
      const parts = Object.fromEntries(zone.formatToParts(wholeSecond).map(part => [part.type, part.value]));
      return [parts.year.padStart(4, '0'), parts.month, parts.day].join('-') === day &&
        [parts.hour, parts.minute, parts.second].join(':') === clock.split('.')[0];
    };
    const eventFields = ['event_id', 'employer_name', 'event_type', 'location', 'status', 'state_version',
      'start_at', 'end_at', 'local_date', 'local_start_time', 'local_end_date', 'local_end_time',
      'timezone_name', 'utc_offset', 'end_utc_offset'];
    function eventSnapshot(item) {
      const text = (value, max) => typeof value === 'string' && value.length > 0 && Array.from(value).length <= max;
      const instant = value => {
        if (!text(value, 40)) return false;
        const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d{6})?)([+-]\d{2}:\d{2}(?::\d{2})?)$/.exec(value);
        return !!match && match[0] === value && calendarDate(match[1]) &&
          localTime(match[2]) && utcOffset(match[3]);
      };
      const instantEpoch = value => {
        const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d{6})?)([+-].+)$/.exec(value);
        return localEpoch(match[1], match[2], match[3]);
      };
      if (!item || !eventId(item.event_id) || !text(item.employer_name, 500) || !text(item.location, 2000) ||
          !['interview', 'trial_day', 'phone_call', 'video_call', 'other'].includes(item.event_type) ||
          !['proposed', 'accepted', 'declined', 'reschedule_requested', 'cancelled'].includes(item.status) ||
          !Number.isSafeInteger(item.state_version) || item.state_version < 1 || !instant(item.start_at) ||
          !calendarDate(item.local_date) || !localTime(item.local_start_time) ||
          !text(item.timezone_name, 200) || !utcOffset(item.utc_offset) ||
          !(item.end_at === null ? [item.local_end_date, item.local_end_time, item.end_utc_offset].every(value => value === null) :
            instant(item.end_at) &&
            calendarDate(item.local_end_date) && localTime(item.local_end_time) && utcOffset(item.end_utc_offset))) {
        throw new Error('Invalid event snapshot.');
      }
      const start = instantEpoch(item.start_at);
      if (localEpoch(item.local_date, item.local_start_time, item.utc_offset) !== start ||
          (item.end_at !== null && (instantEpoch(item.end_at) <= start ||
            localEpoch(item.local_end_date, item.local_end_time, item.end_utc_offset) !== instantEpoch(item.end_at)))) {
        throw new Error('Inconsistent event timestamps.');
      }
      // Browser and server timezone databases may differ historically. Fail closed
      // on disagreement instead of displaying an unverified zone label.
      if (/^[+-]/.test(item.timezone_name)) throw new Error('Invalid event timezone.');
      if (!timezoneMatches(item.timezone_name, start, item.local_date, item.local_start_time) ||
          (item.end_at !== null && !timezoneMatches(item.timezone_name, instantEpoch(item.end_at),
            item.local_end_date, item.local_end_time))) {
        throw new Error('Event timezone data does not match this browser.');
      }
      return Object.fromEntries(eventFields.map(key => [key, item[key]]));
    }
    let eventVersionFloorId = null;
    let eventVersionFloor = null;
    function invalidateEventDecline(message =
      'Select a proposed event and load its current detail before declining.') {
      eventDeclineAvailable = false;
      eventDeclineVersion = null;
      eventDeclineReconciliationAvailable = false;
      eventDeclineReconciliationVersion = null;
      byId('confirm-event-decline').checked = false;
      byId('confirm-event-decline').disabled = true;
      byId('decline-event').disabled = true;
      byId('event-decline-reconciliation-verified').checked = false;
      byId('event-decline-reconciliation-verified').disabled = true;
      byId('confirm-event-decline-completed').disabled = true;
      byId('confirm-event-decline-not-completed').disabled = true;
      byId('event-decline-state').textContent = message;
    }
    function enableEventDecline(version) {
      eventDeclineAvailable = true;
      eventDeclineVersion = version;
      eventDeclineReconciliationAvailable = false;
      eventDeclineReconciliationVersion = null;
      byId('event-decline-state').textContent =
        'Review the selected event, then explicitly confirm this sandbox decline.';
      byId('confirm-event-decline').checked = false;
      byId('confirm-event-decline').disabled = false;
      byId('decline-event').disabled = true;
      byId('event-decline-reconciliation-verified').checked = false;
      byId('event-decline-reconciliation-verified').disabled = true;
      byId('confirm-event-decline-completed').disabled = true;
      byId('confirm-event-decline-not-completed').disabled = true;
    }
    function enableEventDeclineReconciliation(version) {
      eventDeclineAvailable = false;
      eventDeclineVersion = null;
      eventDeclineReconciliationAvailable = true;
      eventDeclineReconciliationVersion = version;
      byId('event-decline-state').textContent =
        'The provider response was ambiguous. Verify the durable sandbox outcome before recording it.';
      byId('confirm-event-decline').checked = false;
      byId('confirm-event-decline').disabled = true;
      byId('decline-event').disabled = true;
      byId('event-decline-reconciliation-verified').checked = false;
      byId('event-decline-reconciliation-verified').disabled = false;
      byId('confirm-event-decline-completed').disabled = true;
      byId('confirm-event-decline-not-completed').disabled = true;
    }
    byId('confirm-event-decline').addEventListener('change', () => {
      byId('decline-event').disabled =
        !eventDeclineAvailable || !byId('confirm-event-decline').checked || eventDeclineInFlight;
    });
    byId('event-decline-reconciliation-verified').addEventListener('change', () => {
      const enabled = eventDeclineReconciliationAvailable &&
        byId('event-decline-reconciliation-verified').checked && !eventDeclineInFlight;
      byId('confirm-event-decline-completed').disabled = !enabled;
      byId('confirm-event-decline-not-completed').disabled = !enabled;
    });
    function clearEventDetail() {
      if (eventDetailTrigger && eventDetailTrigger.isConnected) eventDetailTrigger.disabled = false;
      eventDetailTrigger = null;
      eventDetailGeneration += 1;
      selectedEventId = null;
      selectedEventVersion = null;
      invalidateEventDecline();
      byId('event-detail').setAttribute('aria-busy', 'false');
      byId('event-detail').textContent = '';
      byId('event-detail-state').textContent = '';
    }
    function clearEvents() {
      clearEventDetail(); eventsGeneration += 1; nextEventCursor = null;
      selectedEventId = null; selectedEventVersion = null;
      eventVersionFloorId = null; eventVersionFloor = null;
      byId('list-events').disabled = false;
      byId('owned-events').setAttribute('aria-busy', 'false');
      byId('owned-events').replaceChildren(); byId('events-state').textContent = '';
      byId('next-events').disabled = true;
    }
    async function loadEventDetail(identifier, minimumVersion, trigger) {
      const versionFloor = eventVersionFloorId === identifier && eventVersionFloor !== null
        ? Math.max(minimumVersion, eventVersionFloor) : minimumVersion;
      clearEventDetail();
      const generation = eventDetailGeneration;
      eventDetailTrigger = trigger;
      trigger.disabled = true;
      byId('event-detail').setAttribute('aria-busy', 'true');
      byId('event-detail-state').textContent = 'Loading event…';
      try {
        const detail = await call('/v1/candidate/schedule-event?event_id=' + encodeURIComponent(identifier), {},
          'Event detail request failed.', true);
        if (generation !== eventDetailGeneration) return;
        if (!detail || detail.schema_version !== 1 || !detail.event || detail.event.event_id !== identifier) throw new Error('Invalid event detail.');
        const snapshot = eventSnapshot(detail.event);
        if (snapshot.state_version < versionFloor) throw new Error('Stale event detail.');
        selectedEventId = identifier;
        selectedEventVersion = snapshot.state_version;
        eventVersionFloorId = identifier;
        eventVersionFloor = snapshot.state_version;
        byId('event-detail').textContent = JSON.stringify(snapshot, null, 2);
        byId('event-detail-state').textContent = 'Read-only event detail.';
        if (['proposed', 'reschedule_requested'].includes(snapshot.status)) {
          enableEventDecline(snapshot.state_version);
        } else {
          invalidateEventDecline('Only proposed or reschedule-requested events can be declined.');
        }
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
          'Schedule events request failed.', true);
        if (generation !== eventsGeneration) return;
        if (!page || page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || eventId(page.next_after))) throw new Error('Invalid event page.');
        const items = page.items.map(eventSnapshot);
        if (!items.every(item => selectedStatus === '' || item.status === selectedStatus)) throw new Error('Invalid event status filter.');
        if (!items.every((item, index) => item.event_id > (index ? items[index - 1].event_id : (after || ''))) ||
            (page.next_after !== null && page.next_after !== items.at(-1).event_id)) throw new Error('Invalid event cursor.');
        for (const [index, item] of items.entries()) {
          const row = document.createElement('li');
          row.textContent = item.employer_name + ' — ' + item.event_type + ' — ' + item.status + ' — ' +
            item.local_date + ' ' + item.local_start_time + ' ' + item.utc_offset + ' (' + item.timezone_name + ')';
          const button = document.createElement('button');
          button.type = 'button'; button.textContent = 'View event';
          button.setAttribute('aria-label', 'View event ' + (index + 1) + ': ' + item.event_id);
          button.addEventListener('click', () => {
            if (generation === eventsGeneration) guarded(
              () => loadEventDetail(item.event_id, item.state_version, button), true);
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
    byId('list-events').addEventListener('click', () => guarded(() => loadEvents(), true));
    byId('event-status').addEventListener('change', clearEvents);
    byId('next-events').addEventListener('click', () => guarded(() => {
      if (nextEventCursor !== null) return loadEvents(nextEventCursor);
    }, true));
    byId('token').addEventListener('input', clearEvents);
    const clearOverview = () => { byId('run-overview').textContent = 'Refresh status to load a snapshot.'; };
    const show = (value, preserveOverview = false) => {
      if (!preserveOverview) clearOverview();
      byId('result').textContent = JSON.stringify(value, null, 2);
    };
    const runPath = (suffix) => '/v1/candidate/runs/' + encodeURIComponent(byId('run-id').value) + suffix;
    let applicationsGeneration = 0;
    let nextApplicationCursor = null;
    let applicationDetailGeneration = 0;
    let applicationDetailTrigger = null;
    let selectedApplicationId = null;
    let selectedApplicationVersion = null;
    let applicationVersionFloorId = null;
    let applicationVersionFloor = null;
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
      selectedApplicationVersion = null;
      byId('load-application-history').disabled = true;
      applicationDetailGeneration += 1;
      byId('application-detail').setAttribute('aria-busy', 'false');
      byId('application-detail').textContent = '';
      byId('application-detail-state').textContent = '';
    }
    async function loadApplicationDetail(applicationId, minimumVersion, trigger) {
      const versionFloor = applicationVersionFloorId === applicationId &&
        applicationVersionFloor !== null
        ? Math.max(minimumVersion, applicationVersionFloor) : minimumVersion;
      clearApplicationDetail();
      const generation = applicationDetailGeneration;
      applicationDetailTrigger = trigger;
      trigger.disabled = true;
      byId('application-detail').setAttribute('aria-busy', 'true');
      byId('application-detail-state').textContent = 'Loading application…';
      try {
        const detail = await call('/v1/candidate/application?application_id=' + encodeURIComponent(applicationId), {},
          'Application detail request failed.', true);
        if (generation !== applicationDetailGeneration) return;
        const item = detail && detail.application;
        if (detail.schema_version !== 1 || !item || item.application_id !== applicationId ||
            typeof item.job_id !== 'string' || !item.job_id || Array.from(item.job_id).length > 200 ||
            typeof item.job_title !== 'string' || Array.from(item.job_title).length > 500 ||
            typeof item.company_id !== 'string' || Array.from(item.company_id).length > 200 ||
            !['saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'].includes(item.status) ||
            !Number.isSafeInteger(item.state_version) || item.state_version < 1 ||
            item.state_version < versionFloor ||
            !['created_at', 'updated_at'].every(key => typeof item[key] === 'string' &&
              item[key].length <= 64 && Number.isFinite(Date.parse(item[key])))) {
          throw new Error('Invalid application detail.');
        }
        const snapshot = {};
        for (const key of ['application_id', 'job_id', 'job_title', 'company_id', 'status', 'state_version', 'created_at', 'updated_at']) snapshot[key] = item[key];
        byId('application-detail').textContent = JSON.stringify(snapshot, null, 2);
        byId('application-detail-state').textContent = 'Read-only application detail.';
        selectedApplicationId = applicationId;
        selectedApplicationVersion = item.state_version;
        applicationVersionFloorId = applicationId;
        applicationVersionFloor = item.state_version;
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
          'Application history request failed.', true);
        if (generation !== applicationHistoryGeneration) return;
        const statuses = ['saved', 'applied', 'interview', 'offer', 'rejected', 'withdrawn'];
        const transitions = {saved: ['applied', 'withdrawn'], applied: ['interview', 'rejected', 'withdrawn'],
          interview: ['offer', 'rejected', 'withdrawn'], offer: ['withdrawn'], rejected: [], withdrawn: []};
        if (!history || history.schema_version !== 1 || history.application_id !== applicationId ||
            !Number.isSafeInteger(history.state_version) || history.state_version < 1 ||
            selectedApplicationVersion === null || history.state_version < selectedApplicationVersion ||
            !Array.isArray(history.items) || history.items.length > 4 ||
            history.items.length !== history.state_version - 1 || !history.items.every((item, index) =>
              item && item.sequence === index && statuses.includes(item.from_status) && statuses.includes(item.to_status) &&
              transitions[item.from_status].includes(item.to_status) &&
              typeof item.occurred_at === 'string' && item.occurred_at.length <= 64 && Number.isFinite(Date.parse(item.occurred_at)) &&
              (index === 0 || (item.from_status === history.items[index - 1].to_status &&
                Date.parse(item.occurred_at) >= Date.parse(history.items[index - 1].occurred_at))))) {
          throw new Error('Invalid application history.');
        }
        selectedApplicationVersion = history.state_version;
        applicationVersionFloorId = applicationId;
        applicationVersionFloor = history.state_version;
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
    byId('load-application-history').addEventListener(
      'click', () => guarded(loadApplicationHistory, true));
    function clearApplications() {
      clearApplicationDetail();
      applicationVersionFloorId = null;
      applicationVersionFloor = null;
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
          'Applications request failed.', true);
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
        const applicationIds = page.items.map(item => item.application_id);
        // SQLite compares UTF-8 text by Unicode scalar value. JavaScript's
        // relational operators compare UTF-16 code units, which reverses some
        // BMP/supplementary pairs (for example U+E000 and U+1F600).
        const compareIdentifiers = (left, right) => {
          const leftScalars = Array.from(left, character => character.codePointAt(0));
          const rightScalars = Array.from(right, character => character.codePointAt(0));
          for (let index = 0; index < Math.min(leftScalars.length, rightScalars.length); index += 1) {
            if (leftScalars[index] !== rightScalars[index]) return leftScalars[index] - rightScalars[index];
          }
          return leftScalars.length - rightScalars.length;
        };
        const cursorFloor = after === null || applicationIds.length === 0 ? null :
          compareIdentifiers(applicationIds[0], after);
        if (new Set(applicationIds).size !== applicationIds.length ||
            applicationIds.some((identifier, index) =>
              index > 0 && compareIdentifiers(identifier, applicationIds[index - 1]) <= 0) ||
            (cursorFloor !== null && (cursorFloor < 0 ||
              (page.next_after !== null && cursorFloor === 0))) ||
            (after !== null && page.next_after !== null && compareIdentifiers(page.next_after, after) <= 0) ||
            (page.next_after !== null && page.next_after !== page.items.at(-1).application_id)) {
          throw new Error('Invalid application cursor.');
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
            if (generation === applicationsGeneration) guarded(() =>
              loadApplicationDetail(item.application_id, item.state_version, button), true);
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
    byId('list-applications').addEventListener(
      'click', () => guarded(() => loadApplications(), true));
    byId('next-applications').addEventListener('click', () => guarded(() => {
      if (nextApplicationCursor !== null) return loadApplications(nextApplicationCursor);
    }, true));
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
    const runPhases = ['search', 'decision', 'resume', 'application_prepare', 'application_approval',
      'application_submit', 'application_reconciliation', 'message_prepare', 'message_approval',
      'message_send', 'message_reconciliation', 'track', 'interview_coordination',
      'interview_approval', 'interview_accept', 'interview_reconciliation', 'complete', 'failed'];
    const validRunFlags = item =>
      item.terminal === ['complete', 'failed'].includes(item.phase) &&
      (!item.terminal || !item.waiting_for_human);
    const approvalDetailFields = {
      approve_application: ['job_id', 'job_title', 'company', 'company_id',
        'candidate_snapshot_sha256', 'application_id', 'application_version',
        'application_status', 'artifact_content', 'artifact_sha256'],
      approve_message: ['message_id', 'sender', 'recipient', 'subject', 'body'],
      approve_interview: ['event_id', 'employer', 'event_type', 'location', 'local_date',
        'local_start_time', 'local_end_date', 'local_end_time', 'timezone', 'utc_offset',
        'end_utc_offset', 'status'],
    };
    const approvalPhases = {
      approve_application: 'application_approval',
      approve_message: 'message_approval',
      approve_interview: 'interview_approval',
    };
    const hasExactFields = (value, fields) => value && typeof value === 'object' &&
      !Array.isArray(value) && Object.keys(value).length === fields.length &&
      fields.every(field => Object.hasOwn(value, field));
    const approvalTextLimits = {
      approve_application: {job_id: 200, job_title: 500, company: 200, company_id: 200,
        application_id: 200, application_status: 20, artifact_content: 100000},
      approve_message: {message_id: 200, sender: 200, recipient: 200, subject: 500, body: 100000},
      approve_interview: {event_id: 200, employer: 500, event_type: 50, location: 2000,
        local_date: 50, local_start_time: 50, local_end_date: 50, local_end_time: 50,
        timezone: 100, utc_offset: 20, end_utc_offset: 20, status: 50},
    };
    const validApprovalText = (value, maximum) => {
      if (typeof value !== 'string' || Array.from(value).length > maximum ||
          /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/.test(value)) return false;
      try { encodeURIComponent(value); } catch (error) { return false; }
      return true;
    };
    const communicationIdentifier = value => typeof value === 'string' &&
      /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
    const normalizedMessageText = (value, maximum, header = false) =>
      validApprovalText(value, maximum) && value.length > 0 &&
      !/^\p{White_Space}|\p{White_Space}$/u.test(value) &&
      (!header || !/[\u0000-\u001f\u007f-\u009f]/.test(value)) &&
      (header || !value.includes('\r'));
    const normalizedApplicationText = (value, maximum) =>
      validApprovalText(value, maximum) && value.length > 0 &&
      !/^\p{White_Space}|\p{White_Space}$/u.test(value) && !value.includes('\r');
    const sha256Hex = async value => Array.from(new Uint8Array(
      await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))))
      .map(byte => byte.toString(16).padStart(2, '0')).join('');
    const canonicalJson = value => {
      if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
      if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
        .map(key => JSON.stringify(key) + ':' + canonicalJson(value[key])).join(',') + '}';
      return JSON.stringify(value);
    };
    const approvalFingerprint = (kind, title, details) => sha256Hex(canonicalJson({
      details, kind, title,
    }));
    const validApprovalDetails = async (kind, value) => {
      const fields = approvalDetailFields[kind];
      if (!fields || !hasExactFields(value, fields)) return false;
      if (kind === 'approve_application') {
        return normalizedApplicationText(value.job_id, 200) &&
          normalizedApplicationText(value.job_title, 500) &&
          normalizedApplicationText(value.company, 200) &&
          (value.company_id === null || normalizedApplicationText(value.company_id, 200)) &&
          typeof value.candidate_snapshot_sha256 === 'string' &&
          /^[0-9a-f]{64}$/.test(value.candidate_snapshot_sha256) &&
          normalizedApplicationText(value.application_id, 200) &&
          value.application_status === 'saved' &&
          Number.isSafeInteger(value.application_version) && value.application_version >= 1 &&
          normalizedApplicationText(value.artifact_content, 100000) &&
          typeof value.artifact_sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.artifact_sha256) &&
          await sha256Hex(value.artifact_content) === value.artifact_sha256;
      }
      if (kind === 'approve_message') {
        return communicationIdentifier(value.message_id) &&
          communicationIdentifier(value.sender) && communicationIdentifier(value.recipient) &&
          normalizedMessageText(value.subject, approvalTextLimits[kind].subject, true) &&
          normalizedMessageText(value.body, approvalTextLimits[kind].body);
      }
      if (kind === 'approve_interview') {
        const nullableFields = ['local_end_date', 'local_end_time', 'end_utc_offset'];
        const validScalars = fields.every(field => validApprovalText(value[field], approvalTextLimits[kind][field]) ||
          (nullableFields.includes(field) && value[field] === null));
        const endValues = nullableFields.map(field => value[field]);
        if (!validScalars || !eventId(value.event_id) ||
            !['interview', 'trial_day', 'phone_call', 'video_call', 'other'].includes(value.event_type) ||
            !['proposed', 'reschedule_requested'].includes(value.status) ||
            !calendarDate(value.local_date) || !localTime(value.local_start_time) ||
            !timezoneName(value.timezone) || !utcOffset(value.utc_offset) ||
            !(endValues.every(item => item === null) ||
              (calendarDate(value.local_end_date) && localTime(value.local_end_time) &&
                utcOffset(value.end_utc_offset)))) return false;
        const start = localEpoch(value.local_date, value.local_start_time, value.utc_offset);
        if (!timezoneMatches(value.timezone, start, value.local_date, value.local_start_time)) return false;
        if (endValues.every(item => item === null)) return true;
        const end = localEpoch(value.local_end_date, value.local_end_time, value.end_utc_offset);
        return end > start && timezoneMatches(value.timezone, end, value.local_end_date, value.local_end_time);
      }
      return fields.every(field => validApprovalText(value[field], approvalTextLimits[kind][field]));
    };
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
          'Run statuses request failed.', true);
        if (generation !== listGeneration) return;
        const validId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,199}$/.test(value);
        if (page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || validId(page.next_after)) ||
            !page.items.every(item => item && validId(item.run_id) && runPhases.includes(item.phase) &&
              Number.isSafeInteger(item.state_version) && item.state_version >= 0 &&
              typeof item.waiting_for_human === 'boolean' && typeof item.terminal === 'boolean' &&
              (selectedPhase === '' || item.phase === selectedPhase))) {
          throw new Error('Invalid run page.');
        }
        if (!page.items.every((item, index) => item.run_id >
              (index ? page.items[index - 1].run_id : (after || ''))) ||
            (page.next_after !== null && page.next_after !== page.items.at(-1).run_id)) {
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
            selectRun(item.run_id, item.state_version);
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
    byId('list-runs').addEventListener('click', () => guarded(() => loadRunList(), true));
    byId('next-runs').addEventListener('click', () => guarded(() => {
      if (nextRunCursor !== null) return loadRunList(nextRunCursor);
    }, true));
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
      byId('prompt').disabled = startInFlight || statusInFlight || decisionInFlight || recoveryInFlight || promptInFlight;
    }
    function setReconciliationAttestation(id, enabled) {
      const checkbox = byId(id); checkbox.checked = false; checkbox.disabled = !enabled; }
    function invalidateApplicationReconciliation(message =
      'Refresh a run in application reconciliation before recording independently verified provider evidence.') {
      reconciliationAvailable = false;
      reconciliationVersion = null;
      byId('reconciliation-state').textContent = message;
      byId('provider-submission-id').value = '';
      byId('provider-submission-id').disabled = true;
      setReconciliationAttestation('application-reconciliation-verified', false);
      byId('confirm-submitted').disabled = true; byId('confirm-not-submitted').disabled = true;
    }
    function enableApplicationReconciliation(version) {
      if (reconciliationVersion !== version) byId('provider-submission-id').value = '';
      reconciliationAvailable = true;
      reconciliationVersion = version;
      byId('reconciliation-state').textContent =
        'Verify the provider outcome outside this sandbox, then record exactly one result for the displayed version.';
      byId('provider-submission-id').disabled = false;
      setReconciliationAttestation('application-reconciliation-verified', true);
      byId('confirm-submitted').disabled = true;
      byId('confirm-not-submitted').disabled = true;
    }
    function invalidateMessageReconciliation(message =
      'Refresh a run in message reconciliation before recording the independently verified delivery outcome.') {
      messageReconciliationAvailable = false;
      messageReconciliationVersion = null;
      byId('message-reconciliation-state').textContent = message;
      setReconciliationAttestation('message-reconciliation-verified', false);
      byId('confirm-delivered').disabled = true; byId('confirm-not-delivered').disabled = true;
    }
    function enableMessageReconciliation(version) {
      messageReconciliationAvailable = true;
      messageReconciliationVersion = version;
      byId('message-reconciliation-state').textContent =
        'Verify the provider outcome outside this sandbox, then record exactly one result for the displayed version.';
      setReconciliationAttestation('message-reconciliation-verified', true);
      byId('confirm-delivered').disabled = true;
      byId('confirm-not-delivered').disabled = true;
    }
    function invalidateInterviewReconciliation(message =
      'Refresh a run in interview reconciliation before recording independently verified calendar evidence.') {
      interviewReconciliationAvailable = false;
      interviewReconciliationVersion = null;
      byId('interview-reconciliation-state').textContent = message;
      byId('provider-event-id').value = '';
      byId('provider-event-id').disabled = true;
      setReconciliationAttestation('interview-reconciliation-verified', false);
      byId('confirm-calendar-completed').disabled = true; byId('confirm-calendar-not-completed').disabled = true;
    }
    function enableInterviewReconciliation(version) {
      if (interviewReconciliationVersion !== version) byId('provider-event-id').value = '';
      interviewReconciliationAvailable = true;
      interviewReconciliationVersion = version;
      byId('interview-reconciliation-state').textContent =
        'Verify the calendar provider outcome outside this sandbox, then record one result for the displayed version.';
      byId('provider-event-id').disabled = false;
      setReconciliationAttestation('interview-reconciliation-verified', true);
      byId('confirm-calendar-completed').disabled = true;
      byId('confirm-calendar-not-completed').disabled = true;
    }
    function bindReconciliationAttestation(id, available, buttons) {
      byId(id).addEventListener('change', event => { const enabled = available() && event.currentTarget.checked;
        for (const button of buttons) byId(button).disabled = !enabled; });
    }
    bindReconciliationAttestation('application-reconciliation-verified', () => reconciliationAvailable, ['confirm-submitted', 'confirm-not-submitted']);
    bindReconciliationAttestation('message-reconciliation-verified', () => messageReconciliationAvailable, ['confirm-delivered', 'confirm-not-delivered']);
    bindReconciliationAttestation('interview-reconciliation-verified', () => interviewReconciliationAvailable, ['confirm-calendar-completed', 'confirm-calendar-not-completed']);
    function invalidateReconciliationForVersion(version) {
      if (reconciliationAvailable && reconciliationVersion !== version) {
        invalidateApplicationReconciliation(
          'Run version changed. Refresh the application reconciliation state.');
      }
      if (messageReconciliationAvailable && messageReconciliationVersion !== version) {
        invalidateMessageReconciliation(
          'Run version changed. Refresh the message reconciliation state.');
      }
      if (interviewReconciliationAvailable && interviewReconciliationVersion !== version) {
        invalidateInterviewReconciliation(
          'Run version changed. Refresh the interview reconciliation state.');
      }
    }
    function invalidateAllReconciliation(message) {
      invalidateApplicationReconciliation(message);
      invalidateMessageReconciliation(message);
      invalidateInterviewReconciliation(message);
    }
    function selectRun(runId, minimumVersion = null) {
      clearOverview();
      selectedRunVersion = minimumVersion;
      inputContextGeneration += 1;
      latestResultRequest += 1;
      byId('run-id').value = runId;
      invalidateApprovalPrompt();
      invalidateApplicationReconciliation();
      invalidateMessageReconciliation();
      invalidateInterviewReconciliation();
    }
    async function call(path, options = {}, safeError = null, preserveOverview = false) {
      if (!preserveOverview) clearOverview();
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
        if (!renderRaw) {
          const safe = new Error(safeError);
          safe.status = error.status;
          safe.code = error.code;
          error = safe;
        }
        error.resultRequest = resultRequest;
        throw error;
      }
    }
    const validRunIdentifier = value => typeof value === 'string' && value.length > 0 &&
      value.length <= 200 && /^[A-Za-z0-9][A-Za-z0-9._:@+-]*$/.test(value);
    function clearInterviewIntents() {
      interviewIntentsGeneration += 1;
      interviewIntentsInFlight = false;
      selectedInterviewIntent = null;
      nextInterviewIntentCursor = null;
      byId('pending-interview-intents').replaceChildren();
      byId('pending-interview-intents').setAttribute('aria-busy', 'false');
      byId('interview-intents-state').textContent =
        'Load owner-scoped pending interview actions after signing in.';
      byId('next-interview-intents').disabled = true;
      byId('load-interview-intents').disabled = false;
      byId('interview-intent-detail').textContent = '';
    }
    function validateInterviewIntent(item) {
      const fields = ['run_id', 'state_version', 'action_type', 'operation_status', 'event'];
      if (!item || typeof item !== 'object' || Array.isArray(item) ||
          Object.keys(item).length !== fields.length || !fields.every(key => Object.hasOwn(item, key)) ||
          !validRunIdentifier(item.run_id) || !Number.isSafeInteger(item.state_version) ||
          item.state_version < 1 || item.action_type !== 'calendar.accept' ||
          item.operation_status !== 'reconciliation_required') {
        throw new Error('Invalid pending interview intent.');
      }
      const event = eventSnapshot(item.event);
      if (!['proposed', 'reschedule_requested'].includes(event.status)) {
        throw new Error('Invalid pending interview event.');
      }
      return {run_id: item.run_id, state_version: item.state_version,
        action_type: item.action_type, operation_status: item.operation_status, event};
    }
    async function loadInterviewIntents(after = null) {
      if (interviewIntentsInFlight) throw new Error('Pending interview actions are already loading.');
      const generation = ++interviewIntentsGeneration;
      const cursor = after;
      if (cursor === null) {
        selectedInterviewIntent = null;
        byId('pending-interview-intents').replaceChildren();
        byId('interview-intent-detail').textContent = '';
        nextInterviewIntentCursor = null;
      }
      interviewIntentsInFlight = true;
      byId('load-interview-intents').disabled = true;
      byId('next-interview-intents').disabled = true;
      byId('pending-interview-intents').setAttribute('aria-busy', 'true');
      byId('interview-intents-state').textContent = 'Loading pending interview actions…';
      try {
        const page = await call('/v1/candidate/interview-intents?limit=25' +
          (cursor === null ? '' : '&after=' + encodeURIComponent(cursor)), {},
          'Pending interview actions request failed.', true);
        if (generation !== interviewIntentsGeneration) return;
        if (!page || typeof page !== 'object' || Array.isArray(page) ||
            Object.keys(page).length !== 3 ||
            !['schema_version', 'items', 'next_after'].every(key => Object.hasOwn(page, key)) ||
            page.schema_version !== 1 || !Array.isArray(page.items) || page.items.length > 25 ||
            !(page.next_after === null || validRunIdentifier(page.next_after))) {
          throw new Error('Invalid pending interview action page.');
        }
        const items = page.items.map(validateInterviewIntent);
        if (!items.every((item, index) =>
            item.run_id > (index ? items[index - 1].run_id : (cursor || ''))) ||
            (page.next_after !== null &&
              (page.next_after !== items.at(-1)?.run_id || items.length !== 25))) {
          throw new Error('Invalid pending interview action cursor.');
        }
        for (const item of items) {
          const row = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = item.event.employer_name + ' — ' + item.event.local_date +
            ' ' + item.event.local_start_time + ' ' + item.event.timezone_name +
            ' — run ' + item.run_id;
          button.addEventListener('click', () => selectInterviewIntent(item));
          row.appendChild(button);
          byId('pending-interview-intents').appendChild(row);
        }
        nextInterviewIntentCursor = page.next_after;
        byId('next-interview-intents').disabled = page.next_after === null;
        byId('interview-intents-state').textContent = pageSummary(
          items.length, 'pending interview action', 'pending interview actions',
          page.next_after !== null, items.length ? 'Select an action to refresh its run status.' :
          'No pending interview actions were found.');
      } catch (error) {
        if (generation === interviewIntentsGeneration) {
          byId('pending-interview-intents').replaceChildren();
          nextInterviewIntentCursor = null;
          byId('next-interview-intents').disabled = true;
          byId('interview-intents-state').textContent =
            'Pending interview actions could not be verified. Load them again.';
        }
        throw error;
      } finally {
        if (generation === interviewIntentsGeneration) {
          interviewIntentsInFlight = false;
          byId('load-interview-intents').disabled = false;
          byId('pending-interview-intents').setAttribute('aria-busy', 'false');
        }
      }
    }
    function selectInterviewIntent(intent) {
      if (startInFlight || statusInFlight || promptInFlight || recoveryInFlight ||
          decisionInFlight || reconciliationInFlight) {
        throw new Error('Wait for the current workflow request to finish.');
      }
      const selected = intent;
      byId('run-id').value = selected.run_id;
      invalidateInputContext();
      selectedInterviewIntent = selected;
      byId('interview-intent-detail').textContent = JSON.stringify({
        run_id: selected.run_id,
        state_version: selected.state_version,
        action_type: selected.action_type,
        operation_status: selected.operation_status,
        event: selected.event
      }, null, 2);
      byId('interview-intents-state').textContent =
        'Selected action. Loading a fresh owner-scoped run status before enabling reconciliation.';
      byId('status').click();
    }
    async function guarded(operation, preserveOverview = false) {
      try { await operation(); } catch (error) {
        if (error.resultRequest === undefined || error.resultRequest === latestResultRequest) {
          show({error: String(error.message || error)}, preserveOverview);
        }
      }
    }
    byId('start').addEventListener('click', () => guarded(async () => {
      if (startInFlight) throw new Error('A run start is already in progress.');
      if (statusInFlight || promptInFlight || recoveryInFlight || decisionInFlight || reconciliationInFlight) {
        throw new Error('Wait for the current workflow request to finish.');
      }
      startInFlight = true;
      byId('start').disabled = true;
      byId('start-panel').setAttribute('aria-busy', 'true');
      const requestNumber = ++latestStartRequest;
      const startContextGeneration = inputContextGeneration;
      const requestedRunId = byId('start-run-id').value;
      try {
        const payload = await call('/v1/candidate/runs', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({schema_version: 1, run_id: requestedRunId,
            keyword: byId('keyword').value, candidate_profile: byId('profile').value,
            location: byId('location').value}),
        }, 'Run start request failed.');
        if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
            Object.keys(payload).length !== 2 || !Object.hasOwn(payload, 'run_id') ||
            !Object.hasOwn(payload, 'phase') ||
            typeof payload.run_id !== 'string' || !/^scoped-v2:[0-9a-f]{128}$/.test(payload.run_id) ||
            !runPhases.includes(payload.phase)) {
          throw new Error('Invalid run start response.');
        }
        if (requestNumber === latestStartRequest &&
            startContextGeneration === inputContextGeneration) {
          show({status: 201, payload: {run_id: payload.run_id, phase: payload.phase}});
          selectRun(payload.run_id);
        }
      } finally {
        startInFlight = false;
        byId('start').disabled = false;
        byId('start-panel').setAttribute('aria-busy', 'false');
        byId('prompt').disabled = statusInFlight || decisionInFlight || recoveryInFlight || promptInFlight;
      }
    }));
    function invalidateInputContext() {
      clearOverview();
      clearInterviewIntents();
      selectedRunVersion = null;
      inputContextGeneration += 1;
      latestResultRequest += 1;
      latestStartRequest += 1;
      invalidateApprovalPrompt();
      invalidateApplicationReconciliation();
      invalidateMessageReconciliation();
      invalidateInterviewReconciliation();
      byId('result').textContent = 'Inputs changed. Reload the current state.';
    }
    byId('run-id').addEventListener('input', invalidateInputContext);
    byId('token').addEventListener('input', invalidateInputContext);
    for (const id of ['start-run-id', 'keyword', 'location', 'profile']) {
      byId(id).addEventListener('input', invalidateInputContext);
    }
    byId('clear-session').addEventListener('click', () => {
      for (const id of ['token', 'profile', 'keyword', 'location', 'run-id', 'start-run-id', 'run-phase', 'application-status', 'event-status']) byId(id).value = '';
      clearEvents(); clearApplications(); clearRunList(); invalidateInputContext();
      byId('result').textContent = 'Session cleared from this tab. Enter credentials again to continue.';
    });
    byId('load-interview-intents').addEventListener('click', () =>
      guarded(() => loadInterviewIntents(), true));
    byId('next-interview-intents').addEventListener('click', () => guarded(() => {
      if (nextInterviewIntentCursor !== null) return loadInterviewIntents(nextInterviewIntentCursor);
    }, true));
    byId('status').addEventListener('click', () => guarded(async () => {
      if (statusInFlight) throw new Error('A status refresh is already in progress.');
      if (startInFlight) throw new Error('Wait for the run start to finish.');
      if (promptInFlight || recoveryInFlight || decisionInFlight || reconciliationInFlight) throw new Error('Wait for the workflow action to finish.');
      statusInFlight = true;
      byId('status').disabled = true;
      byId('inspect-panel').setAttribute('aria-busy', 'true');
      clearOverview();
      const runId = byId('run-id').value;
      const statusContextGeneration = inputContextGeneration;
      try {
        const snapshot = await call(runPath('/status'), {}, 'Status request failed.');
        if (statusContextGeneration !== inputContextGeneration ||
            runId !== byId('run-id').value) return;
        const statusFields = ['schema_version', 'run_id', 'state_version', 'phase',
          'waiting_for_human', 'terminal'];
        if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot) ||
            Object.keys(snapshot).length !== statusFields.length ||
            !statusFields.every(field => Object.hasOwn(snapshot, field)) ||
            snapshot.schema_version !== 1 || snapshot.run_id !== runId ||
            !Number.isSafeInteger(snapshot.state_version) || snapshot.state_version < 0 ||
            (selectedRunVersion !== null && snapshot.state_version < selectedRunVersion) ||
            !runPhases.includes(snapshot.phase) || typeof snapshot.waiting_for_human !== 'boolean' ||
            typeof snapshot.terminal !== 'boolean' || !validRunFlags(snapshot)) {
          throw new Error('Invalid status snapshot.');
        }
        selectedRunVersion = snapshot.state_version;
        const selectedIntent = selectedInterviewIntent;
        const selectedIntentMatches = selectedIntent === null || (
          selectedIntent.run_id === runId &&
          selectedIntent.state_version === snapshot.state_version &&
          snapshot.phase === 'interview_reconciliation' && snapshot.waiting_for_human);
        if (selectedIntent && !selectedIntentMatches) {
          selectedInterviewIntent = null;
          byId('interview-intent-detail').textContent = '';
          byId('interview-intents-state').textContent =
            'The selected intent changed after it was listed. Reload pending interview actions.';
        } else if (selectedIntent) {
          byId('interview-intent-detail').textContent = JSON.stringify({
            run_id: selectedIntent.run_id,
            state_version: selectedIntent.state_version,
            action_type: selectedIntent.action_type,
            operation_status: selectedIntent.operation_status,
            event: selectedIntent.event
          }, null, 2);
          byId('interview-intents-state').textContent =
            'Fresh run status matches the selected interview intent version.';
        }
        if (snapshot.phase === 'application_reconciliation' && snapshot.waiting_for_human) {
          enableApplicationReconciliation(snapshot.state_version);
        } else {
          invalidateApplicationReconciliation();
        }
        if (snapshot.phase === 'message_reconciliation' && snapshot.waiting_for_human) {
          enableMessageReconciliation(snapshot.state_version);
        } else {
          invalidateMessageReconciliation();
        }
        if (snapshot.phase === 'interview_reconciliation' && snapshot.waiting_for_human && selectedIntentMatches) {
          enableInterviewReconciliation(snapshot.state_version);
        } else {
          invalidateInterviewReconciliation(selectedIntent && !selectedIntentMatches
            ? 'Pending interview intent is stale. Reload it before reconciling.' : undefined);
        }
        if (approvalPrompt && (snapshot.state_version !== approvalPrompt.state_version ||
            approvalPhases[approvalPrompt.action_kind] !== snapshot.phase ||
            !snapshot.waiting_for_human)) {
          invalidateApprovalPrompt('Run state changed. Load the current approval prompt.');
        }
        show({status: 200, payload: Object.fromEntries(
          statusFields.map(field => [field, snapshot[field]])
        )});
        const state = snapshot.terminal ? 'Finished' : snapshot.waiting_for_human ? 'Your decision is needed' : 'In progress';
        byId('run-overview').textContent = state + ' — ' + snapshot.phase.replaceAll('_', ' ') +
          ' (version ' + snapshot.state_version + '). Run: ' + snapshot.run_id;
      } catch (error) {
        if (statusContextGeneration === inputContextGeneration &&
            runId === byId('run-id').value) {
          invalidateApprovalPrompt('Status could not be verified. Load the current approval prompt.');
          invalidateApplicationReconciliation('Status could not be verified. Refresh run status.');
          invalidateMessageReconciliation('Status could not be verified. Refresh run status.');
          invalidateInterviewReconciliation('Status could not be verified. Refresh run status.');
        }
        throw error;
      } finally {
        statusInFlight = false;
        byId('status').disabled = false;
        byId('inspect-panel').setAttribute('aria-busy', 'false');
        byId('prompt').disabled = startInFlight || decisionInFlight || recoveryInFlight || promptInFlight;
      }
    }));
    byId('continue').addEventListener('click', () => guarded(async () => {
      if (startInFlight) throw new Error('Wait for the run start to finish.');
      if (decisionInFlight) throw new Error('Wait for the current decision to finish.');
      if (promptInFlight) throw new Error('Wait for the approval prompt to finish loading.');
      if (statusInFlight) throw new Error('Wait for the status refresh to finish.');
      if (recoveryInFlight) throw new Error('Recovery is already running.');
      if (reconciliationInFlight) throw new Error('Wait for reconciliation to finish.');
      recoveryInFlight = true;
      byId('continue').disabled = true;
      byId('prompt').disabled = true;
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      invalidateAllReconciliation(
        'Recovery may change workflow state. Refresh run status before reconciliation.');
      try {
        const runId = byId('run-id').value;
        const requestNumber = latestResultRequest + 1;
        const payload = await call(runPath('/continue'), {method: 'POST'}, 'Recovery request failed.');
        if (requestNumber !== latestResultRequest) return;
        if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
            Object.keys(payload).length !== 2 || payload.run_id !== runId ||
            !Object.hasOwn(payload, 'phase') || !runPhases.includes(payload.phase)) {
          throw new Error('Invalid recovery response.');
        }
        if (requestNumber === latestResultRequest) {
          show({status: 200, payload: {run_id: payload.run_id, phase: payload.phase}});
        }
        invalidateApprovalPrompt('Recovery finished. Load the current approval prompt.');
      } catch (error) {
        if (error.code === 'recovery_unavailable') {
          byId('approve').disabled = !approvalPrompt;
          byId('decline').disabled = !approvalPrompt;
          error.message = 'Recovery is unavailable while a decision is pending. Use the current approval prompt.';
        } else if (error.code === 'approval_conflict') {
          invalidateApprovalPrompt('Run state changed during recovery. Refresh run status.');
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
      if (startInFlight) throw new Error('Wait for the run start to finish.');
      if (decisionInFlight) throw new Error('Wait for the current decision to finish.');
      if (recoveryInFlight) throw new Error('Wait for recovery to finish.');
      if (statusInFlight) throw new Error('Wait for the status refresh to finish.');
      if (promptInFlight) throw new Error('An approval prompt is already loading.');
      if (reconciliationInFlight) throw new Error('Wait for reconciliation to finish.');
      promptInFlight = true;
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
        const approvalDetailsValid = loadedPrompt && typeof loadedPrompt === 'object' &&
          !Array.isArray(loadedPrompt) && Object.hasOwn(loadedPrompt, 'action_kind') &&
          Object.hasOwn(loadedPrompt, 'details') &&
          await validApprovalDetails(loadedPrompt.action_kind, loadedPrompt.details);
        const fingerprintValid = approvalDetailsValid &&
          typeof loadedPrompt.title === 'string' &&
          typeof loadedPrompt.action_fingerprint === 'string' &&
          await approvalFingerprint(loadedPrompt.action_kind, loadedPrompt.title, loadedPrompt.details) ===
            loadedPrompt.action_fingerprint;
        if (promptGeneration !== approvalPromptGeneration || promptRunId !== byId('run-id').value) return;
        const fields = ['schema_version', 'run_id', 'state_version', 'action_kind', 'title',
          'details', 'action_fingerprint', 'allowed_decisions'];
        if (!loadedPrompt || typeof loadedPrompt !== 'object' || Array.isArray(loadedPrompt) ||
            Object.keys(loadedPrompt).length !== fields.length || !fields.every(key => Object.hasOwn(loadedPrompt, key)) ||
            loadedPrompt.schema_version !== 1 || loadedPrompt.run_id !== promptRunId ||
            !Number.isSafeInteger(loadedPrompt.state_version) || loadedPrompt.state_version < 0 ||
            (selectedRunVersion !== null && loadedPrompt.state_version < selectedRunVersion) ||
            !Object.hasOwn(approvalDetailFields, loadedPrompt.action_kind) ||
            !validApprovalText(loadedPrompt.title, 500) || !loadedPrompt.title.trim() ||
            !approvalDetailsValid || !fingerprintValid ||
            typeof loadedPrompt.action_fingerprint !== 'string' || !/^[0-9a-f]{64}$/.test(loadedPrompt.action_fingerprint) ||
            !Array.isArray(loadedPrompt.allowed_decisions) || loadedPrompt.allowed_decisions.length !== 2 ||
            !['approve', 'decline'].every(value => loadedPrompt.allowed_decisions.includes(value))) {
          throw new Error('Invalid approval prompt. Load it again.');
        }
        selectedRunVersion = loadedPrompt.state_version;
        invalidateReconciliationForVersion(selectedRunVersion);
        approvalPrompt = loadedPrompt;
        byId('action-title').textContent = loadedPrompt.title;
        byId('action-details').textContent = JSON.stringify(loadedPrompt.details, null, 2);
        byId('approve').disabled = false;
        byId('decline').disabled = false;
      } catch (error) {
        if (promptGeneration === approvalPromptGeneration) {
          byId('action-title').textContent = error.code === 'approval_unavailable'
            ? 'Approval is no longer available. Refresh run status.'
            : error.code === 'approval_conflict'
              ? 'Run state changed while loading approval. Refresh run status.'
              : 'Approval prompt unavailable. Load it again.';
        }
        throw error;
      } finally {
        promptInFlight = false;
        if (activePromptLoadGeneration === promptGeneration) {
          activePromptLoadGeneration = null;
        }
        byId('prompt').disabled = startInFlight || statusInFlight || decisionInFlight || recoveryInFlight;
      }
    }));
    async function decide(decision) {
      if (startInFlight) throw new Error('Wait for the run start to finish.');
      if (recoveryInFlight) throw new Error('Wait for recovery to finish.');
      if (promptInFlight) throw new Error('Wait for the approval prompt to finish loading.');
      if (statusInFlight) throw new Error('Wait for the status refresh to finish.');
      if (!approvalPrompt) throw new Error('Load the current prompt first.');
      if (decisionInFlight) throw new Error('A decision is already being submitted.');
      if (reconciliationInFlight) throw new Error('Wait for reconciliation to finish.');
      decisionInFlight = true;
      byId('approve').disabled = true;
      byId('decline').disabled = true;
      byId('prompt').disabled = true;
      byId('continue').disabled = true;
      const payload = {schema_version: 1, run_id: approvalPrompt.run_id,
        state_version: approvalPrompt.state_version,
        action_fingerprint: approvalPrompt.action_fingerprint, decision};
      const requestNumber = latestResultRequest + 1;
      let completionMessage = 'Decision result is uncertain. Load the current approval prompt.';
      invalidateAllReconciliation(
        'Decision may change workflow state. Refresh run status before reconciliation.');
      try {
        const result = await call('/v1/candidate/runs/' + encodeURIComponent(payload.run_id) + '/approval', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)}, 'Decision request failed.');
        if (requestNumber !== latestResultRequest) return;
        if (!result || typeof result !== 'object' || Array.isArray(result) ||
            Object.keys(result).length !== 2 || result.run_id !== payload.run_id ||
            !Object.hasOwn(result, 'phase') || !runPhases.includes(result.phase)) {
          throw new Error('Invalid decision response.');
        }
        if (requestNumber === latestResultRequest) {
          show({status: 200, payload: {run_id: result.run_id, phase: result.phase}});
        }
        completionMessage = 'Decision finished. Load the current prompt.';
      } catch (error) {
        if (error.code === 'approval_conflict') {
          completionMessage = 'Approval changed. Load the current approval prompt.';
        } else if (error.code === 'approval_unavailable') {
          completionMessage = 'Approval is no longer available. Refresh run status.';
        }
        throw error;
      } finally {
        decisionInFlight = false;
        invalidateApprovalPrompt(completionMessage);
        byId('continue').disabled = false;
      }
    }
    byId('approve').addEventListener('click', () => guarded(() => decide('approve')));
    byId('decline').addEventListener('click', () => guarded(() => decide('decline')));
    const validProviderSubmissionId = value => {
      if (typeof value !== 'string' || value.length < 1 || Array.from(value).length > 1000 ||
          value !== value.trim() || /[\u0000-\u001f\u007f]/.test(value)) return false;
      try { encodeURIComponent(value); } catch (error) { return false; }
      return true;
    };
    const reconciliationResultPhases = {
      application: new Set(['message_prepare', 'message_approval', 'message_send',
        'message_reconciliation', 'track', 'interview_coordination', 'interview_approval', 'interview_accept',
        'interview_reconciliation', 'complete', 'failed']),
      message: new Set(['track',
        'interview_coordination', 'interview_approval', 'interview_accept',
        'interview_reconciliation', 'complete', 'failed']),
      interview: new Set(['complete', 'failed']),
    };
    const validReconciliationResult = (result, runId, allowedPhases) =>
      result && typeof result === 'object' && !Array.isArray(result) &&
      Object.keys(result).length === 2 && result.run_id === runId &&
      Object.hasOwn(result, 'phase') && allowedPhases.has(result.phase);
    async function reconcileApplication(submitted) {
      if (!reconciliationAvailable || selectedRunVersion === null ||
          reconciliationVersion !== selectedRunVersion) {
        throw new Error('Refresh the application reconciliation state first.');
      }
      if (startInFlight || statusInFlight || promptInFlight || recoveryInFlight || decisionInFlight) {
        throw new Error('Wait for the current workflow request to finish.');
      }
      if (reconciliationInFlight) throw new Error('Reconciliation is already being submitted.');
      const runId = byId('run-id').value;
      const observedVersion = reconciliationVersion;
      const providerSubmissionId = byId('provider-submission-id').value;
      if (submitted && !validProviderSubmissionId(providerSubmissionId)) {
        throw new Error('A valid provider submission ID is required.');
      }
      reconciliationInFlight = true;
      byId('reconciliation-panel').setAttribute('aria-busy', 'true');
      invalidateApplicationReconciliation('Recording verified provider evidence…');
      try {
        const request = {schema_version: 1, state_version: observedVersion, submitted};
        if (submitted) request.provider_submission_id = providerSubmissionId;
        const result = await call('/v1/candidate/runs/' + encodeURIComponent(runId) +
          '/application-reconciliation', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(request)}, 'Application reconciliation request failed.');
        if (!validReconciliationResult(
            result, runId, reconciliationResultPhases.application)) {
          throw new Error('Invalid application reconciliation response.');
        }
        show({status: 200, payload: {run_id: result.run_id, phase: result.phase}});
        byId('provider-submission-id').value = '';
        invalidateApprovalPrompt('Reconciliation finished. Load the current approval prompt.');
        invalidateApplicationReconciliation('Reconciliation finished. Refresh run status.');
      } catch (error) {
        invalidateApplicationReconciliation(error.code === 'approval_conflict'
          ? 'Run state changed. Refresh run status before reconciling.'
          : 'Reconciliation result is uncertain. Refresh run status before retrying.');
        throw error;
      } finally {
        reconciliationInFlight = false;
        byId('reconciliation-panel').setAttribute('aria-busy', 'false');
      }
    }
    byId('confirm-submitted').addEventListener('click', () =>
      guarded(() => reconcileApplication(true)));
    byId('confirm-not-submitted').addEventListener('click', () =>
      guarded(() => reconcileApplication(false)));
    async function reconcileMessage(delivered) {
      if (!messageReconciliationAvailable || selectedRunVersion === null ||
          messageReconciliationVersion !== selectedRunVersion) {
        throw new Error('Refresh the message reconciliation state first.');
      }
      if (startInFlight || statusInFlight || promptInFlight || recoveryInFlight || decisionInFlight) {
        throw new Error('Wait for the current workflow request to finish.');
      }
      if (reconciliationInFlight) throw new Error('Reconciliation is already being submitted.');
      const runId = byId('run-id').value;
      const observedVersion = messageReconciliationVersion;
      reconciliationInFlight = true;
      byId('message-reconciliation-panel').setAttribute('aria-busy', 'true');
      invalidateMessageReconciliation('Recording verified provider outcome…');
      try {
        const result = await call('/v1/candidate/runs/' + encodeURIComponent(runId) +
          '/message-reconciliation', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({schema_version: 1, state_version: observedVersion, delivered})},
          'Message reconciliation request failed.');
        if (!validReconciliationResult(
            result, runId, reconciliationResultPhases.message)) {
          throw new Error('Invalid message reconciliation response.');
        }
        show({status: 200, payload: {run_id: result.run_id, phase: result.phase}});
        invalidateApprovalPrompt('Reconciliation finished. Load the current approval prompt.');
        invalidateMessageReconciliation('Reconciliation finished. Refresh run status.');
      } catch (error) {
        invalidateMessageReconciliation(error.code === 'approval_conflict'
          ? 'Run state changed. Refresh run status before reconciling.'
          : 'Reconciliation result is uncertain. Refresh run status before retrying.');
        throw error;
      } finally {
        reconciliationInFlight = false;
        byId('message-reconciliation-panel').setAttribute('aria-busy', 'false');
      }
    }
    byId('confirm-delivered').addEventListener('click', () =>
      guarded(() => reconcileMessage(true)));
    byId('confirm-not-delivered').addEventListener('click', () =>
      guarded(() => reconcileMessage(false)));
    async function reconcileInterview(completed) {
      if (!interviewReconciliationAvailable || selectedRunVersion === null ||
          interviewReconciliationVersion !== selectedRunVersion) {
        throw new Error('Refresh the interview reconciliation state first.');
      }
      if (startInFlight || statusInFlight || promptInFlight || recoveryInFlight || decisionInFlight) {
        throw new Error('Wait for the current workflow request to finish.');
      }
      if (reconciliationInFlight) throw new Error('Reconciliation is already being submitted.');
      const runId = byId('run-id').value;
      const observedVersion = interviewReconciliationVersion;
      const providerEventId = byId('provider-event-id').value;
      if (completed && !validProviderSubmissionId(providerEventId)) {
        throw new Error('A valid provider event ID is required.');
      }
      reconciliationInFlight = true;
      byId('interview-reconciliation-panel').setAttribute('aria-busy', 'true');
      invalidateInterviewReconciliation('Recording verified calendar-provider evidence…');
      try {
        const request = {schema_version: 1, state_version: observedVersion, completed};
        if (completed) request.provider_event_id = providerEventId;
        const result = await call('/v1/candidate/runs/' + encodeURIComponent(runId) +
          '/interview-reconciliation', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(request)}, 'Interview reconciliation request failed.');
        if (!validReconciliationResult(
            result, runId, reconciliationResultPhases.interview)) {
          throw new Error('Invalid interview reconciliation response.');
        }
        show({status: 200, payload: {run_id: result.run_id, phase: result.phase}});
        invalidateApprovalPrompt('Reconciliation finished. Load the current approval prompt.');
        selectedInterviewIntent = null;
        byId('interview-intent-detail').textContent = '';
        byId('interview-intents-state').textContent =
          'Interview action reconciled. Reload pending interview actions for the latest state.';
        invalidateInterviewReconciliation('Reconciliation finished. Refresh run status.');
      } catch (error) {
        selectedInterviewIntent = null;
        byId('interview-intent-detail').textContent = '';
        byId('interview-intents-state').textContent =
          'Interview action outcome is uncertain or stale. Reload pending interview actions.';
        invalidateInterviewReconciliation(error.code === 'approval_conflict'
          ? 'Run state changed. Refresh run status before reconciling.'
          : 'Reconciliation result is uncertain. Refresh run status before retrying.');
        throw error;
      } finally {
        reconciliationInFlight = false;
        byId('interview-reconciliation-panel').setAttribute('aria-busy', 'false');
      }
    }
    byId('confirm-calendar-completed').addEventListener('click', () =>
      guarded(() => reconcileInterview(true)));
    byId('confirm-calendar-not-completed').addEventListener('click', () =>
      guarded(() => reconcileInterview(false)));
    function validEventDeclineResult(result, eventIdentifier) {
      return result && typeof result === 'object' && !Array.isArray(result) &&
        Object.keys(result).length === 5 &&
        result.schema_version === 1 && result.event_id === eventIdentifier &&
        ['proposed', 'reschedule_requested', 'declined'].includes(result.status) &&
        Number.isSafeInteger(result.state_version) && result.state_version >= 1 &&
        ['confirmed', 'reconciliation_required', 'no_effect_confirmed'].includes(result.provider_status);
    }
    async function declineSelectedEvent() {
      if (!eventDeclineAvailable || selectedEventId === null ||
          selectedEventVersion === null || eventDeclineVersion !== selectedEventVersion) {
        throw new Error('Reload the selected event before declining.');
      }
      if (!byId('confirm-event-decline').checked) {
        throw new Error('Explicit candidate confirmation is required.');
      }
      if (eventDeclineInFlight || reconciliationInFlight) {
        throw new Error('Wait for the current candidate action to finish.');
      }
      const eventIdentifier = selectedEventId;
      const version = eventDeclineVersion;
      eventDeclineInFlight = true;
      byId('event-decline-panel').setAttribute('aria-busy', 'true');
      invalidateEventDecline('Submitting the explicitly confirmed sandbox decline…');
      try {
        const result = await call('/v1/candidate/schedule-event/decline', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({schema_version: 1, event_id: eventIdentifier,
            state_version: version, confirm_decline: true}),
        }, 'Schedule decline request failed.');
        if (!validEventDeclineResult(result, eventIdentifier)) {
          throw new Error('Invalid schedule decline response.');
        }
        if (result.provider_status === 'reconciliation_required') {
          if (result.state_version !== version ||
              !['proposed', 'reschedule_requested'].includes(result.status)) {
            throw new Error('Invalid ambiguous decline snapshot.');
          }
          enableEventDeclineReconciliation(version);
        } else if (result.provider_status === 'confirmed') {
          if (result.status !== 'declined' || result.state_version !== version + 1) {
            throw new Error('Invalid confirmed decline snapshot.');
          }
          clearEventDetail();
          byId('event-decline-state').textContent =
            'Sandbox provider confirmed the decline. Reload events for the latest snapshot.';
        } else if (result.provider_status === 'no_effect_confirmed') {
          if (!['proposed', 'reschedule_requested'].includes(result.status) ||
              result.state_version !== version) {
            throw new Error('Invalid no-effect decline snapshot.');
          }
          clearEventDetail();
          byId('event-decline-state').textContent =
            'Sandbox provider confirmed no decline effect. Reload the event and make a new explicit decision before retrying.';
        } else {
          throw new Error('Unexpected decline outcome.');
        }
        show({status: result.provider_status === 'confirmed' ? 200 : 202,
          payload: {event_id: eventIdentifier, status: result.status,
            state_version: result.state_version, provider_status: result.provider_status}});
      } catch (error) {
        invalidateEventDecline(error.code === 'approval_conflict'
          ? 'Event version changed. Reload the event before deciding again.'
          : 'Decline result is uncertain. Reload the event before taking another action.');
        throw error;
      } finally {
        eventDeclineInFlight = false;
        byId('event-decline-panel').setAttribute('aria-busy', 'false');
      }
    }
    async function reconcileSelectedEventDecline(completed) {
      if (!eventDeclineReconciliationAvailable || selectedEventId === null ||
          eventDeclineReconciliationVersion === null ||
          eventDeclineReconciliationVersion !== selectedEventVersion) {
        throw new Error('Reload the event and pending decline evidence before reconciling.');
      }
      if (!byId('event-decline-reconciliation-verified').checked) {
        throw new Error('Explicit evidence attestation is required.');
      }
      if (eventDeclineInFlight || reconciliationInFlight) {
        throw new Error('Wait for the current candidate action to finish.');
      }
      const eventIdentifier = selectedEventId;
      const version = eventDeclineReconciliationVersion;
      eventDeclineInFlight = true;
      byId('event-decline-panel').setAttribute('aria-busy', 'true');
      invalidateEventDecline('Reconciling durable sandbox provider evidence…');
      try {
        const result = await call('/v1/candidate/schedule-event/decline-reconciliation', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({schema_version: 1, event_id: eventIdentifier,
            state_version: version, completed, confirm_reconciliation: true}),
        }, 'Schedule decline reconciliation failed.');
        if (!validEventDeclineResult(result, eventIdentifier)) {
          throw new Error('Invalid schedule decline reconciliation response.');
        }
        if (completed) {
          if (result.provider_status !== 'confirmed' || result.status !== 'declined' ||
              result.state_version !== version + 1) {
            throw new Error('Provider evidence did not confirm the expected decline.');
          }
          clearEventDetail();
          byId('event-decline-state').textContent =
            'Sandbox provider evidence confirmed the decline. Reload events for the latest snapshot.';
        } else {
          if (result.provider_status !== 'no_effect_confirmed' ||
              !['proposed', 'reschedule_requested'].includes(result.status) ||
              result.state_version !== version) {
            throw new Error('Provider evidence did not confirm absence of the decline effect.');
          }
          clearEventDetail();
          byId('event-decline-state').textContent =
            'No decline effect was confirmed. Reload the event and make a new explicit decision before retrying.';
        }
        show({status: 200, payload: {event_id: eventIdentifier, status: result.status,
          state_version: result.state_version, provider_status: result.provider_status}});
      } catch (error) {
        invalidateEventDecline(error.code === 'approval_conflict'
          ? 'Provider evidence conflicts or the event changed. Reload the event.'
          : 'Reconciliation result is uncertain. Reload the event before retrying.');
        throw error;
      } finally {
        eventDeclineInFlight = false;
        byId('event-decline-panel').setAttribute('aria-busy', 'false');
      }
    }
    byId('decline-event').addEventListener('click', () =>
      guarded(() => declineSelectedEvent(), true));
    byId('confirm-event-decline-completed').addEventListener('click', () =>
      guarded(() => reconcileSelectedEventDecline(true), true));
    byId('confirm-event-decline-not-completed').addEventListener('click', () =>
      guarded(() => reconcileSelectedEventDecline(false), true));
  </script>
</body>
</html>
"""
