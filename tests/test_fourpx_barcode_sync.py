from app import db
from app.models import Order, ShippingParcel, Shop
from app.services.fourpx_barcode_sync_service import sync_barcodes
from app.services.fourpx_client import FourPxError


class LabelClient:
    def __init__(self, key, secret, *, environment):
        assert key == 'test-key'

    def get_label(self, number):
        if number == 'FAIL':
            raise FourPxError('unavailable')
        return {'label_barcode': f'GV26{number}', 'child_label_barcode': [],
                'label_url_info': {'logistics_label': 'https://bss-fss.4px.com/a.pdf'}}


def test_bulk_registers_real_barcodes_without_scanning_and_is_idempotent(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids['a'])
        shop.set_fourpx_credentials('test-key', 'test-secret')
        order = Order.query.filter_by(shop_id=shop.id).first()
        other = Order.query.filter_by(shop_id=shop_ids['b']).first()
        parcels = [ShippingParcel(shop_id=shop.id, order_id=order.id, label_number=number, source='4px')
                   for number in ('ONE', 'TWO', 'FAIL')]
        db.session.add_all(parcels)
        foreign = ShippingParcel(shop_id=other.shop_id, order_id=other.id, label_number='OTHER', source='4px')
        db.session.add(foreign)
        db.session.commit()
        ids = [p.id for p in parcels] + [foreign.id]
        result = sync_barcodes(shop.id, ids, client_factory=LabelClient)
        assert result['updated'] == 2
        assert len(result['failed']) == 1
        assert parcels[0].label_barcode == 'GV26ONE'
        assert parcels[1].label_barcode == 'GV26TWO'
        assert foreign.label_barcode is None
        assert order.warehouse_status == 'pending'
        assert all(p.scanned_at is None for p in parcels)
        assert sync_barcodes(shop.id, ids, client_factory=LabelClient)['updated'] == 0


def test_conflicting_existing_barcode_never_overwritten(app, shop_ids):
    with app.app_context():
        shop = db.session.get(Shop, shop_ids['a'])
        shop.set_fourpx_credentials('test-key', 'test-secret')
        order = Order.query.filter_by(shop_id=shop.id).first()
        existing = ShippingParcel(shop_id=shop.id, order_id=order.id, label_number='EXISTING',
                                  label_barcode='GV26ONE', source='4px')
        missing = ShippingParcel(shop_id=shop.id, order_id=order.id, label_number='ONE', source='4px')
        db.session.add_all([existing, missing])
        db.session.commit()
        result = sync_barcodes(shop.id, [missing.id], client_factory=LabelClient)
        assert result['updated'] == 0
        assert len(result['failed']) == 1
        assert missing.label_barcode is None
        assert existing.label_barcode == 'GV26ONE'
