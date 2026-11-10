import { request } from '@umijs/max';

export interface AiApiKeyItem {
  id: number;
  name: string;
  key_prefix: string;
  shop_ids: number[];
  scopes: string[];
  is_active: boolean;
  ip_allowlist?: string[];
  daily_send_limit?: number;
  today_calls?: number;
  today_sends?: number;
  last_used_at?: string | null;
  expires_at?: string | null;
  created_at?: string | null;
}

export interface CreatedAiApiKey extends AiApiKeyItem {
  api_key: string;
}

export async function listAiApiKeys() {
  return request<{ success: boolean; data: { keys: AiApiKeyItem[]; scopes: Record<string, string> } }>(
    '/api/ai-keys',
    { method: 'GET' },
  );
}

export interface AiApiAuditRow {
  id: number;
  key_id?: number | null;
  method: string;
  path: string;
  status_code: number;
  duration_ms?: number | null;
  ip?: string | null;
  created_at?: string | null;
}

export async function listAiApiAudit(params?: { page?: number; per_page?: number; key_id?: number }) {
  return request<{ success: boolean; data: { items: AiApiAuditRow[]; pagination: { page: number; per_page: number; total: number; pages: number } } }>(
    '/api/ai-keys/audit',
    { method: 'GET', params: params || {} },
  );
}

export async function createAiApiKey(payload: {
  name: string;
  shop_ids: number[];
  scopes: string[];
  expires_in_days?: number;
  ip_allowlist?: string[];
  daily_send_limit?: number;
  today_calls?: number;
  today_sends?: number;
}) {
  return request<{ success: boolean; data: CreatedAiApiKey }>('/api/ai-keys', {
    method: 'POST',
    data: payload,
  });
}

export async function updateAiApiKey(id: number, payload: {
  name?: string;
  shop_ids?: number[];
  scopes?: string[];
  ip_allowlist?: string[];
  daily_send_limit?: number;
  expires_in_days?: number | null;
}) {
  return request<{ success: boolean; data: AiApiKeyItem }>(`/api/ai-keys/${id}`, {
    method: 'PATCH',
    data: payload,
  });
}

export async function rotateAiApiKey(id: number) {
  return request<{ success: boolean; data: CreatedAiApiKey }>(`/api/ai-keys/${id}/rotate`, {
    method: 'POST',
  });
}

export async function revokeAiApiKey(id: number) {
  return request<{ success: boolean; data: AiApiKeyItem }>(`/api/ai-keys/${id}/revoke`, {
    method: 'POST',
  });
}
