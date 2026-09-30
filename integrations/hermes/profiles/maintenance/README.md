# Maintenance Hermes profile

This directory is the canonical source for the `maintenance` profile's intent and persona. The active Hermes profile is at `C:\Users\win-10\AppData\Local\hermes\profiles\maintenance`.

- Purpose: troubleshooting, repair, and technical support for the project's heavy equipment.
- Workspace and `terminal.cwd`: `E:\KomatsoAI`.
- Model/provider at initial setup: `gpt-6-sol` / `openai-codex`.
- The root `E:\KomatsoAI\AGENTS.md` and each device's `AGENTS.md` remain the main project context and define the equipment workflows.
- Built-in Memory and User Profile are disabled (`memory.memory_enabled: false`, `memory.user_profile_enabled: false`).

## Rebuild

Start from a healthy Hermes `default` profile with `hermes profile create maintenance --clone-from default --description "Specialized Komatsu maintenance, troubleshooting and technical support agent."`. This clones the basic configuration and skills without messaging channels. Copy this directory's `SOUL.md` to the runtime profile and use `hermes -p maintenance` for subsequent checks. Do not change the sticky default profile.

Verify the workspace, model/provider, disabled memory settings, and messaging isolation in the new runtime profile. A cloned `.env` may retain project-specific variables such as `KOMATSO_TELEGRAM_EXISTING_USERS`; remove any inherited messaging allowlists from `maintenance`. Bale/Telegram bot credentials, real user IDs, and routing settings must remain absent until separately configured. The current runtime also omits an inactive legacy `custom_providers` entry inherited from `default`; if it reappears after cloning, remove it from `maintenance` only so `doctor` passes without changing the active model.

Keep runtime `.env`, `auth.json`, API keys, OAuth credentials, bot tokens, `state.db`, sessions, logs, memories, and cron state outside Git. The runtime `config.yaml` is not canonicalized here; the stable maintenance-specific requirements are documented above. Validate with `hermes profile show maintenance`, `hermes -p maintenance config check`, and `hermes -p maintenance doctor`.

## Bale tool access under multiplex

The active Maintenance Bale allowlist is `platform_toolsets.bale:
[search, komatso_maintenance, no_mcp]`. The effective model schemas are
`web_search`, `maintenance_partbook_lookup`, and
`maintenance_manual_evidence`. The `no_mcp` sentinel prevents globally
enabled MCP servers from widening the messaging surface. Keep terminal, file,
code execution, browser automation, connections, skills management, and
delegation out of Bale. The CLI toolset remains separate.

The Maintenance plugin runs its own bounded Python and file operations behind
the dedicated tools. `known_plugin_toolsets.telegram` includes
`komatso_maintenance` while `platform_toolsets.telegram` omits it, so
Telegram receives only `web_search`. Hermes treats a plugin listed as known
but absent from the platform allowlist as disabled.

Run `tools/security/verify_messaging_tools.py` with the installed Hermes
Python for both messaging platforms after a config edit or Hermes upgrade.
It checks the resolved model schemas and rejects any tool outside the expected
set. Runtime config and credentials remain outside Git.

## Technical routing and Parent fast path

`SOUL.md` determines source intent before the generic technical route.
Part Number, Parts Book identification and Figure/Item requests start with
`maintenance_partbook_lookup`; mixed requests use it first and retain Shop
Manual evidence for diagnosis. Simple VERIFIED local identification needs no
manual/web lookup. HD785-7 B1, HD785-5, WA600-6 2010 and HD465-7R are indexed production books;
misses and coverage gaps permit fallback and never prove nonexistence.
The Part tool reads full device rules, then calls the existing
`tools/fleet/partbook_lookup.py --verify` once. The manual dispatch guard stops
Part-only requests before any Shop Manual/web batch. Both tools use the
existing `komatso_maintenance` toolset and direct schemas.

### WA600-6 Part Book index (September 2026)

`tools/partbook_index.py --book WA600-6-2010` builds the text-layer
`WA600-6/WA600-6-partbook-2010.pdf` into the existing SQLite/FTS5 index.
The builder copies the current DB, replaces only that book's records, checks
other-book counts, then atomically installs the result. HD785-7 B1 remains
indexed and B2 remains unindexed. The shared extractor uses a small
`wa600_vector` layout profile for printed group labels, column offsets,
table Figure IDs and vector exploded views. No PDF scan occurs at lookup time.

