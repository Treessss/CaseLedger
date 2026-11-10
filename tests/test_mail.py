"""客服邮件：状态判断要准，打开和发送都不能退化成全箱扫描。"""

import pytest

from app import db
from app.models import MailDraft, MailMessage, MailSend, MailSetting
from app.services import mail_client, mail_service
from app.services.mail_scope import mailbox_scope


@pytest.fixture(autouse=True)
def mail_shop_scope():
    with mailbox_scope(1):
        yield


def _message(message_id, **overrides):
    data = {
        "id": message_id,
        "shop_id": overrides.get("shop_id", 1),
        "provider_message_id": message_id,
        "thread_id": overrides.get("thread_id", f"thread-{message_id}"),
        "folder_id": overrides.get("folder_id", "folder-in"),
        "direction": overrides.get("direction", "in"),
        "sender": overrides.get("sender", "customer@example.com"),
        "recipient": "support@caseease.com",
        "customer": overrides.get("customer", "customer@example.com"),
        "subject": overrides.get("subject", "Need help with my case"),
        "body": overrides.get("body", "Hello, my case arrived broken."),
        "sent_at": overrides.get("sent_at", 1_700_000_000_000),
        "has_attachment": overrides.get("has_attachment", False),
        "is_system": overrides.get("is_system", False),
        "header_checked": overrides.get("header_checked", True),
        "hydrated": overrides.get("hydrated", True),
        "status": overrides.get("status", "verify"),
        "is_latest": overrides.get("is_latest", True),
    }
    return MailMessage(**data)


@pytest.fixture()
def connected(app):
    """把邮箱标记成已连接、历史同步已完成。"""
    with app.app_context():
        mail_client.put_setting("account", "123456")
        mail_client.put_setting("email", "support@caseease.com")
        mail_client.put_setting("own", "support@caseease.com")
        mail_client.put_setting("complete", "true")
        yield
        from app.models import AfterSale

        AfterSale.query.filter(AfterSale.source_mail_message_id.isnot(None)).update({"source_mail_message_id": None})
        MailSend.query.delete()
        MailDraft.query.delete()
        MailMessage.query.delete()
        db.session.commit()


def test_status_reply_followup_and_ignored(app, connected):
    with app.app_context():
        db.session.add_all(
            [
                _message("in-1", thread_id="t1", sent_at=1000),
                _message("sys-1", thread_id="t2", sent_at=1500, is_system=True),
            ]
        )
        db.session.commit()
        mail_service.recompute_status(["in-1", "sys-1"])
        assert db.session.get(MailMessage, "in-1").status == "pending"
        assert db.session.get(MailMessage, "sys-1").status == "ignored"

        # 我方回复之后 → 已回复
        db.session.add(
            _message(
                "out-1",
                thread_id="t1",
                direction="out",
                sender="support@caseease.com",
                customer="customer@example.com",
                sent_at=2000,
            )
        )
        db.session.commit()
        mail_service.recompute_status(["in-1"])
        assert db.session.get(MailMessage, "in-1").status == "replied"

        # 客户又来信 → 新的那封是"客户追加"，旧的仍然是已回复
        db.session.add(_message("in-2", thread_id="t1", sent_at=3000))
        db.session.commit()
        mail_service.recompute_status(["in-1", "in-2"])
        assert db.session.get(MailMessage, "in-1").status == "replied"
        assert db.session.get(MailMessage, "in-2").status == "followup"


def test_only_latest_inbound_per_thread_is_listed(app, connected, client, admin_headers):
    with app.app_context():
        for index in range(3):
            db.session.add(
                _message(f"t1-{index}", thread_id="t1", sent_at=1000 + index, is_latest=False)
            )
        db.session.add(_message("t1-new", thread_id="t1", sent_at=5000, is_latest=True))
        db.session.add(_message("t2-1", thread_id="t2", sent_at=4000, is_latest=True))
        db.session.commit()
        mail_service.recompute_status(["t1-0", "t1-1", "t1-2", "t1-new", "t2-1"])

    response = client.get("/api/mail/messages?shop_id=1&status=all", headers=admin_headers)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert {item["id"] for item in data["items"]} == {"t1-new", "t2-1"}
    assert data["pagination"]["total"] == 2


def test_opening_one_mail_only_touches_its_thread(app, connected, client, admin_headers):
    """打开一封邮件不能把整箱邮件读出来：这里放 300 封无关邮件做对照。"""
    with app.app_context():
        db.session.add(_message("target", thread_id="t-target", sent_at=9_000_000))
        db.session.add(
            _message(
                "target-reply",
                thread_id="t-target",
                direction="out",
                customer="customer@example.com",
                sent_at=9_000_001,
            )
        )
        for index in range(300):
            db.session.add(
                _message(f"noise-{index}", thread_id=f"noise-{index}", sent_at=1_000 + index)
            )
        db.session.commit()

    response = client.get("/api/mail/messages/target?shop_id=1", headers=admin_headers)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["mail"]["id"] == "target"
    # 会话里只有这一组的往来，没有混进无关邮件
    assert {item["id"] for item in data["thread"]} == {"target", "target-reply"}
    # 最新的那封排在最上面
    assert [item["id"] for item in data["thread"]] == ["target-reply", "target"]


