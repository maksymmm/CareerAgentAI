from __future__ import annotations

import pytest


def test_candidate_workflow_mutation_discards_inflight_owner_collection_reads():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    from career_agent_ai.application.api.candidate_sandbox_ui import candidate_sandbox_html

    bootstrap = r"""
    window.__candidatePendingCollections = [];
    const candidateResponse = (payload, status = 200) => new Response(
      JSON.stringify(payload),
      {status, headers: {'Content-Type': 'application/json'}}
    );
    window.fetch = (input, options = {}) => {
      const url = typeof input === 'string' ? input : input.url;
      const collectionPaths = [
        '/v1/candidate/run-statuses',
        '/v1/candidate/applications',
        '/v1/candidate/schedule-events',
      ];
      if (collectionPaths.some(path => url.startsWith(path))) {
        return new Promise(resolve => window.__candidatePendingCollections.push({
          url, options, resolve,
        }));
      }
      if (url === '/v1/candidate/runs/run-1/continue') {
        return Promise.resolve(candidateResponse({run_id: 'run-1', phase: 'complete'}));
      }
      return Promise.resolve(candidateResponse({error: 'not_found'}, 404));
    };
    window.__releaseCandidateCollections = () => {
      const pending = window.__candidatePendingCollections.splice(0);
      for (const request of pending) {
        let payload;
        if (request.url.startsWith('/v1/candidate/run-statuses')) {
          payload = {schema_version: 1, items: [{
            run_id: 'owned-run-1', phase: 'complete', state_version: 1,
            waiting_for_human: false, terminal: true,
          }], next_after: null};
        } else if (request.url.startsWith('/v1/candidate/applications')) {
          payload = {schema_version: 1, items: [{
            application_id: 'application-1', job_id: 'job-1', job_title: 'Engineer',
            company_id: 'company-1', status: 'saved', state_version: 1,
          }], next_after: null};
        } else {
          payload = {schema_version: 1, items: [], next_after: null};
        }
        request.resolve(candidateResponse(payload));
      }
      return pending.length;
    };
    """

    html = candidate_sandbox_html().decode("utf-8")
    marker = "    'use strict';"
    assert html.count(marker) == 1
    html = html.replace(marker, marker + "\n" + bootstrap, 1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            page.locator("#token").fill("synthetic-owner-token")
            page.locator("#run-id").fill("run-1")

            # Hold all owner-scoped collection reads before they can render.
            page.locator("#list-runs").click()
            page.locator("#list-applications").click()
            page.locator("#list-events").click()
            page.wait_for_function(
                "() => window.__candidatePendingCollections.length === 3"
            )

            # A successful workflow mutation invalidates every collection and
            # its detail/history generations before the request is sent.
            page.locator("#continue").click()
            page.wait_for_function(
                "() => document.querySelector('#result').textContent.includes('complete')"
            )
            for identifier in (
                "owned-runs", "owned-applications", "owned-events",
            ):
                assert page.locator(f"#{identifier} li").count() == 0
            for identifier in (
                "list-state", "applications-state", "events-state",
                "application-detail", "application-history", "event-detail",
            ):
                assert page.locator(f"#{identifier}").text_content() == ""
            for identifier in (
                "next-runs", "next-applications", "next-events",
            ):
                assert page.locator(f"#{identifier}").is_disabled()

            # Deliver responses that were initiated against the pre-mutation
            # state. Generation checks must discard all of them.
            assert page.evaluate("window.__releaseCandidateCollections()") == 3
            page.wait_for_timeout(50)
            for identifier in (
                "owned-runs", "owned-applications", "owned-events",
            ):
                assert page.locator(f"#{identifier} li").count() == 0
            for identifier in (
                "list-state", "applications-state", "events-state",
                "application-detail", "application-history", "event-detail",
            ):
                assert page.locator(f"#{identifier}").text_content() == ""
            for identifier in (
                "next-runs", "next-applications", "next-events",
            ):
                assert page.locator(f"#{identifier}").is_disabled()
        finally:
            browser.close()