The verified source has 795 PDF pages: 674 parts-list pages, 650 pages with a
vector view and 24 continuation tables without a new view. The index contains
621 Figures and 12,721 source rows (12,306 orderable rows, 4,890 distinct
normalized PNs); 930 rows carry review flags and one row is rejected. Front
matter and the numerical index are outside the row index. Source serial notes
remain raw; the AA engine group is also stored as engine applicability, while
other groups use machine applicability. A continuation row uses the Figure's
verified view page. Direct Part-only VERIFIED lookup automatically renders one
unique Figure page, or two distinct VERIFIED alternatives; optional technical
Part enrichment remains non-rendering. Run
`python -m unittest tools.fleet.test_partbook_wa600 tools.test_maintenance_wa600_part`
after rebuilding. Older benchmark sections below describe their historical
HD785-only pilot state.

### HD785-5 Part Book index (September 2026)

`tools/partbook_index.py --book HD785-5` builds the text-layer
`HD785-5/HD785-5 part book.pdf` on the same SQLite pipeline. The book profile
is `hd785_5_raster`: `ITEM` headers, footer `Ref.` figure ids, section-divider
groups, and a left raster exploded view. A repeated-prefix serial such as
`J10001-J10030` is parsed only when the existing serial form does not match.
Duplicate parts lists are skipped by figure identity plus normalized table
rows; a later page keeps view metadata only when the first copy has no
exploded-view raster. Direct Part lookup does not render a parts-list page
when no verified view exists. HD785-7 B1 and WA600-6 are not rebuilt.
The candidate database is built twice and installed only when those digests
match and the other books' digests stay unchanged.

The verified source has 554 PDF pages. 529 parts-list pages are indexed
(517 with a left raster view and 12 with no exploded-view raster). Four later
pages repeat the ENGINE RELATED PARTS tables and are not ingested again.
The index contains 506 Figures and 10,580 source rows (10,182 rows with a
part number, 4,868 distinct normalized PNs); 989 rows need review and 398
rows are rejected because the text layer has an icon glyph instead of a part
number. Machine applicability is `J10001-UP`; engine group 03 uses
`0012121-`. A direct Part lookup renders a verified view when one exists and
returns text only when the figure has no raster. Run
`python -m unittest tools.fleet.test_partbook_hd785_5 tools.test_maintenance_hd785_5_part`
after rebuilding.

### HD465-7R Part Book index (September 2026)

`tools/partbook_index.py --book HD465-7R` builds the text-layer
`HD465-7R_HD605-7R/HD465-7R  Parts Book.pdf` on the same SQLite pipeline.
The book profile is `hd465_raster`: `INDEX` headers, a right-hand `Ref. :`
figure id, a left raster only when that illustration Ref matches, and section
titles from divider pages. Page-code prefix `03` is used twice, so the second
run is group `03-2`. Engine applicability is taken from the `SAA6D170E-5R`
banner; a bare engine serial such as `610017` is open-ended only in that
context. HD605-7R is not indexed. HD785-7 B1, HD785-5 and WA600-6 are not rebuilt.
The candidate database is built twice and installed only when those digests
match and the other books' digests stay unchanged.

The verified source has 466 PDF pages. 438 parts-list pages are indexed.
421 Figures are stored. Eleven sheets whose illustration Ref disagrees with
the parts-list Ref stay text-only. The index contains 8,540 source rows;
182 rows are rejected because the text layer has an icon glyph instead of a
part number, and 158 rows need review. Machine applicability is `J20116-UP`.
Engine rows under the engine banner use `610017` and up. A direct Part lookup
renders a verified left raster when the Refs agree and returns text only
otherwise. Run
`python -m unittest tools.fleet.test_partbook_hd465 tools.test_maintenance_hd465_part`
after rebuilding.

`SOUL.md` routes technical requests: greetings, clarification and trivial answers
go to the Parent directly; a technical/manual question about a supported
model uses the Parent fast path; a specific fleet unit's fault, or technical
plus fleet/history evidence, uses two-stream delegation below. A pure
technical question does not run fleet tools or delegate.

