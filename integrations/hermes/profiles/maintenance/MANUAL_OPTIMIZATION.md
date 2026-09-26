# Technical worker optimization, September 2026

## Eight-call trace from the preceding pass

This is an observable decision trace from tool requests/results, not a claim
about hidden reasoning. That run had 8 API calls, 7 successful logged tools and
one failed web attempt, 65.6s model latency, 80.05s Technical time, 36,498 final
input tokens and 140.28s overall time.

| Call | Model latency | Observable decision / tools | Needed? / replacement |
|---|---:|---|---|
| 1 | 6.3s | Read verified HD785-7 device AGENTS | Rules required; model round unnecessary. Dispatcher reads the complete file and supplies selected unique policies. |
| 2 | 3.6s | Load maintenance-two-stream-evidence with skill_view | Workflow required by generic skill guidance; repeated orchestration round unnecessary. Dispatcher reads skill, supplies Technical scope and native attestation. |
| 3 | 3.9s | Invoke indexed manual_evidence_probe for original question | Evidence required. Model-specific path resolution, index interpretation and indexed searches are deterministic. Packet also supplies complete bounded topic pages. |
| 4 | 5.9s | Probe --help and web_search; web backend failed | Help round mechanical and avoidable. Explicit stable commands are in prepared context. Required web search now shares retrieve batch. |
| 5 | 5.7s | Read PDF pages 247, 250, 251, 363, 371, 1165, 155 | Actual text required for supported values/conditions; packet and a single bounded gap read cover this need. The requested 18K page cap was not the actual bounded output cap. |
| 6 | 8.4s | Read 248, 249, 362, 364, 1164 | Continuation and ambiguity checks can be valid. Generic PDF heading/form/index grouping returns continuation candidates; necessary remaining gaps join finish batch. |
| 7 | 7.6s | Discover session/artifact location, render four pages and validate them | Image choice remains technical judgment. Session lookup and validation decisions are mechanical: approved renderer gets runtime identity, batch validates PNGs automatically. |
| 8 | 24.2s | Return Technical evidence summary | Necessary model synthesis; retained. |

The previous pass already used manual_sections at the first probe call. It did
not ignore the index, but still spent turns loading instructions, discovering
CLI features, reading page groups and deciding the next mechanical operation.
The original 14-call / 135.3s result preceded that pass. This pass traces the
requested latest eight calls and addresses remaining instruction/retrieval
round trips, rather than claiming an unobserved trace for all fourteen calls.

## Changes

- Same model/provider: gpt-6-sol / openai-codex.
- Maintenance-only native preparation; root paragraphs/question are not copied
  twice. Selected HD785-7 policy is 5,776 characters versus 18,132 in the full
  device source. The source is read fully before dependent retrieval.
- Parent goal drift is normalized to the original question. Incomplete reported
  codes are preserved without speculative code-specific searches.
- manual_sections remains the primary router. Generic chapter intent selects
  troubleshooting, testing and structure; sparse multipart indexes consolidate
  to their indexed parent. A missed index retains full-manual fallback.
- PDF titles are recognized from actual typography; continuation candidates
  stay within form/index bounds. Text and continuation limits are explicit.
- Keyword aliases use word boundaries: Persian negation no longer accidentally
  activates the slow/speed alias. No benchmark page/fault is hard-coded in
  retrieval or policy selection. Page numbers in tests are source assertions.
- PDF/web retrieval and read/render/extract follow-ups run as concurrent CLI
  batches. Image decoding/dimensions/bytes are validated in the render batch.
- Failed operations retain independent successful evidence and explicit errors.

## Read-only regressions

| Question | Model | Evidence checks |
|---|---|---|
| Steering wheel is heavy | HD785-7 | Complete H-12 topic, high-idle condition and 20.6 (+0.98/0) MPa retained. |
| Engine does not start | PC800-8R | All three S-2 branches retained: engine does not turn, turns without exhaust, smoke without starting; CA559 cross-reference retained. |
| Boom is slow / pressure test | PC800-8R | Complete normal/heavy-lift H-5 pages, P-mode, 2.9 MPa and engine-stopped preparation retained. |

The tests inspect real indexed local PDF text, exact sources, bounds, no fallback
and no duplicate pages. They are retrieval regressions, not field measurements
or separate provider-model quality evaluations. Separate preparation tests
verify Maintenance/Technical isolation, Fleet preservation, model validation,
question/root/device deduplication, safety/serial retention and receipt binding.
Batch tests verify real parallel execution barriers, independent failure
retention, runtime request scope and multiplex profile selection.

## Measurement protocol

Use the same local UTF-8 question file, machine, gpt-6-sol/openai-codex, fresh
parent/child sessions and user-confirmed connected VPN. Compare three runs per
stable version and report medians, preserving outliers. Source data and bot
messaging are excluded. The existing local harness joins the real two-child
batch inline because it has no Gateway adapter; it exercises the configured
Bale prompt/tools without sending a Bale message.

Current-day baseline uses the prior committed architecture with working native
web search. The preceding single eight-call run had a failed web backend and
its VPN state was not controlled throughout; these are different measurement
conditions. Report that single reference separately from today's matched
three-run baseline. Model latency is the sum of logged API request latency;
context is actual final API input tokens. Overall time starts at conversation
turn entry and excludes Python/agent startup. Harness wall time additionally
includes initialization. Parallel backend durations are not summed as wall
latency. Tool counts use captured transcripts because parallel completion logs
can omit a session tag; failed attempts are shown separately.

