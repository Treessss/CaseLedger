import { request } from '@umijs/max';

export interface OrderItem {
  id: number;
  shopify_order_id: string;
  order_number: string;
  customer_email?: string;
  customer_name?: string;
  total_price: number;
  actual_received?: number;
  payment_method?: string;
  payment_fee: number;
  product_cost: number;
  shipping_cost: number;
  gross_profit?: number;
  profit_margin?: number;
  financial_status?: string;
  fulfillment_status?: string;
  order_date?: string;
  created_at?: string;
  shipping_cost_cny?: number;
  fangguo_cost_cny?: number;
  gross_profit_cny: number;
  payments?: any[];
  // 新增分摊费用字段
  allocated_product_cost: number;
  allocated_shipping_cost: number;
  allocated_expenses?: {
    expense_count: number;
    total_amount: number;
    expenses: Array<{
      id: number;
      category: string;
      amount: number;
      description: string;
      date: string;
    }>;
  };
}

export interface OrderListParams {
  page?: number;
  per_page?: number;
  status?: string;
  financial_status?: string;
  fulfillment_status?: string;
  start_date?: string;
  end_date?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}

export interface OrderListResponse {
  success: boolean;
  data: {
    orders: OrderItem[];
    pagination: {
      page: number;
      per_page: number;
      total: number;
      pages: number;
    };
  };
  message?: string;
}

export interface OrderDetailResponse {
  success: boolean;
  data: OrderItem;
  message?: string;
}

/** 获取订单列表 */
export async function getOrders(params?: OrderListParams) {
  return request<OrderListResponse>('/api/orders', {
    method: 'GET',
    params,
  });
}

/** 获取订单详情 */
export async function getOrderDetail(orderId: number) {
  return request<OrderDetailResponse>(`/api/orders/${orderId}`, {
    method: 'GET',
  });
}

/** 更新订单 */
export async function updateOrder(orderId: number, data: Partial<OrderItem>) {
  return request<OrderDetailResponse>(`/api/orders/${orderId}`, {
    method: 'PUT',
    data,
  });
}

/** 获取最近订单 */
export async function getRecentOrders() {
  return request('/api/orders/recent', {
    method: 'GET',
  });
}

/** 获取订单统计 */
export async function getOrderStats() {
  return request('/api/orders/stats', {
    method: 'GET',
  });
}