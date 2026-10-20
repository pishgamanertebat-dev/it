# OIL_CHANGE simplified inline workflow — 2026-10-20

**READY — OIL CHANGE SIMPLIFIED INLINE WORKFLOW ACTIVE**

The exact tested source hashes are installed at the canonical runtime import
path. One controlled Gateway restart completed cleanly: PID 15644 → 10968.
Bale and Telegram are connected, all four profiles (default/admin/maintenance/net)
are served, and the session store is healthy. Client visual rendering remains
**SIMULATED**, as explained below. The deployment commit is the commit containing
this report.

The change is limited to four existing Bale presentation/handler modules and
four verification files. No new runtime layer or database migration is added.
The pre-existing `tools/authorization/test_net.py` change is excluded.

| Requirement | Implemented behavior |
|---|---|
| Picker text | Only `➕ افزودن/ویرایش`; no duplicate machine/service list or manual-code instructions |
| Device selection | Inline buttons only; single or multiple typed device codes are rejected |
| Fleet | All 47 currently valid planning units; two columns; pages of 16 / 16 / 15 |
| Confirmation | Applies one confirmed device/service cycle, then returns to the same page |
| Repeated selection | Same message is updated; stable snapshot ordering prevents devices shifting between pages |
| Message-edit failure | Existing lifecycle removes the stale keyboard and sends a replacement; the edit is not applied twice |
| Finish | One `✅ پایان و بازگشت به پیشنهاد` button; confirmed choices remain; no final order is created |
| Internal back | Confirmation → cycle selection → device list; pending changes never enter the proposal |
| Full exit | Existing proposal cancellation and typed whole-form cancellation remain available |
| Service meaning | Only the order's service cycle changes; actual meters and source planning bytes are preserved |

Navigation payloads are `[next]`, `[next, previous]`, `[previous]`. This reverses
the old middle-page row in response to the user's observed RTL Bale placement.
There are no invalid or dummy navigation buttons. The shared keyboard builder
preserves row order; the active bridge does not reverse it.

