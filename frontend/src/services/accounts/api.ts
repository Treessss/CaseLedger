import { request } from '@umijs/max';

export interface AccountItem {
  id: string;
  shop_id?: number;
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
  page?: number;
  per_page?: number;
  platform?: string;
  status?: string;
  account_name?: string;
}

export interface AccountListResponse {
  success: boolean;
  /** 账户数组（分页信息在顶层 pagination） */
  data: AccountItem[];
  pagination?: {
    page: number;
    pages: number;
    per_page: number;
    total: number;
    has_next: boolean;
    has_prev: boolean;
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
  const { current, pageSize, ...rest } = params || {};
  return request<AccountListResponse>('/api/accounts', {
    method: 'GET',
    params: {
      ...rest,
      page: rest.page ?? current,
      per_page: rest.per_page ?? pageSize,
    },
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

export interface AccountSummary {
  total_accounts: number;
  total_balance: number;
  month_recharge: number;
  month_consumption: number;
  platform_summary: Record<
    string,
    {
      count: number;
      total_balance: number;
      accounts: Array<{ id: number; account_name: string; balance: number }>;
    }
  >;
}

export interface RechargeItem {
  id: number;
  account_id: number;
  account_name?: string;
  amount: number;
  currency?: string;
  recharge_method?: string;
  description?: string;
  status?: string;
  recharge_date?: string;
}

export interface ConsumptionItem {
  id: number;
  account_id: number;
  amount: number;
  currency?: string;
  consumption_type?: string;
  description?: string;
  consumption_date?: string;
}

/** 账户汇总（余额、本月充值/消耗、按平台分组） */
export async function getAccountSummary(params?: {
  platform?: string;
  start_date?: string;
  end_date?: string;
}) {
  return request<{ success: boolean; data: AccountSummary }>('/api/accounts/summary', {
    method: 'GET',
    params: params || {},
  });
}

/** 账户充值（通用入口，account_id 在 body 中） */
export async function rechargeAccount(data: {
  account_id: number;
  amount: number;
  currency?: string;
  recharge_method?: string;
  description?: string;
}) {
  return request<{ success: boolean; message?: string; recharge?: RechargeItem }>(
    '/api/accounts/recharge',
    {
      method: 'POST',
      data,
    },
  );
}

/** 账户充值记录 */
export async function getAccountRecharges(
  accountId: number,
  params?: { page?: number; per_page?: number; status?: string },
) {
  return request<{
    success: boolean;
    recharges: RechargeItem[];
    pagination: { total: number };
  }>(`/api/accounts/${accountId}/recharges`, {
    method: 'GET',
    params: params || {},
  });
}

/** 账户消耗记录 */
export async function getAccountConsumptions(
  accountId: number,
  params?: { page?: number; per_page?: number; consumption_type?: string },
) {
  return request<{
    success: boolean;
    consumptions: ConsumptionItem[];
    pagination: { total: number };
  }>(`/api/accounts/${accountId}/consumptions`, {
    method: 'GET',
    params: params || {},
  });
}
