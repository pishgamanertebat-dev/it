"""Read-only fleet device identity resolution backed by KomatsoAI's official registry.

Uses E:\\KomatsoAI\\data\\fleet\\db\\fleet_ops.db (machines + machine_aliases) and
owner-verified input aliases from tools.fleet.equipment_codes.
Never merges ambiguous raw numeric codes (such as 601 -> EX601 vs WA601, or 321 -> EX321 vs HD321)
and never guesses an identity when the source workbook omits the machine type.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from tools.fleet.equipment_codes import EXCAVATOR_ALIASES
from .normalization import normalize_persian, normalize_raw_code

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FLEET_DB = PROJECT_ROOT / "data" / "fleet" / "db" / "fleet_ops.db"
DEFAULT_MAINT_LAYOUT = PROJECT_ROOT / "tools" / "fleet" / "maintenance_daily" / "layout.json"

GROUP_AMBIGUOUS = "مبهم (نیازمند تأیید هویت)"
GROUP_UNREGISTERED = "ثبت‌نشده در Registry"


def infer_device_group(canonical_code: str, type_hint: str | None) -> str:
    """Map a confirmed registry machine to a clean Persian fleet group."""
    code = (canonical_code or "").upper()
    hint = normalize_persian(type_hint or "")
    if code.startswith("EX") or "بیل" in hint:
        return "بیل مکانیکی"
    if code.startswith(("HD", "U")) or "دامپ" in hint or "تراک" in hint:
        return "دامپتراک"
    if code.startswith(("WA", "W")) or "لودر" in hint:
        return "لودر"
    if code.startswith("D") and not code.startswith("DG") or "بلدوزر" in hint:
        return "بلدوزر"
    if code.startswith(("S", "TA", "TR", "DG")) or any(w in hint for w in ("کامیون", "تانکر", "خاور", "ژنراتور")):
        return "پشتیبانی و کامیون"
    return "سایر ماشین‌آلات ثبت‌شده"


def clean_device_display_name(canonical_code: str, type_hint: str | None) -> str:
    """Build a crisp Persian display title for a confirmed registry device."""
    hint = normalize_persian(type_hint or "")
    hint = re.sub(r"[_]+", " ", hint)
    hint = re.sub(r"\s+", " ", hint).strip()
    if hint:
        return f"{hint} ({canonical_code})"
    return canonical_code


@dataclass(frozen=True)
class RegistryMachine:
    machine_id: int
    canonical_code: str
    machine_type_hint: str
    model_key: str | None
    identity_status: str
    device_group: str
    display_name: str
    numeric_suffix: str


@dataclass(frozen=True)
class DeviceResolution:
    raw_code: str
    canonical_device_key: str
    identity_status: str  # RESOLVED | AMBIGUOUS | UNREGISTERED
    registry_machine_id: int | None
    registry_canonical_code: str | None
    device_name: str
    device_type: str | None
    device_group: str
    ambiguous_candidates: tuple[dict[str, Any], ...] = ()
    resolution_note: str = ""


class FleetDeviceRegistry:
    """Read-only fleet identity registry loaded from fleet_ops.db."""

    def __init__(
        self,
        fleet_db_path: Path | str = DEFAULT_FLEET_DB,
        maint_layout_path: Path | str | None = DEFAULT_MAINT_LAYOUT,
    ) -> None:
        self.fleet_db_path = Path(fleet_db_path)
        self.maint_layout_path = Path(maint_layout_path) if maint_layout_path else None
        self.machines_by_id: dict[int, RegistryMachine] = {}
        self.by_canonical: dict[str, RegistryMachine] = {}
        self.by_numeric: dict[str, list[RegistryMachine]] = defaultdict(list)
        self.by_alias: dict[str, list[RegistryMachine]] = defaultdict(list)
        self.layout_hints: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.fleet_db_path.exists():
            raise FileNotFoundError(f"Fleet registry DB not found: {self.fleet_db_path}")
        uri = self.fleet_db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT id, canonical_code, machine_type_hint, model_key, identity_status "
                "FROM machines ORDER BY id"
            ).fetchall()
            for r in rows:
                canon = normalize_raw_code(r["canonical_code"]).upper()
                hint = normalize_persian(r["machine_type_hint"] or "")
                num_m = re.search(r"(\d+)$", canon)
                suffix = num_m.group(1) if num_m else ""
                group = infer_device_group(canon, hint)
                m = RegistryMachine(
                    machine_id=int(r["id"]),
                    canonical_code=canon,
                    machine_type_hint=hint,
                    model_key=r["model_key"],
                    identity_status=str(r["identity_status"] or "PROVISIONAL"),
                    device_group=group,
                    display_name=clean_device_display_name(canon, hint),
                    numeric_suffix=suffix,
                )
                self.machines_by_id[m.machine_id] = m
                self.by_canonical[canon] = m
                if suffix and not re.fullmatch(r"(?:S|TA|TR|U|DG)\d+", canon):
                    # Prefix+digits machines (EX1251, HD706, W471, D151, EX601, WA601, EX321, HD321)
                    self.by_numeric[suffix].append(m)

            alias_rows = conn.execute(
                "SELECT machine_id, alias_code FROM machine_aliases ORDER BY id"
            ).fetchall()
            for ar in alias_rows:
                m = self.machines_by_id.get(int(ar["machine_id"]))
                ac = normalize_raw_code(ar["alias_code"]).upper()
                if m and ac and m not in self.by_alias[ac]:
                    self.by_alias[ac].append(m)

        if self.maint_layout_path and self.maint_layout_path.exists():
            try:
                raw = json.loads(self.maint_layout_path.read_text(encoding="utf-8"))
                for sheet_title, rule in (raw.get("sheets") or {}).items():
                    if not rule.get("exclude"):
                        self.layout_hints[normalize_raw_code(sheet_title).upper()] = normalize_persian(
                            rule.get("device") or sheet_title
                        )
            except Exception:
                self.layout_hints = {}

    def resolve_workbook_code(
        self,
        raw_code: Any,
        *,
        same_day_dayshift_slot: int = 1,
        same_day_dayshift_count: int = 1,
    ) -> DeviceResolution:
        """Resolve a raw code from a downtime workbook row strictly without guessing.

        If a raw numeric code matches >1 machine in fleet_ops.db (e.g. 601 -> EX601 & WA601,
        or 321 -> EX321 & HD321), it is flagged AMBIGUOUS and never silently mapped.
        If an ambiguous code appears multiple times in the primary day shift on the same day
        (like 601 appearing in row 13 and row 46/48), separate track keys (`AMBIG:601#1`,
        `AMBIG:601#2`) preserve both machines distinctly without fabricating confirmation.
        """
        code = normalize_raw_code(raw_code)
        if not code:
            raise ValueError("Empty raw device code")
        upper = code.upper()

        # 1. Exact canonical code match (e.g., S1, TA1, EX1251, HD706, WA601)
        if upper in self.by_canonical:
            m = self.by_canonical[upper]
            return DeviceResolution(
                raw_code=code,
                canonical_device_key=m.canonical_code,
                identity_status="RESOLVED",
                registry_machine_id=m.machine_id,
                registry_canonical_code=m.canonical_code,
                device_name=m.display_name,
                device_type=m.machine_type_hint or None,
                device_group=m.device_group,
                resolution_note="تطابق مستقیم با کد مرجع Registry",
            )

        # 2. Pure numeric code: check ALL canonical machines sharing that numeric suffix
        if re.fullmatch(r"\d+", upper):
            candidates = list(self.by_numeric.get(upper, ()))
            if len(candidates) == 1:
                m = candidates[0]
                return DeviceResolution(
                    raw_code=code,
                    canonical_device_key=m.canonical_code,
                    identity_status="RESOLVED",
                    registry_machine_id=m.machine_id,
                    registry_canonical_code=m.canonical_code,
                    device_name=m.display_name,
                    device_type=m.machine_type_hint or None,
                    device_group=m.device_group,
                    resolution_note="تطابق یکتا با Registry ناوگان",
                )
            if len(candidates) > 1:
                cand_info = tuple(
                    {
                        "machine_id": m.machine_id,
                        "canonical_code": m.canonical_code,
                        "device_type": m.machine_type_hint,
                        "device_group": m.device_group,
                    }
                    for m in candidates
                )
                if same_day_dayshift_count > 1:
                    track_key = f"AMBIG:{upper}#{same_day_dayshift_slot}"
                    slot_fa = "ردیف اول روزانه" if same_day_dayshift_slot == 1 else f"ردیف {same_day_dayshift_slot} روزانه"
                    cand_codes = " / ".join(m.canonical_code for m in candidates)
                    return DeviceResolution(
                        raw_code=code,
                        canonical_device_key=track_key,
                        identity_status="AMBIGUOUS",
                        registry_machine_id=None,
                        registry_canonical_code=None,
                        device_name=f"کد مبهم {code} ({slot_fa})",
                        device_type=None,
                        device_group=GROUP_AMBIGUOUS,
                        ambiguous_candidates=cand_info,
                        resolution_note=(
                            f"کد خام {code} در Registry با چند دستگاه ({cand_codes}) منطبق است و در فایل منبع "
                            f"روزانه {same_day_dayshift_count} بار بدون ستون نوع دستگاه ثبت شده؛ جهت جلوگیری از "
                            f"ادغام نادرست، {slot_fa} جداگانه نگهداری و علامت‌گذاری شد."
                        ),
                    )
                cand_codes = " / ".join(m.canonical_code for m in candidates)
                return DeviceResolution(
                    raw_code=code,
                    canonical_device_key=f"AMBIG:{upper}",
                    identity_status="AMBIGUOUS",
                    registry_machine_id=None,
                    registry_canonical_code=None,
                    device_name=f"کد مبهم {code} (نیازمند تأیید هویت)",
                    device_type=None,
                    device_group=GROUP_AMBIGUOUS,
                    ambiguous_candidates=cand_info,
                    resolution_note=(
                        f"کد خام {code} در Registry با چند دستگاه ({cand_codes}) مشترک است و بدون نوع دستگاه "
                        f"در منبع ثبت شده؛ بدون نگاشت حدسی حفظ شد."
                    ),
                )

        # 3. Non-numeric or unregistered numeric alias check (only if unambiguous in machine_aliases)
        if upper in self.by_alias and not re.fullmatch(r"\d+", upper):
            alias_matches = self.by_alias[upper]
            if len(alias_matches) == 1:
                m = alias_matches[0]
                return DeviceResolution(
                    raw_code=code,
                    canonical_device_key=m.canonical_code,
                    identity_status="RESOLVED",
                    registry_machine_id=m.machine_id,
                    registry_canonical_code=m.canonical_code,
                    device_name=m.display_name,
                    device_type=m.machine_type_hint or None,
                    device_group=m.device_group,
                    resolution_note="تطابق یکتا از طریق جدول Alias در Registry",
                )

        # 4. Unregistered in fleet_ops.db — keep transparently without guessing!
        return DeviceResolution(
            raw_code=code,
            canonical_device_key=f"UNREG:{upper}",
            identity_status="UNREGISTERED",
            registry_machine_id=None,
            registry_canonical_code=None,
            device_name=f"دستگاه {code} (ثبت‌نشده در Registry)",
            device_type=None,
            device_group=GROUP_UNREGISTERED,
            resolution_note="کد دستگاه در جدول machines پایگاه داده fleet_ops.db ثبت نشده است.",
        )

    def resolve_user_query_device(self, user_input: str) -> dict[str, Any]:
        """Resolve a user-provided device reference (e.g., '706', 'بیل 1252', '1002', '601', 'لودر 601', '717').

        Supports Persian/English digits, owner-verified excavator aliases (1001-1006 -> EX1251-EX1256),
        type qualifiers ('بیل', 'لودر', 'دامپتراک', 'بلدوزر'), and returns explicit disambiguation when ambiguous.
        """
        raw = normalize_persian(user_input)
        if not raw:
            return {"status": "not_found", "query": user_input, "candidates": []}

        # Extract optional Persian type hint from user query
        wants_excavator = any(w in raw for w in ("بیل", "بیلمکانیکی", "excavator", "ex"))
        wants_loader = any(w in raw for w in ("لودر", "loader", "wa"))
        wants_dump = any(w in raw for w in ("دامپ", "تراک", "دامپتراک", "dump", "hd"))
        wants_dozer = any(w in raw for w in ("بلدوزر", "بولدوزر", "dozer"))

        # Check explicit grader / non-numeric Persian name
        if "گریدر" in raw:
            return {
                "status": "unregistered",
                "query": user_input,
                "canonical_device_key": "UNREG:گریدر",
                "raw_code": "گریدر",
            }

        # Extract core token (e.g. EX1252, HD706, WA601, TA1, S1, or digits 706 / 1252 / 601)
        token_m = re.search(r"\b([A-Za-z]{1,3}\d{1,4}|\d{3,4})\b", raw)
        token = token_m.group(1).upper() if token_m else normalize_raw_code(raw).upper()

        # Owner-verified excavator input aliases: 1001..1006 -> EX1251..EX1256
        bare_digits = re.sub(r"^(?:EX)", "", token)
        if bare_digits in EXCAVATOR_ALIASES:
            mapped = EXCAVATOR_ALIASES[bare_digits]
            m = self.by_canonical.get(mapped)
            if m:
                return {
                    "status": "resolved",
                    "query": user_input,
                    "canonical_device_key": m.canonical_code,
                    "machine": m,
                    "alias_applied": f"{bare_digits} -> {mapped}",
                }

        # Direct canonical match
        if token in self.by_canonical:
            m = self.by_canonical[token]
            return {
                "status": "resolved",
                "query": user_input,
                "canonical_device_key": m.canonical_code,
                "machine": m,
            }

        # Numeric suffix lookup
        if re.fullmatch(r"\d+", token):
            candidates = list(self.by_numeric.get(token, ()))
            if len(candidates) == 1:
                m = candidates[0]
                return {
                    "status": "resolved",
                    "query": user_input,
                    "canonical_device_key": m.canonical_code,
                    "machine": m,
                }
            if len(candidates) > 1:
                # Filter by explicit user-provided type qualifier if present
                filtered = []
                for m in candidates:
                    if wants_excavator and m.canonical_code.startswith("EX"):
                        filtered.append(m)
                    elif wants_loader and m.canonical_code.startswith(("WA", "W")):
                        filtered.append(m)
                    elif wants_dump and m.canonical_code.startswith("HD"):
                        filtered.append(m)
                    elif wants_dozer and m.canonical_code.startswith("D"):
                        filtered.append(m)
                if len(filtered) == 1:
                    m = filtered[0]
                    return {
                        "status": "resolved_from_type_qualifier",
                        "query": user_input,
                        "canonical_device_key": m.canonical_code,
                        "raw_code": token,
                        "machine": m,
                        "note": (
                            f"در فایل منبع توقفات، کد {token} بدون ستون نوع دستگاه ثبت شده و به صورت مبهم "
                            f"نگهداری می‌شود؛ بنابراین انتساب قطعی آن به {m.canonical_code} نیازمند تأیید مالک است."
                        ),
                    }
                return {
                    "status": "ambiguous",
                    "query": user_input,
                    "raw_code": token,
                    "candidates": [
                        {
                            "canonical_code": m.canonical_code,
                            "device_name": m.display_name,
                            "device_group": m.device_group,
                        }
                        for m in candidates
                    ],
                }
            # Numeric code not in fleet_ops.db (e.g., 717..721)
            return {
                "status": "unregistered",
                "query": user_input,
                "canonical_device_key": f"UNREG:{token}",
                "raw_code": token,
            }

        return {"status": "not_found", "query": user_input, "candidates": []}
