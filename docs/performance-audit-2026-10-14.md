# 全店接口性能检查与优化（2026-10-14）

## 发布后核验

- 已发布到 CaseLedger 主服务器，51 个主要查询复测全部返回 200。
- 后端 566 项、前端 57 项回归通过；隔离 PostgreSQL 的最后一件并发竞争、原单/售后共享库存和撤回余额验证通过。
- 新增计时头仅对本次请求已鉴权的用户返回 SQL 计时；未鉴权请求不暴露这些诊断信息。

| 模块 | 发布后复测 ms |
| --- | ---: |
| 订单列表 | 2773 |
| 订单详情 | 2715 |
| 待采购 | 2293 |
| 库存管理 | 2249 |
| 库存流水 | 594 |
| 发货中心 | 2332 |
| 售后列表 | 571 |


## 范围与验证

- 对 51 个主要本地查询请求，在服务器上只读测量耗时、数据库查询次数和返回状态；优化候选全部返回 200。
- 所有 API 注册路径均纳入接口清单；写接口及会触发第三方调用的接口不向真实订单执行，通过隔离测试、现有回归和代码检查验证。
- 外部依赖包括 Shopify、4PX、Zoho 和 AI。网络等待不等同于本地接口耗时；已有分批、限流、并发、超时和后台草稿机制保留。
- 性能数据为单次、带性能分析器的服务器测量，不是延迟保证。优化前后时间不同，实时业务数据和服务器负载可能变化。
- 未清空库存，未代用户执行出库、采购、物流回填或邮件发送。无模型/表结构变更。

## 已实施

- 需求数量及订单参考一次按变体归总，取消库存变体与全部订单之间的重复遍历。
- 发货中心和批量面单在每次核对中共用一个实时库存计划，下载结束仍重新核对数量及出库状态。
- 售后列表批量读取本页所需实物库存；状态筛选使用实时实物可行性，取消全店采购预测的无关计算。
- 装箱预览只验证当前原单/售后实物数量，不计算全店采购队列。
- 库存流水先分页再加载图片、名称；商品/SKU 搜索仍在筛选前解析相关信息，日汇总覆盖全部匹配记录。
- 修复轻量订单查询的 noload 空商品关系污染库存需求计算的问题，并加入回归测试。
- 接口增加请求编号及耗时观测。慢请求日志仅包含模块、方法、状态及耗时，不记录参数、凭证、客户姓名/邮箱或正文；未鉴权请求不暴露 SQL 耗时。
- 已检查服务器 JSON 压缩配置，现有 gzip 已启用，无需更改全局服务器设置。

## 查询接口复测