def test_send_does_not_run_a_full_mailbox_sync(app, connected, monkeypatch):
    """发送前只做局部检查：绝不能再整套同步一遍（这是原项目发送慢的原因）。"""
    with app.app_context():
        db.session.add(_message("m-send", thread_id="t-send", sent_at=1000))
        db.session.add(MailDraft(message_id="m-send", reply="Thanks for reaching out!"))
        db.session.commit()
        mail_service.recompute_status(["m-send"])

        calls = []

        def fake_api(path, method="GET", body=None, params=None, timeout=25):
            calls.append((path, method, body))
            return {"status": "ok"}

        def explode(*args, **kwargs):
            raise AssertionError("发送过程中不应该调用全量同步")

        monkeypatch.setattr(mail_client, "api", fake_api)
        monkeypatch.setattr(mail_service, "sync", explode)

        result = mail_service.send_reply("m-send")
        assert result["sent"] is True
        # 真正发出的只有"回复这封邮件"这一个请求
        assert [(path, method) for path, method, _ in calls] == [
            ("accounts/123456/messages/m-send", "POST")
        ]
        assert db.session.get(MailMessage, "m-send").status == "replied"
        assert db.session.get(MailSend, "m-send").state == "sent"


def test_shop_isolation_for_every_mail_operation(app, client, admin_headers, connected):
    with app.app_context():
        db.session.add(_message("private", thread_id="shared-thread"))
        db.session.add(_message("other", shop_id=2, thread_id="shared-thread", direction="out"))
        db.session.add(MailDraft(message_id="private", reply="private draft"))
        db.session.commit()
    assert client.get("/api/mail/messages", headers=admin_headers).status_code == 400
    status = client.get("/api/mail/status?shop_id=2", headers=admin_headers).get_json()["data"]
    assert status["connected"] is False and status["email"] == ""
    assert client.get("/api/mail/messages/private?shop_id=2", headers=admin_headers).status_code == 404
    detail = client.get("/api/mail/messages/private?shop_id=1", headers=admin_headers).get_json()["data"]
    assert [entry["id"] for entry in detail["thread"]] == ["private"]
    for action, body in [("reply", {"reply": "changed"}), ("ignore", {}), ("guidance", {"guidance": "changed"}), ("send", {})]:
        response = client.post(f"/api/mail/messages/private/{action}?shop_id=2", headers=admin_headers, json=body)
        assert response.status_code == 404
    with app.app_context():
        assert db.session.get(MailDraft, "private").reply == "private draft"


def test_ignored_message_stays_ignored_but_new_customer_mail_is_pending(app, connected):
    with app.app_context():
        db.session.add(_message("ignored", thread_id="t", sent_at=1000))
        db.session.commit()
        mail_service.ignore_message("ignored")
        mail_service.recompute_status(["ignored"])
        assert db.session.get(MailMessage, "ignored").status == "ignored"
        db.session.add(_message("new", thread_id="t", sent_at=2000))
        db.session.commit()
        mail_service.recompute_status(["new"])
        mail_service._refresh_latest_flags(["t"])
        db.session.commit()
        pending = mail_service.list_messages({})
        assert [row["id"] for row in pending["items"]] == ["new"]
        mail_service.ignore_message("ignored", False)
        assert db.session.get(MailMessage, "ignored").ignored_at is None


def test_same_provider_id_in_two_mailboxes_is_safe(app, connected):
    with app.app_context():
        raw = {"messageId": "same", "threadId": "t", "fromAddress": "customer@example.com", "receivedTime": "1000"}
        first, _ = mail_service.upsert_message(mail_service.normalize(raw, "Inbox"))
        db.session.commit()
        with mailbox_scope(2):
            second, created = mail_service.upsert_message(mail_service.normalize(raw, "Inbox"))
            db.session.commit()
            assert created and second.id != first.id
            assert second.provider_message_id == first.provider_message_id
            again, created = mail_service.upsert_message(mail_service.normalize(raw, "Inbox"))
            assert not created and again.id == second.id


def test_customer_order_matching_and_mail_after_sale_flow(app, connected, client, admin_headers):
    from app.models import Order, OrderItem

    with app.app_context():
        orders = Order.query.order_by(Order.id).all()
        for order in orders:
            order.customer_email = "Customer@Example.com"
        order = orders[0]
        order.warehouse_status = "shipped"
        item = OrderItem(order_id=order.id, shop_id=order.shop_id, shopify_line_item_id="mail-item", title="Case", quantity=1)
        db.session.add(item)
        db.session.add(_message("source"))
        db.session.commit()
        order_id, item_id = order.id, item.id
        matched = mail_service.get_message("source")["orders"]
        assert len(matched) == 3
        assert all(row["order_number"].startswith("#A") for row in matched)
    response = client.post("/api/after-sales", headers=admin_headers, json={
        "order_id": order_id, "kind": "missing", "reason": "邮件漏发反馈",
        "source_mail_message_id": "source", "items": [{"source_order_item_id": item_id, "quantity": 1}],
    })
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["source_mail_message_id"] == "source"
    assert data["purchase_lists"] == [] and data["status"] == "pending_purchase"


