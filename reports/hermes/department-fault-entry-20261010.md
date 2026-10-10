# Department fault entry — deployed 2026-10-10

READY — DEPARTMENT FAULT ENTRY DEPLOYED

## Operational result

- Bale 654806764 and 1732374823 enter CODE directly for mechanical; 387679249 enters CODE for metalwork.
- Existing approved/private identity and active organizational role are required. Scoped checks protect commands, callbacks, preview, commit under lock, and the final replacement boundary.
- Department users see confirm, edit and exit. Clear and section switching are absent and rejected by the backend.
- The fault button is hidden for 641220453 and 1294822197. Their organizational roles, Work Orders, read rights and existing backend NET authority are preserved.
- TTL, actor/chat/message/stage/revision binding, operation IDs and restart/retry recovery reuse the original handler and writer.

## One-workbook mine import

The only operational workbook remains `E:\Function\گزارش روزانه رانندگان2.xlsx`.
Mechanical maps to D and metalwork to E, identified by the existing header parser, date and canonical device code.

The installed `C:\Users\win-10\AppData\Local\hermes\scripts\mine_file_sync.py` stages incoming mine bytes through its existing protected copy. Only the exact fault-report destination invokes `tools.fleet.repairs.sync_service`; every other destination retains its original publication path.

The merge uses the existing `runtime/repairs-entry/audit.sqlite3`, writer lock, backups, prepared/succeeded journal recovery and atomic replacement. Confirmed Bale edits are reconstructed by date/device/section. Incoming values equal to the recorded baseline or latest confirmed local value are accepted; a different same-cell value raises `FAULT_SYNC_CELL_CONFLICT`, preserves the entire healthy local workbook and records the failed import. Unjournaled local changes also block publication. Repeated incoming hashes cause no workbook rewrite.

Initialization used the read-only snapshot previously verified byte-identical to the mine and still identical to the live file. The journal cutoff is rowid 3, activated at `2026-10-10T09:38:37.650579+00:00`. The three historical operations preceding this verified baseline are not resurrected. Future confirmed operations are protected. No live workbook content was changed during activation.

The latest incoming mine workbook supplies unrelated information, sheets and formatting. Existing addressed cells retain incoming styling. Missing rows/days use the existing template/writer. New rows append after actual content, preserving mine footer data, merges and print columns. The Windows writer lock now initializes its marker only after obtaining the byte-range lock, fixing an observed concurrent-request error.

The mine share became unavailable before deployment. Its existing bounded timeout/backoff remains active. No remote writes or sync-schedule changes were made. New local entries remain available while the mine computer is offline; its next successful import uses the installed merge hook. Live reconnection could not be observed while the mine was offline; disconnect/reconnect behavior passed on isolated fixtures.

## Validation and activation

- 132 related authorization/UI/lifecycle/writer/sync regressions passed.
- 28 existing mine-sync regressions passed against the staged hook, including other files and worker locks.
- 23 final writer/sync tests passed after adding footer/print preservation and actual open-file lock coverage.
- An isolated copy of the real workbook preserved all 179 historical sheet signatures, both departmental descriptions, unrelated mine data, valid alias 469 → HD469, repeat idempotency and conflict safety. The live workbook hash remained `122bd93b156d907e7cb4f43fe77263d2b62c2ce4acbdf8c5f8bf84089bb1d55c`.
- No test records were written to the live workbook and no test messages were sent to staff.
- Controlled drain of PID 7868 completed. The existing supervisor supplied PID 2672, avoiding a duplicate start. Its control socket and status confirm Bale/Telegram connected, four profiles served and session storage healthy. Pairing fix e7208cd011 is active; Hermes Core was not edited.
- The final backend writer revision loads in fresh per-request subprocesses; another Gateway restart was unnecessary.

## Scheduled reports

Existing driver reports include the complete mine report, so they cannot be classified as copies solely of each user's Bale submissions. They were preserved, as were overflow and maintenance reports. Active driver recipients remain mechanical: 654806764, 1732374823; metalwork: 387679249; office/supervision: 397185913, 514458396. The old `repairs_daily` and `metalwork_daily` jobs remain disabled. Schedule configuration hash is unchanged.

## Files and evidence

Changed: `settings/repairs_entry.json`, `tools/authorization/net.py`, `tools/bale_ui/runtime.py`, `tools/fleet/repairs/entry_bale.py`, `entry_service.py` and their relevant tests.

Added: `tools/fleet/repairs/sync_service.py`, `test_department_entry.py`, `test_sync_service.py`, `rehearse_department_sync.py`, `integrations/hermes/deploy_department_entry.py`, and `integrations/hermes/patches/mine-fault-merge-20261010.patch`.

The installed sync change is captured by the patch. Other pre-existing workspace modifications were left untouched.

Evidence and backups: `runtime/department-fault-entry-20261010/` — initial/continuation backups, deployment tests, other-sync tests, real-copy evidence, online audit backup, installed hashes, Gateway restart record and final verification. Production backups continue under `runtime/repairs-entry/backups` before publication. Import conflicts are recorded in `fault_sync_failures`; successful merges and confirmed Bale edits share `edits`.
