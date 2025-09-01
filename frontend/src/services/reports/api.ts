import { request } from '@umijs/max';

export interface FinancialData {
  date: string;
  income: number;
  expense: number;
  profit: number;
}

export interface ReportParams {
  start_date?: string;
  end_date?: string;
  report_type?: 'daily' | 'weekly' | 'monthly' | 'yearly';
  category?: string;
}

export interface FinancialReportResponse {
  success: boolean;
  data: {
    financial_data: FinancialData[];
    summary: {
      total_income: number;
      total_expense: number;
      total_profit: number;
      profit_margin: number;
    };
  };
  message?: string;
}

export interface ExpenseReportResponse {
  success: boolean;
  data: {
    categories: Array<{
      category: string;
      amount: number;
      percentage: number;
    }>;
    total_amount: number;
  };
  message?: string;
}

export interface ProfitReportResponse {
  success: boolean;
  data: {
    monthly_profit: Array<{
      month: string;
      profit: number;
      margin: number;
    }>;
    total_profit: number;
    average_margin: number;
  };
  message?: string;
}

/** 获取财务报表 */
export async function getFinancialReport(params?: ReportParams) {
  return request<FinancialReportResponse>('/api/reports/financial', {
    method: 'GET',
    params,
  });
}

/** 获取费用报表 */
export async function getExpenseReport(params?: ReportParams) {
  return request<ExpenseReportResponse>('/api/reports/expenses', {
    method: 'GET',
    params,
  });
}

/** 获取利润报表 */
export async function getProfitReport(params?: ReportParams) {
  return request<ProfitReportResponse>('/api/reports/profit', {
    method: 'GET',
    params,
  });
}

/** 获取财务汇总 */
export async function getFinancialSummary() {
  return request('/api/reports/financial-summary', {
    method: 'GET',
  });
}

/** 导出报表 */
export async function exportReport(params: ReportParams & { format: 'excel' | 'pdf' }) {
  return request('/api/reports/export', {
    method: 'POST',
    data: params,
    responseType: 'blob',
  });
}