def test_send_is_blocked_when_customer_sent_a_newer_mail(app, connected, monkeypatch):
    with app.app_context():
        db.session.add(_message("m-old", thread_id="t-x", sent_at=1000))
        db.session.add(_message("m-new", thread_id="t-x", sent_at=5000))
        db.session.add(MailDraft(message_id="m-old", reply="Here is your answer"))
        db.session.commit()

        monkeypatch.setattr(mail_client, "api", lambda *a, **k: {})
        with pytest.raises(Exception) as excinfo:
            mail_service.send_reply("m-old")
        assert "更新的邮件" in str(excinfo.value)
        assert db.session.get(MailSend, "m-old") is None


def test_send_is_idempotent(app, connected, monkeypatch):
    with app.app_context():
        db.session.add(_message("m-once", thread_id="t-once", sent_at=1000))
        db.session.add(MailDraft(message_id="m-once", reply="Hello"))
        db.session.commit()
        mail_service.recompute_status(["m-once"])
        monkeypatch.setattr(mail_client, "api", lambda *a, **k: {})
        mail_service.send_reply("m-once")
        with pytest.raises(Exception) as excinfo:
            mail_service.send_reply("m-once")
        assert "已经回复过" in str(excinfo.value)
        assert MailSend.query.filter_by(message_id="m-once").count() == 1


def test_draft_requires_consent_and_deepseek_key(app, connected):
    with app.app_context():
        db.session.add(_message("m-draft", thread_id="t-draft", sent_at=1000))
        db.session.commit()
        with pytest.raises(Exception) as excinfo:
            mail_service.generate_draft("m-draft")
        assert "DeepSeek" in str(excinfo.value)
        mail_client.put_setting("consent", "true")
        with pytest.raises(Exception) as excinfo:
            mail_service.generate_draft("m-draft")
        assert "API Key" in str(excinfo.value)


def test_guidance_is_saved_and_blocks_auto_safe(app, connected, monkeypatch):
    with app.app_context():
        db.session.add(_message("m-guid", thread_id="t-guid", sent_at=1000))
        db.session.commit()
        mail_client.put_setting("consent", "true")
        mail_client.put_setting("deepseek", "sk-test")

        monkeypatch.setattr(
            mail_service,
            "_deepseek",
            lambda *a, **k: {
                "summary": "客户反映外壳破损",
                "category": "damaged",
                "confidence": 0.9,
                "reason": "需要索赔资料",
                "reply": "We are sorry about the damage.",
            },
        )
        draft = mail_service.generate_draft("m-guid", "先道歉，再让客户提供订单号和照片")
        assert draft["guidance"].startswith("先道歉")
        # 带了人工关键点，就必须人工确认后才能发
        assert draft["safe"] is False
        assert draft["reply"] == "We are sorry about the damage."


def test_plain_text_and_matching_helpers():
    assert "line1" in mail_service.plain_text("<p>line1</p><script>x</script>")
    assert mail_service.first_address("Support <Support@CaseEase.com>") == "support@caseease.com"
    assert mail_service.normalized_subject("Re: Fwd: Where is my order") == "where is my order"
    left = _message("a", thread_id="")
    left.rfc_message_id = "<abc@mail>"
    right = _message("b", thread_id="")
    right.references = ["<abc@mail>"]
    assert mail_service.matches(left, right) is True


def test_plain_text_collapses_excessive_blank_lines():
    """邮件正文里叠出来的三四个空行要压成一个空行。"""
    raw = (
        "You received a new message from your online store's contact form.<br><br><br><br>"
        "<div>Country Code:</div><div>US</div><br><br><br>"
        "<div>Name:</div><div>Megan Newton</div>"
    )
    text = mail_service.plain_text(raw)
    assert "\n\n\n" not in text
    assert text.startswith("You received a new message from your online store's contact form.")
    # 字段名和值会并成一行，字段之间的空行去掉
    assert "Country Code: US" in text
    assert "Name: Megan Newton" in text

    messy = "A\n\n\n\n\nB\n   \nC"
    assert mail_service.tidy_whitespace(messy) == "A\n\nB\n\nC"
    assert mail_service.tidy_whitespace("A\r\n\r\n\r\nB") == "A\n\nB"


def test_tidy_whitespace_reformats_shopify_form():
    """Shopify 联系表单：缩进 + 字段间空行，要排成能直接读的样子。"""
    raw = (
        " You received a new message from your online store's contact form.\n\n"
        "        Country Code:\n        US\n\n"
        "        Name:\n        Megan Newton\n\n"
        "        Email:\n        mbnewton89@gmail.com\n\n"
        "        Body:\n        Hi I ordered a Dopamine Ring stand case and it broke.\n"
    )
    assert mail_service.tidy_whitespace(raw) == (
        "You received a new message from your online store's contact form.\n\n"
        "Country Code: US\n"
        "Name: Megan Newton\n"
        "Email: mbnewton89@gmail.com\n"
        "Body: Hi I ordered a Dopamine Ring stand case and it broke."
    )


