import { request as rawRequest } from '@umijs/max';
import { getSelectedShopId } from '@/utils/shop';

// Scope every request, including drafts, translations and writes (not just the list).
function request<T = unknown>(url: string, options?: { method?: string; data?: unknown; params?: Record<string, unknown>; responseType?: 'blob' }): Promise<T> {
  return rawRequest<T>(url, {
  ...options,
  params: { ...options?.params, shop_id: getSelectedShopId() },
  getResponse: false,
  });
}

/** 邮件模块的接口封装；页面里不直接发 request。 */

export type MailStatus =
  | 'pending'
  | 'followup'
  | 'replied'
  | 'verify'
  | 'uncertain'
  | 'ignored';

export interface MailStatusInfo {
  connected: boolean;
  credentials: boolean;
  client_id: string;
  deepseek: boolean;
  account: string;
  email: string;
  dc: string;
  model: string;
  consent: boolean;
  complete: boolean;
  last_sync: number;
  job_error?: string;
  message_count: number;
}

export interface MailItem {
  ignored?: boolean;
  id: string;
  thread_id?: string;
  direction: 'in' | 'out';
  from: string;
  to?: string;
  customer: string;
  subject: string;
  time: number;
  attachment: boolean;
  system: boolean;
  status: MailStatus;
  evidence?: string;
  hydrated: boolean;
  has_draft?: boolean;
  has_guidance?: boolean;
  latest?: boolean;
  text?: string;
}

export interface MailAttachment { id: string; name: string; size: number; inline: boolean; image: boolean }

export async function listMailAttachments(id: string) {
  return request<{success: boolean; data: MailAttachment[]}>(`/api/mail/messages/${encodeURIComponent(id)}/attachments`);
}

export async function downloadMailAttachment(id: string, attachmentId: string) {
  try {
    return await request<Blob>(`/api/mail/messages/${encodeURIComponent(id)}/attachments/${encodeURIComponent(attachmentId)}/download`, {responseType: 'blob'});
  } catch (error) {
    const body = (error as {response?: {data?: unknown}})?.response?.data;
    if (body instanceof Blob) {
      let detail: {message?: string} | undefined;
      try {detail = JSON.parse(await body.text());} catch { /* Keep the original network error. */ }
      if (detail?.message) throw new Error(detail.message);
    }
    throw error;
  }
}

export interface MailDraftPayload {
  guidance: string;
  reply: string;
  summary?: string;
  reason?: string;
  category?: string;
  confidence?: number;
  safe: boolean;
  edited: boolean;
  stale?: boolean;
  generated_at?: string;
  analysis?: MailSupportAnalysis;
}

export interface MailSupportFacts {
  photos_verified: boolean;
  fault: 'unknown' | 'merchant' | 'customer';
  damage_timing: 'unknown' | 'on_arrival' | 'after_use';
  received_date: string;
}

export interface MailSupportAnalysis {
  intent?: string; intent_label?: string; order_id?: number; order_number?: string;
  language?: string; history_count?: number; action?: string; discount_eligible?: boolean;
  policy_version?: string; tracking_updated?: boolean;
  facts?: MailSupportFacts;
  actions?: string[]; warnings?: string[]; missing_evidence?: string[];
  issues?: string[]; supplied_evidence?: string[]; missing_information?: string[];
  discount?: {code: string; reused: boolean};
  tracking?: Array<{number: string; url?: string; latest?: {happenedAt: string; message?: string; status: string};
    source?: string; tracking_checked_at?: string; tracking_warning?: string; tracking_error?: string}>;
}

export interface MailDetail {
  orders: Array<{
    id: number; order_number: string; customer_name?: string; order_date?: string;
    financial_status: string; fulfillment_status?: string; warehouse_status: string;
    can_after_sale: boolean;
    tracking: Array<{ number: string; company?: string; url?: string; scanned_at?: string }>;
  }>;
  mail: MailItem;
  thread: MailItem[];
  draft: MailDraftPayload | null;
  send: { id: string; state: string; to: string; error?: string } | null;
}

export async function ignoreMail(id: string, ignored = true) {
  return request(`/api/mail/messages/${encodeURIComponent(id)}/ignore`, {
    method: 'POST', data: { ignored },
  });
}

export async function getMailStatus() {
  return request<{ success: boolean; data: MailStatusInfo }>('/api/mail/status', {
    method: 'GET',
  });
}

