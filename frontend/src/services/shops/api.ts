import { request } from '@umijs/max';

export interface ShopItem {
  id: number;
  name: string;
  shop_url: string;
  currency?: string;
  is_active: boolean;
  notes?: string;
  last_sync?: string | null;
  last_sync_attempt_at?: string | null;
  last_sync_error?: string | null;
  pending_fee_count?: number;
  created_at?: string;
  updated_at?: string;
  /** 店铺绑定的应用 Client ID（唯一绑定方式：域名 + Client ID + Client Secret） */
  oauth_client_id?: string;
  /** 回显时为打码值，留空表示不修改 */
  oauth_client_secret?: string;
  /** 域名 + Client ID + Client Secret 齐全才算已绑定 */
  has_credentials?: boolean;
  has_oauth_credentials?: boolean;
  fourpx_app_name?: string | null;
  fourpx_resource_code?: string | null;
  fourpx_environment?: 'test' | 'production';
  has_fourpx_credentials?: boolean;
  order_count?: number;
  expense_count?: number;
}

export interface ShopPayload {
  name?: string;
  shop_url?: string;
  oauth_client_id?: string;
  oauth_client_secret?: string;
  currency?: string;
  notes?: string;
  is_active?: boolean;
  fourpx_app_name?: string;
  fourpx_app_key?: string;
  fourpx_app_secret?: string;
  fourpx_resource_code?: string;
  fourpx_environment?: 'test' | 'production';
}

/** 店铺列表（管理员返回全部，子账户只返回被分配的店铺） */
export async function listShops(params?: { include_inactive?: boolean }) {
  return request<{ success: boolean; data: ShopItem[] }>('/api/shops', {
    method: 'GET',
    params: params || {},
  });
}

/** 新增店铺（仅管理员） */
export async function createShop(payload: ShopPayload) {
  return request<{ success: boolean; message?: string; data: ShopItem }>(
    '/api/shops',
    {
      method: 'POST',
      data: payload,
    },
  );
}

/** 修改店铺（仅管理员） */
export async function updateShop(id: number, payload: ShopPayload) {
  return request<{ success: boolean; message?: string; data: ShopItem }>(
    `/api/shops/${id}`,
    {
      method: 'PUT',
      data: payload,
    },
  );
}

/** 删除店铺（仅管理员） */
export async function deleteShop(id: number, force = false) {
  return request<{
    success: boolean;
    message?: string;
    has_data?: boolean;
    order_count?: number;
    expense_count?: number;
  }>(`/api/shops/${id}`, {
    method: 'DELETE',
    params: force ? { force: true } : {},
  });
}

/** 测试店铺连接 */
export async function testShopConnection(shopId?: number) {
  return request<{ success: boolean; message?: string; data?: any }>(
    '/api/sync/test',
    {
      method: 'GET',
      params: shopId ? { shop_id: shopId } : {},
    },
  );
}

/** 同步指定店铺的订单 */
export async function syncShopOrders(shopId: number, daysBack = 30) {
  return request<{ success: boolean; message?: string; stats?: any }>(
    '/api/sync/orders',
    {
      method: 'POST',
      data: { shop_id: shopId, days_back: daysBack, limit: 250 },
    },
  );
}
