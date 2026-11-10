"""Support policies and external writes remain deterministic, scoped and human-confirmed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import db
from app.models import MailMessage, MailDraft, MailSend, Order
from app.services import mail_client, mail_service
from app.services import mail_support_service as service
from app.services import mail_support_shopify as shopify
from app.services.mail_scope import mailbox_scope
from app.utils.errors import ValidationError


@pytest.fixture()
def support(app, monkeypatch):
    with app.app_context(), mailbox_scope(1):
        mail_client.put_setting('consent', 'true')
        row = MailMessage(id='support', provider_message_id='support', shop_id=1,
            thread_id='thread-support', direction='in', sender='support-test@example.com',
            customer='support-test@example.com', subject='My order #10472', body='My case is broken',
            hydrated=True, header_checked=True, status='pending', is_latest=True,
            sent_at=int(datetime.utcnow().timestamp()*1000))
        order = Order(shop_id=1, shopify_order_id='10472', order_number='10472',
            customer_email='support-test@example.com', total_price=20, subtotal_price=20,
            order_date=datetime.utcnow(), financial_status='paid', warehouse_status='pending')
        db.session.add_all([row, order])
        db.session.commit()
        monkeypatch.setattr(shopify, 'shipment_events', lambda *_: [])
        yield row, order


def decision(row, order, intent='damaged', **overrides):
    facts = {'photos_verified': True, 'fault': 'unknown', 'damage_timing': 'after_use', 'received_date': ''}
    facts.update(overrides.pop('facts', {}))
    return service.rule_decision(intent, row, order, overrides.pop('events', []), facts,
                                 overrides.pop('assessment', {}), [row])


def delivery_event(days):
    return {'number': 'TEST', 'delivered_at': (datetime.now(timezone.utc)-timedelta(days=days)).isoformat()}


@pytest.mark.parametrize('days, timing, action, eligible', [
    (3, 'after_use', 'free_replacement', False),
    (10, 'after_use', 'confirm_discount', True),
    (10, 'on_arrival', 'manual_review', False),
    (10, 'unknown', 'manual_review', False),
])
def test_damage_time_and_late_arrival_review(support, days, timing, action, eligible):
    row, order = support
    result = decision(row, order, events=[delivery_event(days)], facts={'damage_timing': timing})
    assert result['action'] == action
    assert result['discount_eligible'] is eligible


def test_photos_delivery_missing_and_wrong_item(support):
    row, order = support
    assert decision(row, order)['action'] == 'ask_received_date'
    assert decision(row, order, facts={'photos_verified': False})['action'] == 'request_photos'
    row.has_attachment = True
    assert decision(row, order, facts={'photos_verified': False})['action'] == 'verify_photos'
    row.has_attachment = False
    assert decision(row, order, facts={'photos_verified': False}, assessment={'photos_supplied': True})['action'] == 'verify_photos'
    assert decision(row, order, intent='wrong_item', facts={'fault': 'customer'})['discount_eligible']
    assert decision(row, order, intent='wrong_item', facts={'fault': 'merchant'})['action'] == 'free_replacement'
    assert not decision(row, order, intent='wrong_item')['discount_eligible']


def test_first_issue_report_controls_seven_day_window(support):
    row, order = support
    first = SimpleNamespace(id='first', direction='in', sent_at=row.sent_at-8*86400000)
    result = service.rule_decision('damaged', row, order, [delivery_event(10)], service.clean_facts(
        {'photos_verified': True, 'damage_timing': 'on_arrival'}), {'first_issue_message_id': 'first'}, [first, row])
    assert result['action'] == 'free_replacement'


@pytest.mark.parametrize('intent', ['order_change', 'address_change', 'cancellation'])
def test_warehouse_scan_not_shopify_status_is_cutoff(support, intent):
    row, order = support
    order.fulfillment_status = 'fulfilled'
    order.shipping_label_number = '4PXTEST'
    result = decision(row, order, intent=intent)
    assert result['action'] in ('modify_order', 'cancel_refund')
    assert any('4PX' in action for action in result['actions'])
    order.warehouse_status = 'shipped'
    assert decision(row, order, intent=intent)['action'] == 'manual_review'


def test_partial_scan_and_stale_tracking_are_exceptions(support):
    row, order = support
    proxy = SimpleNamespace(warehouse_status='pending', shipped_at=None,
                            parcels=[SimpleNamespace(scanned_at=datetime.utcnow())])
    assert decision(row, proxy, intent='cancellation')['action'] == 'manual_review'
    order.warehouse_status = 'shipped'
    events = [{'latest': {'happenedAt': (datetime.now(timezone.utc)-timedelta(days=8)).isoformat(),
                         'status': 'IN_TRANSIT', 'message': 'In transit'}}]
    assert decision(row, order, intent='shipping', events=events)['action'] == 'logistics_exception'
    events[0]['latest']['status'] = 'DELIVERED'
    assert decision(row, order, intent='shipping', events=events)['action'] == 'tracking_reply'
    assert decision(row, order, intent='delivered_missing')['action'] == 'logistics_exception'


def test_generation_combines_history_and_never_sends_or_creates_discount(support, monkeypatch):
    row, order = support
    db.session.add(MailMessage(id='support-history', shop_id=1, provider_message_id='hist',
        thread_id=row.thread_id, direction='out', customer=row.customer, hydrated=True,
        sent_at=row.sent_at-1000, body='We previously asked for photos', status='replied'))
    db.session.commit()
    calls = []
    def ai(messages, **kwargs):
        calls.append(messages)
        return {'intent': 'customer_wrong', 'summary': '客户下错', 'language': 'French',
                'reason': '型号不符', 'reply': 'Bonjour, nous pouvons vous aider.'}
    monkeypatch.setattr(mail_service, '_deepseek', ai)
    monkeypatch.setattr(shopify, 'confirmed_discount', lambda *_: pytest.fail('Automatic discount creation'))
    monkeypatch.setattr(mail_client, 'api', lambda *_args, **_kwargs: pytest.fail('Automatic email sending'))
    draft = service.generate(row.id, facts={'fault': 'customer'})
    assert len(calls) == 2 and 'previously asked' in calls[0][1]['content']
    assert draft['safe'] is False and not draft['stale']
    assert draft['analysis']['discount_eligible'] and draft['analysis']['language'] == 'French'
    assert MailSend.query.count() == 0
    with pytest.raises(ValidationError, match='禁止自动发送'):
        mail_service.send_reply(row.id, automatic=True)


def test_mail_draft_uses_actual_carrier_event_when_shopify_has_none(support, monkeypatch):
    row, order = support
    order.warehouse_status = 'shipped'
    db.session.commit()
    number = '4PX3003188042135CN'
    monkeypatch.setattr(shopify, 'shipment_events', lambda *_: [
        {'number': number, 'url': 'https://track.4px.com/#/result/0/' + number, 'latest': None}])
    facts = {'happenedAt': '2026-10-04T23:16:45+00:00', 'status': 'DELIVERED',
             'message': 'Delivered-Left at safe place'}
    def enrich(shop, events):
        assert shop.id == order.shop_id and events[0]['number'] == number
        return [{**events[0], 'latest': facts, 'delivered_at': facts['happenedAt'],
                 'source': '4PX official tracking API'}]
    monkeypatch.setattr(service.mail_tracking_service, 'enrich_tracking', enrich)
    prompts = []
    def ai(messages, **_kwargs):
        prompts.append(messages)
        return {'intent': 'shipping', 'reply': 'Your parcel was delivered to a safe place.'}
    monkeypatch.setattr(mail_service, '_deepseek', ai)
    monkeypatch.setattr(mail_client, 'api', lambda *_a, **_k: pytest.fail('Must not send mail'))
    result = service.generate(row.id)
    assert result['analysis']['tracking'][0]['latest'] == facts
    assert result['analysis']['action'] == 'tracking_reply'  # Delivered >7 days is not stale in transit.
    assert 'Delivered-Left at safe place' in prompts[1][1]['content']
    assert MailSend.query.count() == 0


def test_complete_recent_carrier_cache_skips_shopify_tracking_call(support, monkeypatch):
    from app.models import TrackingRecord
    row, order = support
    order.shipping_label_number = '4PXCACHED'
    order.shipping_label_source = '4px'
    order.warehouse_status = 'shipped'
    db.session.add(TrackingRecord(shop_id=1, number='4PXCACHED', checked_at=datetime.utcnow(),
        latest={'happenedAt': datetime.utcnow().isoformat()+'Z', 'status': 'FPX_O_RR', 'message': 'Sorted'}))
    db.session.commit()
    monkeypatch.setattr(shopify, 'shipment_events', lambda *_: pytest.fail('Unneeded Shopify request'))
    monkeypatch.setattr(mail_service, '_deepseek', lambda *_a, **_k: {'intent': 'shipping', 'reply': 'Your parcel was sorted.'})
    result = service.generate(row.id)
    assert result['analysis']['tracking'][0]['latest']['message'] == 'Sorted'
    assert not result['stale']


def test_new_customer_message_expires_draft_and_blocks_old_generation(support, monkeypatch):
    row, order = support
    monkeypatch.setattr(mail_service, '_deepseek', lambda *_args, **_kwargs: {'intent': 'other', 'reply': 'Draft'})
    service.generate(row.id)
    assert not service.draft_state(row, db.session.get(MailDraft, row.id))['stale']
    db.session.add(MailMessage(id='support-new', shop_id=1, provider_message_id='new', thread_id=row.thread_id,
        customer=row.customer, direction='in', sent_at=row.sent_at+1000, body='New question', hydrated=True))
    db.session.commit()
    assert service.draft_state(row, db.session.get(MailDraft, row.id))['stale']
    with pytest.raises(ValidationError, match='最新邮件'):
        service.generate(row.id)
    with pytest.raises(ValidationError, match='过期'):
        mail_service.send_reply(row.id)


def test_changed_guidance_and_order_expire_drafts(support, monkeypatch):
    row, order = support
    monkeypatch.setattr(mail_service, '_deepseek', lambda *_args, **_kwargs: {'intent': 'other', 'reply': 'Draft'})
    result = service.generate(row.id)
    assert not result['stale']
    order.warehouse_status = 'shipped'
    db.session.commit()
    assert service.draft_state(row, db.session.get(MailDraft, row.id))['stale']
    result = service.generate(row.id)
    assert not result['stale']
    mail_service.save_guidance(row.id, '新的处理要求')
    assert service.draft_state(row, db.session.get(MailDraft, row.id))['stale']


@pytest.mark.parametrize('intent,action', [('acknowledgement', 'acknowledge'), ('product_question', 'product_advice')])
def test_thanks_and_product_questions_do_not_require_an_order(support, intent, action):
    row, _order = support
    result = decision(row, None, intent=intent)
    assert result['action'] == action
    assert result['missing_evidence'] == [] and not result['discount_eligible']


def test_confirmation_inquiry_uses_unique_order_without_reasking_order_number(support):
    row, order = support
    assert decision(row, order, intent='order_confirmation')['action'] == 'order_confirmation_reply'
    assert decision(row, None, intent='order_confirmation')['action'] == 'identify_order'


def test_unavailable_evidence_goes_to_review_not_repeated_requests_or_free_remedy(support):
    row, order = support
    result = decision(row, order, facts={'photos_verified': False}, assessment={'evidence_unavailable': True})
    assert result['action'] == 'manual_review'
    assert not result['discount_eligible']
    assert any('不重复要求' in item for item in result['missing_evidence'])


def test_details_overlay_new_tracking_but_do_not_rewrite_saved_reply(support):
    from app.models import TrackingRecord
    row, order = support
    latest = {'happenedAt': '2026-09-02T07:00:00+00:00', 'status': 'DELIVERED', 'message': 'Collected from pick up location'}
    draft = MailDraft(message_id=row.id, reply='Staff-edited draft: awaiting tracking', edited=True,
        analysis={'intent': 'shipping', 'order_id': order.id, 'policy_version': service.POLICY_VERSION,
                  'tracking': [{'number': '4PXEXACT', 'latest': None}],
                  'warnings': ['未取得可靠最新轨迹，不能编造物流状态或判断7天未更新']})
    db.session.add_all([draft, TrackingRecord(shop_id=1, number='4PXEXACT', latest=latest,
        checked_at=datetime.utcnow(), delivered_at=datetime(2026, 9, 2, 7))])
    db.session.commit()
    result = service.draft_state(row, draft)
    assert result['analysis']['tracking'][0]['latest'] == latest
    assert result['analysis']['tracking_updated'] and result['stale']
    assert result['analysis']['warnings'] == []
    assert draft.reply == result['reply'] == 'Staff-edited draft: awaiting tracking'
    assert draft.analysis['tracking'][0]['latest'] is None
    assert not db.session.dirty


def test_policy_update_expires_old_ai_drafts_and_new_rules_are_used(support, monkeypatch):
    row, _order = support
    prompts = []
    def ai(messages, **_kwargs):
        prompts.append(messages)
        return {'intent': 'acknowledgement', 'reply': 'Thank you!'}
    monkeypatch.setattr(mail_service, '_deepseek', ai)
    result = service.generate(row.id)
    assert result['analysis']['action'] == 'acknowledge'
    assert result['analysis']['policy_version'] == service.POLICY_VERSION
    assert not result['stale']
    assert all('two-hour cancellation cutoff' in prompt[0]['content'] for prompt in prompts)
    draft = db.session.get(MailDraft, row.id)
    draft.analysis = {**draft.analysis, 'policy_version': 'old'}
    assert service.draft_state(row, draft)['stale']
    result = service.generate(row.id)
    assert not result['stale']
    mail_service.save_guidance(row.id, '新的处理要求')
    assert service.draft_state(row, db.session.get(MailDraft, row.id))['stale']


def test_unrelated_orders_and_shop_scope_are_rejected(support, client, admin_headers, limited_headers):
    row, order = support
    with pytest.raises(ValidationError):
        service.selected_order(row, requested=order.id+100)
    path = '/api/mail/messages/support/exchange-discount'
    assert client.post(path+'?shop_id=1', json={'confirmed': True}).status_code == 401
    assert client.post(path+'?shop_id=1', json={'confirmed': True}, headers=limited_headers).status_code == 403
    assert client.post(path+'?shop_id=2', json={'confirmed': True}, headers=admin_headers).status_code == 404
    assert client.post(path+'?shop_id=1', json={}, headers=admin_headers).status_code == 400


def existing_discount(order, **overrides):
    record = {'title': 'CaseLedger exchange '+str(order.id), 'status': 'ACTIVE', 'usageLimit': 1,
              'endsAt': None, 'asyncUsageCount': 0,
              'combinesWith': {'orderDiscounts': False, 'productDiscounts': False, 'shippingDiscounts': False},
              'context': {'__typename': 'DiscountBuyerSelectionAll'},
              'customerGets': {'value': {'percentage': 0.6}, 'items': {'allItems': True}}}
    record.update(overrides)
    return {'codeDiscountNodeByCode': {'id': 'gid://shopify/DiscountCodeNode/1', 'codeDiscount': record}}


def test_discount_creation_and_reuse_are_exact_and_single_use(support, monkeypatch):
    row, order = support
    calls = []
    def execute(shop, query, variables, **kwargs):
        calls.append((query, variables))
        if query == shopify.DISCOUNT_QUERY:
            return {'codeDiscountNodeByCode': None}
        return {'discountCodeBasicCreate': {'codeDiscountNode': {'id': 'discount-1'}, 'userErrors': []}}
    monkeypatch.setattr(shopify.graphql, '_execute', execute)
    result = shopify.confirmed_discount(order)
    assert result['code'] == '10472exchange'
    values = calls[-1][1]['input']
    assert values['customerGets'] == {'value': {'percentage': 0.6}, 'items': {'all': True}}
    assert values['usageLimit'] == 1 and values['endsAt'] is None
    assert values['context'] == {'all': 'ALL'}
    assert not any(values['combinesWith'].values())
    monkeypatch.setattr(shopify.graphql, '_execute', lambda *_args, **_kwargs: existing_discount(order))
    assert shopify.confirmed_discount(order)['reused']
    monkeypatch.setattr(shopify.graphql, '_execute', lambda *_args, **_kwargs: existing_discount(order, asyncUsageCount=1))
    with pytest.raises(ValidationError, match='已使用'):
        shopify.confirmed_discount(order)
    monkeypatch.setattr(shopify.graphql, '_execute', lambda *_args, **_kwargs: existing_discount(order, usageLimit=2))
    with pytest.raises(ValidationError, match='规则不一致'):
        shopify.confirmed_discount(order)


def test_confirmation_is_required_and_preserves_discount_if_ai_fails(support, monkeypatch):
    row, order = support
    monkeypatch.setattr(mail_service, '_deepseek', lambda *_args, **_kwargs: {'intent': 'customer_wrong', 'reply': 'Draft'})
    service.generate(row.id, facts={'fault': 'customer'})
    with pytest.raises(ValidationError, match='明确确认'):
        service.confirm_discount(row.id, {})
    monkeypatch.setattr(shopify, 'confirmed_discount', lambda *_: {'code': '10472exchange', 'id': 'discount', 'reused': False})
    def failure(*args, **kwargs):
        raise ValidationError('AI 暂时失败')
    monkeypatch.setattr(mail_service, '_deepseek', failure)
    with pytest.raises(ValidationError, match='AI'):
        service.confirm_discount(row.id, {'confirmed': True})
    assert db.session.get(MailDraft, row.id).analysis['discount']['code'] == '10472exchange'
    assert MailSend.query.count() == 0
