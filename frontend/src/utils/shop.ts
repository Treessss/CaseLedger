/**
 * 当前选中的店铺（全局数据过滤范围）。
 *
 * ALL_SHOPS 表示不限制店铺；管理员可选「全部店铺」，
 * 子账户只会看到自己被分配的店铺（后端强制校验）。
 */
export const ALL_SHOPS = 'all';

const SHOP_KEY = 'caseledger_selected_shop';

export const getSelectedShopId = (): string => {
  try {
    return localStorage.getItem(SHOP_KEY) || ALL_SHOPS;
  } catch {
    return ALL_SHOPS;
  }
};

export const setSelectedShopId = (shopId: string): void => {
  try {
    localStorage.setItem(SHOP_KEY, shopId || ALL_SHOPS);
  } catch {
    // localStorage 不可用时忽略
  }
};

export const clearSelectedShopId = (): void => {
  try {
    localStorage.removeItem(SHOP_KEY);
  } catch {
    // 同上
  }
};