**Bale rendering and transport are SIMULATED. No real-client visual test was
performed, so physical next-right / previous-left placement remains unverified.**
The [official Bale API documentation](https://docs.bale.ai/#inlinekeyboardmarkup)
defines keyboard rows without specifying physical RTL placement. Its
[message-edit API](https://docs.bale.ai/#editmessagetext) supports updating text and
inline markup. All three generated payloads are retained in
`runtime/oil-inline-20261020/actual-fleet-keyboards.json`.

Safe cleanup:

- Removed `oil_edit_items`, the exclusive text-code parser; its only runtime
  caller was the replaced oil editor. Shared aliases/resolvers remain.
- Removed `oil_confirmation_keyboard`, used only for the removed batch-confirm
  UI, and the exclusive `oil_batch_codes_keyboard` / `BATCH_PROMPT`.
- Removed `_oil_interval_text`, an unreferenced wrapper; the actual interval
  renderer remains in use.
- Removed four batch-only fields: `edit_origin_stage`, `edit_interval_return`,
  `edit_confirm_return`, `edit_confirm_page`, plus batch pending-device routing
  and batch-confirm pagination. Old persisted fields are explicitly consumed
  during restoration rather than causing dataclass construction failure.
- Removed redundant oil `edit_change` / `edit_cancel` controls. Internal steps
  have one back action; the picker has one finish action.
- Runtime diff: 190 lines removed, 89 added, net **101 fewer lines**. These are
  diff totals, including replacements, rather than a count of exclusively dead lines.

Evidence: `legacy-references-before.txt`, `removed-definitions.json`,
`runtime-import-audit.json`, and `reviewed.patch` under the run directory.
Repository imports, callback resolution, persisted sessions and all three active
bridge copies were checked. None of the bridges references the deleted symbols.
Historical release stages and their backups remain intact.

`add_items`, `apply_oil_edits`, alias normalization, generic ADD_CODES/ADD_ACTION,
the worker's legacy oil-add rejection, frozen v1 entry points and shared
GREASING/AIR_FILTER paths are retained. Removing these would require a separate
consumer/recovery audit; they were not treated as dead code.

Old oil editor sessions migrate to the new picker, retain confirmed items and
their page, discard pending changes, and invalidate old keyboard revisions.
`افزودن/ویرایش` can reopen the migrated draft without losing those items.
Legacy, forged, duplicate, expired and unauthorized callbacks cannot apply edits
or recreate the retired text/batch path. Current-version pending confirmations
survive restart and still require an explicit inline confirmation.

Final verification artifacts under `runtime/oil-inline-20261020/`:

| Artifact | Result |
|---|---|
| `audit-final.json` | 70 passed; no failures/errors/skips |
| `unified-final.json` | 25 passed; updated prior 22-test contract plus migration, edit fallback and native multi-device output coverage |
| `regression-final.json` | 200 passed; OC/GR/AF, Review, Dispatch, tokens/leases, uncertainty, revision fences and final copy; no failures/errors/skips |
| `postinstall-smoke.json` | 4 installed-code checks passed; exact canonical source hashes match the tested package |
| `restore-smoke.json` | Prior code imported from restored backup; both active database snapshots match table hashes and pass integrity |
| `actual-fleet-keyboards.json` | Read-only live-source snapshot: all 47 valid units, three pages |
| `postinstall/multi/evidence/multi-device-native.json` | Inline selection → independent real Excel/native PDF for PC1250/1600 and PC600/1800; numbers, dates and cycles verified |
| `postinstall/e2e/evidence/e2e.json` | Native manager PDF review hash, revision relationship and old dispatch fence verified |
| `evidence/pc1250-native.json` | Native 1600/1800/2000 Excel/PDF; original technical cells/styles preserved |
| `production-data-final.json` | All 25 fleet + 15 active authorization tables unchanged; both DB integrity checks pass |
| `gateway-final.json`, `profile-config-checks.json` | New running PID, connected Bale/Telegram, four profiles; all four config checks exit 0 |
| `supervisor-final.json` | Task XML and launcher hashes unchanged |
| `tested-patch.json`, `tested-patch.zip` | Exact final source hashes; ZIP CRC verified |

The 295 required tests and four installed-code checks passed. All 16 original
source/template workbook hashes and all protected business/safety code remain
unchanged. Deleted symbols have no remaining repository Python references:
`deleted-symbol-references-after.txt`.

All final suites record exact source hashes and reject source changes during
execution. Transport is fake and non-loopback networking is blocked. Outputs use
isolated temporary databases; only test evidence is copied into this run directory.
Initial sandbox-only native-PDF failures (`Excel COM 0x80070520`) were resolved
by rerunning outside the sandbox; they are recorded in `sandbox-com-limitation.json`
and the preliminary `regression-tests.json`. That preliminary 200-test run had
eight PDF/model failures and one obsolete typed-device-selection expectation.
The latter was updated to validated inline callbacks, explicit picker completion
and the new return stage; its independent-output/review assertions were retained.
The focused updated contract test passed. No test failure is hidden or skipped.
The first post-install harness launch encountered the shared Hermes `tools`
namespace before loading tests. The harness now extends `tools.__path__` exactly
as the active bridge does; all four subsequent installed-code checks passed.
This required no runtime source change.

Complete pre-install backup:
`E:/KomatsoAI/backup/oil-inline-20261020-release.zip`

SHA-256: `889dc7786ed0c1e14ad78d6fa969c17a8745c786286b8f5fa5f8357674b7db51`

It contains prior code, coherent snapshots of `data/fleet/db/fleet_ops.db` and
the **active** `reports/telegram_usage/telegram_users.db`, original source/template
workbooks and the UI snapshot. ZIP/workbook CRC and restored code/database smoke
checks passed. The first backup contained the older same-named authorization DB
under `data`; it is retained separately and superseded by the complete archive.
The active authorization snapshot also matches the prior release's snapshot.

Rollback while Gateway and work-order execution are idle:

```powershell
E:\KomatsoAI\.venv\Scripts\python.exe -B -X utf8 E:\KomatsoAI\runtime\oil-inline-20261020\release.py rollback
hermes gateway restart
```

Rollback checks installed/backup hashes and restores only the eight release
files. It converts pending oil UI edits to the prior proposal stage while
preserving confirmed items. It never restores operational databases, overwrites
later orders, or rewrites review/dispatch/receipt history.