The Maintenance plugin registers `maintenance_manual_evidence` in the
`komatso_maintenance` plugin toolset. Plugin toolsets are enabled on a
platform unless listed in that platform's `known_plugin_toolsets`, so keep
`komatso_maintenance` out of `known_plugin_toolsets.bale`. Set
`tools.tool_search.enabled: off` in the Maintenance runtime config only
(`hermes -p maintenance config set tools.tool_search.enabled off`): Hermes
defers every plugin tool behind `tool_search`/`tool_describe`/`tool_call`
otherwise, adding a discovery round trip. On this surface the only other
deferred tool is `process_manage`, and the three bridge schemas disappear.

```text
retrieve: read device AGENTS.md in full -> applicable policy (presentation kept)
          + private request -> manual_worker_batch retrieve
          (indexed packet || native web_search)
finish:   manual_worker_batch finish (render + PNG validation || bounded text || web_extract)
Parent:   one final answer with the returned MEDIA paths
```

The tool reuses the same `select_rules`, request files and batch CLI as the
Technical child; nothing is reimplemented. The model supplies English Shop
Manual `keywords` that normalize colloquial or misspelled wording; without
them a colloquial Persian question can produce no search terms. Retrieval is
bounded to three per question; `broad=true` scans the whole manual (10-30 s)
after an indexed miss. `finish` accepts only a `request_id` created by the
same session's retrieve, and URLs from that retrieve's search results. The
handler refuses delegated child sessions, so the child contracts are unchanged.

### PC1250SP-8R Part Book index (September 2026)

The 789-page Filemarket PC1250-8R compilation contains an explicitly printed
PC1250SP-8R S/N 35001-UP (W/O EGR, +55C) parts section on PDF pages 137-788.
Its preceding SAA6D170E-5CR-W engine section states S/N 610001-UP; PDF page
136 names a different engine variant and is excluded. Only the PC1250SP-8R
model key is enabled. The `pc1250_top_view` profile uses PDF bookmarks for
groups, printed Reference IDs for Figures, and a matching top raster view.
Variable-width tables can continue on viewless pages. Exact rows are verified
against the PDF before direct media; `@` serial notation and multi-Figure
ambiguity suppress automatic rendering.

The index has 18 groups, 598 Figures, 784 parts-list pages (598 with a
matching view, 186 without a new view), and 13,487 source rows. Of these,
676 blank-PN rows are rejected and 314 other rows need verification.
The book was built twice in a private copy of production SQLite; all
per-table digests matched, and HD785-7 B1, WA600-6, HD785-5, and HD465-7R
digests were preserved before and after atomic installation. Run
`python -m unittest tools.fleet.test_partbook_pc1250sp
tools.test_maintenance_pc1250sp_part` for source-grounded checks.

### PC800-8 Part Book index (September 2026)

`PC800/KOMATSU PC800-8 PART BOOK.pdf` is the PC800-8 S/N 50001-UP (ecot3)
parts catalogue, engine SAA6D140E-5F-03 S/N 530001-UP. It uses the shared
top-view profile. Pages 368-435 are printed `PC800LC-8` and are not indexed
as PC800-8 evidence. PC800-8R is not an indexed parts model. The shop-manual
model list is unchanged, so PC800-8 does not become a Shop Manual identity.
Run `python -m unittest tools.fleet.test_partbook_pc800
tools.test_maintenance_pc800_part`.

## Fleet context during channel lockdown

Maintenance Bale does not expose `delegate_task` or generic host tools.
The former two-stream fleet workflow depends on those tools and is unavailable
through messaging in this phase. The current SOUL asks for a verified model
and uses the dedicated Manual path while clearly distinguishing any
user-supplied fleet observations from verified operational history. Restoring
live fleet context requires a future strictly scoped domain tool.

## Local live benchmark

The measurements below describe the previous tool surface and are historical;
they are not instructions to re-enable delegation or host tools on Bale.

