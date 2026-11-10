/**
 * 菜单文案。key 是 `menu.<routes.ts 里的 name>`，ProLayout 会自动拼。
 *
 * 命名口径（避免混淆）：
 *   - 资金账户（accounts）        = 收付款用的钱
 *   - 平台账号（platformAccounts）= 各平台后台的登录凭据
 *   - 员工账号（subAccounts）     = 本系统里给同事开的登录账号
 */
export default {
  'menu.logistics': '物流轨迹',
  'menu.products': '商品管理',
  'menu.login': '登录',

  // 菜单分组（只在显示层分组，路径不变，见 app.tsx 的 menuDataRender）
  'menu.groupOrders': '订单中心',
  'menu.groupWarehouse': '仓储作业',
  'menu.groupFinance': '财务中心',

  // 日常作业
  'menu.dashboard': '仪表板',
  'menu.orders': '订单管理',
  'menu.orders.detail': '订单详情',
  'menu.orders.edit': '编辑订单',
  'menu.purchases': '采购管理',
  'menu.inventory': '库存管理',
  'menu.shipping': '发货中心',
  'menu.declarationCheck': '4PX 预申报',
  'menu.packingCheck': '装箱核对',
  'menu.shippingOutbound': '扫码出库',
  'menu.afterSales': '售后管理',
  'menu.mail': '客服邮件',

  // 财务
  'menu.accounts': '资金账户',
  'menu.expenses': '费用管理',
  'menu.settings': '费用配置',
  'menu.reports': '财务报表',

  // 系统管理（配一次就很少动，收进二级菜单）
  'menu.system': '系统管理',
  // 嵌套路由的文案 key 是「父级.子级」，ProLayout 按完整路径拼
  'menu.system.shops': '店铺管理',
  'menu.system.platformAccounts': '平台账号',
  'menu.system.subAccounts': '员工账号',
  'menu.system.aiApi': 'AI 接口',
};