def test_strip_quoted_handles_outlook_dashed_separator():
    """Outlook 的 "---- On … wrote ----" 也要能切掉。"""
    body = (
        "Hi Megan,\n\nSorry about the broken ring.\n\n"
        "Best regards,\nCaseease Support Team  "
        "---- On Tue, 06 Oct 2026 13:54:40 -0400 Caseease (Shopify) "
        "<mailer@shopify.com> wrote ----\n\n"
        "You received a new message from your online store's contact form."
    )
    cleaned = mail_service.strip_quoted(body)
    assert cleaned.endswith("Caseease Support Team")
    assert "wrote ----" not in cleaned
    assert "contact form" not in cleaned


def test_strip_quoted_keeps_only_the_new_message():
    """正文里引用的历史要切掉，只留这封信自己的内容。"""
    gmail = (
        "Thank you for the offer, however I will be upgrading my phone.\n\n"
        "Can you please let me know when a refund can be issued.\n\n"
        "Kind Regards,\n\nSimone.\n"
        "On Wed, 23 Sep 2026 at 5:42 pm, CASEEASE <support_agent@caseease.com> wrote:\n\n"
        "Hi Simone,\n\nThank you for getting back to us...\n"
    )
    stripped = mail_service.strip_quoted(gmail)
    assert "upgrading my phone" in stripped
    assert "Kind Regards" in stripped
    assert "wrote:" not in stripped
    assert "Thank you for getting back to us" not in stripped

    chinese = "好的，我知道了。\n\n在 2026年9月23日 CASEEASE 写道：\n\n之前的内容"
    assert mail_service.strip_quoted(chinese) == "好的，我知道了。"

    outlook = (
        "Please refund me.\n\n"
        "From: CASEEASE <support@caseease.com>\n"
        "Sent: Wednesday, September 23, 2026 5:42 PM\n"
        "To: Simone\nSubject: Re: Order 8561\n\nold content"
    )
    assert mail_service.strip_quoted(outlook) == "Please refund me."

    # 整封都是引用（客户没写正文，只点了回复）→ 留空，内容在上一封里
    assert mail_service.strip_quoted("> 上一封的内容\n> 继续") == ""
    assert mail_service.strip_quoted("On Wed, 23 Sep 2026, CASEEASE <x@y.com> wrote:") == ""

    plain = "Just a normal reply without any quoting."
    assert mail_service.strip_quoted(plain) == plain


def test_strip_quoted_handles_flattened_summary():
    """Zoho 的摘要把换行压成空格，引用起点会出现在行中间——也要能切掉。"""
    flattened = (
        "Can this order be canceled? It's been more than 2 weeks since I placed it. "
        "On Fri, Sep 18, 2026 at 8:25 PM Caseease < support@caseease.com > wrote:"
    )
    assert mail_service.strip_quoted(flattened) == (
        "Can this order be canceled? It's been more than 2 weeks since I placed it."
    )

    with_arrows = (
        "Please confirm receipt of email  > On 3 Oct 2026, at 09:00, Josie <j@me.com> wrote: > > Hi > old"
    )
    assert mail_service.strip_quoted(with_arrows) == "Please confirm receipt of email"

    chinese_flattened = "麻烦帮我退款 在 2026年10月1日 CASEEASE 写道：之前的内容"
    assert mail_service.strip_quoted(chinese_flattened) == "麻烦帮我退款"

    # 中文客户端另一种写法："某某 <邮箱> 于…写道："
    chinese_yu = (
        "hello 已收到回复，后续由我为您提供谷歌广告官方服务 "
        "CASEEASE <support_agent@caseease.com> 于2025年7月28日周一 16:58写道："
    )
    assert mail_service.strip_quoted(chinese_yu) == "hello 已收到回复，后续由我为您提供谷歌广告官方服务"

    # 客户真实来信：正文后面跟了整段引用，只保留客户自己写的那几句
    simone = (
        "Thank you for the offer, however I will be upgrading my phone in a couple of weeks.\n\n"
        "Can you please let me know when a refund can be issued.\n\n"
        "Kind Regards,\n\nSimone. \n"
        "On Wed, 23 Sep 2026 at 5:42 pm, CASEEASE <support_agent@caseease.com> wrote:\n\n"
        "Hi Simone,\n\nWe sincerely apologize for the mix-up"
    )
    stripped_simone = mail_service.strip_quoted(simone)
    assert stripped_simone.endswith("Simone.")
    assert "wrote:" not in stripped_simone
    assert "sincerely apologize" not in stripped_simone
    # 邮箱里的 ">" 不能被当成引用符号吃掉
    assert "CASEEASE <support_agent@caseease.com>" not in stripped_simone


