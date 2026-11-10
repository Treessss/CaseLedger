"""Backed-up all-shop inventory reset, without external API calls."""
import argparse
import importlib.util
import json
from pathlib import Path

from flask import g

from app import create_app, db
from app.models import Shop, User


parser = argparse.ArgumentParser()
parser.add_argument('--service-path', required=True)
parser.add_argument('--backup', required=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
assert Path(args.backup).stat().st_size > 0
spec = importlib.util.spec_from_file_location('inventory_zero_service', args.service_path)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
application = create_app()
with application.app_context():
    admin = User.query.filter_by(role='admin').first()
    g.current_user = {'authority': 'admin', 'user_id': admin.id}
    try:
        result = service.zero_inventory([shop.id for shop in Shop.query.all()],
                                        reason='用户确认所有店铺库存归零 2026-10-11')
        if args.apply:
            db.session.commit()
        else:
            db.session.rollback()
        print(json.dumps({'mode': 'apply' if args.apply else 'dry-run', 'shops': result}, ensure_ascii=False))
    except Exception:
        db.session.rollback()
        raise