## Final measured comparison

| Metric | Current-day baseline, median of 3 | Optimized, median of 3 |
|---|---:|---:|
| Technical model API calls | 7 (runs: 12, 7, 7) | 3 (all three runs) |
| Successful model-issued tool calls | 8 | 2 |
| Tool attempts including errors | 9 | 2; no errors |
| Technical model latency sum | 59.9s | 39.9s |
| Total Technical latency | 111.13s | 54.95s |
| Initial API input context | 15,501 tokens | 18,424 tokens |
| Final API input context | 40,043 tokens | 29,202 tokens |
| Parent model latency sum | 47.0s | 44.5s |
| Parent model API calls | 3 | 3 (runs: 3, 3, 4) |
| Fleet total time | 31.72s | 24.87s |
| Fleet model API calls | 3 | 2 (runs: 4, 2, 2) |
| Overall conversation latency | 166.23s | 103.56s |

Fleet's contract after step 4 and its native tool surface are byte-for-byte
unchanged. Its measured variation is ordinary live-run variation, not a Fleet
optimization. Initial Technical context increases because applicable device
instructions are present before the first call; final context decreases 27.1%
and repeated instruction round trips disappear. Overall time decreases 37.7%
and Technical time decreases 50.6% against the matched baseline.

Against the preceding single eight-call reference: 8 -> 3 API calls,
65.6 -> 39.9s model time, 80.05 -> 54.95s Technical time,
36,498 -> 29,202 final tokens, and 140.28 -> 103.56s overall.
The original user-provided 14-call reference was 135.3s Technical,
111.4s model, ~56.6K final tokens and ~196.1s overall. Its conditions differ
from the matched current-day series; do not treat these as repeated medians.

| Optimized run | Technical APIs | Tools | Model time | Technical time | Final input | Overall |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 3 | 2 | 37.3s | 54.21s | 29,171 | 103.56s |
| 2 | 3 | 2 | 39.9s | 55.04s | 29,207 | 103.14s |
| 3 | 3 | 2 | 39.9s | 54.95s | 29,202 | 115.68s |

All three question-file hashes match the current-day baseline. The network
adapter status signature also matches the start of the series; the user
confirmed the VPN remained connected. This checks adapter state, not continuous
network quality. No failed native web operation occurred in the final series.

Each of the two model-issued tool calls contains two backend operations, so
there are four backend operations per normal run. A required additional page
read would make five; counts are not hidden by reporting only CLI invocations.
Retrieve batch wall times were 8.855, 7.175 and 7.018s; finish batch wall times
5.167, 4.800 and 5.076s. Their operation times overlap. Final synthesis API calls
alone took 26.0, 24.3 and 29.2s. Model time remains about 73% of Technical time.

## Intermediate iterations and rejected approaches

Prompt-only batching: 3 runs, median 6 API calls, 68.30s Technical and 39,855
final tokens. Explicit instructions still did not reliably batch skill/rules
or web/render operations. Minimal preparation alone had one measured trial:
4 APIs, 67.38s and 30,790 tokens, but redundant instruction reads remained.
Trusted receipt-bound native prompt attestation removed those reads; its first
trial still used 7 APIs, 77.11s and 32,065 tokens because the model split web,
rendering and text lookups into separate responses.

CLI batching without normalized child scope had two trials: 3 APIs/80.37s and
8 APIs/177.13s. The second Parent goal expanded incidental fleet codes/shift
symptoms into new diagnostic goals, leading to full-PDF sweeps despite complete
indexed evidence. These trials are retained in private measurements and are
not included as the final version. The approved hook now binds Technical's
goal to the original question and preserves incomplete codes as uncertainty.
There is no fault/page-specific shortcut or artificial model iteration cap.

## Evidence quality and remaining limit

Retrieved evidence in all final runs retained the correct 7001-UP Shop Manual, complete H-11 cause
and value table, pressure-test conditions, exact PDF/form references and
3-4 validated genuine page images. Returned pressures were checked against
real PDF text and visually against rendered H-11/test pages. The different
A10001-UP web preview was explicitly separated and did not supply the project
manual's values. Incomplete recorded codes were not converted into guessed
failure diagnoses; no unverified pins or field test results were invented.
All 21 tests passed, including the three different cross-model retrieval
regressions. These checks establish retrieval/evidence preservation, not a
claim that every future generated answer or every model's manual layout is
fully evaluated.

The stable architecture meets <=4 model calls, 40-60s Technical latency and a
materially smaller final context in all three normal-path runs. Extra evidence
lookups remain available when necessary. The model/provider, especially final
synthesis, is now the largest measured bottleneck. A controlled A/B with the
user-proposed Gemini 3.8 Flash is a useful next experiment if that exact route
is available; no model change or availability claim is made in this pass.

## Deployment and health

The plugin is copied and enabled only under profiles/maintenance. Native
Gateway control-socket reload returned reloaded=true with the Maintenance
hook loaded, without restarting the default Gateway. New prepared Technical
sessions receive the native policy section; existing session prompts retain
Hermes's normal caching lifecycle. Runtime/canonical plugin and SOUL copies
are checked for equality. Config check passed; doctor reported all checks
passed, with optional integration/dependency notices. Gateway is running and Bale is connected without an error after activation. No user-visible benchmark message, production data
write, default-profile edit, model change or push was performed.