def test_backfill_bodies_cleans_previously_stored_quotes(app, connected):
    with app.app_context():
        db.session.add(
            _message(
                "quoted-1",
                thread_id="t-quoted",
                body="My new message.\n\nOn Wed, 23 Sep 2026, CASEEASE <x@y.com> wrote:\n\nold",
            )
        )
        db.session.commit()

        result = mail_service.backfill_bodies()
        assert result["bodies_cleaned"] == 1
        assert db.session.get(MailMessage, "quoted-1").body == "My new message."
        # 幂等
        assert mail_service.backfill_bodies()["bodies_cleaned"] == 0


def test_missing_thread_id_falls_back_to_own_id(app):
    """Zoho 的列表接口约四分之一邮件不给 threadId，缺了也不能让它隐身。"""
    with app.app_context():
        mail_client.put_setting("own", "support@caseease.com")
        without = mail_service.normalize(
            {
                "messageId": "m-1",
                "fromAddress": "customer@example.com",
                "summary": "hi",
                "subject": "Order 11978",
            },
            "Inbox",
        )
        assert without["thread_id"] == "m-1"

        with_thread = mail_service.normalize(
            {
                "messageId": "m-2",
                "threadId": "t-9",
                "fromAddress": "customer@example.com",
                "summary": "hi",
                "subject": "Order 11978",
            },
            "Inbox",
        )
        assert with_thread["thread_id"] == "t-9"


def test_message_time_prefers_sent_date_in_gmt(app):
    """时间一律用 sentDateInGMT。receivedTime 是"邮箱本地时区"的墙上时间，
    用它会把客户来信排到我方回复之前，导致"客户又来信"被误判成"已回复"。"""
    with app.app_context():
        mail_client.put_setting("own", "support@caseease.com")
        # 同一封邮件：receivedTime 比真实时间早 7 小时（真实邮箱就是这样）
        data = mail_service.normalize(
            {
                "messageId": "m-time",
                "fromAddress": "customer@example.com",
                "subject": "Order 1",
                "summary": "hi",
                "receivedTime": "1791311120410",
                "sentDateInGMT": "1791336316000",
            },
            "Inbox",
        )
        assert data["sent_at"] == 1791336316000

        # 出站邮件本来就用 sentDateInGMT，保持一致
        out = mail_service.normalize(
            {
                "messageId": "m-out",
                "fromAddress": "support@caseease.com",
                "toAddress": "customer@example.com",
                "subject": "Re: Order 1",
                "summary": "hello",
                "receivedTime": "1791310556770",
                "sentDateInGMT": "1791335756000",
            },
            "Sent",
        )
        assert out["sent_at"] == 1791335756000


def test_orphan_mail_still_shows_up_in_the_list(app, connected, client, admin_headers):
    """没有会话号的邮件补齐后必须出现在列表里（用户报的就是这类邮件"没同步"）。"""
    with app.app_context():
        db.session.add(
            _message(
                "orphan-1",
                thread_id="",  # Zoho 没给 threadId
                customer="ourhobbyfarmlife@gmail.com",
                subject="Cancel order Order #11978",
                sent_at=9_000_000,
                is_latest=False,
            )
        )
        db.session.commit()
        mail_service.backfill_status()

        row = db.session.get(MailMessage, "orphan-1")
        assert row.thread_id == "orphan-1"
        assert row.is_latest is True

    data = client.get("/api/mail/messages?shop_id=1&status=all", headers=admin_headers).get_json()["data"]
    assert "orphan-1" in {item["id"] for item in data["items"]}


def test_references_merge_orphan_back_into_real_thread(app, connected):
    """拿到邮件头后，用引用链把孤儿邮件并回真正的会话。"""
    with app.app_context():
        parent = _message("parent-1", thread_id="thread-real", sent_at=1000)
        parent.rfc_message_id = "<parent@mail>"
        orphan = _message("orphan-2", thread_id="orphan-2", sent_at=2000)
        orphan.references = ["<parent@mail>"]
        db.session.add_all([parent, orphan])
        db.session.commit()

        assert mail_service.resolve_thread_from_refs(orphan) == "thread-real"
        # 把自己的 id 从引用里排除，避免自引用
        orphan.references = ["<parent@mail>", "<orphan@mail>"]
        orphan.rfc_message_id = "<orphan@mail>"
        db.session.commit()
        assert mail_service.resolve_thread_from_refs(orphan) == "thread-real"


def test_opening_a_mail_cleans_stale_body(app, connected, client, admin_headers):
    """规则更新前缓存下来的正文，打开时要按新规则重新清洗（不再联网）。"""
    with app.app_context():
        db.session.add(
            _message(
                "stale-body",
                thread_id="t-stale",
                body=(
                    "  Hi\nMy case is broken.\n\n"
                    "On 22 Sep 2026, at 2:40 pm, Kayla <kaylamay@outlook.com> wrote:\n\n"
                    "        old quoted content"
                ),
            )
        )
        db.session.commit()

    response = client.get("/api/mail/messages/stale-body?shop_id=1", headers=admin_headers)
    assert response.status_code == 200
    text = response.get_json()["data"]["mail"]["text"]
    assert "wrote:" not in text
    assert "old quoted content" not in text
    assert text.startswith("Hi")
    assert "My case is broken." in text


