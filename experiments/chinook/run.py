"""Run pinned public-sample migrations and verify outcomes; standard library only."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import platform
import sqlite3
import sys
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from patchday.core import rehearse, quote  # noqa: E402
from patchday.report import html_report  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, description: str) -> None:
    if not condition:
        raise RuntimeError(description)


def sample_report(report: dict, provenance: dict, license_text: str) -> str:
    # Keep attribution with the standalone report when it leaves this repo.
    license_text = "\n".join(line.rstrip() for line in license_text.splitlines())
    source = (f'<p class="sub">Public sample: <a href="{escape(provenance["repository"], quote=True)}">'
              f'Chinook {escape(provenance["release"])}</a> · '
              '<a href="#chinook-license">MIT license</a>. Fictional customers and generated sales.</p>')
    notice = ('<details id="chinook-license"><summary>Chinook sample license</summary>'
              f'<pre style="white-space:pre-wrap;padding:16px">{escape(license_text)}</pre></details>')
    return html_report(report).replace('<main>', '<main>' + source).replace('</main>', notice + '</main>')


def run(output: Path, database: Path | None = None) -> dict:
    provenance = json.loads((HERE / "source.json").read_text(encoding="utf-8"))
    license_path = HERE / "LICENSE.chinook"
    require(digest(license_path) == provenance["license_sha256"], "License checksum mismatch")
    # Each run has its own directory. Existing evidence and databases stay intact.
    output.mkdir(parents=True, exist_ok=False)
    if database is None:
        database = output / provenance["asset"]
        with urlopen(provenance["url"], timeout=60) as response:
            payload = response.read(provenance["bytes"] + 1)
        require(len(payload) == provenance["bytes"], "Unexpected download length")
        require(hashlib.sha256(payload).hexdigest() == provenance["sha256"], "Download checksum mismatch")
        database.write_bytes(payload)
    require(digest(database) == provenance["sha256"], "Database checksum mismatch; use the exact pinned asset")
    (output / "LICENSE.chinook").write_bytes(license_path.read_bytes())
    database = database.resolve()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        counts = {name: db.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]
                  for (name,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}
        composers = db.execute("SELECT count(Composer) FROM Track").fetchone()[0]
        playlist_members = db.execute("SELECT count(*) FROM PlaylistTrack WHERE PlaylistId=1").fetchone()[0]
    require(counts["Track"] == 3503 and composers == 2525 and counts["Invoice"] == 412,
            "Unexpected sample counts")
    cases = [
        ("invoice-status", ["001_invoice_status.sql", "002_mark_paid.sql"], "passed"),
        ("invalid-customer", ["001_invoice_status.sql", "003_invalid_customer.sql"], "failed"),
        ("drop-composer", ["004_drop_composer.sql"], "review"),
        ("clear-playlist", ["005_clear_playlist.sql"], "review"),
        ("erase-composer-values", ["006_erase_composer_values.sql"], "passed"),
    ]
    results = []
    for name, files, expected in cases:
        report = rehearse(database, [HERE / "migrations" / file for file in files])
        source_unchanged = digest(database) == provenance["sha256"]
        # Preserve the report even when an expected outcome changes.
        (output / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / f"{name}.html").write_text(sample_report(report, provenance, license_path.read_text(encoding="utf-8")), encoding="utf-8")
        require(source_unchanged, f"{name}: source bytes changed")
        require(report["status"] == expected, f"{name}: expected {expected}, got {report['status']}")
        if name == "invoice-status":
            require(report["steps"][1]["changed_rows"] == counts["Invoice"], "Invoice update count mismatch")
        elif name == "invalid-customer":
            require(report["rolled_back"] and [step["status"] for step in report["steps"]] == ["rolled_back", "failed"],
                    "Failed batch did not report rollback of the earlier migration")
            require(not report["changes"] and "FOREIGN KEY" in report["error"], "Expected foreign-key rejection")
        elif name == "drop-composer":
            track = next(change for change in report["changes"] if change["object"] == "table:Track")
            require(track["before"]["rows"] == track["after"]["rows"] == counts["Track"], "Track row count changed")
            require(any('"Composer"' in risk for risk in report["risks"]), "Missing Composer warning")
        elif name == "clear-playlist":
            table = next(change for change in report["changes"] if change["object"] == "table:PlaylistTrack")
            require(table["before"]["rows"] - table["after"]["rows"] == playlist_members, "Playlist deletion count mismatch")
        elif name == "erase-composer-values":
            require(report["steps"][0]["changed_rows"] == composers and not report["changes"] and not report["risks"],
                    "Value-level loss heuristic changed; update the documented limitation")
        results.append({"case": name, "files": files, "expected_status": expected,
                        "observed_status": report["status"], "source_sha256_unchanged": source_unchanged,
                        "rolled_back": report["rolled_back"], "risks": report["risks"], "error": report["error"],
                        "row_writes": [step["changed_rows"] for step in report["steps"]]})
        print(f"{name:24} {report['status']:6} source SHA-256 unchanged")
    summary = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
               "platform": platform.system(), "source": provenance, "table_rows": counts,
               "nonnull_composer_values": composers, "playlist_1_members": playlist_members, "cases": results}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("work/chinook"), help="New directory for the verified download and reports")
    parser.add_argument("--database", type=Path, help="Use an already downloaded asset; the same SHA-256 is required")
    args = parser.parse_args()
    try:
        run(args.output, args.database)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Chinook experiment failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
