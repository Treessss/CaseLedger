/**
 * @name umi 的路由配置
 * @description 只支持 path,component,routes,redirect,wrappers,name,icon 的配置
 * @param path  path 只支持两种占位符配置，第一种是动态参数 :id 的形式，第二种是 * 通配符，通配符只能出现路由字符串的最后。
 * @param component 配置 location 和 path 匹配后用于渲染的 React 组件路径。可以是绝对路径，也可以是相对路径，如果是相对路径，会从 src/pages 开始找起。
 * @param routes 配置子路由，通常在需要为多个路径增加 layout 组件时使用。
 * @param redirect 配置路由跳转
 * @param wrappers 配置路由组件的包装组件，通过包装组件可以为当前的路由组件组合进更多的功能。 比如，可以用于路由级别的权限校验
 * @param name 配置路由的标题，默认读取国际化文件 menu.ts 中 menu.xxxx 的值，如配置 name 为 login，则读取 menu.ts 中 menu.login 的取值作为标题
 * @param icon 配置路由的图标，取值参考 https://ant.design/components/icon-cn， 注意去除风格后缀和大小写，如想要配置图标为 <StepBackwardOutlined /> 则取值应为 stepBackward 或 StepBackward，如想要配置图标为 <UserOutlined /> 则取值应为 user 或者 User
 * @doc https://umijs.org/docs/guides/routes
 */
export default [
  {
    path: '/user',
    layout: false,
    routes: [
      {
        name: 'login',
        path: '/user/login',
        component: './user/login',
      },
    ],
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    icon: 'dashboard',
    access: 'dashboard',
    component: './Dashboard',
  },
  {
    path: '/orders',
    name: 'orders',
    icon: 'shopping',
    access: 'orders',
    component: './Orders/List',
  },
  {
    path: '/purchases',
    name: 'purchases',
    icon: 'shoppingCart',
    access: 'fulfillment',
    component: './Purchases',
  },
  {
    path: '/products',
    name: 'products',
    icon: 'appstore',
    access: 'products',
    component: './Products',
  },
  {
    path: '/inventory',
    name: 'inventory',
    icon: 'inbox',
    access: 'inventory',
    component: './Inventory',
  },
  {
    path: '/logistics',
    name: 'logistics',
    icon: 'global',
    access: 'fulfillment',
    component: './Logistics',
  },
  {
    path: '/shipping',
    name: 'shipping',
    icon: 'barcode',
    access: 'fulfillment',
    component: './Shipping',
  },
  {
    path: '/declaration-exceptions',
    redirect: '/declaration-check',
  },
  {
    path: '/declaration-check',
    name: 'declarationCheck',
    icon: 'fileSearch',
    access: 'fulfillment',
    component: './DeclarationCheck',
  },
  {
    path: '/packing-check',
    name: 'packingCheck',
    icon: 'scan',
    access: 'fulfillment',
    component: './PackingCheck',
  },
  {
    path: '/shipping-outbound',
    name: 'shippingOutbound',
    icon: 'export',
    access: 'fulfillment',
    component: './ShippingOutbound',
  },
  {
    path: '/after-sales',
    name: 'afterSales',
    icon: 'customerService',
    access: 'after_sales',
    component: './AfterSales',
  },
  {
    path: '/mail',
    name: 'mail',
    icon: 'mail',
    access: 'mail',
    component: './MailDesk',
  },
  {
    path: '/orders/detail/:id',
    name: 'orders.detail',
    access: 'orders',
    component: './Orders/Detail',
    hideInMenu: true,
  },
   {
     path: '/orders/edit/:id',
     name: 'orders.edit',
     access: 'orders',
     component: './Orders/Edit',
     hideInMenu: true,
   },
   {
    path: '/accounts',
    name: 'accounts',
    icon: 'wallet',
    access: 'accounts',
    component: './Accounts',
  },
  {
    path: '/expenses',
    name: 'expenses',
    icon: 'dollar',
    access: 'expenses',
    component: './Expenses',
  },
  {
    path: '/reports',
    name: 'reports',
    icon: 'barChart',
    access: 'reports',
    component: './Reports',
  },
  {
    path: '/settings',
    name: 'settings',
    icon: 'setting',
    access: 'settings',
    component: './Settings',
  },
  // 配置类功能很少改，收进一个「系统管理」子菜单，日常菜单更干净
  {
    path: '/system',
    name: 'system',
    icon: 'setting',
    routes: [
      {
        path: '/system/shops',
        name: 'shops',
        icon: 'shop',
        access: 'shops',
        component: './Shops',
      },
      {
        path: '/system/platform-accounts',
        name: 'platformAccounts',
        icon: 'key',
        access: 'platform_accounts',
        component: './PlatformAccounts',
      },
      {
        path: '/system/ai-api',
        name: 'aiApi',
        access: 'settings',
        component: './AiApi',
      },
      {
        path: '/system/sub-accounts',
        name: 'subAccounts',
        icon: 'team',
        access: 'sub_accounts',
        component: './SubAccounts',
      },
    ],
  },
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '*',
    layout: false,
    component: './404',
  },
];