def test_same_subject_reply_marks_verify_not_pending(app, connected):
    """会话号/引用链断了，但同客户同主题发过回复 → 只能标"待核查"，
    绝不能标成"尚未回复"，否则会让人重复回复客户。"""
    with app.app_context():
        db.session.add(
            _message("ask", thread_id="t-ask", customer="c@x.com", subject="Order 8561", sent_at=1000)
        )
        db.session.add(
            _message(
                "reply",
                thread_id="t-other",
                direction="out",
                customer="c@x.com",
                subject="Re: Order 8561",
                sent_at=2000,
            )
        )
        db.session.commit()

        mail_service.recompute_status(["ask"])
        row = db.session.get(MailMessage, "ask")
        assert row.status == "verify"
        assert "同主题" in row.evidence

        # 没有同主题回复时才是真的"尚未回复"
        db.session.add(
            _message("ask-2", thread_id="t-ask2", customer="d@x.com", subject="Where is my order", sent_at=3000)
        )
        db.session.commit()
        mail_service.recompute_status(["ask-2"])
        assert db.session.get(MailMessage, "ask-2").status == "pending"


def test_client_credentials_can_come_from_settings(app):
    """Client Secret 允许在界面里填（加密存库），不必改 .env 重启。"""
    with app.app_context():
        MailSetting.query.delete()
        db.session.commit()


def test_opening_a_mail_stays_fast_with_a_huge_mailbox(app, connected):
    """打开一封邮件只能是"几条按索引的查询"，不能随邮箱总量线性变慢。

    原项目就是在这里退化的：打开一封邮件会把整箱邮件读出来重新分类。
    """
    from sqlalchemy import event

    with app.app_context():
        db.session.bulk_save_objects(
            [
                MailMessage(
                    shop_id=1,
                    provider_message_id=f"noise-{index}",
                    id=f"noise-{index}",
                    thread_id=f"noise-{index}",
                    direction="in",
                    customer=f"c{index}@example.com",
                    subject="noise",
                    sent_at=1000 + index,
                    has_attachment=False,
                    is_system=False,
                    header_checked=True,
                    hydrated=True,
                    status="pending",
                    is_latest=True,
                )
                for index in range(3000)
            ]
        )
        db.session.add(_message("hot", thread_id="t-hot", sent_at=9_000_000))
        db.session.commit()

        statements = []
        engine = db.engine

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            detail = mail_service.get_message("hot")
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert detail["mail"]["id"] == "hot"
        # 主键查邮件 + 查会话 + 草稿 + 发送记录，几条就够；
        # 真出现全表扫描时这里会立刻飙到几十条以上。
        assert len(statements) <= 8, statements


def test_shopify_form_mail_without_email_line_does_not_crash(app):
    """Shopify 表单来信的正文里没有 "Email:" 行时，不能崩掉整次同步。"""
    with app.app_context():
        mail_client.put_setting("own", "support@caseease.com")
        data = mail_service.normalize(
            {
                "messageId": "form-1",
                "threadId": "tf",
                "fromAddress": "shopify@shopify.com",
                "summary": "Hi, my case arrived cracked",
                "subject": "New message",
            },
            "Inbox",
        )
        # 取不到真实客户邮箱就退回发件人，后续靠打开邮件时再解析正文
        assert data["customer"] == "shopify@shopify.com"

        with_line = mail_service.normalize(
            {
                "messageId": "form-2",
                "threadId": "tf",
                "fromAddress": "shopify@shopify.com",
                "summary": "Email: Buyer@Example.com\nMy case is broken",
                "subject": "New message",
            },
            "Inbox",
        )
        assert with_line["customer"] == "buyer@example.com"


def test_sync_skips_broken_rows_instead_of_failing(app, connected, monkeypatch):
    """一页里混进格式异常的行时，跳过它并记警告，整次同步仍然成功。"""
    with app.app_context():
        folders = [
            {"folderId": "in-1", "folderType": "Inbox"},
            {"folderId": "sent-1", "folderType": "Sent"},
        ]
        page = [
            {
                "messageId": "ok-1",
                "threadId": "t-ok",
                "fromAddress": "Customer <customer@example.com>",
                "summary": "Please help",
                "subject": "Broken case",
                "receivedTime": 5000,
                "hasAttachment": "0",
            },
            "这不是一条合法邮件",  # 脏数据
        ]

        def fake_api(path, method="GET", body=None, params=None, timeout=25):
            if path.endswith("/folders"):
                return folders
            # 只有收件箱那一页有内容，已发送文件夹返回空
            return page if (params or {}).get("folderId") == "in-1" else []

        monkeypatch.setattr(mail_client, "api", fake_api)
        result = mail_service.sync()

        assert result["processed"] == 2
        assert result["skipped"] == 1
        assert result["warnings"]
        assert db.session.get(MailMessage, "ok-1") is not None
        # 后面的用例还要用"历史已同步完"这个前提
        mail_client.put_setting("complete", "true")


