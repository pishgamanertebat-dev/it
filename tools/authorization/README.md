# Authorization: minimal overflow slice

Profile (context/expertise/tools), Role (organization) and Capability (operation/resource) are independent. No admin profile or profile routing is introduced.

## Persistent store and schema

The existing identity SQLite database is the single store:
`E:\KomatsoAI\reports\telegram_usage\telegram_users.db`.
Registration remains `channel_users.registration_status`; pairing is unchanged.
New tables:

- `auth_roles(role PK, display_name)` — `office_supervisor`, سرپرست دفتر.
- `auth_capabilities(capability PK, resource)` — two independent permissions:
  `reports.overflow.read` and `reports.overflow.daily_receive`, both scoped to `reports.overflow`.
- `auth_role_capabilities(role, capability)` — composite PK, foreign keys.
- `auth_user_roles(platform, user_id, role, active, assigned_at, assigned_by, updated_at)` — composite PK; references the existing identity and role.
- `auth_events` — operator assignment/deactivation audit.
- `auth_migrations(version PK, applied_at)` — migration 1.

No direct-user capability grants, wildcard resources, file permissions or second identity table exist. Additional organizational roles can use the same tables/API later.

`AuthorizationStore.roles()` and `has_capability()` join active assignments, role capabilities and approved registration. Reads use SQLite `mode=ro`, a transaction snapshot, and schema-version checks. Missing/corrupt/unavailable storage denies access. Reads never migrate/create storage. Migration and assignments use `BEGIN IMMEDIATE`, foreign keys and transactional rollback. Migration is repeatable and makes an online integrity-checked SQLite backup before mutation. Operator assignment commands also back up first.

## Backend enforcement

`tools/fleet/overflow/bale.py` checks the real `event.source.user_id`, Bale private DM, matching private chat identity, approved registration and `reports.overflow.read`. Denied requests are intercepted before agent/admin routing and do not invoke the generator. Menu visibility grants nothing. Direct delivery rechecks before generation and before each image; revocation while the worker runs prevents delivery.

The canonical registry bridge intercepts overflow commands even when denied, so they do not fall through to an LLM. Its authorization-version check reloads a legacy cached public backend after native plugin hot reload. Source: `integrations/hermes/plugins/komatso-bale-registry/__init__.py`.

The existing deterministic generator and workbook interpretation are reused unchanged. The messaging/scheduled worker passes an explicit fixed `--source E:\Function\سرریز روزانه.xlsx`; environment overrides cannot expand this resource. Developer CLI fixture-source options remain local and are not messaging capabilities.

## Daily delivery and holder replacement

The existing `overflow_daily_test` job/history is retained in `settings/schedules.yaml`:
`recipient_role: office_supervisor`, `timezone: Asia/Tehran`, cron hour 9/minute 0. No overflow recipient ID occurs in the schedule or business source. Each occurrence requests exactly the previous Tehran calendar day as a Jalali date (e.g. 1405/07/13 at 09:00 requests 1405/07/12). Empty schedule params are required: a fixed date cannot override this policy. Missing dates never fall back to the latest sheet; a mismatched generator date is rejected. The image template is unchanged and yesterday does not receive a false stale warning. Existing repairs jobs are unchanged.

The existing Windows `KomatsoAI Schedules` task runs the canonical scheduler every minute; it is not modified. No Hermes cron overflow job is created. Each due execution resolves active Bale office-supervisor assignments from SQLite:

- Zero holders: `skipped / no_active_holder`; no generator or send.
- More than one: `skipped / ambiguous_holders`; no automatic selection, even if one identity has been revoked.
- Exactly one: require approved persisted private Bale identity and `reports.overflow.daily_receive`.
- Storage failure: `skipped / store_unavailable`.

The delivery operation repeats resolution before generation, after generation and before every send; a changed holder aborts that run. Logs identify the reason and holder count. The existing `(schedule_id, due)` ledger prevents a repeated run; no blind retry is introduced. No LLM or agent is used.

To change the supervisor, change only the assignment atomically through the local operator command. Neither code, Cron nor profiles change:

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.authorization.manage migrate
& .\.venv\Scripts\python.exe -X utf8 -m tools.authorization.manage assign --bale-user-id <CONFIRMED_ID> --actor <OPERATOR> --replace
& .\.venv\Scripts\python.exe -X utf8 -m tools.authorization.manage resolve
```

An assignment requires an existing approved Bale identity. A second active holder is rejected unless replacement is explicit. The resolver still fails closed for preexisting/manual/corrupt ambiguous assignments. Commands are not bot/agent tools.

## Deployment and verification

Canonical first. `integrations/hermes/deploy_overflow_authorization.py` follows the existing backup/copy/hash pattern for default and maintenance registry twins, using atomic replacement. Shared project tools/schedules are already canonical. Activate with the existing native `reload_gateway_plugins` control verb for both served homes; no restart/update or launcher/Task edits are required.

Phase 1 configs/core/toolsets remain unchanged. Model-tool allowlists must pass for default Bale/Telegram and maintenance Bale. Native security files run in separate hermetic processes to avoid registry-state contamination.

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m unittest tools.authorization.test_authorization tools.scheduler.test_runner tools.fleet.overflow.test_report -v
& .\.venv\Scripts\python.exe -X utf8 -m tools.scheduler.runner --check
```

Tests use synthetic identities, temporary SQLite, and fake Bale transports; no live delivery is triggered. The generator regression may read the existing overflow workbook, but never saves it. No production Work Order data is mutated.
