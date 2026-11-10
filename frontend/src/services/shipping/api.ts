import { request } from '@umijs/max';

export interface PackingLine {
  title: string;
  variant_title: string;
  sku: string;
  image_url: string;
  quantity: number;
}

export interface PackingList {
  priority?: number;
  packing?: {id: number; state: string; user_id?: number; expires_at: string} | null;
  type: 'order' | 'after_sale';
  id: number;
  reference: string;
  original_order_number?: string | null;
  customer_name?: string | null;
  shipping_address?: Record<string, string | null>;
  status: string;
  cancelled?: boolean;
  financial_status?: string;
  reason?: string | null;
  note?: string | null;
  /** Shopify remark on the original order, including after-sale packing lists. */
  order_note?: string | null;
  labels: Array<{ tracking_number?: string | null; barcode?: string | null; scanned: boolean }>;
  expected_parcel_count: number;
  items: PackingLine[];
  total_quantity: number;
  /**
   * 这一枪扫完之后能不能直接确认出库。
   *
   * 打包台把「核对」和「出库」合并在同一页，靠的就是这份判断；
   * 规则与发货中心的扫码出库完全一致（后端共用同一段校验）。
   */
  outbound?: {
    /** true = 再扫一次同一张面单就能出库 */
    ready: boolean;
    /** 拦住出库的原因：商品没备齐、标签不齐、未付款…… */
    blockers: string[];
    /** 本次扫的编号是不是出库要扫的那张面单 */
    code_matches_label: boolean;
    /** 这张面单已经出过库了 */
    already_scanned: boolean;
    expected_parcel_count: number;
    scanned_parcel_count: number;
  };
}

export interface PackingTarget { shop_id: number; kind: 'order' | 'after_sale'; id: number }

export async function startPacking(target: PackingTarget) {
  return request<{success: boolean; data: NonNullable<PackingList['packing']>}>('/api/shipping/packing/start', {method: 'POST', data: target});
}

export async function cancelPacking(target: PackingTarget) {
  return request<{success: boolean; data: NonNullable<PackingList['packing']>}>('/api/shipping/packing/cancel', {method: 'POST', data: target});
}

export async function setPackingPriority(target: PackingTarget, priority: 0 | 100) {
  return request<{success: boolean; data: {id: number; priority: number}}>('/api/shipping/packing/priority', {method: 'POST', data: {...target, priority}});
}

export async function lookupPackingList(shopId: number, code: string) {
  return request<{ success: boolean; data: PackingList }>('/api/shipping/packing-lookup', {
    method: 'GET', params: { shop_id: shopId, code }, skipErrorHandler: true,
  });
}

export interface ReadyOrder {
  id: number;
  shop_id: number;
  order_number: string;
  customer_name?: string | null;
  order_date: string;
  expected_parcel_count: number;
  registered_parcel_count: number;
  scanned_parcel_count: number;
  label_state: 'awaiting_labels' | 'ready_to_scan' | 'partially_scanned';
}

export interface ReadySummary {
  ready_orders: number;
  awaiting_labels: number;
  ready_to_scan: number;
  partially_scanned: number;
  after_sale_ready: number;
}

export interface ReadyAfterSale {
  id: number;
  customer_reference: string;
  order_id: number;
  order_number: string;
  tracking_number?: string;
  label_barcode?: string;
  created_at: string;
}

export async function listReadyOrders(params: {
  shop_id: number;
  page?: number;
  per_page?: number;
  search?: string;
}) {
  return request<{
    success: boolean;
    data: {
      orders: ReadyOrder[];
      after_sales: ReadyAfterSale[];
      summary: ReadySummary;
      pagination: { page: number; per_page: number; total: number; pages: number };
    };
  }>('/api/shipping/ready', { method: 'GET', params });
}

/** Merge every currently ready-to-scan 4PX parcel in this shop, across all list pages. */
export async function downloadReadyLabelsPdf(shopId: number) {
  return request<Blob>('/api/shipping/ready-labels/pdf', {
    method: 'GET', params: { shop_id: shopId }, responseType: 'blob', timeout: 300000,
  });
}

