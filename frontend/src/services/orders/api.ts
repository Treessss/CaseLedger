import { request } from '@umijs/max';

export interface OrderItem {
  id: number;
  /** 归属店铺：全部店铺视图下用来区分订单来自哪家店 */
  shop_id?: number | null;
  shop_name?: string | null;
  shopify_order_id: string;
  order_number: string;
  customer_email?: string;
  customer_name?: string;
  total_price: number;
  currency?: string;
  actual_received?: number;
  /** 到账口径：refund_pending=退款待结算估算；gateway=网关真实净额 */
  actual_received_source?: 'gateway' | 'estimated' | 'refund_pending';
  settlement_amount?: number | null;
  settlement_currency?: string | null;
  settlement_amount_cny?: number | null;
  settlement_exchange_rate?: number | null;
  exchange_rate_refresh_currencies?: string[];
  payment_method?: string;
  payment_fee: number;
  /** 手续费来源：gateway=网关真实值；estimated=费率表估算；null=还没有支付记录 */
  fee_source?: 'gateway' | 'estimated' | null;
  product_cost: number;
  shipping_cost: number;
  gross_profit?: number;
  profit_margin?: number;
  financial_status?: string;
  fulfillment_status?: string;
  warehouse_status?: 'pending' | 'shipped' | 'legacy_unverified' | 'legacy_partial_unverified';
  shipping_label_number?: string | null;
  /** List projection: Shopify-only tracking numbers; full fulfillments load in detail. */
  shopify_tracking_numbers?: string[];
  shipping_label_source?: string | null;
  expected_parcel_count?: number;
  parcels?: ShippingParcel[];
  shipped_at?: string | null;
  purchase_initiated?: boolean;
  procurement_status?: 'not_started' | 'purchasing' | 'shortage' | 'partially_stocked' | 'ready_to_ship' | 'stocked';
  procurement_needed_quantity?: number;
  procurement_stocked_quantity?: number;
  note?: string;
  fulfillments?: Array<{
    id?: string | null;
    status?: string | null;
    tracking_company?: string | null;
    tracking_numbers?: string[];
    tracking_urls?: string[];
    created_at?: string | null;
    updated_at?: string | null;
  }>;
  order_date?: string;
  created_at?: string;
  shopify_details_synced_at?: string | null;
  shipping_cost_cny?: number;
  shipping_cost_source?: string | null;
  fangguo_cost_cny?: number;
  gross_profit_cny: number;
  payments?: any[];
  // 新增分摊费用字段
  allocated_product_cost: number;
  allocated_shipping_cost: number;
  allocated_expenses?: {
    expense_count: number;
    total_amount: number;
    expenses: Array<{
      id: number;
      category: string;
      amount: number;
      description: string;
      date: string;
    }>;
  };
}

export interface ShippingParcel {
  id: number;
  label_number: string;
  label_barcode?: string | null;
  source: string;
  scanned_at?: string | null;
  scanned_by_user_id?: number | null;
  created_at?: string | null;
}

export async function fetchFourPxLabel(orderId: number, parcelId: number) {
  return request<{ success: boolean; data: {
    order_id: number; parcel_id: number; tracking_number: string;
    label_barcode: string; pdf_url: string;
  } }>(`/api/orders/${orderId}/parcels/${parcelId}/4px-label`, { method: 'POST' });
}

export interface ParcelWorkflowData {
  order_id: number;
  warehouse_status: string;
  expected_parcel_count: number;
  scanned_parcel_count: number;
  parcels: ShippingParcel[];
  shipped_at?: string | null;
}

export async function writebackShopifyTracking(orderId: number) {
  return request<{ success: boolean; data: { status: string; tracking_numbers: string[]; warehouse_status: string;
    customer_notification_requested: boolean } }>(
    `/api/orders/${orderId}/shopify-writeback`, { method: 'POST', timeout: 240000 },
  );
}

export interface OrderListParams {
  page?: number;
  per_page?: number;
  status?: string;
  financial_status?: string;
  fulfillment_status?: string;
  warehouse_status?: 'pending' | 'shipped' | 'legacy_unverified' | 'legacy_partial_unverified';
  payment_method?: string;
  start_date?: string;
  end_date?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}

export interface OrderListResponse {
  success: boolean;
  data: {
    orders: OrderItem[];
    exchange_rate_refresh_currencies?: string[];
    pagination: {
      page: number;
      per_page: number;
      total: number;
      pages: number;
    };
  };
  message?: string;
}