| 请求 | 优化前 ms | 候选复测 ms | 查询次数（前→后） | 状态 |
| --- | ---: | ---: | ---: | --- |
| `/currentUser` | 69 | 67 | 6 → 6 | 200 |
| `/users` | 34 | 34 | 6 → 6 | 200 |
| `/permissions` | 30 | 12 | 3 → 3 | 200 |
| `/shops` | 130 | 108 | 9 → 9 | 200 |
| `/shops/1` | 77 | 58 | 9 → 9 | 200 |
| `/orders` | 3977 | 2448 | 36 → 37 | 200 |
| `/orders?procurement_status=ready_to_ship` | 3736 | 1865 | 36 → 37 | 200 |
| `/orders/recent` | 733 | 745 | 32 → 32 | 200 |
| `/orders/stats` | 129 | 125 | 7 → 7 | 200 |
| `/orders/workload` | 5001 | 2793 | 81 → 84 | 200 |
| `/orders/12794` | 3791 | 1876 | 38 → 39 | 200 |
| `/orders/12794/neighbors` | 108 | 99 | 15 → 15 | 200 |
| `/orders/12794/purchase-lists` | 380 | 162 | 8 → 8 | 200 |
| `/purchase-candidates` | 3637 | 2070 | 38 → 39 | 200 |
| `/purchase-lists` | 340 | 418 | 10 → 10 | 200 |
| `/purchase-lists/31` | 315 | 332 | 16 → 16 | 200 |
| `/inventory?group_by_product=true` | 4129 | 1985 | 28 → 29 | 200 |
| `/inventory/adjustments` | 57 | 62 | 8 → 8 | 200 |
| `/inventory/movements` | 1966 | 476 | 15 → 15 | 200 |
| `/inventory/receiving/products?search=Ring` | 1160 | 1360 | 10 → 10 | 200 |
| `/shipping/ready` | 7996 | 2079 | 58 → 38 | 200 |
| `/shipping/outbound/recent` | 163 | 198 | 17 → 17 | 200 |
| `/shipping/packing-lookup?code=13425` | 154 | 173 | 19 → 19 | 200 |
| `/after-sales` | 4629 | 524 | 38 → 20 | 200 |
| `/after-sales/41` | 374 | 348 | 19 → 19 | 200 |
| `/after-sales/catalog/products?search=Ring` | 948 | 2611 | 9 → 9 | 200 |
| `/products?search=Ring` | 873 | 965 | 11 → 11 | 200 |
| `/expenses` | 144 | 88 | 10 → 10 | 200 |
| `/expenses/categories` | 18 | 15 | 3 → 3 | 200 |
| `/expenses/income-categories` | 17 | 14 | 3 → 3 | 200 |
| `/expenses/summary` | 78 | 68 | 9 → 9 | 200 |
| `/accounts` | 47 | 40 | 5 → 5 | 200 |
| `/accounts/platforms` | 13 | 20 | 3 → 3 | 200 |
| `/accounts/summary` | 52 | 46 | 6 → 6 | 200 |
| `/platform-accounts` | 39 | 48 | 5 → 5 | 200 |
| `/platform-accounts/platforms` | 19 | 16 | 4 → 4 | 200 |
| `/platform-accounts/search?search=test` | 21 | 24 | 4 → 4 | 200 |
| `/reports/financial-summary` | 235 | 557 | 8 → 8 | 200 |
| `/reports/financial` | 192 | 189 | 8 → 8 | 200 |
| `/reports/revenue-trend` | 319 | 284 | 5 → 5 | 200 |
| `/reports/expense-analysis` | 497 | 77 | 4 → 4 | 200 |
| `/reports/profit-analysis` | 410 | 274 | 8 → 8 | 200 |
| `/mail/status` | 115 | 119 | 18 → 18 | 200 |
| `/mail/messages` | 122 | 118 | 10 → 10 | 200 |
| `/mail/draft-jobs` | 34 | 37 | 7 → 7 | 200 |
| `/sync/status` | 320 | 262 | 11 → 11 | 200 |
| `/settings/fees` | 28 | 26 | 4 → 4 | 200 |
| `/exchange/supported-currencies` | 15 | 14 | 3 → 3 | 200 |
| `/accounts/10` | 37 | 35 | 6 → 6 | 200 |
| `/accounts/10/recharges` | 43 | 44 | 6 → 6 | 200 |
| `/accounts/10/consumptions` | 52 | 47 | 6 → 6 | 200 |

## 结果一致性与测试

- 在同一 PostgreSQL 只读快照内比较三个店铺的库存、需求、采购中、待补、原单/售后可发货集合及建议批次，结果相同。
- 售后全部、已备齐、采购中、待采购、已出库筛选与原规则结果逐项相同。
- 完整后端与前端回归、权限检查、分页、重复扫码、通用型号、采购缺货及货损、入库、撤回、财务日期/金额等测试通过。
- 新增轻量列表关系恢复和接口计时/未鉴权隔离测试。真实写操作未用于压测。

## 边界

- 批量面单下载、AI 草稿生成、邮箱正文/附件获取、Shopify 同步/回填等仍有第三方耗时。本次未向真实账号发送邮件或重复回填来测速度。
- 完整变体选择器保留全部款式、颜色和型号，没有通过截断变体来降低响应大小。
- 现有较快的财务、费用、账户、邮件列表和权限查询保留业务实现；它们也有统一耗时观测。

## API 登记清单

清单中的“实测”指上表的代表性只读请求，不表示每一种参数组合都已压测。“模块验证”表示纳入代码检查和模块回归，未在真实业务数据上执行写操作或外部调用。

