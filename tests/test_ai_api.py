"""AI 接口第一版：管理员申请 API Key + 客服邮件读写接口。"""

from datetime import datetime

from app import db
from app.models import AiApiAudit, AiApiKey, MailMessage, Order


def _headers(raw_key):
    return {"X-API-Key": raw_key}


def _create_key(client, admin_headers, shop_ids, scopes=None, expires_in_days=None):
    payload = {"name": "AI 测试", "shop_ids": [shop_ids["a"]],
               "scopes": scopes if scopes is not None else ["mail_read", "mail_send"]}
    if expires_in_days:
        payload["expires_in_days"] = expires_in_days
    response = client.post("/api/ai-keys", headers=admin_headers, json=payload)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["api_key"]


def test_only_admin_can_create_ai_key(client, admin_headers, limited_headers, shop_ids):
    denied = client.post("/api/ai-keys", headers=limited_headers,
                         json={"name": "x", "shop_ids": [shop_ids["a"]]})
    assert denied.status_code == 403
    raw = _create_key(client, admin_headers, shop_ids)
    assert raw.startswith("cai_")
    listing = client.get("/api/ai-keys", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.get_json()["data"]["keys"][0]["key_prefix"] == raw[:12]


def test_ai_key_auth_scope_and_shop_isolation(client, admin_headers, shop_ids):
    read_only = _create_key(client, admin_headers, shop_ids, scopes=["mail_read"])
    both = _create_key(client, admin_headers, shop_ids, scopes=["mail_read", "mail_send"])

    assert client.get("/api/ai/health").status_code == 401
    assert client.get("/api/ai/health", headers=_headers("cai_invalid")).status_code == 401
    ok = client.get("/api/ai/health", headers=_headers(read_only))
    assert ok.status_code == 200 and ok.get_json()["data"]["ok"] is True

    # 只有读权限，发邮件被拒
    denied = client.post("/api/ai/mail/sync", headers=_headers(read_only), json={})
    assert denied.status_code == 403 and denied.get_json()["code"] == "FORBIDDEN_SCOPE"

    # 店铺范围：只能访问分配的店铺
    out_of_scope = client.get(f"/api/ai/mail/messages?shop_id={shop_ids['b']}", headers=_headers(both))
    assert out_of_scope.status_code == 403



def test_ai_mail_messages_and_audit(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids, scopes=["mail_read"])
    with client.application.app_context():
        db.session.add(MailMessage(
            id="ai-msg-1", shop_id=shop_ids["a"], provider_message_id="ai-msg-1",
            direction="in", sender="customer@example.com", customer="customer@example.com",
            subject="Where is my order?", sent_at=int(datetime.utcnow().timestamp() * 1000),
            status="pending", is_latest=True, is_system=False,
        ))
        db.session.commit()
    response = client.get(f"/api/ai/mail/messages?shop_id={shop_ids['a']}&status=all",
                          headers=_headers(raw))
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert any(row["subject"] == "Where is my order?" for row in data["items"])
    with client.application.app_context():
        assert AiApiAudit.query.count() >= 1


def test_revoke_key_blocks_requests(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids)
    with client.application.app_context():
        key = AiApiKey.query.order_by(AiApiKey.id.desc()).first()
        key_id = key.id
    assert client.post(f"/api/ai-keys/{key_id}/revoke", headers=admin_headers).status_code == 200
    assert client.get("/api/ai/health", headers=_headers(raw)).status_code == 401


def test_ai_read_scopes_orders_logistics_and_openapi(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids,
                      scopes=["orders_read", "logistics_read", "procurement_read",
                              "inventory_read", "after_sales_read", "purchase_draft"])
    with client.application.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        from urllib.parse import quote
        order_number = quote(order.order_number, safe="")
    headers = _headers(raw)
    shop = shop_ids["a"]
    for path in (
        f"/api/ai/orders?shop_id={shop}&per_page=5",
        f"/api/ai/orders/{order_number}?shop_id={shop}",
        f"/api/ai/orders/{order_number}/timeline?shop_id={shop}",
        f"/api/ai/logistics/records?shop_id={shop}&tab=pending",
        f"/api/ai/logistics/summary?shop_id={shop}",
        f"/api/ai/procurement/candidates?shop_id={shop}&view=needs_purchase",
        f"/api/ai/purchase-lists?shop_id={shop}",
        f"/api/ai/inventory/summary?shop_id={shop}",
        f"/api/ai/inventory/movements?shop_id={shop}",
        f"/api/ai/after-sales?shop_id={shop}",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.get_json())
    openapi = client.get("/api/ai/openapi.json")
    assert openapi.status_code == 200
    assert "/api/ai/orders" in openapi.get_json()["paths"]


def test_ai_dry_run_send_does_not_send(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids)
    with client.application.app_context():
        db.session.add(MailMessage(
            id="ai-msg-dry", shop_id=shop_ids["a"], provider_message_id="ai-msg-dry",
            direction="in", sender="customer@example.com", customer="customer@example.com",
            subject="Hello", sent_at=int(datetime.utcnow().timestamp() * 1000),
            status="pending", is_latest=True, is_system=False,
        ))
        db.session.commit()
    response = client.post(f"/api/ai/mail/messages/ai-msg-dry/send?shop_id={shop_ids['a']}&dry_run=true",
                           headers=_headers(raw), json={"reply": "Hi there"})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["dry_run"] is True and data["reply"] == "Hi there"
    assert data["to"] == "customer@example.com"


def test_ai_key_ip_allowlist_and_audit_listing(client, admin_headers, shop_ids):
    response = client.post("/api/ai-keys", headers=admin_headers, json={
        "name": "限制 IP", "shop_ids": [shop_ids["a"]], "scopes": ["mail_read"],
        "ip_allowlist": ["10.9.8.7"],
    })
    assert response.status_code == 201
    raw = response.get_json()["data"]["api_key"]
    assert client.get("/api/ai/health", headers=_headers(raw)).status_code == 401
    audit = client.get("/api/ai-keys/audit", headers=admin_headers)
    assert audit.status_code == 200
    assert "pagination" in audit.get_json()["data"]


def test_ai_discovery_and_usage_endpoints(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids, scopes=["orders_read", "mail_read"])
    headers = _headers(raw)
    for path in ("/api/ai/me", "/api/ai/shops", "/api/ai/capabilities", "/api/ai/usage"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.get_json())
    me = client.get("/api/ai/me", headers=headers).get_json()["data"]
    assert me["key"]["name"] == "AI 测试"
    assert me["usage"]["rate_limit_per_minute"] == 120
    capabilities = client.get("/api/ai/capabilities", headers=headers).get_json()["data"]
    assert capabilities["endpoints"]["orders"]["granted"] is True
    assert capabilities["endpoints"]["inventory"]["granted"] is False


def test_ai_fields_projection_limits_response(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids, scopes=["orders_read"])
    response = client.get(f"/api/ai/orders?shop_id={shop_ids['a']}&fields=order_number,warehouse_status",
                          headers=_headers(raw))
    assert response.status_code == 200
    item = response.get_json()["data"]["items"][0]
    assert set(item.keys()) == {"order_number", "warehouse_status"}


def test_ai_key_update_and_rotate(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids, scopes=["mail_read"])
    with client.application.app_context():
        key_id = AiApiKey.query.order_by(AiApiKey.id.desc()).first().id
    updated = client.patch(f"/api/ai-keys/{key_id}", headers=admin_headers,
                           json={"name": "改名后", "scopes": ["orders_read"], "daily_send_limit": 5})
    assert updated.status_code == 200
    assert updated.get_json()["data"]["name"] == "改名后"
    assert updated.get_json()["data"]["scopes"] == ["orders_read"]
    # 旧范围失效
    assert client.get("/api/ai/mail/messages?shop_id=1", headers=_headers(raw)).status_code == 403
    rotated = client.post(f"/api/ai-keys/{key_id}/rotate", headers=admin_headers)
    assert rotated.status_code == 200
    new_raw = rotated.get_json()["data"]["api_key"]
    assert new_raw != raw
    assert client.get("/api/ai/health", headers=_headers(raw)).status_code == 401
    assert client.get("/api/ai/health", headers=_headers(new_raw)).status_code == 200


def test_ai_rate_limit_errors_after_120_calls(app):
    from app.services.ai_api_service import _RATE_LIMITS, _rate_limit, RATE_LIMIT_PER_MINUTE
    from app.utils.errors import RateLimitError
    _RATE_LIMITS.clear()
    try:
        for _ in range(RATE_LIMIT_PER_MINUTE):
            _rate_limit(999999)
        with pytest_raises_rate_limit():
            _rate_limit(999999)
    finally:
        _RATE_LIMITS.clear()


class pytest_raises_rate_limit:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        from app.utils.errors import RateLimitError
        return exc_type is not None and issubclass(exc_type, RateLimitError)


def test_ai_after_sale_write_flow(client, admin_headers, shop_ids):
    raw = _create_key(client, admin_headers, shop_ids,
                      scopes=["after_sales_read", "after_sales_write"])
    headers = _headers(raw)
    with client.application.app_context():
        order = Order.query.filter_by(shop_id=shop_ids["a"]).order_by(Order.id).first()
        order.warehouse_status = "shipped"
        db.session.commit()
        order_id = order.id
        order_number = order.order_number
        from app.models import OrderItem
        source = OrderItem.query.filter_by(order_id=order.id, quantity=1).first()
        if source is None:
            source = OrderItem(shop_id=shop_ids["a"], order_id=order.id,
                               shopify_line_item_id="ai-after-sale-line",
                               title="Case", variant_title="Blue", quantity=1, price=10)
            db.session.add(source)
            db.session.commit()
        source_id = source.id
    from urllib.parse import quote
    lookup = client.get(
        f"/api/ai/after-sales/lookup?shop_id={shop_ids['a']}&order_number={quote(order_number, safe='')}",
        headers=headers)
    assert lookup.status_code == 200, lookup.get_json()
    created = client.post("/api/ai/after-sales", headers=headers, json={
        "order_id": order_id, "kind": "missing", "reason": "AI 测试售后",
        "items": [{"source_order_item_id": source_id, "quantity": 1}],
    })
    assert created.status_code == 201, created.get_json()
    after_sale_id = created.get_json()["data"]["id"]
    note = client.put(f"/api/ai/after-sales/{after_sale_id}/note", headers=headers,
                      json={"note": "AI 备注"})
    assert note.status_code == 200, note.get_json()
    cost = client.post(f"/api/ai/after-sales/{after_sale_id}/cost", headers=headers,
                       json={"cost_type": "shipping", "amount_cny": 12.5, "note": "补发运费"})
    assert cost.status_code == 200, cost.get_json()
    tracking = client.post(f"/api/ai/after-sales/{after_sale_id}/tracking", headers=headers,
                           json={"tracking_number": "1ZTESTAI0001"})
    assert tracking.status_code == 200, tracking.get_json()
    detail = client.get(f"/api/ai/after-sales/{after_sale_id}?shop_id={shop_ids['a']}", headers=headers)
    assert detail.status_code == 200
    assert detail.get_json()["data"]["tracking_number"] == "1ZTESTAI0001"


def test_ai_can_send_new_mail_to_arbitrary_address(client, admin_headers, shop_ids, monkeypatch):
    from app.services import mail_service
    raw = _create_key(client, admin_headers, shop_ids, scopes=["mail_send"])
    headers = _headers(raw)
    captured = {}

    def fake_send(to, subject, content, files=None, cc=None, bcc=None):
        captured.update({"to": to, "subject": subject, "content": content,
                         "files": files, "cc": cc, "bcc": bcc})
        return {"sent": True, "to": [to], "subject": subject, "attachments": []}

    monkeypatch.setattr(mail_service, "send_new_mail", fake_send)
    response = client.post("/api/ai/mail/send", headers=headers, json={
        "to": "prospect@example.com", "subject": "新品推荐", "content": "您好，这是主动发送的邮件",
    })
    assert response.status_code == 200, response.get_json()
    assert captured["to"] == "prospect@example.com"
    assert captured["subject"] == "新品推荐"
    # 预览模式不发送
    captured.clear()
    dry = client.post("/api/ai/mail/send?dry_run=true", headers=headers, json={
        "to": "prospect@example.com", "subject": "预览", "content": "内容",
    })
    assert dry.status_code == 200
    assert dry.get_json()["data"]["dry_run"] is True
    assert captured == {}


def test_send_new_mail_builds_zoho_request(app, monkeypatch):
    from app.services import mail_client, mail_service
    monkeypatch.setattr(mail_client, "get_setting", lambda key, default="": {
        "account": "A100", "email": "service@example.com"}.get(key, default))
    calls = {}

    def fake_api(path, method=None, body=None, **kwargs):
        calls.update({"path": path, "method": method, "body": body})
        return {"messageId": "m1"}

    monkeypatch.setattr(mail_client, "api", fake_api)
    monkeypatch.setattr(mail_service, "sync_sent_head", lambda: None)
    with app.app_context():
        result = mail_service.send_new_mail("customer@example.com", "你好", "这是主动邮件",
                                            cc="cc@example.com")
    assert result["sent"] is True
    assert calls["path"] == "accounts/A100/messages"
    assert calls["method"] == "POST"
    body = calls["body"]
    assert body["toAddress"] == "customer@example.com"
    assert body["ccAddress"] == "cc@example.com"
    assert body["subject"] == "你好"
    assert body["content"] == "这是主动邮件"
    assert body["fromAddress"] == "service@example.com"
