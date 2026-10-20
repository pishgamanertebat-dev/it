# Oil change unified add/edit — 2026-10-20

Status: **READY — OIL CHANGE UNIFIED ADD/EDIT WORKFLOW ACTIVE**.

The exact tested patch is installed at `E:/KomatsoAI`. One controlled restart
drained Gateway PID 17396 cleanly and activated PID 15644. Live control-socket
verification reports Bale/Telegram connected, session store OK, and all four
profiles served: default/admin/maintenance/net. Scheduled Task, launcher,
profile configurations and plugin bridges are unchanged.

The oil proposal has one `➕ افزودن/ویرایش` action, an independent remove action,
and the existing final creation/exit actions. GR/AF retain their add/remove menus.
The editor accepts one or up to 100 codes, retains the 16-unit/two-column picker,
and stages every addition/cycle choice until explicit confirmation. Existing units
are identified; duplicate aliases reject the whole input. Batch defaults remain
the snapshot's cycle, with optional per-unit changes before confirmation.

The six audit findings are addressed:

- All oil selection and edit data, including the shared oil add helper, use the
  complete proposal snapshot. Final source hashes still gate creation.
- Near-due warnings are rebuilt from evaluations minus selected units after
  add/edit/remove; removing a 14-hour near-due unit restores its warning.
- Correctable errors return to the proposal; changed sources offer a fresh
  proposal, preserving revision identity. Uncertain failures offer inspection
  without an automatic retry or a false success.
- Revision parents are rejected at removal selection, text input and confirmation;
  the existing backend revision guard remains unchanged.
- A same-cycle choice is a no-op for existing units, or an ordinary default-cycle
  addition for new units. It does not create a false override/change note.
- Back moves through the logical prior stage; cancel-current-edit preserves the
  confirmed proposal. Full-form exit is a separate explicit action.

Old oil add callbacks are unavailable. Restored legacy add/edit sessions return
to their preserved proposal and invalidate their pending keyboard revision.
New editor sessions preserve their page, pending choices and confirmation on restart.

Additional user-authorized correction: only PC1250-8 output cell C2 now prints the
selected service cycle. Its original cell style and all other technical cells,
formulas, capacities, shared strings and package assets are preserved. Source
templates are never rewritten. Native Excel/PDF outputs for 1600/1800/2000 passed.

Read-only scan of all 14 models / 140 source sheets found PC1250's ten stale
`5200ساعت` titles and four already-known PC600 source-title mismatches
(400/1400/1600/1800). PC600 already has an approved output-only correction;
that existing behavior and its source files were retained. No additional model
was added to this patch.

Validation and deployment evidence is under
`E:/KomatsoAI/runtime/oil-unified-20261020T082101Z/`:

| Evidence | Result |
|---|---|
| `audit-tests.json` | 70 passed, 0 failures/errors/skips |
| `unified-tests.json` | 22 passed, 0 failures/errors/skips |
| `regression-tests.json` | 200 passed, 0 failures/errors/skips; OC/GR/AF |
| `postinstall-smoke.json` | 1 passed on installed code; native PDF review, revision and old-dispatch fence |
| `evidence/e2e.json` | NET menu to native reviewed PDF; revised version; old dispatch rejected |
| `evidence/pc1250-native.json` | Actual 1600/1800/2000 Excel/PDF, technical cells/styles/source preserved |
| `template-title-audit.json` | Read-only 140-sheet title inventory |
| `restore-smoke.json` | Prior keyboard restored/imported; both restored databases pass integrity |
| `production-data-check.json` | 25 fleet + 15 authorization tables unchanged at predeployment check |
| `tested-patch.json`, `tested-patch.zip` | Exact staged hashes and CRC-verified patch bundle |
| `installed.json`, `gateway-final.json` | Installed hashes, new live PID, connected transports/four profiles |
| `production-data-final.json` | All 40 operational/authorization tables and DB integrity preserved |
| `supervisor-final.json` | Scheduled Task and launcher hashes unchanged |
| `installed-snapshot-check.json` | All 47 valid units available in three pages; no independent oil add action |

The 292 required/regression tests and the additional installed-code smoke passed.
All 16 original source/template workbooks retain their pre-change SHA-256 hashes.

PC1250 native outputs, under the evidence directory:

| Cycle | Workbook / PDF basename |
|---|---|
| 1600 | `OC-1405-06-08-001.xlsx` / `.pdf` |
| 1800 | `OC-1405-06-09-001.xlsx` / `.pdf` |
| 2000 | `OC-1405-06-10-001.xlsx` / `.pdf` |

Transport in tests is **SIMULATED**. The end-to-end print/review tests use the real
project builder, native Excel COM PDF export, real PDF parsing and real review
hash validation. No production order, real-user message, or test PDF delivery was made.
The per-device independent OC number/workbook/PDF contract is unchanged.
Review hashes, superseding, dispatch attempts/tokens/leases, uncertain sends and
receipt/recovery contracts retain their existing implementation.

Backup:
`E:/KomatsoAI/backup/oil-unified-20261020T082101Z.zip`

SHA-256: `addfadbfe410838e5db7dee14d753bc7258ca7b59b280bdeb497921e888de0b5`

The backup contains prior code, coherent SQLite backups and original source/template
workbooks; ZIP CRC, workbook packages and restored DB integrity were verified.
The pre-existing change in `tools/authorization/test_net.py` is excluded from the commit.

Rollback, while Gateway/work-order execution is idle:

```powershell
E:\KomatsoAI\.venv\Scripts\python.exe -B -X utf8 E:\KomatsoAI\runtime\oil-unified-20261020T082101Z\release.py rollback
hermes gateway restart
```

The rollback guard verifies installed hashes, restores only release code/tests,
and converts new transient UI state to the previous handler's schema while
preserving confirmed proposals. It does not restore or overwrite operational DBs,
historical orders, reviews, receipts or later production activity.