Use 	ools/bench_maintenance_delegation.py --question-file <private UTF-8 file> with
HERMES_HOME set to the Maintenance profile and PYTHONPATH set to the installed
Hermes source. It uses the real model, Bale platform prompt, and the configured
21 Bale tools without a messaging adapter. It joins the real two-child batch in
the same turn because this local harness has no Gateway to deliver detached
results. Use `tools/analyze_maintenance_bench.py <session-id>` for timing
from the local agent log; it reports `parent-direct` or `two-stream` and the
fast-path phases/components. Gateway sessions are read from `state.db`; for
harness runs pass the same `--transcripts-dir` to both scripts. Keep benchmark
questions, IDs, answers, and logs outside Git.
## Maintenance Technical preparation

Copy `integrations/hermes/plugins/komatso-maintenance-manual` into the
Maintenance home's `plugins/komatso-maintenance-manual`, including
`MANUAL_WORKER.md`, and add `komatso-maintenance-manual` to that profile's
`plugins.enabled`. Keep its `SOUL.md` synchronized with the canonical source.
Do not enable this plugin in default or other profiles. The hook also checks
that its home is `profiles/maintenance`; unmarked calls and Fleet tasks remain
untouched. Preparation failure blocks the marked delegation with an explanation.

The parent places verified model and the exact question once in a first-line
`KOMATSO_MANUAL_TASK_V3` JSON object. The native `pre_tool_call` hook replaces
that metadata with selected device rules, the compact workflow, background
machine facts and a private runtime request path. Technical's goal follows the
original question; incidental fleet codes do not create extra diagnostic goals.
Complete displayed failure codes can be routed; incomplete action codes remain
uncertainty. No device/manual/index/source data is changed.

A one-use internal receipt binds the native system section to this Technical
child; it is absent from Fleet, Parent, unprepared children and other profiles.
No Hermes core modification, tool override or model change is used. Reload the
Maintenance plugin manager through the native Gateway control socket for new
sessions; a default Gateway restart is unnecessary.

Normal Technical path:

```text
prepared device policy + original question
    -> retrieve batch: indexed PDF packet || existing native web_search
    -> Technical judgment: needed gaps, images and relevant URL
    -> finish batch: bounded text || approved render + PNG validation || native web_extract
    -> sourced Technical summary
```

The two CLI calls are:

```powershell
E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_worker_batch.py retrieve --request-file REQUEST_FILE
E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_worker_batch.py finish --request-file REQUEST_FILE --render-pages IMAGE_PAGES --read-pages MISSING_TEXT_PAGES --web-url 'RELEVANT_URL'
```

Omit unused finish options. Retrieve optionally accepts `--component` and a
complete `--fault-code`. The model selects the URL from actual search results;
the batch does not choose a source by guessed relevance. Native web tooling
retains its configured provider, URL validation and extraction limits. Web
failure remains explicit and does not invalidate local PDF evidence. The
prepared profile home is passed to the isolated web process so multiplex
execution cannot use the default profile's configuration.

These are two model-issued tool calls, containing four backend operations
(packet, web search, render/validate, extraction), or five with needed text.
`batch_metrics` reports every operation and concurrent wall time. Additional
batches remain allowed for material missing evidence. Source data is read-only;
private requests, web result metadata and rendered artifacts are local runtime.

## Indexed manual evidence

After loading the selected device AGENTS.md, retrieve one bounded packet:

```powershell
E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\manual_evidence_probe.py --model MODEL --problem "QUESTION" --packet
```

Pass `--fault-code CODE` only for a complete displayed code, and `--component`
when known. The probe resolves the model's Shop Manual through its index,
validates page count and folder, and searches indexed ranges in one batch.
Chapter intent keeps troubleshooting, tests and system descriptions together.
A multipart index with no leaf descriptions uses its indexed parent range.
An unresolved index retains a full-manual fallback.

The packet includes actual PDF page text, page/form references, graphics flags
and candidate continuation groups identified by PDF headings, font hierarchy,
form numbers and index boundaries. It uses no benchmark page or fault rules.
Complete text is capped at 22K characters; `text_truncated` and
`continuation_limited` identify missing coverage. A continuation group is a
routing aid, not proof that every page in it supports the user's diagnosis.
Follow a limit only when that topic is needed. The index itself is never
technical evidence. Verify ambiguous table columns and diagram labels before
claiming exact values or pins.

Select the smallest necessary images after reading the packet:

```powershell
E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\manual_evidence_probe.py --model MODEL --pages SELECTED_PAGES --render-only
```

