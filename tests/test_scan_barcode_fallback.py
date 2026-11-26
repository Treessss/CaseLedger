"""Scan-time recovery for a GV label whose barcode reached the DB late."""


def test_gv_scan_refetches_pending_barcodes(monkeypatch):
    from app.services import fourpx_barcode_sync_service as barcode_service
    from app.services import shipping_queue_service as queue_service

    calls = []
    monkeypatch.setattr(barcode_service, "sync_after_sale_barcodes",
                        lambda shop_id, limit=20: calls.append(("after_sale", shop_id, limit)))
    monkeypatch.setattr(barcode_service, "sync_barcodes",
                        lambda shop_id: calls.append(("parcel", shop_id)))
    queue_service._supplement_scan_barcodes(1, "GV26CAA0U088096219")
    assert calls == [("after_sale", 1, 20), ("parcel", 1)]


def test_non_label_scan_does_not_trigger_refetch(monkeypatch):
    from app.services import fourpx_barcode_sync_service as barcode_service
    from app.services import shipping_queue_service as queue_service

    calls = []
    monkeypatch.setattr(barcode_service, "sync_after_sale_barcodes", lambda *a, **k: calls.append("as"))
    monkeypatch.setattr(barcode_service, "sync_barcodes", lambda *a, **k: calls.append("parcel"))
    queue_service._supplement_scan_barcodes(1, "20616")
    assert calls == []