| API 路径 | 方法 | 本次验证 |
| --- | --- | --- |
| `/api/shops` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/shops` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shops/<int:shop_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/shops/<int:shop_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/shops/<int:shop_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/currentUser` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/users` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/users` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/permissions` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/users/me/password` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/users/<int:user_id>` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/users/<int:user_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/users/<int:user_id>/shops` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/users/<int:user_id>/permissions` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/users/<int:user_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/orders` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/recent` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/stats` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/workload` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/<int:order_id>/purchase-lists` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/<int:order_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/<int:order_id>/neighbors` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/orders/<int:order_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/refresh-contact` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/shipping-label` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/parcel-count` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/parcels` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/parcels/<int:parcel_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/parcels/<int:parcel_id>/4px-label` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/scan-outbound` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/orders/<int:order_id>/shopify-writeback` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/test` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/orders` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/products` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/fulfillment-status` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/recent` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/status` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/sync/gateway-fees` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/sync/shop-info` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts/platforms` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts/summary` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts/<int:account_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts/<int:account_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts/<int:account_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts/<int:account_id>/recharges` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts/<int:account_id>/recharges` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/recharges/<int:recharge_id>/confirm` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/recharges/methods` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts/recharge` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/accounts/<int:account_id>/consumptions` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/accounts/<int:account_id>/consumptions` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/consumptions/types` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/batch` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/by-orders` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/summary` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/<int:cost_id>` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/<int:cost_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/<int:cost_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/<int:cost_id>/confirm` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/order-costs/batch/<batch_id>/confirm` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/order-cost-batches` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/order-cost-batches/<batch_id>` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/expenses` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/expenses/categories` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/expenses/income-categories` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/expenses/summary` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/expenses` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/expenses/<int:expense_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/expenses/<int:expense_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/expenses/batch-delete` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/reports/financial-summary` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/reports/financial` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/reports/revenue-trend` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/reports/expense-analysis` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/reports/profit-analysis` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/settings/shopify` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/shopify` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/shopify/test` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/shopify/sync` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/fees` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/settings/fees` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/fees/<int:fee_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/settings/fees/<int:fee_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/login/account` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/login/outLogin` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/login/captcha` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/platform-accounts` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/platform-accounts` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/platform-accounts/platforms` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/platform-accounts/search` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/platform-accounts/<int:account_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/platform-accounts/<int:account_id>/password` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/platform-accounts/<int:account_id>` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/platform-accounts/<int:account_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/purchase-candidates` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/purchase-lists` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/after-sale` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/<int:purchase_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/purchase-lists/<int:purchase_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/<int:purchase_id>/confirm` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/<int:purchase_id>/receive` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/<int:purchase_id>/pdf` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/purchase-lists/<int:purchase_id>/excel` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/inventory` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/inventory/adjustments` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/inventory/movements` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/inventory/adjustments` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/inventory/receiving/products` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/inventory/receiving` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/inventory/reservations` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/packing/start` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/packing/cancel` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/packing/priority` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/ready` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/shipping/packing-lookup` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/shipping/ready-labels/pdf` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/scan` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/outbound/recent` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/shipping/outbound/undo` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/4px/preview` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/4px/bind` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/4px/bind-and-writeback` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/4px/batch-bind-and-writeback` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/shipping/4px/batch-bind` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/after-sales` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/<int:after_sale_id>` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/after-sales/<int:after_sale_id>` | DELETE | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/4px/shopify-preview` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/4px/shopify-writeback` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/original-order` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/original-order/<int:order_id>` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/<int:after_sale_id>/note` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/catalog` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/catalog/products` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/after-sales/<int:after_sale_id>/costs` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/4px/preview` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/<int:after_sale_id>/4px/bind` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/after-sales/<int:after_sale_id>/label` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/draft-jobs` | GET, POST | GET 代表性请求实测；其余模块验证 |
| `/api/mail/draft-jobs/<job_id>/cancel` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/status` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/mail/settings` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/connect` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/sync` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/verify` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/mail/messages/<message_id>` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/attachments` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/attachments/<attachment_id>/download` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/guidance` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/hydrate-thread` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/draft` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/exchange-discount` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/reply` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/send` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/messages/<message_id>/ignore` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/mail/translate` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/products` | GET | GET 代表性请求实测；其余模块验证 |
| `/api/products/translations` | PUT | 模块验证，未真实执行写入/外部动作 |
| `/api/exchange/convert` | POST | 模块验证，未真实执行写入/外部动作 |
| `/api/exchange/rates` | GET | 模块验证，未真实执行写入/外部动作 |
| `/api/exchange/supported-currencies` | GET | GET 代表性请求实测；其余模块验证 |
