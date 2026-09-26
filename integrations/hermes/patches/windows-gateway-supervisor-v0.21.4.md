# Hermes v0.21.4 Windows gateway supervisor

Local patch for the installed Hermes core. It is not upstream. A later Hermes
update replaces `%LOCALAPPDATA%\hermes\hermes-agent` and drops this behavior
unless the new tree already contains it.

Base before the patch: `33a30fdd815a965b59dd38aae74a67ecec7b0cca` (v0.21.4).
Local commit on the installed Hermes checkout: `6f329f78d97d4c60d43b9a43588ea65b2590f45a`.
That commit is not pushed.

The patch file next to this note is `windows-gateway-supervisor-v0.21.4.patch`.
It touches only:

- `gateway/run.py`
- `hermes_cli/gateway_windows.py`
- `tests/hermes_cli/test_gateway_windows.py`

## Still required?

Open `hermes_cli/gateway_windows.py` in the updated tree and read
`_build_gateway_vbs_script`.

The patch is still required when that function launches the gateway with
`WScript.Shell.Run` and `bWaitOnReturn` false (`0, False`). That launcher
exits 0 immediately. Scheduled Task `RestartOnFailure` then supervises
`wscript.exe`, not the gateway, so exit 75 never restarts the process.

The patch is also still required when the launcher waits on the child but
restarts every non-zero exit on a fixed short delay with no fast-crash cap.
Five consecutive crashes that die in under 60 seconds, and are not exit 75
or 78, must park the supervisor (`WScript.Quit 0`) after exponential backoff
capped at 300 seconds. Exit 75, and any child that stayed up at least 60
seconds, keep the 15-second recovery.

If the updated function waits on console `python.exe`, recovers exit 75
quickly, and parks a fast non-75 crash loop, do not reapply this patch.
Confirm with the unit tests named in the patch (`test_gateway_vbs_script_supervises_console_python`
and `test_gateway_vbs_rewrites_pythonw_to_console_python`). The launcher must
embed `python.exe`. A stub that forces `pythonw.exe` into the script does not
test the real resolver.

## Reapply

From a clean v0.21.4 tree, at the Hermes checkout:

```powershell
git apply --check E:\KomatsoAI\integrations\hermes\patches\windows-gateway-supervisor-v0.21.4.patch
git apply E:\KomatsoAI\integrations\hermes\patches\windows-gateway-supervisor-v0.21.4.patch
```

Then regenerate the Scheduled Task launcher from the patched code and restart
through `hermes gateway restart` so the running `wscript` loads the new script.
Do not copy a previously generated `Hermes_Gateway.vbs` from another machine.
The generator writes `gateway-service\Hermes_Gateway.vbs` with local paths.

`hermes gateway start` uses `schtasks /Run` when `Hermes_Gateway` is registered.
A direct spawn remains only when that `/Run` fails for a reason other than
"already running".

## What this does not fix

Exit 75 itself can still happen. On the 2026-09-26 outage the liveness watchdog
fired because the gateway event loop was blocked in synchronous filesystem
work (`load_env_file` during the multiplex handoff scope, then
`get_active_profile_name` → `ntpath.realpath` while publishing a Bale fatal
status). The Bale `updater.stop()` CLOSE-WAIT log is the recovery classification
when that deadline cannot run. This patch restarts the process after that exit.
It does not move that filesystem work off the event loop, and `gateway.loop_watchdog`
stays enabled.
