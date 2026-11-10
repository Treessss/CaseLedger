import { request } from '@umijs/max';

export interface AfterSaleItem {
  id: number;
  source_order_item_id: number;
  catalog_order_item_id: number;
  title: string;
  variant_title?: string;
  sku?: string;
  image_url?: string;
  quantity: number;
  reserved_quantity: number;
  available_quantity?: number;
  needed_quantity: number;
  received_quantity: number;
  damaged_quantity: number;
}

export interface AfterSale {
  id: number;
  shop_id: number;
  order_id: number;
  order_number: string;
  customer_reference: string;
  kind: 'resend' | 'exchange' | 'missing';
  reason: string;
  note?: string | null;
  status: 'pending_purchase' | 'purchasing' | 'ready' | 'shipped';
  procurement_status?: 'not_started' | 'draft' | 'ordered' | 'partially_received' | 'received' | 'history';
  stock_status?: 'ready' | 'missing' | 'shipped' | 'invalid';
  stock_missing_quantity?: number;
  purchased_at?: string | null;
  tracking_number?: string;
  label_barcode?: string;
  created_at: string;
  shipped_at?: string;
  total_cost_cny: number;
  items: AfterSaleItem[];
  costs: Array<{ id: number; cost_type: string; amount_cny: number; note?: string; incurred_at: string }>;
  purchase_lists?: Array<{ id: number; name: string; status: string }>;
}

export interface CatalogItem {
  id: number;
  title: string;
  variant_title?: string;
  sku?: string;
  image_url?: string;
  color?: string;
  configuration?: string;
  model?: string;
  status?: string;
}

export interface CatalogProduct {
  shopify_product_id: string;
  title: string;
  variant_count: number;
  image_url?: string;
}
export const searchCatalogProducts = (shopId: number, search: string, page = 1) =>
  request<{ success: boolean; data: { products: CatalogProduct[]; pagination: { total: number; has_more: boolean } } }>(
    '/api/after-sales/catalog/products', { params: { shop_id: shopId, search, page, per_page: 20 } },
  );
export const getCatalogVariants = (shopId: number, productId: string, page = 1) =>
  request<{ success: boolean; data: { items: CatalogItem[]; pagination: { total: number; has_more: boolean } } }>(
    '/api/after-sales/catalog', { params: { shop_id: shopId, shopify_product_id: productId, page, per_page: 200 } },
  );

export const listAfterSales = (params: Record<string, unknown>) =>
  request<{ success: boolean; data: { after_sales: AfterSale[]; counts?: Record<string, number>; pagination: { total: number; page: number; per_page: number } } }>(
    '/api/after-sales', { params },
  );
export const getAfterSale = (id: number) => request<{ success: boolean; data: AfterSale }>(`/api/after-sales/${id}`);
export const deleteAfterSale = (id: number) => request<{ success: boolean; data: { deleted: boolean } }>(`/api/after-sales/${id}`, { method: 'DELETE' });
export const lookupOriginalOrder = (orderNumber: string, shopId?: number | null) =>
  request<{ success: boolean; data: unknown }>('/api/after-sales/original-order', {
    params: { order_number: orderNumber, shop_id: shopId || 'all' },
  });
export const getOriginalOrderSource = (orderId: number) =>
  request<{ success: boolean; data: unknown }>(`/api/after-sales/original-order/${orderId}`);
export const updateAfterSaleNote = (id: number, note: string) =>
  request<{ success: boolean; data: AfterSale }>(`/api/after-sales/${id}/note`, {
    method: 'PUT', data: { note },
  });
export const searchCatalog = (shopId: number, search: string, page = 1) =>
  request<{ success: boolean; data: { items: CatalogItem[]; pagination?: { has_more: boolean; total: number } } }>('/api/after-sales/catalog', { params: { shop_id: shopId, search, page, per_page: 200 } });
export const createAfterSale = (data: unknown) =>
  request<{ success: boolean; data: AfterSale }>('/api/after-sales', { method: 'POST', data });
export const addAfterSaleCost = (id: number, data: unknown) =>
  request<{ success: boolean; data: AfterSale }>(`/api/after-sales/${id}/costs`, { method: 'POST', data });
export const previewAfterSaleFourPx = (shopId: number, date: string) =>
  request<{ success: boolean; data: { rows: CfFourPxRow[] } }>(
    '/api/after-sales/4px/preview', { method: 'POST', data: { shop_id: shopId, date } },
  );
export const bindAfterSaleFourPx = (id: number, date: string, trackingNumber: string) =>
  request<{ success: boolean; data: AfterSale }>(`/api/after-sales/${id}/4px/bind`, {
    method: 'POST', data: { date, tracking_number: trackingNumber },
  });
export interface ManualAfterSaleTracking {
  tracking_number: string;
  label_barcode?: string;
  expected_tracking_number: string | null;
  expected_label_barcode: string | null;
  confirm_replace: boolean;
}
export const bindAfterSaleTracking = (id: number, data: ManualAfterSaleTracking) =>
  request<{ success: boolean; data: AfterSale }>(`/api/after-sales/${id}/tracking`, {
    method: 'PUT', data, skipErrorHandler: true,
  });
export const fetchAfterSaleLabel = (id: number) =>
  request<{ success: boolean; data: { pdf_url: string; label_barcode: string } }>(`/api/after-sales/${id}/label`, { method: 'POST' });

export interface CfFourPxRow {
  after_sale_id?: number;
  order_id?: number;
  order_number?: string;
  customer_reference: string;
  tracking_number: string;
  status: string;
}
export interface CfShopifyPreview extends CfFourPxRow {
  fulfillments: Array<{ id: string; status: string; tracking_numbers: string[] }>;
}
export interface CfWritebackPayload {
  shop_id: number;
  date: string;
  customer_reference: string;
  tracking_number: string;
}
export const previewCfShopify = (data: CfWritebackPayload) =>
  request<{ success: boolean; data: CfShopifyPreview }>('/api/after-sales/4px/shopify-preview', { method: 'POST', data });
export const writebackCfShopify = (data: CfWritebackPayload & { confirm_overwrite: boolean; fulfillment_id: string; expected_tracking_numbers: string[] }) =>
  request<{ success: boolean; data: { status: string; customer_notification_requested: boolean } }>('/api/after-sales/4px/shopify-writeback', { method: 'POST', data });
