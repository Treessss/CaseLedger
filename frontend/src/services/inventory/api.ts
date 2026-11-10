import { request } from '@umijs/max';

export interface InventoryOccupiedOrder {
  order_id: number;
  order_number: string;
  quantity: number;
  after_sale_id?: number;
  kind?: 'ready' | 'waiting' | 'packing';
}

export interface InventoryPoolMetrics {
  demand_quantity?: number;
  on_hand_quantity?: number;
  demand_orders?: InventoryOccupiedOrder[];
  incoming_quantity?: number;
  purchase_needed_quantity?: number;
  ready_quantity?: number;
  waiting_quantity?: number;
  packing_quantity?: number;
  idle_quantity?: number;
  planning_orders?: InventoryOccupiedOrder[];
  purchase_orders?: InventoryOccupiedOrder[];
  incoming_purchases?: Array<{purchase_id: number; quantity: number}>;
}

export interface InventoryItem extends InventoryPoolMetrics {
  group_key: string;
  product_key: string;
  title: string;
  color: string;
  model: string;
  /** 腕链、支架等物理配置；与同型号普通款区分 */
  configuration?: string;
  sku: string;
  image_url: string;
  received_quantity: number;
  shipped_quantity: number;
  reserved_quantity: number;
  occupied_orders: InventoryOccupiedOrder[];
  adjustment_quantity: number;
  on_hand_quantity: number;
  available_quantity: number;
  /** 合并行（如 +Wrist Chain 与普通款）包含的底层 SKU */
  group_keys?: string[];
  components?: Array<{
    group_key: string;
    configuration?: string;
    sku?: string;
    model?: string;
    on_hand_quantity?: number;
    demand_quantity?: number;
  }>;
}

export interface InventoryColor extends InventoryPoolMetrics {
  original_name?: string;
  name: string;
  image_url: string;
  variant_count: number;
  on_hand_quantity: number;
  reserved_quantity: number;
  occupied_orders: InventoryOccupiedOrder[];
  available_quantity: number;
  variants: InventoryItem[];
}

export interface InventoryProduct extends InventoryPoolMetrics {
  group_key: string;
  title: string;
  image_url: string;
  color_count: number;
  variant_count: number;
  on_hand_quantity: number;
  reserved_quantity: number;
  occupied_orders: InventoryOccupiedOrder[];
  available_quantity: number;
  colors: InventoryColor[];
}

export interface InventoryAdjustment {
  id: number;
  shop_id: number;
  group_key: string;
  quantity_delta: number;
  reason: string;
  created_at: string;
}

export async function listInventory(params: {
  shop_id: number;
  page?: number;
  per_page?: number;
  search?: string;
  include_zero?: boolean;
  group_by_product?: boolean;
}) {
  return request<{
    success: boolean;
    data: {
      items: InventoryItem[];
      products: InventoryProduct[];
      summary: InventoryPoolMetrics & {
        variant_count: number;
        product_count: number | null;
        on_hand_quantity: number;
        reserved_quantity: number;
        available_quantity: number;
        /** 总量口径缺口：max(需求 − 在库 − 已采购未到, 0) */
        net_shortage_quantity: number;
        /** 逐型号缺口与总量缺口之差，即型号结构错配件数 */
        structural_gap_quantity: number;
      };
      pagination: { page: number; per_page: number; total: number; pages: number };
    };
  }>('/api/inventory', { method: 'GET', params });
}

export async function listInventoryAdjustments(shopId: number, groupKey?: string) {
  return request<{ success: boolean; data: InventoryAdjustment[] }>('/api/inventory/adjustments', {
    method: 'GET', params: { shop_id: shopId, group_key: groupKey },
  });
}

export async function listReceivingProducts(params: {shop_id: number; page?: number; per_page?: number; search?: string}) {
  return request<{success: boolean; data: {products: InventoryProduct[]; pagination: {total: number}}}>(
    '/api/inventory/receiving/products', {method: 'GET', params},
  );
}

export async function batchReceiveInventory(data: {shop_id: number; items: Array<{group_key: string; quantity: number}>; reason?: string}) {
  return request<{success: boolean; data: {quantity: number; allocated_orders: Array<{order_number?: string; order_id: number}>}}>(
    '/api/inventory/receiving', {method: 'POST', data},
  );
}

export async function createInventoryAdjustment(data: {
  shop_id: number;
  group_key: string;
  quantity_delta: number;
  reason: string;
}) {
  return request<{
    success: boolean;
    data: InventoryAdjustment & {
      /** 因这次扣减而失去库存占用、需要重新采购的订单 */
      released_orders?: Array<{ order_id: number; order_number?: string; quantity: number }>;
      /** 本次入库后自动备给的缺货订单 */
      allocated_orders?: Array<{
        order_id: number;
        order_number?: string;
        group_key: string;
        quantity: number;
      }>;
    };
  }>('/api/inventory/adjustments', { method: 'POST', data });
}

export async function reserveInventoryForOrder(data: {
  shop_id: number;
  group_key: string;
  order_number: string;
  quantity: number;
}) {
  return request<{ success: boolean; data: { order_id: number; order_number: string; quantity: number } }>(
    '/api/inventory/reservations', { method: 'POST', data },
  );
}

export interface InventoryMovement {
  id: string;
  movement_type: 'purchase_receipt' | 'scan_outbound' | 'manual_adjustment' | 'stock_transfer' | 'scan_undo';
  movement_type_label: string;
  order_number?: string | null;
  date: string;
  at: string;
  direction: 'in' | 'out';
  quantity: number;
  group_key: string;
  title: string;
  variant_title: string;
  sku: string;
  image_url: string;
  source: string;
}

export interface InventoryMovementDay {
  date: string;
  in_quantity: number;
  out_quantity: number;
  in_records: number;
  out_records: number;
}

export interface InventoryMovementsResult {
  period: { start_date: string; end_date: string };
  days: InventoryMovementDay[];
  movements: InventoryMovement[];
  totals: { in_quantity: number; out_quantity: number };
  pagination: { page: number; per_page: number; total: number; pages: number };
}

/** 每天的出入库流水（入库=采购到货，出库=扫码出库 + 人工调整） */
export async function listInventoryMovements(params: {
  shop_id: number;
  start_date?: string;
  end_date?: string;
  movement_type?: string;
  direction?: string;
  search?: string;
  page?: number;
  per_page?: number;
}) {
  return request<{ success: boolean; data: InventoryMovementsResult }>(
    '/api/inventory/movements',
    { method: 'GET', params },
  );
}