export interface OrderDetailResponse {
  success: boolean;
  data: OrderItem;
  message?: string;
}

export interface OrderNeighbor {
  id: number;
  order_number: string;
}

export interface OrderNeighborsResponse {
  success: boolean;
  data: {
    previous: OrderNeighbor | null;
    next: OrderNeighbor | null;
  };
  message?: string;
}

/** 获取订单列表 */
export async function getOrders(params?: OrderListParams) {
  return request<OrderListResponse>('/api/orders', {
    method: 'GET',
    params,
  });
}

/** 获取订单详情 */
export async function getOrderDetail(orderId: number) {
  return request<OrderDetailResponse>(`/api/orders/${orderId}`, {
    method: 'GET',
  });
}

/** 获取当前店铺中相邻的订单（与订单列表默认倒序一致） */
export async function getOrderNeighbors(orderId: number) {
  return request<OrderNeighborsResponse>(`/api/orders/${orderId}/neighbors`, {
    method: 'GET',
  });
}

/** 更新订单 */
export async function updateOrder(orderId: number, data: Partial<OrderItem>) {
  return request<OrderDetailResponse>(`/api/orders/${orderId}`, {
    method: 'PUT',
    data,
  });
}

/**
 * 从 Shopify 回源拉取客户信息与收货地址。
 *
 * REST 同步拿到的地址常常只有国家/省份，详情页按需通过 Admin GraphQL 再拉一次。
 */
export async function refreshOrderContact(orderId: number) {
  return request<OrderDetailResponse>(`/api/orders/${orderId}/refresh-contact`, {
    method: 'POST',
  });
}

export async function saveShippingLabel(orderId: number, shippingLabelNumber: string) {
  return request<{ success: boolean; data: ParcelWorkflowData }>(`/api/orders/${orderId}/shipping-label`, {
    method: 'PUT', data: { shipping_label_number: shippingLabelNumber },
  });
}

export async function setOrderParcelCount(orderId: number, expectedParcelCount: number) {
  return request<{ success: boolean; data: ParcelWorkflowData }>(`/api/orders/${orderId}/parcel-count`, {
    method: 'PUT', data: { expected_parcel_count: expectedParcelCount },
  });
}

export async function addOrderParcel(orderId: number, shippingLabelNumber: string) {
  return request<{ success: boolean; data: ParcelWorkflowData }>(`/api/orders/${orderId}/parcels`, {
    method: 'POST', data: { shipping_label_number: shippingLabelNumber },
  });
}

export async function deleteOrderParcel(orderId: number, parcelId: number) {
  return request<{ success: boolean; data: ParcelWorkflowData }>(`/api/orders/${orderId}/parcels/${parcelId}`, {
    method: 'DELETE',
  });
}

export async function scanOrderOutbound(orderId: number, shippingLabelNumber: string) {
  return request<{ success: boolean; data: ParcelWorkflowData }>(`/api/orders/${orderId}/scan-outbound`, {
    method: 'POST', data: { shipping_label_number: shippingLabelNumber },
  });
}

/** 获取最近订单 */
export async function getRecentOrders() {
  return request('/api/orders/recent', {
    method: 'GET',
  });
}

/** 获取订单统计 */
export async function getOrderStats() {
  return request('/api/orders/stats', {
    method: 'GET',
  });
}

/** 该订单属于哪些采购清单（订单详情反向跳转用） */
export async function getOrderPurchaseLists(orderId: number) {
  return request<{
    success: boolean;
    data: Array<{ id: number; name: string; status: string; order_count: number }>;
  }>(`/api/orders/${orderId}/purchase-lists`, { method: 'GET' });
}
export interface FourPxFreight {
  order_id: number;
  tracking_number?: string | null;
  charge_weight_kg?: number | null;
  currency?: string | null;
  total_fee?: number | null;
  subs?: Array<{ fee_name: string; fee_amount: string | number; currency: string }>;
  billed_at?: string | null;
  synced_at?: string | null;
}

/** 同步 4PX 实际出账费用（真实计费重/明细/总价） */
export async function syncFourPxFreight(orderIds: number[]) {
  return request<{ success: boolean; data: { summary: { requested: number; synced: number; failed: number }; results: any[] } }>(
    '/api/shipping/4px/freight/sync',
    { method: 'POST', data: { order_ids: orderIds } },
  );
}
