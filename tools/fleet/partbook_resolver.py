"""Deterministic fleet aliases and source bindings. Never fuzzy-match model numbers.

This registry concerns Part Book routing only; it neither mutates the operational
fleet DB nor changes Shop Manual identities. SQLite remains authoritative for
book model, PDF and serial coverage.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

REGISTRY = Path(__file__).with_name("partbook_fleet_registry.json")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك", "01234567890123456789یک")


def normalize_alias(value):
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).translate(DIGITS).upper()
    text = re.sub(r"[\u200c\u200d\u200e\u200f]", " ", text)
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = re.sub(r"(?<=\d)\s*خط\s*(?=\d)", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Approved colloquial equipment wrappers, never arbitrary sentence removal.
    text = re.sub(r"^(?:(?:بیل مکانیکی|بیل زنجیری|بیل|دستگاه|کوماتسو|KOMATSU)\s+)+", "", text)
    text = re.sub(r"^(PC|HD|WA|R)\s+(?=\d)", r"\1", text)
    text = re.sub(r"(?<=\d)\s+R$", "R", text)
    return text


def load_registry():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported Part Book fleet registry")
    ids = [asset["asset_id"] for asset in registry["assets"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate fleet asset ID")
    return registry


def resolve_model(value, registry=None):
    registry = load_registry() if registry is None else registry
    key = normalize_alias(value)
    matches = []
    for asset in registry["assets"]:
        aliases = asset["aliases"] + list(asset.get("source_aliases", {}))
        if key and key in {normalize_alias(alias) for alias in aliases}:
            matches.append(asset)
    result = {"requested_model": value, "normalized_alias": key}
    if len(matches) > 1:
        return result | {"resolution_status": "AMBIGUOUS", "fleet_asset": None,
                         "choices": [{"asset_id": a["asset_id"], "display_model": a["display_model"]} for a in matches],
                         "question": "کدام دستگاه یا نسخه مدنظر است؟ " + "، ".join(a["display_model"] for a in matches)}
    if not matches:
        return result | {"resolution_status": "UNKNOWN_MODEL", "fleet_asset": None,
                         "error": "Model is not an approved fleet alias; no source was searched."}
    asset = matches[0]
    selected_source = next((book for alias, book in asset.get("source_aliases", {}).items()
                            if normalize_alias(alias) == key), None)
    return result | {"resolution_status": "RESOLVED", "fleet_asset": asset,
                     "matched_identity": "partbook_source" if selected_source else "fleet_alias",
                     "source_alias_book_id": selected_source}


def select_books(con, resolution, explicit_book=None, registry=None):
    registry = load_registry() if registry is None else registry
    asset = resolution["fleet_asset"]
    allowed = asset["book_ids"]
    if explicit_book and explicit_book not in allowed:
        raise ValueError("Requested book_id is not linked to the resolved fleet asset")
    requested = [explicit_book] if explicit_book else allowed
    if not requested:
        return []
    placeholders = ",".join("?" for _ in requested)
    books = [dict(row) for row in con.execute(
        f"SELECT * FROM books WHERE book_id IN ({placeholders}) ORDER BY book_id", requested)]
    if {b["book_id"] for b in books} != set(requested):
        raise ValueError("Registry-linked book_id missing from SQLite; no substitute book permitted")
    for book in books:
        metadata = registry.get("sources", {}).get(book["book_id"], {})
        expected = metadata.get("expected_model", asset["display_model"])
        if book["model"] != expected:
            raise ValueError("SQLite source model differs from approved registry binding")
        book["source_identity"] = metadata or {"identity_status": "SAME_MODEL", "cover_model": book["model"],
                                                "applicability_evidence": None}
    return books


def book_serial_match(book, serial):
    if serial is None or book["machine_serial_from"] is None:
        return None
    prefix, number = serial
    return (prefix == (book["machine_serial_prefix"] or "").upper()
            and number >= book["machine_serial_from"]
            and (book["machine_serial_to"] is None or number <= book["machine_serial_to"]))


def candidate_provenance(candidate, book, resolution, serial, engine_match=None):
    """A PDF verification is evidence of a row, never proof of fleet fit."""
    source = book["source_identity"]
    verified = (bool(candidate.get("part_number"))
                and candidate.get("pdf_verification", {}).get("status") == "VERIFIED")
    coverage = book_serial_match(book, serial)
    reasons = []
    if source["identity_status"] != "SAME_MODEL":
        reasons.append(source["identity_status"])
    if serial is None:
        reasons.append("MACHINE_SERIAL_UNKNOWN")
    elif coverage is False:
        reasons.append("SERIAL_OUTSIDE_SOURCE_COVERAGE")
    elif coverage is None:
        reasons.append("SOURCE_SERIAL_COVERAGE_UNKNOWN")
    if candidate["serial_match"] is False:
        reasons.append("ROW_SERIAL_MISMATCH")
    if engine_match is False:
        reasons.append("ENGINE_SERIAL_MISMATCH")
    if any(str(flag).startswith("ambiguous") for flag in candidate["flags"]):
        reasons.append("ROW_APPLICABILITY_AMBIGUOUS")
    # Current registry has no plate/option/row applicability attestation. Supplied
    # serial, source alias or matching model alone can never manufacture B.
    reasons.append("FLEET_PART_APPLICABILITY_NOT_ATTESTED")
    return {"actual_source_model": book["model"], "cover_model": source["cover_model"],
            "model_match_status": source["identity_status"],
            "source_serial_coverage": {key: book[key] for key in (
                "machine_serial_prefix", "machine_serial_from", "machine_serial_to")},
            "book_serial_match": coverage,
            "source_citation": {"book_id": book["book_id"], "source_pdf": book["source_pdf"],
                                "pdf_pages": candidate["pdf_pages"], "figure": candidate["figure"],
                                "item": candidate["item_raw"]},
            "part_found_status": "PART_FOUND_IN_SOURCE" if verified else "SOURCE_ROW_UNVERIFIED",
            "part_applicability_confirmed": False,
            "applicability_status": "PART_APPLICABILITY_UNCONFIRMED", "applicability_reasons": reasons}
