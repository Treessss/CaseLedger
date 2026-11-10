/**
 * 页面级布局偏好。
 *
 * 装箱核对和扫码出库默认进入「专注模式」：隐藏左侧菜单，把宽度全留给
 * 商品清单。仓库现场大多用小屏或平板，菜单常年用不到，但偶尔也要切回来看数据。
 */
const PACKING_FOCUS_KEY = 'caseledger_packing_focus';

/** 打包台是否隐藏左侧菜单（默认隐藏） */
export const isPackingFocusMode = (): boolean => {
  try {
    return localStorage.getItem(PACKING_FOCUS_KEY) !== '0';
  } catch {
    return true;
  }
};

export const setPackingFocusMode = (enabled: boolean): void => {
  try {
    localStorage.setItem(PACKING_FOCUS_KEY, enabled ? '1' : '0');
  } catch {
    // localStorage 不可用时忽略
  }
};
const PACKING_SCANNER_KEY = 'caseledger_packing_scanner_mode';

export function isPackingScannerMode(): boolean {
  try { return localStorage.getItem(PACKING_SCANNER_KEY) !== 'manual'; } catch { return true; }
}

export function setPackingScannerMode(scanner: boolean): void {
  try { localStorage.setItem(PACKING_SCANNER_KEY, scanner ? 'scanner' : 'manual'); } catch { /* storage unavailable */ }
}
