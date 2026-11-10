/**
 * 路由级权限：key 与后端 app/utils/permissions.py 的注册表一一对应。
 *
 * 管理员默认拥有全部权限；子账户只拥有 currentUser.permissions 里列出的模块。
 * 注意：这里只负责「菜单/路由是否可见」，真正的接口鉴权在后端强制校验。
 */
export const MENU_PERMISSIONS = [
  'dashboard',
  'orders',
  'fulfillment',
  'inventory',
  'products',
  'after_sales',
  'mail',
  'accounts',
  'expenses',
  'reports',
  'platform_accounts',
  'shops',
  'sub_accounts',
  'settings',
] as const;

export type MenuPermission = (typeof MENU_PERMISSIONS)[number];

/** 权限 key → 菜单路径（用于登录后跳转到第一个可见页面）。 */
export const PERMISSION_ROUTES: Record<MenuPermission, string> = {
  dashboard: '/dashboard',
  orders: '/orders',
  fulfillment: '/shipping',
  inventory: '/inventory',
  products: '/products',
  after_sales: '/after-sales',
  mail: '/mail',
  accounts: '/accounts',
  expenses: '/expenses',
  reports: '/reports',
  platform_accounts: '/system/platform-accounts',
  shops: '/system/shops',
  sub_accounts: '/system/sub-accounts',
  settings: '/settings',
};

export function firstAllowedPath(
  permissions: string[] | undefined | null,
  isAdmin: boolean,
): string {
  const granted = new Set(permissions ?? []);
  for (const key of MENU_PERMISSIONS) {
    if (isAdmin || granted.has(key)) {
      return PERMISSION_ROUTES[key];
    }
  }
  // 没有任何模块权限时留在登录页，由管理员开通后再进入
  return '/user/login';
}

/** 菜单路径 → 需要的权限 key（用于判断当前地址是否可访问）。 */
const PATH_PERMISSION: Array<[string, MenuPermission]> = [
  ['/orders', 'orders'],
  ['/purchases', 'fulfillment'],
  ['/logistics', 'fulfillment'],
  ['/shipping', 'fulfillment'],
  ['/packing-check', 'fulfillment'],
  ['/after-sales', 'after_sales'],
  ['/mail', 'mail'],
  ['/inventory', 'inventory'],
  ['/products', 'products'],
  ['/accounts', 'accounts'],
  ['/expenses', 'expenses'],
  ['/reports', 'reports'],
  ['/system/ai-api', 'settings'],
  ['/system/platform-accounts', 'platform_accounts'],
  ['/system/shops', 'shops'],
  ['/system/sub-accounts', 'sub_accounts'],
  ['/system', 'shops'],
  ['/settings', 'settings'],
  ['/dashboard', 'dashboard'],
];

export function permissionOfPath(pathname: string): MenuPermission | null {
  for (const [prefix, key] of PATH_PERMISSION) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
      return key;
    }
  }
  return null;
}

/** 当前地址是否允许访问（菜单权限之外的路由一律放行，交给后端兜底）。 */
export function isPathAllowed(
  pathname: string,
  permissions: string[] | undefined | null,
  isAdmin: boolean,
): boolean {
  const required = permissionOfPath(pathname);
  if (!required) return true;
  if (isAdmin) return true;
  return (permissions ?? []).includes(required);
}

export default function access(
  initialState: { currentUser?: API.CurrentUser } | undefined,
) {
  const { currentUser } = initialState ?? {};
  const isAdmin = currentUser?.access === 'admin';
  const granted = new Set(currentUser?.permissions ?? []);
  const allowed = (key: MenuPermission) => isAdmin || granted.has(key);

  return {
    canAdmin: isAdmin,
    dashboard: allowed('dashboard'),
    orders: allowed('orders'),
    fulfillment: allowed('fulfillment'),
    trackingForceOutbound: allowed('fulfillment') && (isAdmin || granted.has('tracking_force_outbound')),
    inventory: allowed('inventory'),
    products: allowed('products'),
    after_sales: allowed('after_sales'),
    mail: allowed('mail'),
    accounts: allowed('accounts'),
    expenses: allowed('expenses'),
    expensesDelete: allowed('expenses') && (isAdmin || granted.has('expenses_delete')),
    reports: allowed('reports'),
    platform_accounts: allowed('platform_accounts'),
    shops: allowed('shops'),
    sub_accounts: allowed('sub_accounts'),
    settings: allowed('settings'),
  };
}
