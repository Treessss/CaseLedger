import { request } from '@umijs/max';

export interface ExpenseItem {
  id: number;
  category: string;
  description: string;
  amount: number;
  currency?: string; // 货币类型
  original_amount?: number; // 原始金额
  original_currency?: string; // 原始货币类型
  exchange_rate?: number; // 汇率
  date: string;
  status: 'pending' | 'approved' | 'rejected';
  submitter: string;
  receipt_url?: string;
  created_at?: string;
  updated_at?: string;
  order_numbers?: string[]; // 关联的订单号列表
  order_ids?: number[]; // 关联的订单ID列表
  order_id?: number; // 关联的订单ID
  order_info?: {
    id: number;
    order_number: string;
    customer_name: string;
    total_price: number;
  }; // 关联的订单信息
  orders_info?: {
    id: number;
    order_number: string;
    customer_name: string;
    total_price: number;
  }[]; // 所有关联的订单信息
  account_id?: string; // 扣费账户ID
  account_name?: string; // 扣费账户名称
}

export interface ExpenseListParams {
  page?: number;
  per_page?: number;
  category?: string;
  status?: string;
  start_date?: string;
  end_date?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}

export interface ExpenseListResponse {
  success: boolean;
  data: {
    expenses: ExpenseItem[];
    pagination: {
      page: number;
      per_page: number;
      total: number;
      pages: number;
    };
  };
  message?: string;
}

export interface ExpenseDetailResponse {
  success: boolean;
  data: ExpenseItem;
  message?: string;
}

/** 获取费用列表 */
export async function getExpenses(params?: ExpenseListParams) {
  return request<ExpenseListResponse>('/api/expenses', {
    method: 'GET',
    params,
  });
}

/** 获取费用详情 */
export async function getExpenseDetail(expenseId: number) {
  return request<ExpenseDetailResponse>(`/api/expenses/${expenseId}`, {
    method: 'GET',
  });
}

/** 创建费用 */
export async function createExpense(data: Omit<ExpenseItem, 'id' | 'created_at' | 'updated_at'> & { order_ids?: number[]; account_id?: string }) {
  return request<ExpenseDetailResponse>('/api/expenses', {
    method: 'POST',
    data,
  });
}

/** 更新费用 */
export async function updateExpense(expenseId: number, data: Partial<ExpenseItem>) {
  return request<ExpenseDetailResponse>(`/api/expenses/${expenseId}`, {
    method: 'PUT',
    data,
  });
}

/** 删除费用 */
export async function deleteExpense(expenseId: number) {
  return request(`/api/expenses/${expenseId}`, {
    method: 'DELETE',
  });
}

/** 批量删除费用 */
export async function batchDeleteExpenses(expenseIds: number[]) {
  return request('/api/expenses/batch-delete', {
    method: 'POST',
    data: {
      expense_ids: expenseIds,
    },
  });
}

/** 获取费用统计 */
export async function getExpenseStats() {
  return request('/api/expenses/stats', {
    method: 'GET',
  });
}

/** 获取费用分类 */
export async function getExpenseCategories() {
  return request('/api/expenses/categories', {
    method: 'GET',
  });
}