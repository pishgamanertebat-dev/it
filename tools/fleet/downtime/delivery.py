"""Idempotent, restart-durable Bale delivery pipeline for Monthly Fleet Downtime PDFs.

Strict safety & governance invariants:
- Resolves real Bale IDs strictly from KomatsoAI's approved user registry (telegram_users.db)
  and BALE_ADMIN_IDS; NEVER guesses IDs.
- Blocks ambiguous identities (such as surname 'امامی' matching two approved users: 397185913 and 1636934401)
  and reports all candidates clearly to the owner.
- Rejects invalid, unapproved, revoked, rejected, or non-private Bale IDs.
- Stores independent per-recipient receipts in SQLite so retries target ONLY failed recipients
  and Gateway restarts never re-broadcast already-delivered reports.
- Refuses automatic live dispatch of provisional/incomplete reports without explicit owner authorization.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Protocol

from . import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from .normalization import normalize_persian, parse_period, period_label_fa, to_persian_digits
from .repository import DowntimeRepository, utc_now

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUTH_DB = PROJECT_ROOT / "reports" / "telegram_usage" / "telegram_users.db"
DEFAULT_HERMES_ENV = Path(r"C:\Users\win-10\AppData\Local\hermes\.env")

# Owner's named target recipients for monthly Fleet Downtime PDF (Section 13)
OWNER_NAMED_RECIPIENTS: tuple[dict[str, str], ...] = (
    {"key": "project_admin", "label": "ادمین پروژه", "kind": "project_admin"},
    {"key": "pakdel", "label": "پاکدل", "kind": "surname", "match": "پاکدل"},
    {"key": "emami", "label": "امامی", "kind": "surname", "match": "امامی"},
    {"key": "hedayati", "label": "هدایتی", "kind": "surname", "match": "هدایتی"},
    {"key": "shayesteh", "label": "شایسته", "kind": "surname", "match": "شایسته"},
)


class DocumentTransport(Protocol):
    """Abstract Bale document sender (real BaleSender or deterministic simulator for testing)."""

    def check_connection(self) -> None: ...

    def document(self, recipient: str, path: Path, caption: str, *, file_name: str | None = None) -> str: ...

    def close(self) -> None: ...


class SimulatedBaleTransport:
    """In-memory deterministic transport simulator supporting per-recipient failure injection."""

    def __init__(self, *, fail_user_ids: set[str] | None = None, fail_once_user_ids: set[str] | None = None) -> None:
        self.fail_user_ids = set(fail_user_ids or ())
        self.fail_once_user_ids = set(fail_once_user_ids or ())
        self.sent_calls: list[dict[str, Any]] = []
        self._counter = 1000

    def check_connection(self) -> None:
        return None

    def document(self, recipient: str, path: Path, caption: str, *, file_name: str | None = None) -> str:
        uid = str(recipient)
        if not re.fullmatch(r"[1-9][0-9]*", uid):
            raise ValueError(f"Invalid Bale user id: {uid!r}")
        if uid in self.fail_once_user_ids:
            self.fail_once_user_ids.remove(uid)
            raise ConnectionError(f"Simulated transient failure for {uid}")
        if uid in self.fail_user_ids:
            raise ConnectionError(f"Simulated persistent failure for {uid}")
        self._counter += 1
        mid = f"SIM-{self._counter}"
        self.sent_calls.append(
            {
                "recipient": uid,
                "path": str(path),
                "file_name": file_name or Path(path).name,
                "caption": caption,
                "message_id": mid,
            }
        )
        return mid

    def close(self) -> None:
        return None


@dataclass
class ResolvedRecipient:
    recipient_key: str
    recipient_label: str
    resolution_status: str  # RESOLVED | AMBIGUOUS | NOT_FOUND | INVALID_ID | NOT_APPROVED
    bale_user_id: str | None = None
    verified_name: str | None = None
    display_name: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


def _read_bale_admin_ids(hermes_env_path: Path | str = DEFAULT_HERMES_ENV) -> list[str]:
    raw = os.environ.get("BALE_ADMIN_IDS", "")
    if not raw and Path(hermes_env_path).is_file():
        for line in Path(hermes_env_path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("BALE_ADMIN_IDS="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    return [x.strip() for x in raw.split(",") if x.strip()]


def _load_bale_users(auth_db_path: Path | str = DEFAULT_AUTH_DB) -> list[dict[str, Any]]:
    p = Path(auth_db_path)
    if not p.is_file():
        raise FileNotFoundError(f"Bale user registry DB not found: {p}")
    with closing(sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        roles_map: dict[str, list[str]] = {}
        try:
            for ur in conn.execute(
                "SELECT user_id, role FROM auth_user_roles WHERE platform='bale' AND active=1"
            ).fetchall():
                roles_map.setdefault(str(ur["user_id"]), []).append(str(ur["role"]))
        except sqlite3.Error:
            pass
        users = []
        for r in conn.execute(
            "SELECT platform, user_id, chat_id, display_name, verified_name, registration_status "
            "FROM channel_users WHERE platform='bale' ORDER BY CAST(user_id AS INTEGER)"
        ).fetchall():
            d = dict(r)
            d["roles"] = sorted(roles_map.get(str(d["user_id"]), []))
            users.append(d)
        return users


def validate_bale_recipient_id(
    user_id: str,
    *,
    auth_db_path: Path | str = DEFAULT_AUTH_DB,
    label: str | None = None,
    recipient_key: str | None = None,
) -> ResolvedRecipient:
    """Strictly verify a concrete Bale user_id against approved private DM identities."""
    uid = str(user_id or "").strip()
    key = recipient_key or f"uid:{uid}"
    lbl = label or uid
    if not re.fullmatch(r"[1-9][0-9]*", uid):
        return ResolvedRecipient(
            recipient_key=key,
            recipient_label=lbl,
            resolution_status="INVALID_ID",
            bale_user_id=None,
            note=f"شناسه بله «{uid}» فرمت عددی معتبر ندارد.",
        )
    users = {str(u["user_id"]): u for u in _load_bale_users(auth_db_path)}
    u = users.get(uid)
    if not u:
        return ResolvedRecipient(
            recipient_key=key,
            recipient_label=lbl,
            resolution_status="NOT_FOUND",
            bale_user_id=None,
            note=f"شناسه بله {uid} در Registry کاربران ثبت نشده است.",
        )
    if u.get("registration_status") != "approved" or str(u.get("chat_id")) != uid:
        return ResolvedRecipient(
            recipient_key=key,
            recipient_label=lbl,
            resolution_status="NOT_APPROVED",
            bale_user_id=None,
            verified_name=u.get("verified_name"),
            display_name=u.get("display_name"),
            note=f"شناسه {uid} وضعیت تأییدشده خصوصی (approved private DM) ندارد (status={u.get('registration_status')}).",
        )
    return ResolvedRecipient(
        recipient_key=key,
        recipient_label=lbl,
        resolution_status="RESOLVED",
        bale_user_id=uid,
        verified_name=u.get("verified_name"),
        display_name=u.get("display_name"),
        note="شناسه معتبر و تأییدشده در Registry بله.",
    )


def resolve_owner_recipients(
    *,
    auth_db_path: Path | str = DEFAULT_AUTH_DB,
    hermes_env_path: Path | str = DEFAULT_HERMES_ENV,
    explicit_id_overrides: dict[str, str] | None = None,
) -> list[ResolvedRecipient]:
    """Resolve the 5 owner-requested recipients strictly from Registry without guessing.

    Any ambiguous match (e.g., 'امامی' matching two approved users) is flagged AMBIGUOUS
    and delivery for that recipient is blocked until the owner explicitly confirms the ID.
    """
    overrides = explicit_id_overrides or {}
    all_users = _load_bale_users(auth_db_path)
    approved_private = [
        u for u in all_users
        if u.get("registration_status") == "approved"
        and str(u.get("chat_id")) == str(u.get("user_id"))
        and re.fullmatch(r"[1-9][0-9]*", str(u.get("user_id") or ""))
    ]
    admin_ids = _read_bale_admin_ids(hermes_env_path)

    results: list[ResolvedRecipient] = []
    for spec in OWNER_NAMED_RECIPIENTS:
        rkey = spec["key"]
        rlbl = spec["label"]

        if rkey in overrides:
            results.append(
                validate_bale_recipient_id(
                    overrides[rkey],
                    auth_db_path=auth_db_path,
                    label=rlbl,
                    recipient_key=rkey,
                )
            )
            continue

        if spec["kind"] == "project_admin":
            matched = [u for u in approved_private if str(u["user_id"]) in admin_ids]
            if len(matched) == 1:
                u = matched[0]
                results.append(
                    ResolvedRecipient(
                        recipient_key=rkey,
                        recipient_label=rlbl,
                        resolution_status="RESOLVED",
                        bale_user_id=str(u["user_id"]),
                        verified_name=u.get("verified_name"),
                        display_name=u.get("display_name"),
                        note="تطبیق یکتا از BALE_ADMIN_IDS و وضعیت approved در Registry بله.",
                    )
                )
            elif len(matched) > 1:
                results.append(
                    ResolvedRecipient(
                        recipient_key=rkey,
                        recipient_label=rlbl,
                        resolution_status="AMBIGUOUS",
                        candidates=[
                            {
                                "user_id": str(u["user_id"]),
                                "verified_name": u.get("verified_name"),
                                "display_name": u.get("display_name"),
                                "roles": u.get("roles", []),
                            }
                            for u in matched
                        ],
                        note="چند شناسه ادمین در BALE_ADMIN_IDS یافت شد؛ نیازمند تعیین صریح توسط مالک.",
                    )
                )
            else:
                results.append(
                    ResolvedRecipient(
                        recipient_key=rkey,
                        recipient_label=rlbl,
                        resolution_status="NOT_FOUND",
                        note="شناسه تأییدشده برای ادمین پروژه در BALE_ADMIN_IDS یافت نشد.",
                    )
                )
            continue

        # Surname / verified name lookup across approved private Bale identities
        target_norm = normalize_persian(spec["match"])
        matched = [
            u for u in approved_private
            if target_norm in normalize_persian(u.get("verified_name") or "")
            or target_norm in normalize_persian(u.get("display_name") or "")
        ]
        if len(matched) == 1:
            u = matched[0]
            results.append(
                ResolvedRecipient(
                    recipient_key=rkey,
                    recipient_label=rlbl,
                    resolution_status="RESOLVED",
                    bale_user_id=str(u["user_id"]),
                    verified_name=u.get("verified_name"),
                    display_name=u.get("display_name"),
                    note="تطابق یکتا با کاربر تأییدشده در Registry بله.",
                )
            )
        elif len(matched) > 1:
            results.append(
                ResolvedRecipient(
                    recipient_key=rkey,
                    recipient_label=rlbl,
                    resolution_status="AMBIGUOUS",
                    candidates=[
                        {
                            "user_id": str(u["user_id"]),
                            "verified_name": u.get("verified_name"),
                            "display_name": u.get("display_name"),
                            "roles": u.get("roles", []),
                        }
                        for u in matched
                    ],
                    note=(
                        f"بیش از یک کاربر تأییدشده با نام «{rlbl}» در Registry بله وجود دارد؛ "
                        f"جهت جلوگیری از ارسال به فرد اشتباه، ارسال برای این گیرنده متوقف و جهت تصمیم مالک گزارش می‌شود."
                    ),
                )
            )
        else:
            results.append(
                ResolvedRecipient(
                    recipient_key=rkey,
                    recipient_label=rlbl,
                    resolution_status="NOT_FOUND",
                    note=f"کاربر تأییدشده‌ای با نام «{rlbl}» در Registry بله یافت نشد.",
                )
            )

    return results


def build_bale_caption(
    period: str,
    *,
    version_number: int = 1,
    is_revision: bool = False,
    is_provisional: bool = False,
) -> str:
    p_fa = period_label_fa(period)
    if is_revision or version_number > 1:
        header = f"گزارش اصلاحی ماهانه عملکرد و توقفات ماشین‌آلات — {p_fa} (نسخه {to_persian_digits(version_number)})"
    else:
        header = f"گزارش ماهانه عملکرد و توقفات ماشین‌آلات — {p_fa}"

    body = "شامل تحلیل عملکرد ناوگان، علل توقف، مقایسه دستگاه‌ها و کارنامه اختصاصی هر دستگاه."
    lines = [header, "", body]
    if is_provisional:
        lines.extend(["", "توجه: این نسخه شامل هشدار کیفیت داده / روزهای فاقد شیت در فایل منبع است."])
    lines.extend(["", "واحد نگهداری و تعمیرات"])
    return "\n".join(lines)


class DowntimeDeliveryService:
    """Idempotent per-recipient monthly PDF dispatcher with durable SQLite receipt ledger."""

    def __init__(
        self,
        *,
        runtime_dir: Path | str = DEFAULT_RUNTIME_DIR,
        db_path: Path | str | None = None,
        auth_db_path: Path | str = DEFAULT_AUTH_DB,
        hermes_env_path: Path | str = DEFAULT_HERMES_ENV,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.repo = DowntimeRepository(Path(db_path) if db_path else self.runtime_dir / "downtime.sqlite3")
        self.auth_db_path = Path(auth_db_path)
        self.hermes_env_path = Path(hermes_env_path)

    def _already_closed_recipients(
        self,
        *,
        period: str,
        version_id: int,
        pdf_sha256: str,
        is_dry_run: bool,
    ) -> dict[str, dict[str, Any]]:
        """Look up recipients already SENT or UNCERTAIN for this exact PDF artifact across all runs."""
        closed: dict[str, dict[str, Any]] = {}
        with closing(self.repo._connect(write=False)) as conn:
            rows = conn.execute(
                """
                SELECT r.recipient_key, r.bale_user_id, r.delivery_status, r.message_id, r.sent_at
                FROM delivery_receipts r
                JOIN delivery_runs run ON run.run_id = r.run_id
                WHERE r.period = ? AND r.version_id = ? AND r.pdf_sha256 = ?
                  AND run.is_dry_run = ?
                  AND r.delivery_status IN ('SENT', 'UNCERTAIN')
                ORDER BY r.id ASC
                """,
                (period, version_id, pdf_sha256, 1 if is_dry_run else 0),
            ).fetchall()
            for row in rows:
                d = dict(row)
                closed[d["recipient_key"]] = d
                if d.get("bale_user_id"):
                    closed[f"uid:{d['bale_user_id']}"] = d
        return closed

    def _prior_period_sent_versions(self, *, period: str, is_dry_run: bool) -> list[int]:
        with closing(self.repo._connect(write=False)) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT r.version_id
                FROM delivery_receipts r
                JOIN delivery_runs run ON run.run_id = r.run_id
                WHERE r.period = ? AND run.is_dry_run = ? AND r.delivery_status = 'SENT'
                """,
                (period, 1 if is_dry_run else 0),
            ).fetchall()
            return [int(r[0]) for r in rows]

    def deliver_monthly_report(
        self,
        period: str,
        *,
        recipients: list[ResolvedRecipient] | None = None,
        transport: DocumentTransport | None = None,
        dry_run: bool = True,
        allow_provisional: bool = False,
        allow_revision: bool = False,
        approved_by: str | None = None,
        explicit_id_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Deliver the cached monthly PDF report idempotently with independent per-recipient receipts.

        Safety gates:
        - Live sends (dry_run=False) REQUIRE explicit `approved_by` owner confirmation AND an explicit
          live transport (never silently calls live Bale API during tests or automated candidate runs).
        - Provisional / discrepancy-flagged reports cannot be auto-published unless `allow_provisional=True`
          and `approved_by` is set.
        - Revised versions of an already-sent period require `allow_revision=True` (explicit revision policy).
        """
        norm_p = parse_period(period)
        self.repo.ensure_schema()
        ver = self.repo.get_active_version(norm_p)
        if not ver:
            raise ValueError(f"No active imported dataset for period {norm_p}")
        vid = int(ver["version_id"])
        vnum = int(ver["version_number"])

        artifact = self.repo.get_latest_report_artifact(norm_p, version_id=vid)
        if not artifact or not Path(artifact["pdf_path"]).is_file():
            raise FileNotFoundError(f"Prepared PDF report not found for period {norm_p} (version {vnum})")
        pdf_path = Path(artifact["pdf_path"])
        actual_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if actual_sha != artifact["pdf_sha256"]:
            raise RuntimeError("Prepared PDF SHA-256 mismatch; refusing delivery")

        is_provisional = bool(artifact["is_provisional"]) or int(ver["discrepancy_row_count"]) > 0 or int(ver["missing_days_count"]) > 0
        if not dry_run and not approved_by:
            return {
                "status": "BLOCKED_OWNER_APPROVAL_REQUIRED",
                "period": norm_p,
                "version_number": vnum,
                "message": "ارسال واقعی گزارش بدون تأیید صریح مالک مجاز نیست.",
            }
        if is_provisional and not allow_provisional:
            return {
                "status": "BLOCKED_PROVISIONAL_DATA",
                "period": norm_p,
                "version_number": vnum,
                "data_quality_status": ver["data_quality_status"],
                "discrepancy_row_count": ver["discrepancy_row_count"],
                "missing_days_count": ver["missing_days_count"],
                "message": (
                    "گزارش دارای مغایرت حل‌نشده یا روزهای فاقد شیت است؛ انتشار خودکار نهایی متوقف شد تا وضعیت داده "
                    "و اجازه انتشار توسط مالک مشخص شود."
                ),
            }

        # Check revision policy: if an earlier version of this period was already sent, require explicit revision flag
        prior_sent = self._prior_period_sent_versions(period=norm_p, is_dry_run=dry_run)
        is_revision = any(pv != vid for pv in prior_sent) or vnum > 1
        if any(pv != vid for pv in prior_sent) and not allow_revision:
            return {
                "status": "BLOCKED_REVISION_POLICY_REQUIRED",
                "period": norm_p,
                "version_number": vnum,
                "prior_sent_versions": prior_sent,
                "message": "برای ارسال نسخه اصلاحی این ماه، تأیید صریح سیاست ارسال گزارش اصلاحی الزامی است.",
            }

        resolved_list = (
            recipients
            if recipients is not None
            else resolve_owner_recipients(
                auth_db_path=self.auth_db_path,
                hermes_env_path=self.hermes_env_path,
                explicit_id_overrides=explicit_id_overrides,
            )
        )
        caption = build_bale_caption(
            norm_p,
            version_number=vnum,
            is_revision=is_revision,
            is_provisional=is_provisional,
        )

        # Create durable delivery_run row
        now = utc_now()
        kind = "SIMULATED" if dry_run else ("REVISION" if is_revision else "INITIAL")
        with closing(self.repo._connect(write=True)) as conn, conn:
            cur = conn.execute(
                """
                INSERT INTO delivery_runs (
                    period, version_id, artifact_id, pdf_sha256, delivery_kind,
                    is_dry_run, approved_by, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
                """,
                (
                    norm_p,
                    vid,
                    artifact["artifact_id"],
                    actual_sha,
                    kind,
                    1 if dry_run else 0,
                    approved_by,
                    now,
                    now,
                ),
            )
            run_id = int(cur.lastrowid)

        closed = self._already_closed_recipients(
            period=norm_p,
            version_id=vid,
            pdf_sha256=actual_sha,
            is_dry_run=dry_run,
        )

        sender: DocumentTransport | None = transport
        owns_sender = False
        if sender is None:
            if dry_run:
                sender = SimulatedBaleTransport()
                owns_sender = True
            else:
                from tools.scheduler.tasks import BaleSender

                sender = BaleSender()
                owns_sender = True

        receipt_records: list[dict[str, Any]] = []
        sent_cnt = skipped_cnt = failed_cnt = blocked_cnt = 0
        connected = False

        try:
            for rec in resolved_list:
                # Re-verify approved status if resolved to a user_id
                if rec.resolution_status == "RESOLVED" and rec.bale_user_id:
                    recheck = validate_bale_recipient_id(
                        rec.bale_user_id,
                        auth_db_path=self.auth_db_path,
                        label=rec.recipient_label,
                        recipient_key=rec.recipient_key,
                    )
                    if recheck.resolution_status != "RESOLVED":
                        rec = recheck

                if rec.resolution_status == "AMBIGUOUS":
                    st = "BLOCKED_AMBIGUOUS"
                    blocked_cnt += 1
                    err = rec.note
                    mid = None
                elif rec.resolution_status != "RESOLVED" or not rec.bale_user_id:
                    st = "BLOCKED_INVALID"
                    blocked_cnt += 1
                    err = rec.note
                    mid = None
                elif rec.recipient_key in closed or f"uid:{rec.bale_user_id}" in closed:
                    prev = closed.get(rec.recipient_key) or closed[f"uid:{rec.bale_user_id}"]
                    st = "SKIPPED_DUPLICATE"
                    skipped_cnt += 1
                    err = f"قبلاً ارسال شده است (message_id={prev.get('message_id')})"
                    mid = prev.get("message_id")
                else:
                    # Attempt independent send for this recipient
                    try:
                        if not connected:
                            sender.check_connection()
                            connected = True
                        mid = sender.document(
                            rec.bale_user_id,
                            pdf_path,
                            caption,
                            file_name=pdf_path.name,
                        )
                        if not mid:
                            raise RuntimeError("Missing message_id receipt from Bale transport")
                        st = "SENT"
                        sent_cnt += 1
                        err = ""
                    except Exception as exc:
                        st = "FAILED"
                        failed_cnt += 1
                        err = str(exc)
                        mid = None

                ts = utc_now()
                with closing(self.repo._connect(write=True)) as conn, conn:
                    conn.execute(
                        """
                        INSERT INTO delivery_receipts (
                            run_id, period, version_id, pdf_sha256,
                            recipient_key, recipient_name, bale_user_id,
                            resolution_status, delivery_status, message_id,
                            attempt_count, last_error, sent_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            norm_p,
                            vid,
                            actual_sha,
                            rec.recipient_key,
                            rec.verified_name or rec.recipient_label,
                            rec.bale_user_id,
                            rec.resolution_status,
                            st,
                            mid,
                            1 if st in ("SENT", "FAILED") else 0,
                            err,
                            ts if st == "SENT" else None,
                            ts,
                        ),
                    )

                receipt_records.append(
                    {
                        "recipient_key": rec.recipient_key,
                        "recipient_label": rec.recipient_label,
                        "verified_name": rec.verified_name,
                        "bale_user_id": rec.bale_user_id,
                        "resolution_status": rec.resolution_status,
                        "delivery_status": st,
                        "message_id": mid,
                        "candidates": rec.candidates,
                        "error": err,
                    }
                )
        finally:
            if owns_sender and sender is not None:
                sender.close()

        if failed_cnt > 0:
            run_status = "PARTIAL_FAILED" if (sent_cnt + skipped_cnt) > 0 else "FAILED"
        elif blocked_cnt > 0:
            run_status = "COMPLETED_WITH_BLOCKED_RECIPIENTS"
        elif sent_cnt == 0 and skipped_cnt > 0:
            run_status = "ALL_ALREADY_SENT_IDEMPOTENT"
        else:
            run_status = "SUCCEEDED"

        with closing(self.repo._connect(write=True)) as conn, conn:
            conn.execute(
                "UPDATE delivery_runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (run_status, utc_now(), run_id),
            )

        return {
            "status": run_status,
            "run_id": run_id,
            "period": norm_p,
            "version_number": vnum,
            "delivery_kind": kind,
            "is_dry_run": dry_run,
            "pdf_path": str(pdf_path),
            "pdf_sha256": actual_sha,
            "caption": caption,
            "counts": {
                "total": len(receipt_records),
                "sent": sent_cnt,
                "skipped_duplicate": skipped_cnt,
                "failed": failed_cnt,
                "blocked": blocked_cnt,
            },
            "receipts": receipt_records,
        }

    def list_delivery_history(self, period: str | None = None) -> list[dict[str, Any]]:
        if not self.repo.db_path.exists():
            return []
        with closing(self.repo._connect(write=False)) as conn:
            if period:
                runs = conn.execute(
                    "SELECT * FROM delivery_runs WHERE period = ? ORDER BY run_id DESC",
                    (parse_period(period),),
                ).fetchall()
            else:
                runs = conn.execute("SELECT * FROM delivery_runs ORDER BY run_id DESC").fetchall()
            out = []
            for run in runs:
                d = dict(run)
                d["receipts"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM delivery_receipts WHERE run_id = ? ORDER BY id ASC",
                        (d["run_id"],),
                    ).fetchall()
                ]
                out.append(d)
            return out
