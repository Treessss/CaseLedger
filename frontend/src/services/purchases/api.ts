import { request } from '@umijs/max';

export interface PurchaseItem {
  group_key: string;
  shopify_product_id?: string | null;
  shopify_variant_id?: string | null;
  title: string;
  variant_title: string;
  model_title?: string;
  sku: string;
  vendor: string;
  image_url: string;
  quantity: number;
  order_numbers: string[];
}

export interface PurchaseColor {
  original_name?: string;
  name: string;
  quantity: number;
  variant_count: number;
  image_url: string;
  order_numbers: string[];
  variants: PurchaseItem[];
}

export interface PurchaseProduct {
  group_key: string;
  shopify_product_id?: string | null;
  title: string;
  vendor: string;
  image_url: string;
  quantity: number;
  variant_count: number;
  color_count: number;
  order_numbers: string[];
  variants: PurchaseItem[];
  colors: PurchaseColor[];
}

export interface PurchaseListSummary {
  id: number;
  after_sale_id?: number | null;
  after_sale_ids?: number[];
  customer_reference?: string | null;
  customer_references?: string[];
  shop_id: number;
  shop_name?: string | null;
  name: string;
  order_count: number;
  item_count: number;
  product_count: number;
  color_count: number;
  total_quantity: number;
  status: 'draft' | 'ordered' | 'partially_received' | 'received' | 'cancelled';
  confirmed_at?: string | null;
  received_at?: string | null;
  receipt?: Array<{
    group_key: string;
    planned_quantity: number;
    received_quantity: number;
    damaged_quantity: number;
    missing_quantity: number;
    confirmed_missing_quantity?: number;
    pending_quantity?: number;
  }>;
  received_quantity?: number;
  /** 到货破损，数量不计入库存，需要补采或报损 */
  damaged_quantity?: number;
  /** 已确认缺货（供应商少发），已释放采购占用 */
  shortage_quantity?: number;
  /** 还没清点的数量 */
  pending_quantity?: number;
  /** 由本单缺货/破损生成的补采清单 */
  replenishment_ids?: number[];
  receipt_batches?: Array<{
    batch_id?: string | null;
    received_at: string;
    finalize: boolean;
    items: Array<{ group_key: string; received_quantity: number; damaged_quantity: number; missing_quantity?: number }>;
  }>;
  created_at: string;
}

export interface PurchaseListDetail extends PurchaseListSummary {
  edit_token?: string;
  can_edit_items?: boolean;
  order_ids: number[];
  /** 关联订单（本地 id + 订单号），用于跳转到订单详情 */
  orders?: Array<{ id: number; order_number: string }>;
  after_sale_orders?: Array<{
    id: number; customer_reference: string; order_id: number; order_number: string;
    removed: boolean; deleted: boolean;
  }>;
  items: PurchaseItem[];
  products: PurchaseProduct[];
  /** 本次到货顺手补给其它等货订单的库存（后端自动绑定，前端只做提示） */
  skipped_orders?: Array<{ id: number; order_number: string; reason: string }>;
  skipped_after_sales?: Array<{ after_sale_id: number; customer_reference: string; reason: string }>;
  trimmed_orders?: Array<{ order_id: number; order_number: string; group_key: string; quantity: number; variant_title: string; reason: string }>;
  allocated_orders?: Array<{
    order_id: number;
    order_number: string;
    group_key: string;
    quantity: number;
  }>;
}

export interface PurchaseCatalogItem {
  id: number;
  title: string;
  variant_title?: string;
  sku?: string;
  image_url?: string;
  color?: string;
  configuration?: string;
  model?: string;
}

export interface PurchaseCatalogProduct {
  shopify_product_id: string;
  title: string;
  variant_count: number;
  image_url?: string;
}

export const searchPurchaseProducts = (shopId: number, search: string, page = 1) =>
  request<{ success: boolean; data: { products: PurchaseCatalogProduct[]; pagination: { total: number; has_more: boolean } } }>(
    '/api/purchase-lists/catalog/products', { params: { shop_id: shopId, search, page } },
  );
export const getPurchaseVariants = (shopId: number, productId: string, page = 1) =>
  request<{ success: boolean; data: { items: PurchaseCatalogItem[]; pagination: { total: number; has_more: boolean } } }>(
    '/api/purchase-lists/catalog/variants', { params: { shop_id: shopId, shopify_product_id: productId, page, per_page: 200 } },
  );
export const createManualPurchase = (shopId: number, name: string, items: Array<{ product_id: number; quantity: number }>) =>
  request<{ success: boolean; data: PurchaseListDetail }>('/api/purchase-lists/manual', {
    method: 'POST', data: { shop_id: shopId, name, items },
  });
