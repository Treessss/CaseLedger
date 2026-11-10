"""Export old SQLite expenses and preview/apply them to the configured database."""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("export", "preview", "apply"))
    parser.add_argument("snapshot")
    parser.add_argument("--sqlite")
    parser.add_argument("--shop-id", type=int, default=1)
    args = parser.parse_args()
    path = Path(args.snapshot)
    if args.mode == "export":
        conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        payload = {
            "accounts": [dict(row) for row in conn.execute("SELECT * FROM accounts")],
            "expenses": [dict(row) for row in conn.execute("SELECT * FROM expenses ORDER BY id")],
        }
        for row in payload["expenses"]:
            links = conn.execute(
                "SELECT DISTINCT o.shopify_order_id FROM orders o WHERE o.id = ? "
                "OR o.id IN (SELECT order_id FROM expense_orders WHERE expense_id = ?)",
                (row.get("order_id"), row["id"]),
            )
            row["shopify_order_ids"] = [str(item[0]) for item in links if item[0]]
        conn.close()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        print(json.dumps({"exported": len(payload["expenses"])}))
        return

    from app import create_app
    from app.services.expense_migration_service import merge_legacy_expenses

    with create_app().app_context():
        result = merge_legacy_expenses(
            json.loads(path.read_text(encoding="utf-8")), args.shop_id,
            apply=args.mode == "apply",
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
