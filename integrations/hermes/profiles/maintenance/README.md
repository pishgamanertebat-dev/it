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
the parent route. Keep the default one-level delegation depth. The canonical `SOUL.md` contains the complete two-stream trigger and worker contract.
Keep it synchronized with the maintenance runtime SOUL. The existing
`maintenance-two-stream-evidence` skill remains installed for compatibility,
but this path does not require a separate `skill_view` call.
Ensure maintenance agent.disabled_toolsets excludes delegation; leave the CLI toolset list unchanged.

## Local live benchmark

Use 	ools/bench_maintenance_delegation.py --question-file <private UTF-8 file> with
HERMES_HOME set to the Maintenance profile and PYTHONPATH set to the installed
Hermes source. It uses the real model, Bale platform prompt, and the configured
21 Bale tools without a messaging adapter. It joins the real two-child batch in
the same turn because this local harness has no Gateway to deliver detached
results. Use 	ools/analyze_maintenance_bench.py <parent-session-id> for timing
from the local agent log. Keep benchmark questions, IDs, answers, and logs outside Git.
## Indexed manual evidence

After loading the selected device AGENTS.md, the Technical worker calls the
canonical read-only probe once with the verified model and the exact symptom:

```powershell
E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\manual_evidence_probe.py --model HD785-7 --problem "retarder not working"
```

Pass `--fault-code CODE` only for a complete displayed code. The probe reads
that model's `manual_sections.json`, validates the Shop Manual PDF and page
count, searches several candidate ranges in one pass, and returns bounded PDF
excerpts, page references and diagram ranges. A sparse or unresolved index
triggers a full-manual fallback. The index itself is never technical evidence;
complete PDF pages and diagrams must still be checked for exact values or
procedures. For that follow-up, use `--model MODEL --pages PAGE... --render` to
read at most four bounded pages and batch the approved renderer in one call. The older positional `MAP TERMS SECTIONS...` interface remains
available for targeted follow-up searches.
