"""Run a verified dispatch manifest: default dry run, explicit --apply to commit."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from flask import g
from sqlalchemy import text

from app import create_app, db
from app.models import User


def digest_scope(shop_id):
    """Independently confirm financial data and other shops are unchanged."""
    statements = [
        "SELECT id,total_price,subtotal_price,total_tax,shipping_price,currency,actual_received,"
        "settlement_amount,settlement_currency,payment_fee,product_cost,shipping_cost,gross_profit,"
        "profit_margin,order_date,created_at,processed_at,financial_status,cancelled_at,"
        "fulfillment_status,fulfillments FROM orders ORDER BY id",
        "SELECT * FROM expenses ORDER BY id",
        "SELECT * FROM accounts ORDER BY id",
        "SELECT * FROM orders WHERE shop_id != :shop_id ORDER BY id",
        "SELECT * FROM after_sales WHERE shop_id != :shop_id ORDER BY id",
        "SELECT * FROM purchase_lists WHERE shop_id != :shop_id ORDER BY id",
        "SELECT * FROM inventory_adjustments WHERE shop_id != :shop_id ORDER BY id",
        "SELECT * FROM inventory_reservations WHERE shop_id != :shop_id ORDER BY id",
    ]
    hashes = []
    for sql in statements:
        values = [list(row) for row in db.session.execute(text(sql), {"shop_id": shop_id})]
        hashes.append(hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest())
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--service-path")
    parser.add_argument("--backup")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and (not args.backup or not Path(args.backup).is_file() or Path(args.backup).stat().st_size == 0):
        parser.error("A nonempty database backup is required before --apply")
    if args.service_path:
        spec = importlib.util.spec_from_file_location("dispatch_repair", args.service_path)
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
        reconcile_dispatch = service.reconcile_dispatch
    else:
        from app.services.dispatch_reconciliation_service import reconcile_dispatch
    manifest = json.loads(Path(args.manifest).read_text())
    application = create_app()
    with application.app_context():
        admin = User.query.filter_by(role="admin").first()
        if admin is None:
            raise RuntimeError("No administrator available for audited reconciliation")
        g.current_user = {"authority": "admin", "user_id": admin.id}
        try:
            before = digest_scope(manifest['shop_id'])
            result = reconcile_dispatch(manifest, batch_id=args.batch_id)
            after = digest_scope(manifest['shop_id'])
            if before != after:
                raise RuntimeError("Financial or other-shop data changed; rolling back")
            result['financial_and_other_shops_unchanged'] = True
            result['mode'] = 'apply' if args.apply else 'dry-run'
            if args.apply:
                db.session.commit()
            else:
                db.session.rollback()
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            db.session.rollback()
            raise


if __name__ == "__main__":
    main()