This batches the approved renderer and validates PNG decoding, dimensions and
file size, without duplicating page text. Up to 8 explicit pages are accepted;
space and comma lists both work. For a material gap, `--pages PAGE...` returns
up to 20K characters in one follow-up. The older positional `MAP TERMS SECTIONS...`
interface remains available for targeted searches.

Batch a required web_search with retrieval, and the best relevant web_extract
with rendering (`char_limit: 4000`). Web failure is reported once and does not
invalidate confirmed Manual evidence. No model switch is part of this work.

Detailed trace, regression checks and measurements: [MANUAL_OPTIMIZATION.md](MANUAL_OPTIMIZATION.md).


## Part source regression verification (2026-09-27)

The actual Bale session `20260927_131135_8d6dd05f` took 82.5 seconds:
five `maintenance_manual_evidence` calls, including three retrievals, an
English-keyword error and an empty-finish error. It never invoked Part Book.
Its persisted tool results omit both `PARTBOOK_RULES_V1` and
`partbook_lookup.py` from `applicable_device_policy`.

The ingress profile scope is established by native Gateway
`GatewayAdapterLifecycleMixin._make_default_profile_message_handler`, then its turn
runner builds/caches an `AIAgent` from the routed Maintenance configuration.
`agent.system_prompt` loads the profile SOUL and project context;
`agent.prompt_builder.load_agents_md` loads the directory chain from git root
to `terminal.cwd=E:/KomatsoAI`, not the child machine folder. The selected
device rules are read later by the plugin's `retrieve`, after the model has
already selected the tool. There was no deterministic upstream Part/technical
classifier: Maintenance SOUL's Technical routing told the parent that its
FIRST call must be `maintenance_manual_evidence`, whose description offered
only Shop Manual + web retrieval. `select_rules` then matched Part intent with
`part|order|شماره.*قطعه|پارت|سفارش`; it did not match `شماره فنی` or bare PNs,
so it removed the Part heading and part-order paragraphs. Replaying the
previous implementation against the current device file reproduces this.
The historical session has no stored full system-prompt snapshot; the trace
uses its actual logs/tool results plus the installed prompt/config and native
prompt construction code, rather than claiming a missing snapshot exists.

Current HD785-7 AGENTS contains `PARTBOOK_RULES_V1`, Part Book-first lookup,
and the verified-local exception to mandatory Shop Manual/web research.
No legacy "No Parts Book exists" or Shop-Manual-only PN instruction remains.
Generic Shop Manual-first sentences remain scoped by the explicit PN rules.
This file and the MD ignore policy were not changed or tracked by this fix.

Runtime plugin and SOUL were synchronized from the canonical project files,
with backups under the Maintenance profile. Native scoped control-socket
reload returned `reloaded=true`. Fresh live-model agents loaded the actual
Maintenance Bale toolsets (21 tools, including both direct evidence tools;
no `tool_search` bridge) and the source-priority prompt. These tests do not
send messages or deliver artifacts through the Bale messaging adapter.
Use a fresh Bale conversation (`/new`) when validating an existing user chat:
Hermes freezes prompts/tool schemas per session. No Gateway restart or
profile routing change is needed.

| Case | Live Bale-surface tool sequence | Time | Result |
| --- | --- | --- | --- |
| A: شماره فنی 581-91-19110 دستگاه 785-7 | partbook lookup | 17.81 s | PASS; VERIFIED H3410-03A0/item 10 and H3410-03B0/item 5, FILTER, quantity 2 |
| B: شماره فنی قطعه 6218-11-5830 برای HD785-7 | partbook lookup | 19.22 s | PASS; VERIFIED GASKET candidates |
| C: HD785-7 ریتاردر ضعیف شده علت چیست؟ | manual retrieve, finish | 48.18 s | PASS; no Part Book call |
| D: شیر ریتاردر مشکل دارد، روش تست و شماره فنی آن را بده | partbook lookup, manual retrieve, refined partbook lookup, manual finish | 67.99 s | PASS; session history identifies HD785-7; ambiguous valve PN is not replaced with a plate PN |
| E: missing PN 99999-99-99999 | partbook lookup, two targeted web searches | 32.35 s | PASS; incomplete coverage, no false absence claim |

