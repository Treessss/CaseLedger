import { request } from '@umijs/max';

export type TrackingTab = 'preinfo' | 'pending' | 'overdue';
export type TrackingHandling = 'unhandled' | 'handled' | 'all';
export type TrackingAge = 'within24h' | 'beyond24h';
export interface TrackingEvent { happenedAt: string; message: string; status: string; carrier_code?: string }
export interface TrackingRow {
  id: number; order_id: number; order_number: string; after_sale_id?: number;
  number: string; latest?: TrackingEvent; events: TrackingEvent[]; event_count: number;
  checked_at?: string; first_preinfo_at?: string; outbound_at?: string; delivered_at?: string;
  outbound: boolean; observing: boolean; error?: string; url: string;
  next_check_at?: string;
  handled?: boolean; handled_at?: string; handled_by?: number; handled_by_name?: string;
}
export interface TrackingJob {
  id: string; scope: string; status: string; total: number; done: number; failed: number;
  error?: string; updated_at: string; finished_at?: string;
  skipped?: number;
}
export interface TrackingResult {
  items: TrackingRow[]; counts: Record<TrackingTab, number>; total: number; page: number; per_page: number;
  handling_counts?: {handled: number; unhandled: number};
  preinfo_age_counts?: {within24h: number; beyond24h: number};
  preinfo_handling_counts?: {handled: number; unhandled: number};
  job?: TrackingJob; history_job?: TrackingJob; coverage: {total: number; checked: number; unqueried?: number; latest_at?: string};
}
export interface OutboundResult {
  id: number; state: 'succeeded' | 'failed' | 'skipped'; message?: string; order_number?: string;
  stock_result?: {deducted_quantity: number; unaccounted_quantity: number};
}
export interface TrackingAudit {
  id: number; order_id: number; after_sale_id?: number; user_id?: number; created_at: string;
  evidence: {number: string; latest: TrackingEvent};
  stock_result: {deducted_quantity: number; unaccounted_quantity: number;
    shortages: Array<{title: string; variant_title: string; sku?: string; quantity: number}>};
}

export function listTracking(params: {shop_id: number; tab: TrackingTab; page: number; per_page: number; search?: string;
  handling?: TrackingHandling; age?: TrackingAge; order?: 'asc' | 'desc'}) {
  return request<{success: boolean; data: TrackingResult}>('/api/logistics', {params});
}
export function setTrackingHandled(shop_id: number, binding_id: number, handled: boolean) {
  return request<{success: boolean; data: Pick<TrackingRow, 'id' | 'handled' | 'handled_at' | 'handled_by' | 'handled_by_name'>}>(
    `/api/logistics/${binding_id}/handling`, {method: 'PATCH', data: {shop_id, handled}});
}
export function refreshTracking(shop_id: number, scope: TrackingTab | 'due' | 'single', binding_id?: number) {
  return request<{success: boolean; data: TrackingJob}>('/api/logistics/refresh', {method: 'POST', data: {shop_id, scope, binding_id}});
}
export function reconcileTracking(shop_id: number, ids: number[]) {
  return request<{success: boolean; data: {results: OutboundResult[]}}>('/api/logistics/batch-outbound', {
    method: 'POST', data: {shop_id, ids, confirmed: true}, timeout: 120000,
  });
}
export interface TrackingAudits {items: TrackingAudit[]; total: number; page: number; per_page: number}
export function listTrackingAudits(shop_id: number, page = 1) {
  return request<{success: boolean; data: TrackingAudits}>('/api/logistics/outbound-audits', {params: {shop_id, page}});
}
