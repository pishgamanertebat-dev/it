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

When the default Gateway routes Bale conversations to `maintenance`, set `platform_toolsets.bale` explicitly in the maintenance runtime `config.yaml`. The implicit `hermes-bale` fallback can resolve to an empty tool surface in a routed profile. Match the default Bale tool surface so the agent can read device `AGENTS.md` files, search manuals, and use the project's tools:

```yaml
platform_toolsets:
  bale:
    - browser
    - code_execution
    - connections
    - delegation
    - file
    - skills
    - terminal
    - web
```

This is a tool configuration for the routed agent, not a bot credential or a second Bale Gateway. Keep sender routes and real user IDs in the default Gateway's local runtime configuration, outside Git.

## Two-stream delegation

The maintenance Bale `delegation` toolset adds only `delegate_task` to the
existing tool surface. Set `delegation.max_concurrent_children: 2` and
`delegation.independent_completions: false` in the maintenance runtime config.
Keep `delegation.model` and `delegation.provider` unset so both workers inherit
the parent route. Keep the default one-level delegation depth. The canonical `SOUL.md` contains the two-stream trigger; the scoped plugin owns the compact Technical contract.
Keep it synchronized with the maintenance runtime SOUL. The existing
`maintenance-two-stream-evidence` skill remains installed. The preparation hook
reads it and the selected device AGENTS in full, then injects only applicable
unique device policy and the Technical scope. Root/project paragraphs and the
original question are deduplicated. A native system section attests instruction
loading only for the prepared Technical session; Fleet receives no such section.
Ensure maintenance agent.disabled_toolsets excludes delegation; leave the CLI toolset list unchanged.

## Local live benchmark

Use 	ools/bench_maintenance_delegation.py --question-file <private UTF-8 file> with
HERMES_HOME set to the Maintenance profile and PYTHONPATH set to the installed
Hermes source. It uses the real model, Bale platform prompt, and the configured
21 Bale tools without a messaging adapter. It joins the real two-child batch in
the same turn because this local harness has no Gateway to deliver detached
results. Use 	ools/analyze_maintenance_bench.py <parent-session-id> for timing
from the local agent log. Keep benchmark questions, IDs, answers, and logs outside Git.
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
