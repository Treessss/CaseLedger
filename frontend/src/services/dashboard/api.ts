import { request } from '@umijs/max';

export interface DashboardStats {
  order_count: number;
  total_revenue: number;
  total_expenses: number;
  total_profit: number;
  profit_margin: number;
  period: {
    start_date: string;
    end_date: string;
  };
}

export interface DashboardResponse {
  success: boolean;
  data: DashboardStats;
  message?: string;
}

/** 获取仪表板统计数据 */
export async function getDashboardStats(startDate?: string, endDate?: string) {
  const params: any = {};
  if (startDate) params.start_date = startDate;
  if (endDate) params.end_date = endDate;
  
  return request<DashboardResponse>('/api/reports/financial-summary', {
    method: 'GET',
    params,
  });
}

/** 获取最近订单 */
export async function getRecentOrders() {
  return request('/api/orders/recent', {
    method: 'GET',
  });
}

export interface WorkloadSummary {
  pending_ship: number;
  ready_to_ship: number;
  awaiting_purchase: number;
  shipped_today: number;
}

/** 今日作业看板：待发货 / 可出库 / 待采购 / 今日已出库 */
export async function getWorkload() {
  return request<{ success: boolean; data: WorkloadSummary }>(
    '/api/orders/workload',
    { method: 'GET' },
  );
}

/** 获取费用分析数据 */
export async function getExpenseAnalysis(params?: {
  start_date?: string;
  end_date?: string;
}) {
  return request('/api/reports/expense-analysis', {
    method: 'GET',
    params,
  });
}
