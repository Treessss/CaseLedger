import { request } from '@umijs/max';

export interface PlatformAccountItem {
  id: number;
  platform: string;
  username: string;
  notes?: string;
  created_at?: string;
  updated_at?: string;
}

export interface PlatformAccountPayload {
  platform?: string;
  username?: string;
  password?: string;
  notes?: string;
}

/** 平台账号列表（仅管理员） */
export async function listPlatformAccounts(params?: {
  page?: number;
  per_page?: number;
  platform?: string;
  search?: string;
}) {
  return request<{
    success: boolean;
    data: PlatformAccountItem[];
    pagination: { total: number };
  }>('/api/platform-accounts', {
    method: 'GET',
    params: params || {},
  });
}

/** 平台下拉选项 */
export async function getPlatformOptions() {
  return request<{ success: boolean; data: string[] }>('/api/platform-accounts/platforms', {
    method: 'GET',
  });
}

/** 新增平台账号 */
export async function createPlatformAccount(data: PlatformAccountPayload) {
  return request<{ success: boolean; message?: string; data: PlatformAccountItem }>(
    '/api/platform-accounts',
    { method: 'POST', data },
  );
}

/** 修改平台账号 */
export async function updatePlatformAccount(id: number, data: PlatformAccountPayload) {
  return request<{ success: boolean; message?: string; data: PlatformAccountItem }>(
    `/api/platform-accounts/${id}`,
    { method: 'PUT', data },
  );
}

/** 删除平台账号 */
export async function deletePlatformAccount(id: number) {
  return request<{ success: boolean; message?: string }>(`/api/platform-accounts/${id}`, {
    method: 'DELETE',
  });
}

/** 查看明文密码（仅管理员，用于页面上的“显示密码”） */
export async function getPlatformAccountPassword(id: number) {
  return request<{ success: boolean; data: { password: string } }>(
    `/api/platform-accounts/${id}/password`,
    { method: 'GET' },
  );
}
