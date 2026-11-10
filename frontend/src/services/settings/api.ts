import { request } from '@umijs/max';

export interface FeeConfigItem {
  id: number;
  fee_type: string;
  fee_name: string;
  description?: string;
  calculation_method: string;
  percentage_rate?: number | null;
  fixed_amount?: number | null;
  currency?: string;
  is_active?: boolean;
}

export type FeeConfigPayload = Omit<FeeConfigItem, 'id' | 'is_active'>;

/** 手续费配置列表 */
export async function listFeeConfigs() {
  return request<{ success: boolean; data: FeeConfigItem[] }>('/api/settings/fees', {
    method: 'GET',
  });
}

/** 新增手续费配置 */
export async function createFeeConfig(data: FeeConfigPayload) {
  return request<{ success: boolean; message?: string; data: FeeConfigItem }>(
    '/api/settings/fees',
    { method: 'POST', data },
  );
}

/** 修改手续费配置 */
export async function updateFeeConfig(id: number | string, data: FeeConfigPayload) {
  return request<{ success: boolean; message?: string }>(`/api/settings/fees/${id}`, {
    method: 'PUT',
    data,
  });
}

/** 停用手续费配置（软删除） */
export async function deleteFeeConfig(id: number | string) {
  return request<{ success: boolean; message?: string }>(`/api/settings/fees/${id}`, {
    method: 'DELETE',
  });
}