def test_backfill_fixes_unclassified_rows_and_missing_latest(app, connected):
    """同步中断会留下两种残缺：没算过状态、会话没有"最新来信"。都要能补齐。"""
    with app.app_context():
        # 1) 从没算过状态（evidence 为空）
        db.session.add(
            _message("stale", thread_id="t-stale", sent_at=1000, is_latest=False)
        )
        # 2) 算过状态，但整个会话没有一封被标成"最新"
        orphan = _message("orphan", thread_id="t-orphan", sent_at=2000, is_latest=False)
        orphan.evidence = "占位"
        orphan.status = "pending"
        db.session.add(orphan)
        db.session.commit()

        result = mail_service.backfill_status()
        assert result["status_filled"] == 1
        assert result["threads_fixed"] == 1

        fixed = db.session.get(MailMessage, "stale")
        assert fixed.evidence
        assert fixed.status == "pending"
        assert fixed.is_latest is True
        assert db.session.get(MailMessage, "orphan").is_latest is True
        # 幂等：再跑一次没有东西可补
        assert mail_service.backfill_status() == {"status_filled": 0, "threads_fixed": 0}
        mail_service.save_settings({"client_id": "1000.TEST", "client_secret": "shhh"})
        client_id, client_secret = mail_client.client_credentials()
        assert (client_id, client_secret) == ("1000.TEST", "shhh")
        # 落库的是密文，不是明文
        stored = db.session.get(MailSetting, "s1:zoho_client_secret")
        assert stored.value != "shhh"
        assert mail_client.status()["credentials"] is True
        MailSetting.query.delete()
        db.session.commit()


def test_incremental_sync_keeps_completion_and_reads_from_the_top(app, connected, monkeypatch):
    """历史同步完之后，日常增量同步不能把进度标记冲掉，也不能拿 -1 当起始页。"""
    with app.app_context():
        mail_client.put_setting("complete", "false")
        mail_client.put_setting("cursor", "{}")
        folders = [
            {"folderId": "in-1", "folderType": "Inbox"},
            {"folderId": "sent-1", "folderType": "Sent"},
        ]
        starts = []

        def fake_api(path, method="GET", body=None, params=None, timeout=25):
            if path.endswith("/folders"):
                return folders
            starts.append((params or {}).get("start"))
            index = len(starts)
            return [
                {
                    "messageId": f"m-{index}",
                    "threadId": "t-1",
                    "fromAddress": "customer@example.com",
                    "summary": "hello",
                    "subject": "Help",
                    "receivedTime": 1000 + index,
                    "hasAttachment": "0",
                }
            ]

        monkeypatch.setattr(mail_client, "api", fake_api)

        first = mail_service.sync(history=True)
        assert first["complete"] is True
        starts.clear()

        second = mail_service.sync()
        assert second["complete"] is True
        # 两个文件夹都从最新一页（start=1）读，而不是历史游标的 -1
        assert starts == [1, 1]
        mail_client.put_setting("complete", "true")


def test_get_requests_retry_transient_network_errors(app, monkeypatch):
    """同步翻页时 Zoho 偶尔掐连接：GET 要自动重试，POST 不能重试（避免重复发信）。"""
    import requests

    with app.app_context():
        mail_client.put_setting("access", "token")
        mail_client.put_setting("access_expires", str(mail_client.now_ms() + 3_600_000))
        calls = {"GET": 0, "POST": 0}

        def fake_request(method, url, **kwargs):
            calls[method] += 1
            if method == "GET" and calls["GET"] == 1:
                raise requests.exceptions.SSLError("boom")

            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return {"data": {"ok": True}}

            return Response()

        monkeypatch.setattr(mail_client.requests, "request", fake_request)
        monkeypatch.setattr(mail_client.time, "sleep", lambda _seconds: None)

        assert mail_client.api("accounts/1/folders") == {"ok": True}
        assert calls["GET"] == 2

        # POST 不重试：宁可报错也不能冒重复发送的风险
        def always_fail(method, url, **kwargs):
            calls["POST"] += 1
            raise requests.exceptions.SSLError("boom")

        monkeypatch.setattr(mail_client.requests, "request", always_fail)
        with pytest.raises(Exception) as excinfo:
            mail_client.api("accounts/1/messages/2", method="POST", body={})
        assert "连接 Zoho 失败" in str(excinfo.value)
        assert calls["POST"] == 1


