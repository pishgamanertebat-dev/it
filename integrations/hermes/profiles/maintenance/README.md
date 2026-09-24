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
