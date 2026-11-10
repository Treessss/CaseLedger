/**
 * 从未知错误对象里提取可展示的消息。
 *
 * 兼容三种来源：后端返回的 { message }、axios 错误对象、以及普通 Error。
 * 统一入口可以避免每个 catch 里写 `error: any`。
 */
export const getErrorMessage = (error: unknown, fallback = '操作失败'): string => {
  if (typeof error === 'string') {
    return error || fallback;
  }
  if (error && typeof error === 'object') {
    const candidate = error as {
      response?: { data?: { message?: string; errorMessage?: string } };
      message?: string;
    };
    return (
      candidate.response?.data?.message ||
      candidate.response?.data?.errorMessage ||
      candidate.message ||
      fallback
    );
  }
  return fallback;
};

/** 判断是否为表单校验错误（用于 catch 里跳过提示）。 */
export const isValidationError = (error: unknown): boolean =>
  Boolean(error && typeof error === 'object' && 'errorFields' in (error as object));

/** 取出后端返回的错误响应体（如 { message, has_data, order_count }）。 */
export const getErrorData = <T = Record<string, unknown>>(
  error: unknown,
): T | undefined => {
  if (error && typeof error === 'object') {
    return (error as { response?: { data?: T } }).response?.data;
  }
  return undefined;
};