A used two model calls and one tool call. Its targeted index/PDF verification
took 350.2 ms inside the lookup; no Manual/web operation ran. Compared with
the original 82.5-second Gateway turn, the fresh local Bale-surface turn was
64.69 seconds faster (~78%). This is a single-run comparison, not a latency
distribution or a measurement of outbound Bale delivery.

Validation: 11 source-routing tests, 33 existing manual tests and 24 existing
Part Book tests passed. The new routing tests exercise category-level
Persian/English/bare/alphanumeric PN intents, actual VERIFIED PDF lookup,
manual dispatch prevention, technical/mixed paths, misses, serial coverage,
registration and unindexed-model isolation. Live transcripts and questions
are private under `runtime/part-routing-regression` and remain outside Git.

Reproduce using the existing benchmark with Maintenance `HERMES_HOME` and
installed Hermes `PYTHONPATH`; `--history-file` accepts a private JSON message
history for model identity. Output includes tool sequence, verified candidates
and whether source priority was present in the actual assembled prompt.


## Optional technical Part Book enrichment (2026-09-27)

Integration point: the existing Maintenance plugin `retrieve()` calls
`run_retrieval()` after reading full device rules. Parent produces both existing
English Shop Manual `keywords` and optional `part_query` in its existing tool
invocation. When enrichment is applicable, rule selection retains the actual
device Part Book policy alongside technical policy; it must not strip source
authority merely because the original question is diagnostic. Source intent is unchanged: Part-only still uses the direct Part
tool, Mixed retains both sources, and Technical keeps Manual primary. This
optional enrichment is in the Parent fast path; existing delegated-worker CLI
contracts and the two-stream fleet architecture are unchanged.

The Manual batch still uses `manual_worker_batch.py`'s two concurrent branches
(real PDF packet and configured web). A daemon thread beside that existing
batch runs one targeted local `partbook_lookup.py --model MODEL --query NAME
--verify --limit 4`, with a one-second subprocess timeout. It never renders.
No new model/subagent call, tool roundtrip, profile, Gateway route, Hermes core
change, AGENTS edit, MD ignore change or B2 OCR was introduced.

The existing batch executor joins all its futures, so adding an unbounded
Part operation to that executor would risk the critical path. Instead the
plugin snapshots a publication event at Manual completion without waiting or
joining. The compact result is retained for the existing `finish` call, which reuses it
without another lookup or wait, bound to the same request/session; it also
collects a result that became ready after retrieve. Pending state expires. The daemon's
`subprocess.run` kills/reaps an over-budget child. Failed/malformed/timeout/miss/
unusable results never replace Manual evidence or cause enrichment retries.
Unindexed models and absent/invalid queries skip before any lookup subprocess.
The model-specific production gate remains `INDEXED_PART_MODELS`; other
indexes can be enabled after their own data/coverage validation. The lookup
coverage note is model-aware; future models use their actual book/serial
metadata instead of inheriting the HD785-7 pilot coverage text.

Only VERIFIED named rows with a description anchored to the final component
noun of the parent's short English query are exposed. Incidental figure rows
such as a brake oil line in a filter search are omitted. Compact candidates
contain figure/title, item, PN, description, quantity, applicability and
verification, alongside coverage status. PDF row verification does not prove
machine fit, a root cause, or which assembly variant the user owns. Parent
adds at most one useful identification line, preserves variants/applicability,
and can omit irrelevant or unusable results. No forced PN list or guessed PN.
An index miss never proves nonexistence. Exploded views remain explicit via
the direct tool when actually requested/needed.

### Measurements

`tools/bench_maintenance_part_enrichment.py` performs alternating-order paired
baseline/enriched measurements for three technical questions (boom-foot pin
play, steering-pump pressure and brake-filter restriction), three repetitions
per question in both cold and warm conditions: 18 pairs / 36 tool retrievals
per experiment. Cold means a fresh harness interpreter, not an evicted OS
file cache. Warm repeats the same harness process; the production Part CLI
still starts normally each time. The benchmark makes no LLM calls and measures
complete tool retrieval, including rules/request preparation and Manual batch;
it does not measure the entire final-answer conversation or outbound Bale
message delivery. Every pair stores `overhead_ms = enriched_total - baseline_total`,
Manual PDF and batch duration, Part duration and start/end/overlap metrics.