export async function scanShippingLabel(shopId: number, labelNumber: string) {
  return request<{
    success: boolean;
    data: {
      order_id: number;
      order_number: string;
      warehouse_status: string;
      expected_parcel_count: number;
      scanned_parcel_count: number;
    };
  }>('/api/shipping/scan', {
    method: 'POST',
    data: { shop_id: shopId, shipping_label_number: labelNumber },
    skipErrorHandler: true,
  });
}

export interface OutboundScanFailure {
  id: number;
  shop_id: number;
  scan_code: string;
  category: string;
  reason: string;
  order_id?: number;
  after_sale_id?: number;
  order_number?: string;
  user_id?: number;
  operator?: string;
  attempts: number;
  business_date: string;
  created_at: string;
  last_seen_at: string;
}

export interface OutboundFailurePage {
  records: OutboundScanFailure[];
  date: string;
  categories: Record<string, string>;
  summary: Record<string, { records: number; attempts: number }>;
  pagination: { page: number; per_page: number; total: number };
}

export function listOutboundFailures(params: { shop_id: number; date?: string; category?: string; search?: string; page?: number }) {
  return request<{ success: boolean; data: OutboundFailurePage }>('/api/shipping/outbound/failures', {
    params, skipErrorHandler: true,
  });
}

/** 最近扫码出库的记录（每单只留最近一次），出库台用来撤回 */
export interface OutboundRecord {
  id: number;
  order_id: number;
  after_sale_id?: number | null;
  order_number: string;
  code?: string | null;
  created_at: string;
  user_id?: number | null;
  /** false 表示这次出库已经被撤回过了 */
  undoable: boolean;
  kind: 'order' | 'after_sale';
  title: string;
  subtitle?: string;
  status: string;
}

export async function listRecentOutbound(shopId: number, params: {page?: number; per_page?: number; search?: string} = {}) {
  return request<{ success: boolean; data: {
    events: OutboundRecord[];
    pagination?: {page: number; per_page: number; total: number; pages: number};
    today?: {date: string; quantity: number; total_orders?: number; order_count?: number; after_sale_count?: number};
  } }>(
    '/api/shipping/outbound/recent',
    { method: 'GET', params: { shop_id: shopId, ...params }, skipErrorHandler: true },
  );
}

/** 撤回一次扫码出库：订单退回待出库，库存回到出库前 */
export async function undoOutbound(payload: {
  shop_id: number;
  order_id?: number;
  after_sale_id?: number;
  reason?: string;
}) {
  return request<{ success: boolean; data: Record<string, unknown> }>(
    '/api/shipping/outbound/undo',
    { method: 'POST', data: payload },
  );
}

export interface FourPxPreviewRow {
  customer_reference: string | null;
  tracking_number: string | null;
  consignment_status?: string | null;
  order_id?: number | null;
  order_number?: string | null;
  status: string;
}

export interface FourPxPreview {
  date: string;
  shop_id: number;
  total: number;
  ready_to_bind: number;
  exceptions: number;
  rows: FourPxPreviewRow[];
}

export async function previewFourPxPredeclared(shopId: number, date: string) {
  return request<{ success: boolean; data: FourPxPreview }>('/api/shipping/4px/preview', {
    method: 'POST', data: { shop_id: shopId, date },
  });
}

export async function bindFourPxPredeclared(shopId: number, date: string, customerReference: string, trackingNumber: string) {
  return request<{ success: boolean; data: {
    order_id: number; order_number: string; tracking_number: string;
    shopify_status: 'awaiting_parcels' | 'failed' | 'updated' | 'already_fulfilled';
    shopify_error?: string;
  } }>(
    '/api/shipping/4px/bind-and-writeback', {
      method: 'POST', data: {
        shop_id: shopId, date, customer_reference: customerReference, tracking_number: trackingNumber,
      },
    },
  );
}

export interface FourPxBatchOrderResult {
  order_id: number;
  order_number: string;
  newly_bound: number;
  already_bound: number;
  shopify_status: 'ready_to_writeback' | 'awaiting_parcels' | 'failed' | 'updated' | 'already_fulfilled';
  shopify_error?: string | null;
}

