import { request } from '@umijs/max';

export type FreightCostStatus =
  | 'no_4px' | 'freight_missing' | 'cost_missing' | 'diff' | 'matched' | 'cost_no_freight';

export interface FreightCostRow {
  order_id: number;
  order_number: string;
  order_date?: string | null;
  warehouse_status?: string;
  tracking_number?: string | null;
  fourpx_tracking_no?: string | null;
  freight_currency?: string | null;
  freight_total?: number | null;
  freight_billed_at?: string | null;
  cost_id?: number | null;
  cost_shipping?: number;
  cost_source?: string | null;
  cost_status?: string | null;
  diff?: number | null;
  status: FreightCostStatus;
}

export interface FreightCostReconcile {
  shop_id: number;
  date_from: string;
  date_to: string;
  summary: {
    orders: number;
    counts: Record<string, number>;
    freight_total: number;
    cost_total: number;
    missing_cost: number;
    diff: number;
    has_no_4px: number;
  };
  items: FreightCostRow[];
  pagination: { page: number; per_page: number; total: number; pages: number };
  statuses: Record<string, string>;
}

export async function reconcileFreightCosts(params: {
  shop_id: number;
  date_from?: string;
  date_to?: string;
  status?: string;
  search?: string;
  page?: number;
  per_page?: number;
}) {
  return request<{ success: boolean; data: FreightCostReconcile }>('/api/order-costs/fourpx/reconcile', {
    method: 'GET',
    params,
  });
}

export async function applyFreightCosts(payload: { shop_id: number; order_ids: number[] }) {
  return request<{
    success: boolean;
    data: { created: number; skipped: number; results: FreightCostRow[] };
  }>('/api/order-costs/fourpx/apply', { method: 'POST', data: payload });
}