Two separate experiments were run: the full live configured Manual+web path,
and real PDF/index work with a constant unavailable-web response as a
network-free control. The latter is benchmark-only; production web behavior
was not changed. Tables below show medians; their delta column is the difference
of the displayed total medians, while raw paired deltas remain in private JSON.
Negative differences reflect normal timing/cache variance, not a claim that
Part work accelerates Manual retrieval.

#### Real PDF / constant-web control

| Mode / question | Manual PDF baseline/enriched (s) | Part process (ms) | Total baseline/enriched (s) | Delta of medians (ms) | Max paired overhead (ms) |
| --- | --- | --- | --- | --- | --- |
| cold / pin | 5.553 / 4.563 | 360.59 | 5.847 / 4.869 | -977.35 | -531.73 |
| cold / pump | 7.507 / 7.780 | 765.14 | 7.783 / 8.071 | 288.10 | 288.10 |
| cold / filter | 6.163 / 6.531 | 706.81 | 6.461 / 6.832 | 370.22 | 370.22 |
| warm / pin | 4.792 / 3.936 | 347.08 | 4.806 / 3.952 | -854.37 | 44.50 |
| warm / pump | 6.605 / 7.133 | 776.66 | 6.621 / 7.149 | 528.41 | 999.21 |
| warm / filter | 6.308 / 5.814 | 767.06 | 6.322 / 5.829 | -493.64 | -301.35 |

All 18 enriched runs published a result while Manual was running; waiting_ms=0.

#### Full live configured web

| Mode / question | Manual PDF baseline/enriched (s) | Part process (ms) | Total baseline/enriched (s) | Delta of medians (ms) | Max paired overhead (ms) |
| --- | --- | --- | --- | --- | --- |
| cold / pin | 4.886 / 4.890 | 363.76 | 5.579 / 5.594 | 14.45 | 530.07 |
| cold / pump | 7.107 / 7.551 | 827.50 | 7.804 / 8.270 | 465.90 | 517.46 |
| cold / filter | 7.535 / 7.295 | 809.46 | 8.302 / 8.024 | -278.23 | 8479.90 |
| warm / pin | 5.548 / 5.044 | 353.52 | 6.237 / 5.722 | -515.33 | 384.64 |
| warm / pump | 8.886 / 7.755 | 704.04 | 9.546 / 8.415 | -1130.67 | -343.21 |
| warm / filter | 7.997 / 8.714 | 675.98 | 8.664 / 9.440 | 776.27 | 1498.78 |

All 18 enriched runs published a result while Manual was running; waiting_ms=0.

In the constant-web control, the largest positive paired overhead was 999.21 ms;
all 18 pairs met the <=1-second target. Added orchestration time after accounting
for measured Manual-batch variation was at most 4.1 ms. Actual Part process
windows were 325.11-804.86 ms; all pump/filter runs had VERIFIED rows and pin
queries missed safely. The full live-web experiment had Part windows of
352.54-880.77 ms, but some raw total differences exceeded one second: the worst
was +8479.9 ms with +8482.31 ms in the Manual/web batch. That sample's web time
changed from 6171 to 16074 ms while Part had already finished at 695.73 ms.
These live-network totals do NOT support a universal <=1s end-to-end guarantee.
The nonwaiting implementation, overlap timings and controlled measurements
support accepting the local concurrency mechanism; external web/PDF/LLM timing
remains variable and is not hidden as enrichment overhead.

### Real Maintenance Bale surface

Runtime plugin/SOUL were copied from canonical project files with backups,
and native scoped plugin reload returned reloaded=true. Fresh real-model
`platform=bale` agents saw the actual Maintenance toolsets, direct schemas,
and source-priority/enrichment contract; no messaging adapter was used to send
messages. Old conversations should use `/new` for their frozen prompt/schema.

