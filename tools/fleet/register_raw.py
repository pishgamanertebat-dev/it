from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(r"E:\KomatsoAI")
RAW_ROOT = PROJECT_ROOT / "data" / "fleet" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "fleet" / "db" / "fleet_ops.db"

VALID_SOURCES = {
    "daily_fault_reports",
    "maintenance_events",
    "service_events",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            original_path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE,
            file_size INTEGER NOT NULL,
            source_modified_at TEXT,
            imported_at TEXT NOT NULL,
            raw_path TEXT NOT NULL
        )
        """
    )

    conn.commit()


def register_file(source_type: str, source_path: Path) -> None:
    source_path = source_path.resolve()

    if not source_path.exists():
        raise SystemExit(f"ERROR: file not found: {source_path}")

    if not source_path.is_file():
        raise SystemExit(f"ERROR: not a file: {source_path}")

    if source_path.suffix.lower() not in {".xlsx", ".xls", ".xlsm"}:
        raise SystemExit(f"ERROR: unsupported Excel file: {source_path.name}")

    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    digest = sha256_file(source_path)

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

        existing = conn.execute(
            """
            SELECT id, imported_at, raw_path
            FROM ingest_sources
            WHERE sha256 = ?
            """,
            (digest,),
        ).fetchone()

        if existing:
            print("SKIP: identical file already registered")
            print(f"ID: {existing[0]}")
            print(f"Imported: {existing[1]}")
            print(f"Raw: {existing[2]}")
            print(f"SHA256: {digest}")
            return

        source_dir = RAW_ROOT / source_type
        source_dir.mkdir(parents=True, exist_ok=True)

        raw_filename = f"{digest}__{source_path.name}"
        raw_path = source_dir / raw_filename

        # Raw data is immutable: never overwrite an existing snapshot.
        if not raw_path.exists():
            shutil.copy2(source_path, raw_path)

        stat = source_path.stat()

        modified_at = datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(timespec="seconds")

        imported_at = datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")

        conn.execute(
            """
            INSERT INTO ingest_sources (
                source_type,
                original_filename,
                original_path,
                sha256,
                file_size,
                source_modified_at,
                imported_at,
                raw_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                source_path.name,
                str(source_path),
                digest,
                stat.st_size,
                modified_at,
                imported_at,
                str(raw_path),
            ),
        )

        conn.commit()

    print("IMPORT OK")
    print(f"Source: {source_type}")
    print(f"File: {source_path.name}")
    print(f"SHA256: {digest}")
    print(f"Raw: {raw_path}")
    print(f"DB: {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "source_type",
        choices=sorted(VALID_SOURCES),
    )

    parser.add_argument(
        "file",
        help="Full path to Excel file",
    )

    args = parser.parse_args()

    register_file(
        args.source_type,
        Path(args.file),
    )


if __name__ == "__main__":
    main()