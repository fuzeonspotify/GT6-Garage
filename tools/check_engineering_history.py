"""Read-only ledger/reference audit; does not execute native or game checks."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from garagehub.engineering_history import audit_engineering_history

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = audit_engineering_history()
    text = json.dumps(result, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + '\n', encoding='utf-8')
    print(text)
    raise SystemExit(0 if result['passed'] else 1)
