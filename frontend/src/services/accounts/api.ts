import { request } from '@umijs/max';

export interface AccountItem {
  id: string;
  account_name: string;
  platform: string;
  account_id?: string;
  description?: string;
  balance: number;
  currency: string;
  status: 'active' | 'inactive' | 'suspended';
  created_at: string;
  updated_at: string;
}

export interface AccountListParams {
  current?: number;
  pageSize?: number;
  platform?: string;
  status?: string;
  account_name?: string;
}

export interface AccountListResponse {
  success: boolean;
  data: {
    accounts: AccountItem[];
    total: number;
    page: number;
    per_page: number;
  };
  message?: string;
}

export interface AccountDetailResponse {
  success: boolean;
  data: AccountItem;
  message?: string;
}

/** 获取账户列表 */
export async function getAccounts(params?: AccountListParams) {
  return request<AccountListResponse>('/api/accounts', {
    method: 'GET',
    params,
  });
}

/** 获取账户详情 */
export async function getAccountDetail(id: string) {
  return request<AccountDetailResponse>(`/api/accounts/${id}`, {
    method: 'GET',
  });
}

/** 创建账户 */
export async function createAccount(data: Omit<AccountItem, 'id' | 'created_at' | 'updated_at'>) {
  return request<AccountDetailResponse>('/api/accounts', {
    method: 'POST',
    data,
  });
}

/** 更新账户 */
export async function updateAccount(id: string, data: Partial<AccountItem>) {
  return request<AccountDetailResponse>(`/api/accounts/${id}`, {
    method: 'PUT',
    data,
  });
}

/** 删除账户 */
export async function deleteAccount(id: string) {
  return request<{ success: boolean; message?: string }>(`/api/accounts/${id}`, {
    method: 'DELETE',
  });
}

/** 汇率转换 */
export async function convertCurrency(amount: number, fromCurrency: string, toCurrency: string = 'CNY') {
  return request<{
    success: boolean;
    data: {
      original_amount: number;
      original_currency: string;
      converted_amount: number;
      target_currency: string;
      exchange_rate: number;
    };
    message?: string;
  }>('/api/exchange/convert', {
    method: 'POST',
    data: {
      amount,
      from_currency: fromCurrency,
      to_currency: toCurrency,
    },
  });
}