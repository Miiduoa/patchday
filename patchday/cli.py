import argparse
import json
from pathlib import Path
import sys

from .core import InputError, rehearse
from .report import html_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse SQLite migrations on a disposable snapshot.")
    parser.add_argument("database", type=Path)
    parser.add_argument("migrations", nargs="+", type=Path, help="SQL files, applied in the supplied order")
    parser.add_argument("--json", type=Path, dest="json_path", help="Write a new JSON report; never overwrite")
    parser.add_argument("--html", type=Path, dest="html_path", help="Write a new self-contained HTML report")
    parser.add_argument("--timeout", type=float, default=10, help="Time budget in seconds (default: 10)")
    args = parser.parse_args(argv)
    try:
        outputs = [p.resolve() for p in (args.json_path, args.html_path) if p]
        if len(outputs) != len(set(outputs)) or any(p.exists() for p in outputs):
            raise InputError("Report destinations must be distinct new files. Existing files are never overwritten.")
        result = rehearse(args.database, args.migrations, timeout=args.timeout)
        for path, content in ((args.json_path, json.dumps(result, ensure_ascii=False, indent=2)),
                              (args.html_path, html_report(result))):
            if path:
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(content + "\n")
        print(f"PATCHDAY  {result['status'].upper()}  {result['database']}")
        for step in result["steps"]:
            print(f"  {step['status']:12} {step['file']} · {step['statements']} statements")
        for risk in result["risks"]:
            print(f"  REVIEW: {risk}")
        if result["error"]:
            print(f"  {result['error']}")
        print("  Source opened read-only. Snapshot discarded.")
        return {"passed": 0, "failed": 1, "review": 3}[result["status"]]
    except (InputError, OSError) as exc:
        print(f"patchday: {exc}", file=sys.stderr)
        return 2