- Steering pump: Parent supplied `steering pump` in its same manual retrieve.
  VERIFIED main/shared steering-hoist pump alternatives and the separately
  identified emergency pump were ready in 502.21 ms inside a 16394.81-ms Manual
  batch (501.88-ms overlap), then the existing finish call ran: two tool calls,
  three model calls. A repeat took 773.15 ms inside an 8823.45-ms Manual batch.
  Parent may omit PN alternatives when they do not help the immediate test;
  readiness is not a requirement to clutter every diagnosis.
  Final-code repeat: 776.99-ms Part process inside an 8477.81-ms Manual
  batch, 776.47-ms overlap, still retrieve/finish and three model calls. With
  applicable Part policy retained, the final answer added a useful compact
  distinction between the main steering/hoist pump and emergency steering
  pump, noting two main assembly alternatives and fit checks; it did not
  clutter the diagnosis with an unrequested PN list.
- Generic overheating: absent part_query on both technical retrieves; no Part
  subprocess. The second Manual retrieve followed incomplete technical evidence.
- Part-only regression: one direct Part tool, two model calls, 17.31 seconds,
  both original FILTER rows VERIFIED; no Manual/web call.
- Mixed retarder test + PN: direct Part route first, then Manual technical
  evidence. Missing valve identity remained uncertainty; plate PNs were not
  asserted as valve PNs. Optional enrichment did not erase requested Part
  fallback behavior.
- Exact HD785-7 boom-foot wording: the live Parent requested clarification
  because HD785-7 is a dump truck, not a boom-equipped machine. No wrong-model
  evidence or PN was invented. Supplying the explicit query to the real tool
  in the paired benchmark exercises concurrent Manual retrieval and safe Part
  miss; that capability passes even though this user wording needs clarification.

Validation: 12 new enrichment tests, 11 Part-routing regressions, 33 existing
Manual tests and the existing Part Book validation set. Tests include actual
slow-child timeout/kill, simultaneous branch barriers, return-before-Part-done,
late finish collection and session isolation, optional query/skip/no borrowed
model, VERIFIED-only and incidental-row filtering, and no default render.
Private transcripts/metrics/questions are under runtime/part-enrichment-bench.


#### Final-code control repeat

After retaining applicable Part rules and making compact metadata reusable at
finish, the full 18-pair control was repeated with the final code. Each table
row again summarizes three pairs; delta is enriched median minus baseline
median (raw paired overheads are recorded separately).

| Mode / question | Manual PDF baseline/enriched (s) | Part process (ms) | Total baseline/enriched (s) | Delta of medians (ms) | Median paired overhead (ms) | Max paired overhead (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| cold / pin | 5.443 / 5.087 | 362.28 | 5.734 / 5.503 | -231.75 | 761.15 | 894.87 |
| cold / pump | 7.322 / 7.191 | 765.21 | 7.615 / 7.489 | -125.67 | -125.67 | 943.09 |
| cold / filter | 8.155 / 8.028 | 777.27 | 8.449 / 8.332 | -116.47 | -520.96 | 3198.78 |
| warm / pin | 5.891 / 5.911 | 350.46 | 6.079 / 5.927 | -151.86 | -151.86 | 748.37 |
| warm / pump | 9.510 / 9.056 | 778.76 | 9.526 / 9.072 | -453.51 | -486.69 | 375.81 |
| warm / filter | 7.337 / 7.771 | 800.31 | 7.351 / 7.786 | 434.84 | 463.38 | 1155.80 |

All 18 Part branches overlapped the Manual branch, with zero added wait;
Part process duration was 341.67-857.52 ms. All six groups' median paired
overhead was below one second; 16/18 individual pairs were <=1 second.
The largest raw outlier was +3198.78 ms, with +3196.78 ms measured inside the
Manual PDF batch while Part was finished at 597.39 ms. The other over-target
pair was +1155.8 ms. The largest orchestration delta was 164.17 ms (a cold
sample); all other groups' maxima were <=5.27 ms. These measurements support
nonblocking overlap and the typical overhead target, but not a hard universal
wall-clock guarantee; even the network-free control has PDF/OS scheduling
variation. Across the live-web/control/final-control experiments there were
54 pairs / 108 real tool retrievals. None is represented as a guarantee.

Final tests passed: 12 enrichment + 11 routing + 33 Manual + 24 Part Book
checks (80 unique tests). Runtime canonical/plugin and SOUL hashes match.
The production pilot remains HD785-7 B1 only. Expansion is architecturally
ready: enable a model in INDEXED_PART_MODELS only after its own index and
source/coverage validation. No other model index was created by this change.
