"""Read-only shipment events and explicitly confirmed exchange discounts."""
from datetime import datetime
import re

from app.services import shopify_graphql as graphql
from app.utils.errors import ValidationError

TRACKING_QUERY = '''
query SupportTracking($id: ID!) {
  order(id: $id) {
    fulfillments(first: 50) {
      id deliveredAt status
      trackingInfo { number url company }
      events(first: 1, reverse: true, sortKey: HAPPENED_AT) {
        nodes { happenedAt status message }
      }
    }
  }
}
'''

DISCOUNT_QUERY = '''
query SupportDiscount($code: String!) {
  codeDiscountNodeByCode(code: $code) {
    id
    codeDiscount {
      ... on DiscountCodeBasic {
        title status startsAt endsAt usageLimit asyncUsageCount
        minimumRequirement { __typename }
        combinesWith { orderDiscounts productDiscounts shippingDiscounts }
        customerGets {
          value { ... on DiscountPercentage { percentage } }
          items { ... on AllDiscountItems { allItems } }
        }
        context { __typename }
      }
    }
  }
}
'''

DISCOUNT_MUTATION = '''
mutation SupportDiscountCreate($input: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $input) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
'''


def shipment_events(order):
    data = graphql._execute(order.shop, TRACKING_QUERY,
                            {'id': graphql.gid_for_order(order.shopify_order_id)}, timeout=15)
    remote = data.get('order')
    if not isinstance(remote, dict):
        raise ValidationError('Shopify 无法查询该订单物流')
    result = []
    for fulfillment in remote.get('fulfillments') or []:
        if fulfillment.get('status') == 'CANCELLED':
            continue
        events = (fulfillment.get('events') or {}).get('nodes') or []
        latest = events[0] if events else None
        for tracking in fulfillment.get('trackingInfo') or []:
            result.append({**tracking, 'latest': latest, 'delivered_at': fulfillment.get('deliveredAt'),
                           'source': 'Shopify fulfillment event'})
    return result


def exchange_code(order):
    number = str(order.order_number or '').strip().lstrip('#')
    if not re.fullmatch(r'[0-9]{1,30}', number):
        raise ValidationError('原订单号不符合折扣码格式，请人工核对')
    return number+'exchange'


def read_discount(order):
    """Read/validate only. Never create a discount while drafting."""
    code = exchange_code(order)
    data = graphql._execute(order.shop, DISCOUNT_QUERY, {'code': code})
    existing = data.get('codeDiscountNodeByCode')
    if existing:
        discount = existing.get('codeDiscount') or {}
        value = (discount.get('customerGets') or {}).get('value') or {}
        items = (discount.get('customerGets') or {}).get('items') or {}
        compatible = (discount.get('status') == 'ACTIVE'
                      and discount.get('usageLimit') == 1 and not discount.get('endsAt')
                      and discount.get('minimumRequirement') is None
                      and value.get('percentage') == 0.6 and items.get('allItems') is True
                      and (discount.get('context') or {}).get('__typename') == 'DiscountBuyerSelectionAll'
                      and all(discount.get('combinesWith', {}).get(k) is False
                              for k in ('orderDiscounts', 'productDiscounts', 'shippingDiscounts')))
        if discount.get('asyncUsageCount', 0) > 0:
            raise ValidationError('此订单折扣码已使用，请人工处理，不会自动创建第二个')
        if not compatible:
            raise ValidationError('同名折扣码已存在但规则不一致或不可用，请到 Shopify 核对')
        return {'code': code, 'id': existing['id'], 'reused': True}
    return None


def confirmed_discount(order):
    """Only after explicit confirmation: deterministic read-before-create is retry-safe."""
    code = exchange_code(order)
    existing = read_discount(order)
    if existing:
        return existing
    variables = {'input': {
        'title': 'CaseLedger exchange '+str(order.id), 'code': code,
        'startsAt': datetime.utcnow().isoformat()+'Z', 'endsAt': None,
        'context': {'all': 'ALL'},
        'customerGets': {'value': {'percentage': 0.6}, 'items': {'all': True}},
        'usageLimit': 1, 'appliesOncePerCustomer': True,
        'combinesWith': {'orderDiscounts': False, 'productDiscounts': False, 'shippingDiscounts': False},
    }}
    result = graphql._execute(order.shop, DISCOUNT_MUTATION, variables).get('discountCodeBasicCreate') or {}
    if result.get('userErrors') or not (result.get('codeDiscountNode') or {}).get('id'):
        raise ValidationError('Shopify 未确认折扣创建成功，请核对权限或同名代码后重试')
    return {'code': code, 'id': result['codeDiscountNode']['id'], 'reused': False}
