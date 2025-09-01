// 汇率相关API接口
import { request } from '@umijs/max';

// 支持的货币类型
export const SUPPORTED_CURRENCIES = {
  CNY: { code: 'CNY', name: '人民币', symbol: '¥' },
  USD: { code: 'USD', name: '美元', symbol: '$' },
  EUR: { code: 'EUR', name: '欧元', symbol: '€' },
  GBP: { code: 'GBP', name: '英镑', symbol: '£' },
  JPY: { code: 'JPY', name: '日元', symbol: '¥' },
  KRW: { code: 'KRW', name: '韩元', symbol: '₩' },
  HKD: { code: 'HKD', name: '港币', symbol: 'HK$' },
  TWD: { code: 'TWD', name: '台币', symbol: 'NT$' },
  SGD: { code: 'SGD', name: '新加坡元', symbol: 'S$' },
  AUD: { code: 'AUD', name: '澳元', symbol: 'A$' },
  CAD: { code: 'CAD', name: '加元', symbol: 'C$' },
};

// 汇率数据接口
export interface ExchangeRate {
  from_currency: string;
  to_currency: string;
  rate: number;
  updated_at: string;
}

// 汇率转换请求接口
export interface ConvertRequest {
  amount: number;
  from_currency: string;
  to_currency: string;
}

// 汇率转换响应接口
export interface ConvertResponse {
  original_amount: number;
  converted_amount: number;
  from_currency: string;
  to_currency: string;
  exchange_rate: number;
  updated_at: string;
}

// 获取汇率列表
export const getExchangeRates = async (params?: {
  from_currency?: string;
  to_currency?: string;
}): Promise<{ success: boolean; data: ExchangeRate[]; message?: string }> => {
  try {
    const data = await request('/api/exchange/rates', {
      method: 'GET',
      params,
    });
    return data;
  } catch (error) {
    console.error('获取汇率失败:', error);
    return { success: false, data: [], message: '获取汇率失败' };
  }
};

// 货币转换
export const convertCurrency = async (params: ConvertRequest): Promise<{
  success: boolean;
  data?: ConvertResponse;
  message?: string;
}> => {
  try {
    const data = await request('/api/exchange/convert', {
      method: 'POST',
      data: params,
    });
    return data;
  } catch (error) {
    console.error('货币转换失败:', error);
    return { success: false, message: '货币转换失败' };
  }
};

// 获取实时汇率（单个）
export const getExchangeRate = async (fromCurrency: string, toCurrency: string): Promise<{
  success: boolean;
  data?: ExchangeRate;
  message?: string;
}> => {
  try {
    const data = await request(`/api/exchange/rate/${fromCurrency}/${toCurrency}`);
    return data;
  } catch (error) {
    console.error('获取汇率失败:', error);
    return { success: false, message: '获取汇率失败' };
  }
};

// 刷新汇率数据
export const refreshExchangeRates = async (): Promise<{
  success: boolean;
  message?: string;
}> => {
  try {
    const data = await request('/api/exchange/refresh', {
      method: 'POST',
    });
    return data;
  } catch (error) {
    console.error('刷新汇率失败:', error);
    return { success: false, message: '刷新汇率失败' };
  }
};

// 格式化货币显示
export const formatCurrency = (amount: number, currencyCode: string): string => {
  const currency = SUPPORTED_CURRENCIES[currencyCode as keyof typeof SUPPORTED_CURRENCIES];
  if (!currency) {
    return `${amount.toFixed(2)} ${currencyCode}`;
  }
  
  return `${currency.symbol}${amount.toFixed(2)}`;
};

// 获取货币选项（用于下拉框）
export const getCurrencyOptions = () => {
  return Object.values(SUPPORTED_CURRENCIES).map(currency => ({
    label: `${currency.name} (${currency.code})`,
    value: currency.code,
    symbol: currency.symbol,
  }));
};