export async function saveMailSettings(payload: {
  client_id?: string;
  client_secret?: string;
  deepseek?: string;
  model?: string;
  consent?: boolean;
}) {
  return request<{ success: boolean; data: MailStatusInfo }>('/api/mail/settings', {
    method: 'POST',
    data: payload,
  });
}

export async function connectMailbox(payload: {
  dc: string;
  code?: string;
  refresh?: string;
  email?: string;
  redirect_uri?: string;
}) {
  return request<{ success: boolean; data: MailStatusInfo }>('/api/mail/connect', {
    method: 'POST',
    data: payload,
  });
}

export async function syncMails(history = false) {
  return request<{
    success: boolean;
    data: { processed: number; more: boolean; complete: boolean };
  }>('/api/mail/sync', { method: 'POST', data: { history } });
}

export async function verifyMails() {
  return request<{
    success: boolean;
    data: { checked: number; remaining: number; error?: string };
  }>('/api/mail/verify', { method: 'POST' });
}

export async function listMails(params: {
  status?: string;
  search?: string;
  page?: number;
  per_page?: number;
}) {
  return request<{
    success: boolean;
    data: {
      items: MailItem[];
      counts: Record<string, number>;
      pagination: { page: number; per_page: number; total: number; pages: number };
    };
  }>('/api/mail/messages', { method: 'GET', params });
}

export async function getMail(id: string) {
  return request<{ success: boolean; data: MailDetail }>(
    `/api/mail/messages/${encodeURIComponent(id)}`,
    { method: 'GET' },
  );
}

export async function saveGuidance(id: string, guidance: string) {
  return request<{ success: boolean; data: MailDraftPayload }>(
    `/api/mail/messages/${encodeURIComponent(id)}/guidance`,
    { method: 'POST', data: { guidance } },
  );
}

/** 把这次会话里还没取正文的邮件补上 */
export async function hydrateMailThread(id: string, limit = 6) {
  return request<{ success: boolean; data: MailDetail }>(
    `/api/mail/messages/${encodeURIComponent(id)}/hydrate-thread`,
    { method: 'POST', data: { limit } },
  );
}

export async function generateDraft(id: string, guidance?: string, options?: {order_id?: number; facts?: MailSupportFacts}) {
  return request<{ success: boolean; data: MailDraftPayload }>(
    `/api/mail/messages/${encodeURIComponent(id)}/draft`,
    { method: 'POST', data: { guidance, ...options } },
  );
}

export async function confirmExchangeDiscount(id: string) {
  return request<{success: boolean; data: MailDraftPayload}>(
    `/api/mail/messages/${encodeURIComponent(id)}/exchange-discount`,
    {method: 'POST', data: {confirmed: true}},
  );
}

export async function saveReply(id: string, reply: string) {
  return request<{ success: boolean; data: MailDraftPayload }>(
    `/api/mail/messages/${encodeURIComponent(id)}/reply`,
    { method: 'POST', data: { reply } },
  );
}

export async function sendMailReplyWithAttachments(id: string, reply: string, files: File[]) {
  const form = new FormData();
  form.append('reply', reply);
  for (const file of files) form.append('files', file, file.name);
  const options = { method: 'POST', data: form, timeout: 300000 } as any;
  return request<{ success: boolean; data: { sent: boolean; to: string } }>(
    `/api/mail/messages/${encodeURIComponent(id)}/send-with-attachments`,
    options,
  );
}

export async function sendMailReply(id: string) {
  return request<{ success: boolean; data: { sent: boolean; to: string } }>(
    `/api/mail/messages/${encodeURIComponent(id)}/send`,
    { method: 'POST' },
  );
}

export async function translateMail(payload: {
  id?: string;
  kind: 'mail' | 'reply';
  reply?: string;
}) {
  return request<{ success: boolean; data: { text: string; cached: boolean } }>(
    '/api/mail/translate',
    { method: 'POST', data: payload },
  );
}

export type MailDraftJob = {
  id: string; status: string; total: number; done: number;
  succeeded: number; failed: number; skipped: number; error?: string;
  failures: {message_id: string; error: string}[];
};

export async function getDraftJob() {
  return request<{data: MailDraftJob | null}>('/api/mail/draft-jobs');
}

export async function startDraftJob(data: {all?: boolean; message_ids?: string[]; retry_job_id?: string}) {
  return request<{data: MailDraftJob}>('/api/mail/draft-jobs', {method: 'POST', data});
}

export async function cancelDraftJob(id: string) {
  return request<{data: MailDraftJob}>(`/api/mail/draft-jobs/${encodeURIComponent(id)}/cancel`, {method: 'POST'});
}
