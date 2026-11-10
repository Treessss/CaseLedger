/**
 * 登录令牌的本地存取。
 *
 * 令牌只在请求头 Authorization 中发送，不再拼接在 URL 查询参数上
 * （避免令牌出现在访问日志、浏览器历史与 Referer 中）。
 */
const TOKEN_KEY = 'caseledger_token';

export const getToken = (): string => {
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
};

export const setToken = (token: string): void => {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // 隐私模式下 localStorage 可能不可用，忽略即可
  }
};

export const clearToken = (): void => {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 同上
  }
};
