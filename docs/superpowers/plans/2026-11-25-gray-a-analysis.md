# GRAY A Structured Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the local analysis API so article input returns source-linked issues and perspectives in a stable JSON shape.

**Architecture:** Keep the existing stdlib HTTP server and Responses API client. Use a required web-search call to collect cited findings, then a JSON-Schema response call constrained to those findings. The server validates model source IDs and owns the final source list.

**Tech Stack:** Python 3 standard library, OpenAI Responses API, unittest.

**Spec:** `docs/superpowers/specs/2026-11-25-gray-a-analysis-design.md`

## Global Constraints

- Local demo bound to `127.0.0.1:8765`.
- No new Python dependency.
- Preserve `analyze(article, api_key, opener=urlopen)` compatibility.
- Keep legacy `{ "article": "..." }` requests working.
- Never label output as a truth verdict.

## Review Focus

- Article URL not cited by search → return `422 ARTICLE_UNAVAILABLE`.
- Unknown or missing source IDs → remove unsupported perspectives and never fabricate URLs.
- Missing/malformed structured output → return `502 ANALYSIS_FAILED`.
- API timeout → return `504 ANALYSIS_TIMEOUT`.
- Old request payload → continue to analyze successfully.

---

### Task 1: Source-grounded structured analysis

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

**Interfaces:**
- Consumes: existing `analyze(article, api_key, opener=urlopen)`.
- Produces: response object `{version,title,summary,issues,sources}` with server-owned `S1..Sn` identifiers.

- [ ] Add tests for two-call request shape and mapped valid source IDs. Expected: targeted tests fail because current `analyze` returns only `text`, `sources`, `citations`.
- [ ] Add tests for URL-only original source missing and model referencing unknown IDs. Expected: no fabricated result and unsupported perspective removed.
- [ ] Implement search extraction, URL match normalization (ignore fragment and `utm_*` only), strict structured JSON schema, safe ID mapping, and completed/refusal/error checks.
- [ ] Run `python -m unittest test_server.AnalysisTests`. Expected: all analysis tests pass.

### Task 2: Request validation and API error contract

**Files:**
- Modify: `server.py`
- Test: `test_server.py`

**Interfaces:**
- Consumes: Task 1 result and current `Handler.do_POST`.
- Produces: stable HTTP status and `{code,error}` for invalid input, unavailable article, API failures, and timeouts.

- [ ] Add handler tests for `url`, `text`, legacy `article`, invalid JSON/type/length, article unavailable, upstream failure, and timeout. Expected: tests fail because current handler only accepts `article` and returns generic errors.
- [ ] Implement validated input selection and status mapping while preserving localhost/Origin restrictions.
- [ ] Run `python -m unittest test_server`. Expected: full test suite passes.
- [ ] Review diff and verify no frontend files changed.

## Integration Verification

- [ ] Run full test suite `python -m unittest test_server` after the final edit.
- [ ] With an authorized local API key, submit three public Korean news articles and manually check that each displayed perspective has a relevant clickable source.
- [ ] If no API key or network is available, report live verification as pending. Do not mark API-backed verification complete based on mocked tests.