export const updatePurchaseItems = (id: number, editToken: string, items: Array<{ group_key: string; quantity: number }>,
  additions: Array<{ product_id: number; quantity: number }>) =>
  request<{ success: boolean; data: PurchaseListDetail }>(`/api/purchase-lists/${id}/items`, {
    method: 'PATCH', data: { edit_token: editToken, items, additions },
  });

export interface PurchaseCandidate {
  id: number;
  shop_id: number;
  order_number: string;
  customer_name?: string | null;
  financial_status: string;
  fulfillment_status: string;
  procurement_status?: 'not_started' | 'purchasing' | 'shortage' | 'partially_stocked' | 'ready_to_ship' | 'stocked';
  procurement_category: 'unprocured' | 'replenish' | 'purchasing' | 'stocked';
  procurement_needed_quantity: number;
  procurement_pending_quantity: number;
  procurement_stocked_quantity: number;
  order_date: string;
  shopify_details_synced_at?: string | null;
}

export interface AfterSalePurchaseCandidate {
  id: number;
  customer_reference: string;
  order_id: number;
  order_number: string;
  kind: 'resend' | 'exchange' | 'missing';
  status: string;
  needed_quantity: number;
  created_at: string;
}

export async function listPurchaseCandidates(params: {
  shop_id: number;
  page?: number;
  per_page?: number;
  search?: string;
  view?: 'needs_purchase' | 'unprocured' | 'replenish' | 'purchased' | 'purchasing' | 'stocked' | 'after_sale';
}) {
  return request<{
    success: boolean;
    data: {
      orders: PurchaseCandidate[];
      after_sales?: AfterSalePurchaseCandidate[];
      all_order_ids: number[];
      all_after_sale_ids: number[];
      category_counts: Record<'needs_purchase' | 'unprocured' | 'replenish' | 'purchased' | 'purchasing' | 'stocked' | 'after_sale', number>;
      selection_limit: number;
      pagination: { page: number; per_page: number; total: number; pages: number };
    };
  }>('/api/purchase-candidates', { method: 'GET', params });
}

export async function createAfterSalePurchaseList(afterSaleId: number) {
  return request<{ success: boolean; data: PurchaseListDetail }>('/api/purchase-lists/after-sale', {
    method: 'POST', data: { after_sale_id: afterSaleId },
  });
}

export async function deletePurchaseList(id: number) {
  return request<{ success: boolean; data: { id: number; deleted: boolean } }>(`/api/purchase-lists/${id}`, {
    method: 'DELETE',
  });
}

export async function confirmPurchaseList(id: number) {
  return request<{ success: boolean; data: PurchaseListDetail }>(`/api/purchase-lists/${id}/confirm`, {
    method: 'POST',
  });
}

export async function receivePurchaseList(id: number, items: Array<{
  group_key: string;
  received_quantity: number;
  damaged_quantity: number;
  missing_quantity?: number;
}>, finalize = true, batchId?: string) {
  return request<{ success: boolean; data: PurchaseListDetail }>(`/api/purchase-lists/${id}/receive`, {
    method: 'POST',
    data: { items, finalize, batch_id: batchId },
  });
}

export async function listPurchaseLists(params?: { page?: number; per_page?: number }) {
  return request<{
    success: boolean;
    data: {
      purchase_lists: PurchaseListSummary[];
      pagination: { page: number; per_page: number; total: number; pages: number };
    };
  }>('/api/purchase-lists', { method: 'GET', params });
}

export async function createPurchaseList(data: {
  shop_id: number;
  order_ids: number[];
  after_sale_ids?: number[];
  name?: string;
}) {
  return request<{ success: boolean; data: PurchaseListDetail }>('/api/purchase-lists', {
    method: 'POST',
    data,
    timeout: 120000,
  });
}

/** 为已确认缺货/破损生成补采清单（幂等，重复点击返回同一张草稿）。 */
export async function createPurchaseReplenishment(id: number) {
  return request<{ success: boolean; data: PurchaseListDetail }>(`/api/purchase-lists/${id}/replenish`, {
    method: 'POST',
  });
}

export async function getPurchaseList(id: number) {
  return request<{ success: boolean; data: PurchaseListDetail }>(`/api/purchase-lists/${id}`, {
    method: 'GET',
  });
}

/** 下载整份清单，或按一基序号下载其中一个商品大类。 */
export async function downloadPurchaseListExcel(id: number, productIndex?: number) {
  return request<Blob>(`/api/purchase-lists/${id}/excel`, {
    method: 'GET', responseType: 'blob', timeout: 60000,
    params: productIndex !== undefined ? {product_index: productIndex} : undefined,
  });
}

export async function downloadPurchaseListPdf(id: number, productIndex?: number) {
  return request<Blob>(`/api/purchase-lists/${id}/pdf`, {
    method: 'GET',
    params: productIndex ? { product_index: productIndex } : undefined,
    responseType: 'blob',
    timeout: 120000,
  });
}
