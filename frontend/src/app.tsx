import type { Settings as LayoutSettings } from '@ant-design/pro-components';
import { SettingDrawer } from '@ant-design/pro-components';
import {
  InboxOutlined,
  ShoppingOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { RequestConfig, RunTimeLayoutConfig } from '@umijs/max';
import { history } from '@umijs/max';
import React from 'react';
import {
  AvatarDropdown,
  AvatarName,
  Footer,
  Question,
  SelectLang,
} from '@/components';
import ShopSwitcher from '@/components/ShopSwitcher';
import { currentUser as queryCurrentUser } from '@/services/ant-design-pro/api';
import { clearToken } from '@/utils/auth';
import { isPackingFocusMode } from '@/utils/layoutPrefs';
import { firstAllowedPath, isPathAllowed } from './access';
import defaultSettings from '../config/defaultSettings';
import { errorConfig } from './requestErrorConfig';
import '@ant-design/v5-patch-for-react-19';
import BrowserStyleProvider from '@/components/BrowserStyleProvider';

export const rootContainer = (container: React.ReactNode) => (
  <BrowserStyleProvider>{container}</BrowserStyleProvider>
);

const isDev = process.env.NODE_ENV === 'development';
const loginPath = '/user/login';

/**
 * 侧边栏分组。
 *
 * 只改「菜单怎么显示」，不动任何路由：URL 保持 /orders、/inventory 这样，
 * 已有书签、页面里的跳转链接都不用改。
 *
 * 分组靠路径归并，所以某个账号没有这组里任何页面的权限时，这一组整块不显示
 * （菜单数据在传进来之前已经按权限过滤过了）——不会出现点开是空的菜单。
 */
const MENU_GROUPS = [
  {
    key: 'group-orders',
    locale: 'menu.groupOrders',
    label: '订单中心',
    icon: <ShoppingOutlined />,
    paths: ['/orders', '/after-sales', '/mail'],
  },
  {
    key: 'group-warehouse',
    locale: 'menu.groupWarehouse',
    label: '仓储作业',
    icon: <InboxOutlined />,
    // 按现场顺序排：先补货、再看库存，然后预申报检查 → 拣货核对 → 扫码出库，最后是对单/回填
    paths: ['/products', '/purchases', '/inventory', '/declaration-check', '/packing-check', '/shipping-outbound', '/shipping', '/logistics'],
  },
  {
    key: 'group-finance',
    locale: 'menu.groupFinance',
    label: '财务中心',
    icon: <WalletOutlined />,
    paths: ['/accounts', '/expenses', '/reports', '/settings'],
  },
];

type MenuItem = {
  path?: string;
  name?: string;
  locale?: string;
  icon?: React.ReactNode;
  children?: MenuItem[];
  [key: string]: unknown;
};

/**
 * 去掉当前账号没有权限的菜单项。
 *
 * 菜单数据里带着 `unaccessible` 标记，但 ProLayout 是在更靠后的阶段才把它过滤掉，
 * 所以这里要自己先筛一遍：否则子账户会看到一个「点开什么都没有」的空分组。
 */
function dropUnaccessible(items: MenuItem[]): MenuItem[] {
  const result: MenuItem[] = [];
  for (const item of items) {
    if (item.unaccessible === true) continue;
    if (item.children) {
      const children = dropUnaccessible(item.children);
      // 子项全都没权限 → 这个分组本身也不该出现
      if (!children.length) continue;
      result.push({ ...item, children });
    } else {
      result.push(item);
    }
  }
  return result;
}

function regroupMenu(menuData: MenuItem[]): MenuItem[] {
  const visible = dropUnaccessible(menuData);
  const byPath = new Map<string, MenuItem>();
  for (const item of visible) {
    if (item.path) byPath.set(item.path, item);
  }

  const used = new Set<string>();
  const groups: MenuItem[] = [];
  for (const group of MENU_GROUPS) {
    const children = group.paths
      .map((path) => byPath.get(path))
      .filter((item): item is MenuItem => Boolean(item));
    // 这个账号在这一组里一个页面都没有 → 整组不显示
    if (!children.length) continue;
    for (const child of children) {
      if (child.path) used.add(child.path);
    }
    groups.push({
      path: `/${group.key}`,
      key: `/${group.key}`,
      name: group.label,
      locale: group.locale,
      icon: group.icon,
      children,
    });
  }

  // 顶层顺序：仪表板 → 三个业务分组 → 系统管理 → 其它（兜底，避免新增页面漏进菜单）
  const dashboard = byPath.get('/dashboard');
  if (dashboard) used.add('/dashboard');
  const system = byPath.get('/system');
  if (system) used.add('/system');
  const leftovers = visible.filter((item) => !item.path || !used.has(item.path));

  return [
    ...(dashboard ? [dashboard] : []),
    ...groups,
    ...(system ? [system] : []),
    ...leftovers,
  ];
}

/**
 * @see https://umijs.org/docs/api/runtime-config#getinitialstate
 * */
export async function getInitialState(): Promise<{
  settings?: Partial<LayoutSettings>;
  currentUser?: API.CurrentUser;
  loading?: boolean;
  /** 打包台专注模式：隐藏左侧菜单，把宽度全留给商品清单 */
  packingFocus?: boolean;
  fetchUserInfo?: () => Promise<API.CurrentUser | undefined>;
}> {
  const fetchUserInfo = async () => {
    try {
      const msg = await queryCurrentUser({
        skipErrorHandler: true,
      });
      return msg.data;
    } catch (_error) {
      // 令牌缺失或失效：清理本地令牌，由 onPageChange 跳转到登录页。
      // 不再回退到模拟用户，否则未登录也能进入系统。
      clearToken();
      return undefined;
    }
  };
  // 如果不是登录页面，执行
  const { location } = history;
  if (
    ![loginPath, '/user/register', '/user/register-result'].includes(
      location.pathname,
    )
  ) {
    const currentUser = await fetchUserInfo();
    return {
      fetchUserInfo,
      currentUser,
      packingFocus: isPackingFocusMode(),
      settings: defaultSettings as Partial<LayoutSettings>,
    };
  }
  return {
    fetchUserInfo,
    packingFocus: isPackingFocusMode(),
    settings: defaultSettings as Partial<LayoutSettings>,
  };
}

// ProLayout 支持的api https://procomponents.ant.design/components/layout
export const layout: RunTimeLayoutConfig = ({
  initialState,
  setInitialState,
}) => {
  // 仓库专注模式：装箱核对和扫码出库共用菜单偏好，其他页面不受影响。
  const hideMenu =
    ['/packing-check', '/shipping-outbound'].some((path) => history.location.pathname.startsWith(path)) &&
    Boolean(initialState?.packingFocus);

  return {
    menuRender: hideMenu ? false : undefined,
    menuDataRender: (menuData) => regroupMenu(menuData as MenuItem[]),
    actionsRender: () => [<Question key="doc" />, <SelectLang key="SelectLang" />],
    avatarProps: {
      src: initialState?.currentUser?.avatar,
      title: <AvatarName />,
      render: (_, avatarChildren) => {
        return <AvatarDropdown>{avatarChildren}</AvatarDropdown>;
      },
    },
    waterMarkProps: {
      content: initialState?.currentUser?.name,
      // 水印做得更轻，避免盖住表格里的数字
      fontColor: 'rgba(0, 0, 0, 0.04)',
      fontSize: 14,
      gapX: 160,
      gapY: 120,
    },
    footerRender: () => <Footer />,
    onPageChange: () => {
      const { location } = history;
      // 「系统管理」是二级菜单的分组节点，单独访问时落到第一个子页面
      if (location.pathname === '/system' || location.pathname === '/system/') {
        history.push('/system/shops');
        return;
      }
      // 如果没有登录，重定向到 login
      if (!initialState?.currentUser && location.pathname !== loginPath) {
        history.push(loginPath);
        return;
      }
      // 登录后访问根路径：跳到该账号第一个有权限的模块（避免落到无权限的仪表板）
      if (initialState?.currentUser) {
        const isAdmin = initialState.currentUser.access === 'admin';
        const allowed = isPathAllowed(
          location.pathname,
          initialState.currentUser.permissions,
          isAdmin,
        );
        if (location.pathname === '/' || !allowed) {
          history.push(
            firstAllowedPath(initialState.currentUser.permissions, isAdmin),
          );
        }
      }
    },
    menuHeaderRender: undefined,
    // 自定义 403 页面
    // unAccessible: <div>unAccessible</div>,
    // 增加一个 loading 的状态
    childrenRender: (children) => {
      // if (initialState?.loading) return <PageLoading />;
      return (
        <>
          <ShopSwitcher />
          {children}
          {isDev && (
            <SettingDrawer
              disableUrlParams
              enableDarkTheme
              settings={initialState?.settings}
              onSettingChange={(settings) => {
                setInitialState((preInitialState) => ({
                  ...preInitialState,
                  settings,
                }));
              }}
            />
          )}
        </>
      );
    },
    ...initialState?.settings,
  };
};

/**
 * @name request 配置，可以配置错误处理
 * 它基于 axios 和 ahooks 的 useRequest 提供了一套统一的网络请求和错误处理方案。
 * @doc https://umijs.org/docs/max/request#配置
 */
export const request: RequestConfig = {
  // 移除baseURL，让请求通过代理处理
  // baseURL: 'http://127.0.0.1:5000',
  ...errorConfig,
};