export interface FourPxBatchResult {
  summary: {
    selected_pairs: number;
    newly_bound: number;
    already_bound: number;
    skipped_pairs: number;
    updated_orders: number;
    already_fulfilled_orders: number;
    ready_to_writeback_orders: number;
    awaiting_parcels_orders: number;
    failed_orders: number;
  };
  orders: FourPxBatchOrderResult[];
  skipped: Array<{ customer_reference: string; tracking_number: string; reason: string }>;
}

export async function batchBindFourPxPredeclared(
  shopId: number, date: string,
  pairs: Array<{ customer_reference: string; tracking_number: string }>,
) {
  return request<{ success: boolean; data: FourPxBatchResult }>('/api/shipping/4px/batch-bind', {
    method: 'POST', timeout: 180000,
    data: { shop_id: shopId, date, pairs },
  });
}

/** 4PX 预申报检查（只读）：按发货规则给出建议产品、重量、申报价值和拦截原因。 */
export interface DeclarationCheckRow {
  order_id: number;
  order_number: string;
  order_date?: string | null;
  country: string;
  country_name: string;
  postal_code: string;
  quantity: number;
  special_pair: boolean;
  product: { key: string; code: string; name: string; tracking: boolean } | null;
  product_reason: string;
  weight_grams: number;
  declared_value: number;
  declared_currency: string;
  /** 按历史预报口径归并出的申报行：品名、数量、单价、金额 */
  declared_items: Array<{ name_cn: string; name_en: string; quantity: number; unit_price: number; amount: number }>;
  duty_type: string;
  ready: boolean;
  reason_group?: 'address' | 'contact' | 'country' | 'items' | 'other' | null;
  blockers: string[];
  items: Array<{ title: string; variant_title: string; sku: string; quantity: number; unit_price: number; image_url: string }>;
  recipient: Record<string, string>;
  payload_preview: Record<string, unknown> | null;
}

export interface DeclarationCheckResult {
  summary: { candidates: number; ready: number; blocked: number; groups?: Record<string, number> };
  rules: {
    products: Record<string, { code: string; name: string }>;
    weight: { one_item: number; two_items: number; extra_item: number };
    declare: {
      currency: string;
      note: string;
      categories: Array<{ cn: string; en: string; prices: Record<string, number> }>;
    };
    duty_type: string;
    special_title: string;
    reason_groups?: Record<string, string>;
  };
  notices: string[];
  orders: DeclarationCheckRow[];
}

export async function checkFourPxDeclaration(
  shopId: number,
  params: {
    search?: string; page?: number; per_page?: number; order_from?: string; order_to?: string;
    blocked_only?: boolean; reason_group?: string; status?: 'ready' | 'blocked';
  } = {},
) {
  return request<{ success: boolean; data: DeclarationCheckPage }>(
    '/api/shipping/4px/declaration/check',
    { method: 'GET', params: { shop_id: shopId, ...params }, skipErrorHandler: true },
  );
}

/** 4PX 预申报检查分页结果 */
export interface DeclarationCheckPage extends DeclarationCheckResult {
  pagination: { page: number; per_page: number; total: number; pages: number };
}