def test_manual_reply_saves_and_restores_without_generated_draft(app, connected, client, admin_headers):
    with app.app_context():
        db.session.add(_message("m-manual", thread_id="t-manual", sent_at=1000))
        db.session.commit()

    response = client.post(
        "/api/mail/messages/m-manual/reply?shop_id=1", headers=admin_headers,
        json={"reply": "Here is my own reply, typed by hand."},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["reply"] == "Here is my own reply, typed by hand."

    with app.app_context():
        draft = db.session.get(MailDraft, "m-manual")
        assert draft is not None
        assert draft.reply == "Here is my own reply, typed by hand."
        assert draft.edited is True
        assert draft.safe is False
        assert draft.generated_at is None

    detail = client.get("/api/mail/messages/m-manual?shop_id=1", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.get_json()["data"]["draft"]["reply"] == "Here is my own reply, typed by hand."

    empty = client.post(
        "/api/mail/messages/m-manual/reply?shop_id=1", headers=admin_headers, json={"reply": "   "},
    )
    assert empty.status_code == 400


def test_send_reply_with_images_uploads_and_attaches_metadata(app, connected, monkeypatch):
    with app.app_context():
        db.session.add(_message("m-attach", thread_id="t-attach", sent_at=1000))
        db.session.commit()

        uploaded = []

        def fake_upload(name, content, mimetype):
            uploaded.append((name, len(content), mimetype))
            return {"attachmentName": f"zoho-{name}", "attachmentPath": f"/mail/{name}",
                    "storeName": "853887433"}

        captured = {}

        def fake_api(path, method="GET", body=None, **kwargs):
            captured["path"] = path
            captured["body"] = body
            return {}

        monkeypatch.setattr(mail_client, "upload_attachment", fake_upload)
        monkeypatch.setattr(mail_client, "api", fake_api)
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

        result = mail_service.send_reply_with_attachments(
            "m-attach", "Please see the attached photo.", [("photo.png", png, "image/png")],
        )

        assert result["sent"] is True
        assert uploaded == [("photo.png", len(png), "image/png")]
        assert captured["body"]["attachments"] == [{
            "attachmentName": "zoho-photo.png",
            "attachmentPath": "/mail/photo.png",
            "storeName": "853887433",
        }]
        with app.app_context():
            send = db.session.get(MailSend, "m-attach")
            assert send.state == "sent"
            assert send.result["attachments"] == ["zoho-photo.png"]


def test_attachment_validation_rejects_non_images_and_oversize(app, connected):
    import io

    with app.app_context():
        db.session.add(_message("m-bad-attach", thread_id="t-bad-attach", sent_at=1000))
        db.session.commit()
    with pytest.raises(Exception) as excinfo:
        mail_service.send_reply_with_attachments(
            "m-bad-attach", "hello", [("notes.txt", b"not an image", "text/plain")],
        )
    assert "只支持" in str(excinfo.value)
    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 8
    with pytest.raises(Exception) as excinfo:
        mail_service.send_reply_with_attachments(
            "m-bad-attach", "hello", [("huge.png", fake_png, "image/png")] * 6,
        )
    assert "最多" in str(excinfo.value)


def test_send_with_attachments_endpoint_accepts_multipart(app, connected, client, admin_headers, monkeypatch):
    import io

    with app.app_context():
        db.session.add(_message("m-api-attach", thread_id="t-api-attach", sent_at=1000))
        db.session.commit()

    monkeypatch.setattr(mail_client, "upload_attachment", lambda name, content, mime: {
        "attachmentName": name, "attachmentPath": f"/mail/{name}", "storeName": "1"})
    monkeypatch.setattr(mail_client, "api", lambda *a, **k: {})
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    response = client.post(
        "/api/mail/messages/m-api-attach/send-with-attachments?shop_id=1",
        headers=admin_headers,
        data={"reply": "Photo attached.", "files": (io.BytesIO(png), "photo.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["sent"] is True
    with app.app_context():
        send = db.session.get(MailSend, "m-api-attach")
        assert send.result["attachments"] == ["photo.png"]


def test_send_reply_with_video_uploads_and_attaches_metadata(app, connected, monkeypatch):
    with app.app_context():
        db.session.add(_message("m-video", thread_id="t-video", sent_at=1000))
        db.session.commit()

    uploaded = []

    def fake_upload(name, content, mimetype):
        uploaded.append((name, len(content), mimetype))
        return {"attachmentName": f"zoho-{name}", "attachmentPath": f"/mail/{name}",
                "storeName": "853887433"}

    captured = {}

    def fake_api(path, method="GET", body=None, **kwargs):
        captured["body"] = body
        return {}

    monkeypatch.setattr(mail_client, "upload_attachment", fake_upload)
    monkeypatch.setattr(mail_client, "api", fake_api)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64

    result = mail_service.send_reply_with_attachments(
        "m-video", "Please see the attached video.", [("clip.mp4", mp4, "video/mp4")],
    )

    assert result["sent"] is True
    assert uploaded == [("clip.mp4", len(mp4), "video/mp4")]
    assert captured["body"]["attachments"] == [{
        "attachmentName": "zoho-clip.mp4", "attachmentPath": "/mail/clip.mp4",
        "storeName": "853887433",
    }]
    with app.app_context():
        assert db.session.get(MailSend, "m-video").result["attachments"] == ["zoho-clip.mp4"]

    with pytest.raises(Exception) as excinfo:
        mail_service.send_reply_with_attachments(
            "m-video", "fake video", [("fake.mp4", b"<html>not video</html>", "video/mp4")],
        )
    assert "内容与格式不符" in str(excinfo.value)