export interface DeclarationJob {
  id: string;
  shop_id: number;
  status: 'queued' | 'running' | 'done' | 'failed';
  requested: number;
  processed: number;
  created: number;
  failed: number;
  results: DeclarationCreateResult['results'];
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export async function startFourPxDeclarationJob(shopId: number, orderIds: number[]) {
  return request<{ success: boolean; data: DeclarationJob }>(
    '/api/shipping/4px/declaration/jobs',
    { method: 'POST', data: { shop_id: shopId, order_ids: orderIds, confirm: true } },
  );
}

export async function getFourPxDeclarationJob(shopId: number, jobId: string) {
  return request<{ success: boolean; data: DeclarationJob }>(
    `/api/shipping/4px/declaration/jobs/${jobId}`,
    { method: 'GET', params: { shop_id: shopId } },
  );
}

export async function listFourPxDeclarationJobs(shopId: number) {
  return request<{ success: boolean; data: { jobs: DeclarationJob[] } }>(
    '/api/shipping/4px/declaration/jobs',
    { method: 'GET', params: { shop_id: shopId } },
  );
}

export interface DeclarationSyncRow {
  order_id: number;
  order_number?: string;
  status: 'bound' | 'already_bound' | 'not_found' | 'failed' | 'conflict' | 'skipped' | 'invalid';
  tracking_number?: string;
  error?: string | null;
  shopify_status?: string;
}

export interface DeclarationSyncResult {
  summary: { requested: number; bound: number; already_bound: number; not_found: number; failed: number };
  results: DeclarationSyncRow[];
}

export async function syncFourPxManualDeclarations(
  shopId: number,
  payload: { orderIds?: number[]; jobId?: string },
) {
  return request<{ success: boolean; data: DeclarationSyncResult }>(
    '/api/shipping/4px/declaration/sync',
    { method: 'POST', data: {
      shop_id: shopId, order_ids: payload.orderIds, job_id: payload.jobId,
    } },
  );
}

export interface FourPxDeclarationSettings {
  shop_id: number;
  auto_writeback: boolean;
  updated_at?: string | null;
}

export async function getFourPxDeclarationSettings(shopId: number) {
  return request<{ success: boolean; data: FourPxDeclarationSettings }>(
    '/api/shipping/4px/declaration/settings',
    { method: 'GET', params: { shop_id: shopId } },
  );
}

export async function saveFourPxDeclarationSettings(shopId: number, autoWriteback: boolean) {
  return request<{ success: boolean; data: FourPxDeclarationSettings }>(
    '/api/shipping/4px/declaration/settings',
    { method: 'POST', data: { shop_id: shopId, auto_writeback: autoWriteback } },
  );
}

export async function exportFourPxDeclaration(
  shopId: number,
  search?: string,
  range: { from?: string; to?: string } = {},
  extra: { blocked_only?: boolean; reason_group?: string; status?: string } = {},
) {
  return request<Blob>('/api/shipping/4px/declaration/check.csv', {
    method: 'GET',
    params: {
      shop_id: shopId, search: search || undefined, order_from: range.from || undefined,
      order_to: range.to || undefined, blocked_only: extra.blocked_only ? 'true' : undefined,
      reason_group: extra.reason_group || undefined, status: extra.status || undefined,
    },
    responseType: 'blob', timeout: 60000,
  });
}

export interface DeclarationPlanGroup {
  product: { key: string; code: string; name: string; tracking: boolean };
  order_numbers: string[];
  order_count: number;
  quantity: number;
  weight_grams: number;
  declared_value: number;
}

export interface DeclarationPlanResult {
  summary: { requested: number; matched: number; eligible: number; blocked: number; invalid: number };
  invalid_order_ids: number[];
  groups: DeclarationPlanGroup[];
  orders: DeclarationCheckRow[];
}

export async function planFourPxDeclaration(shopId: number, orderIds: number[]) {
  return request<{ success: boolean; data: DeclarationPlanResult }>(
    '/api/shipping/4px/declaration/plan',
    { method: 'POST', data: { shop_id: shopId, order_ids: orderIds }, skipErrorHandler: true },
  );
}

export interface DeclarationCreateResult {
  summary: { requested: number; created: number; failed: number };
  barcode_sync?: { updated: number; failed: number };
  auto_writeback?: { enabled: boolean; updated: number; already_fulfilled: number; awaiting: number; failed: number };
  results: Array<{
    order_id: number;
    order_number?: string;
    status: 'created' | 'created_after_retry' | 'already_exists' | 'skipped' | 'failed' | 'invalid';
    tracking_number?: string;
    error?: string;
  }>;
}

/** 真实向 4PX 建单（必须由页面二次确认后调用）。 */
export async function createFourPxDeclaration(shopId: number, orderIds: number[]) {
  return request<{ success: boolean; data: DeclarationCreateResult }>(
    '/api/shipping/4px/declaration/create',
    { method: 'POST', data: { shop_id: shopId, order_ids: orderIds, confirm: true }, skipErrorHandler: true },
  );
}
