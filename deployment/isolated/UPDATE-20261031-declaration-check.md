# 2026-10-31 更新记录：4PX 预申报检查页（只读，不建单）

- 服务器：192.3.236.57，HTTPS 8443；Compose 项目 `caseledger-copy`。
- 新前端目录：`/opt/caseledger-copy/releases/declaration-check-20261031b/frontend/dist`。
- 后端新镜像：`caseledger-copy-app:local`（`d04a9483a214`），web/worker/beat/各 worker 已重建重启。
- 本次没有数据库迁移。

## 改动内容

1. **新增页面「仓储作业 → 4PX 预申报检查」**（`/declaration-check`）。
   把「物流下单条件」翻译成可核对的建议：国家 + 件数 + 特殊型号 + 两张邮编表 → 物流产品代码，
   同时给出预估预报重、按订单商品货值估算的申报价值、拦截原因和请求体预览。
2. **规则引擎**（`app/services/fourpx_declaration_service.py`）：
   - 美国：邮编命中岛屿清单 → `S5118 美国岛屿专线-普货`；否则 1–2 件 → `S5058 轻小件`，3 件及以上 → `QC 标准挂号`；
     两件都是 Gradient Magnetic 手机壳套装 → `QC`。
   - 英国 / 澳大利亚：同上（无岛屿规则）。
   - 加拿大：邮编不在「智选不可达」清单 → `S5191 智选产品-标准普货`（重量按 1 件 0.12kg、2 件 0.18kg）；
     不可达 → 1–2 件回退 `S5058`，3 件及以上回退 `QC`；两件特殊型号 → `QC`。
   - 新西兰 / 荷兰：统一 `QC 标准挂号`。
   - 规则外国家、缺电话/邮编/地址、件数为 0 的订单会被列出但标红，不进入可下单列表。
3. **邮编数据**：`app/data/fourpx_postal_codes.json`（由业务提供的两张表生成）：
   加拿大智选不可达 8,262 个 6 位邮编；美国岛屿可服务 580 个 5 位邮编。
4. **接口**：`GET /api/shipping/4px/declaration/check?shop_id=&search=&limit=`，
   权限沿用 `/api/shipping` 的 `fulfillment`；只读：不调用 4PX 下单接口、不写包裹/订单/库存。
5. 产品代码来自店铺 4PX 账号 2026-10-30 的实时产品列表，并用运费试算验证过可用。

## 历史数据核对（2026-10-16 ~ 10-30，1,289 张 4PX 委托单，匹配本地订单 1,205 张）

- 实际路由与规则基本一致：AU 1 件 → S5058（328 单）、2 件 → S5058/QC、3+ → QC；
  US 1 件 → S5058（449 单）、岛屿邮编 → S5118（13 单）；NZ → QC（28 单）。
- 加拿大实际以 S5058 为主（66 单），智选 S5191 只有 13 单 —— 规则要求「优先智选」，
  页面会按规则建议 S5191，现场差异需要业务再确认（是否只有部分邮编/产品可用）。
- 4PX 实测确认重：1 件中位 91g（特殊型号 117g）、2 件中位 166g（特殊型号 212g）、3+ 中位 246g。
  页面预报重按 120g/件、2 件 180g 估算，真实重量以 4PX 到仓实测为准。

## 申报价值

- 4PX 接口（`ds.xms.order.get`）对这批联邮通委托**不返回申报价值**：1,289 条里只有 2 条带
  `currency_code/parcel_total_value_confirm` 字段；面单 PDF 也不打印金额，只打印品名和件数（如 `Phone case*1`）。
- 因此页面暂按「订单商品货值合计」估算（USD），并在提示里标注待确认；
  等从 4PX 后台导出申报信息或业务确认口径后，再改成正式配置。

## 验证

- 后端：`venv/bin/python -m pytest -q` 全量通过（含新增 15 项预申报检查用例）。
- 前端：新增页面 3 项 jest 用例通过；`npx tsc --noEmit`、`biome lint`、`npm run build` 通过（30 个 JS 资源，Safari 13 语法检查通过）。
- 线上：`/`、`/declaration-check/`、`/shipping/` 均 200；未登录访问新接口返回 401；
  用管理员会话实测接口返回 200 个候选、全部可预申报，产品分布 S5058 167 / S5191 18 / QC 13 / S5118 2；
  nginx 无新增 404。
- 说明：前端全量 jest 并行跑时，`src/pages/Logistics` 有一项偶发失败（单跑通过），与本次改动无关。

## 回滚

```bash
# 前端
ln -sfn /opt/caseledger-copy/releases/outbound-shipped-details-20261029/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist

# 后端镜像（旧镜像仍保留）
docker tag 1821fbeedf37 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

## 边界

- 本页只做规则检查，**不会向 4PX 创建委托单**。
- 真实下单还缺：发件人/退件地址、揽收信息（已确认是上门揽收）、duty_type/税号、预生成标签权限。
- 当前规则里的「加拿大优先智选」「两件特殊型号」等口径如与现场不符，改服务里的常量即可。

---

# 2026-10-31 二次更新：申报配置改为历史 4PX 订单口径

## 数据来源

- 从微信收到的 4PX 导出（2026-09-28 下载）：
  - `预报订单数据.xls` + `预报订单数据 (1).xls` + `订单数据 (1).xls`：共 158 条唯一预报单，
    含 申报中文品名 / 申报英文品名 / 申报数量 / 申报单价（其中 124 条为单商品行，已与本地订单逐单核对）；
  - `9月7/8/9、9月15 卡包、7月6/7` 账单明细：374 条，用于核对申报品名（手机壳/钱包/手机保护膜/磁吸支架）。
- 4PX OpenAPI（`ds.xms.order.get`）本身不返回申报价值，面单 PDF 也不打印金额，因此本次以导出数据为准。

## 统计结论（已写进服务代码）

- 申报英文品名：手机壳 → `Phone case`（历史导出里还有 phone case / Phone Case / 拼写错误 phoen case，统一用 Phone case）；
  手机链 → `phone chain`；手机保护膜 → `Mobile phone protective film`；钱包 → `Wallet`；磁吸支架 → `Magnetic support bracket`。
- 申报单价（每件）：**美国 1.20 USD、澳大利亚 1.20 USD、加拿大 1.00 USD、英国 1.00 USD、新西兰 1.00 USD**；
  手机链 1.00 USD；手机保护膜 1.20 USD；钱包只出现过品名、没有单价记录，先沿用同国家口径并标注待确认。
- 申报数量 = 订单该商品件数；多品类商品按品类分行申报（如手机壳 + 手机链）。
- 158 条里 139 条走轻小件、14 条加拿大走智选、5 条标准挂号，与页面规则一致。

## 页面变化

- 「申报价值」列改为按上述口径计算（不再按订单售价），一次支付示例：
  US 1 件 = 1.20、2 件 = 2.40；CA/GB 1 件 = 1.00；保护膜 1.20/件。
- 展开「查看请求体」新增「报关明细」：品名（中/英）、数量、单价、金额；
  `declare_product_info` 按品类生成，含 `declare_product_name_cn/en`、数量、单价、币种 USD、原产国 CN。
- 页头显示申报口径说明；`rules.declare` 一并返回品类价格表，后续可直接改服务常量。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261031c/frontend/dist`。
- 后端镜像：`caseledger-copy-app:local`（`3bc791e46077`），已重启 web/worker/beat/各 worker。
- 无数据库迁移。

## 线上验证

- 接口返回 200 个候选、全部可预申报；申报价值分布：US 1.2/2.4/3.6、AU 1.2–4.8、CA 1.0/2.0/3.0、
  GB 1.0/2.0/3.2、NZ 1.0，符合国家口径。
- `/`、`/declaration-check/` 200；未登录接口 401；容器全部 Up。
- 后端全量 pytest 与前端 tsc/biome/build 均在本地通过。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261031b/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag d04a9483a214 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-10-31 第三次更新：服务端分页、CSV 导出、批量预申报清单（仍不建单）

## 背景

生产店铺 1 实际待处理 345 单（18 页），原页面一次最多显示 100 单，且无法导出人工清单。

## 改动内容

1. **后端分页**：`GET /api/shipping/4px/declaration/check` 支持 `page` / `per_page`（默认 20，最大 100），
   返回 `pagination`；`summary` 按全部候选统计，不再只统计当前页。
2. **CSV 导出**：`GET /api/shipping/4px/declaration/check.csv`，UTF-8 BOM，最多 2000 行，
   列：订单号/日期/国家/邮编/件数/建议产品代码/名称/规则依据/预报重/申报价值/币种/状态/拦截原因。
3. **批量预申报清单**：`POST /api/shipping/4px/declaration/plan`，入参 `shop_id` + `order_ids`（最多 200 单）：
   - 只接纳仍符合条件（待出库、已付款、无包裹）的订单，其余进 `invalid_order_ids`；
   - 按建议物流产品分组汇总：订单数、件数、合计预报重、申报价值合计、订单号清单；
   - 返回每单完整检查行（含请求体预览），**不调用 4PX、不写库**。
4. **前端**：表格改为服务端分页（20/页）+ 订单号搜索；只有「可预申报」的行可勾选；
   新增「生成批量清单」弹窗和「导出清单」（CSV）按钮。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261031d/frontend/dist`。
- 后端镜像：`caseledger-copy-app:local`（`6175ccdf3b62`），已重启 web/worker/beat/各 worker。
- 无数据库迁移。

## 线上验证

- 分页：店铺 1 共 **345 单 / 18 页**，第 1、2 页各返回 20 行且订单不重复；
- CSV：40.9 KB，346 行（表头 + 345 单）；
- 批量清单：勾选 2 单可预申报订单 → eligible 2、invalid 0，分组 `S5058 × 2 单、申报合计 3.60 USD`；
- `/`、`/declaration-check/` 200；`check.csv` 与 `plan` 未登录均 401；容器全部 Up；
- 后端 20 项预申报测试通过；前端页面 5 项用例通过、tsc/biome/build 通过。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261031c/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag 3bc791e46077 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-10-31 第四次更新：真实预申报建单（发件人走 4PX 账号默认值）

## 关键验证（生产账号，已取消，无残留）

按业务要求「用现在 4PX 的申报方式」，先用可取消的试探请求把必填字段确认清楚：

1. `ds.xms.order.create` 允许 `sender`、`return_info`、`deliver_to_recipient_info` 传**空对象**，
   `deliver_type_info` 只传 `{"deliver_type": "2"}`（上门揽收），4PX 使用账号默认的发件人/揽收设置；
2. `declare_product_info` 必须至少有：中英文品名、数量、**进口与出口两套申报单价和币种**
   （只传 export 会报 `参数不能为空:申报品报关单价`）；
3. 试探单创建成功后立即调用 `ds.xms.order.cancel` 取消，按 ref_no 回查为空，4PX 端无残留；
4. 说明：失败并报校验错误的 ref_no 不会生成委托单，但短时间内重用会提示 `in processing`，重试要换新的 ref 或先回查。

## 新增能力

- 后端 `create_declarations`：批量向 4PX 建单（每次 ≤50 单，必须 `confirm=true`）。
  - 只处理检查结果里「可预申报」的订单，已有包裹的跳过；
  - 建单成功后写 `shipping_parcels`，`source=4px-auto`，`label_number`=4PX 跟踪号、`label_barcode`=面单条码；
  - 报错时先按 ref_no 回查 `ds.xms.order.get`，能查到就按「已建单（回查）」处理，避免重复建单；
  - 请求体：发件人/退件/投递信息为空对象（账号默认），申报明细用历史口径（品名、$1.00/$1.20 单价）。
- 接口：`POST /api/shipping/4px/declaration/create`，入参 `shop_id`、`order_ids`、`confirm`。
- 页面：勾选订单后点「真实预申报」→ 二次确认弹窗（说明会真实占用 4PX 单号）→ 建单结果表
  （已建单/已建单（回查）/跳过/失败 + 4PX 单号 + 失败原因）。

## 部署与验证

- 后端镜像：`caseledger-copy-app:local`（待填）；
- 前端：`/opt/caseledger-copy/releases/declaration-check-20261031e/frontend/dist`；
- 本地全量 pytest、页面测试、tsc/biome/build 通过；
- 线上：用一个真实待出库订单端到端建单验证后取消并清理本地包裹记录（详见下文）。

---

# 2026-10-31 第五次更新：真实建单上线（仅 US/CA/GB/NZ）+ 事故说明

## 修正上一条的试探结论

继续用可取消试探确认发件人规则：

| 目的地 | 发件人传空对象 | 结论 |
| --- | --- | --- |
| US | ✅ 建单成功（随后取消） | 用 4PX 账号默认发件人 |
| CA | ✅ 建单成功（随后取消） | 同上 |
| GB | ✅ 建单成功（随后取消） | 同上 |
| NZ（产品 QC） | ✅ 建单成功（随后取消） | 同上 |
| AU（S5058 / QC 都试了） | ❌ 报「请填写发件人姓名/地址/邮编/电话/省州/城市」 | 必须逐单提供发件人 |

所以 `create_declarations` 增加了国家限制：只对 `US/CA/GB/NZ` 真正调用 4PX；
AU 直接跳过并提示「该国家需要配置 4PX 发件人后才能自动建单」，不做任何外部请求。

## 建单流程（已上线）

- 建单前先按 `ref_no`（订单号）调 `ds.xms.order.get` 回查：
  - 已有 4PX 委托单 → 直接接回并写本地包裹（`already_exists`），不重复建单；
  - 没有 → 调 `ds.xms.order.create`，成功后写 `shipping_parcels`（`source=4px-auto`）。
- 每次最多 50 单，必须二次确认；失败/跳过/已存在逐条返回。
- 页面：「真实预申报」按钮 → 确认弹窗 → 结果表（已建单 / 已建单（回查）/ 4PX 已存在（已接回）/ 跳过 / 失败）。

## 生产验证

- 用真实待出库 US 订单 **16148** 端到端验证：建单成功，单号 `4PX3003332940317CN`，随后调 `ds.xms.order.cancel` 取消并清理本地包裹；订单回到待预申报。
- AU 订单 13483 被正确拦截（未向 4PX 发请求，无本地包裹）。
- `/`、`/declaration-check/` 200；未登录调用建单接口 401；后端全量 pytest、前端测试/tsc/biome/build 通过。
- 前端：`/opt/caseledger-copy/releases/declaration-check-20261031f/frontend/dist`；
  后端镜像：`caseledger-copy-app:local`（`3fe1c2bee7ff`）。

## ⚠️ 事故说明：订单 13483（AU）的 4PX 预申报被取消

- 时间：2026-10-31 约 19:15（北京时间）。
- 经过：端到端验证用订单 13483（AU）时，`create_order` 报错（AU 缺发件人）；
  失败回查 `ds.xms.order.get` 发现该订单在 4PX **已存在一张委托单**（应为之前人工预申报），
  代码按「已建单」接回后，验证脚本立即调用了取消接口 → 该单 4PX 预申报被取消。
- 影响：13483 目前 **4PX 端无委托单**，本地无包裹记录、订单仍在待预申报列表；
  如果已打印过旧面单，该面单已失效。
- 处理：需要在 4PX 后台重新预申报该单（或配置 AU 发件人后用本系统重建）。
  本次已有的防护：建单前先回查、AU 直接拦截，正常批量操作不会重复此问题。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261031d/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag 6175ccdf3b62 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-10-31 售后补发面单条码修复（GV 条码扫不上）

## 问题

售后补发单在「发货中心 → 售后补发 · 4PX 对单」批量「只绑定售后」后，只写入了 4PX 的运单号；
但加拿大智选等产品的**实体面单条码与运单号不是同一个号**（运单 `OLS…`、面单 `GV…`）。
售后绑定流程没有回填 `label_barcode`，仓库扫面单上的 GV 条码时匹配不到售后单 → 扫码出库关联不上。

数据核对：线上 61 条已绑定运单的售后单里，**58 条缺实体面单条码**，其中 6 条运单号是 `OLS…`。

## 修复

1. **绑定即回填条码**：`after_sale_service.bind_fourpx()` 绑定 4PX 运单后，顺手调用
   `ds.xms.label.get` 取回实体面单条码并保存 `after_sales.label_barcode`；
   取条码失败不阻断绑定（仍可手动「获取面单」补）。
2. **补数脚本**：`app.services.fourpx_barcode_sync_service.sync_after_sale_barcodes()`
   + `scripts/backfill_after_sale_barcodes.py`，按已绑定的运单号批量回填实体条码，
   校验与其他包裹/售后单的条码冲突；只读 4PX，不改物流绑定和出库状态。
3. 已对生产执行补数：**58 条补齐，0 失败**，`after_sales` 中已无缺条码记录。

## 验证

- 修复前样例：`CF13217` 运单 `OLS3060236952907`、面单条码 `GV26CAA0U081671013`（扫描匹配不到）；
- 修复后：`packing-lookup` 用 **GV26CAA0U081671013 和 OLS3060236952907 都能命中 CF13217**；
  已出库的 `CF13750` 用 `GV847893550GB` 命中且正确显示已出库；
- 新增 3 项测试覆盖：绑定后自动回填条码并可扫、补数幂等、条码冲突不覆盖；
- 相关售后测试套件全绿；后端全量 pytest 通过；
- 后端镜像：`caseledger-copy-app:local`（`c00d0d45c232`），已重启 web/worker/beat/各 worker；无数据库迁移、无前端改动。

## 回滚

```bash
docker tag 3fe1c2bee7ff caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 更新：自动建单下游打通 + 按订单号范围人工预申报

## 1) 打通自动建单的包裹链路（必须项）

之前自动建单写入 `source=4px-auto`，但面单下载、实体条码同步、Shopify 回填、物流轨迹识别只认 `source=4px`，
自动建出来的单会卡在「打不出面单 / 扫不了码 / 回填不了」。已改为写入 `source=4px`，现有链路全部可用。

## 2) 建单后自动同步实体面单条码（必须项）

加拿大智选等产品：运单号 `OLS…`、实体面单条码 `GV…`。建单返回的不一定是实体条码。
现在建单结束后自动调用条码同步（按 parcel_id，多线程回查 `ds.xms.label.get`），
把实体条码补到 `shipping_parcels.label_barcode`，仓库扫 GV 条码即可出库；结果里返回 `barcode_sync` 统计。

生产验证：CA 订单 **16165（S5191）** 建单 → 运单 `OLS3060236973339`，自动补到实体条码
`GV26CAA0U081701880`，`source=4px`，面单接口正常返回 PDF；随后取消 4PX 委托单并清理本地包裹。

## 3) 按订单号范围人工筛选并预申报（不做定时自动）

- 后端：`check` / `check.csv` / `create` 支持 `order_from` / `order_to`（数字订单号范围）；
  `create` 未勾选订单时可用范围直接选单（每次最多 50 单）。
- 前端：检查页新增「起始单号 / 结束单号 → 按范围筛选 / 清除」，以及「全选筛选结果」；
  选中后点「真实预申报」，超过 50 单会自动按 50 一批分批建单并汇总结果。
- 验证：范围 `13400–13500` 只返回订单 13483；`16000–16100` 返回 0 单，过滤生效。

## 4) 发件人信息排查结论（AU 仍缺）

用户要求从历史 4PX 申报信息里找发件人，已穷举检查：

- 26 个微信收到的 xlsx/xls（含 4PX 预报订单数据、账单明细、价格表；全部 sheet、全部行）——无发件人字段；
- 102 个 PDF，其中真实 4PX 面单批次（如 `1789502247578.pdf`、`1789281945567.pdf`）只有 TO/GW/Ref/品名，无发件人；
- 4PX API `ds.xms.order.get`（1.0.0/1.1.0）、面单接口返回——均不含发件人；
- 历史导出里只有：收件人、产品/申报品名/单价/数量/重量、国家、邮编。

结论：发件人是 4PX **账号级设置**，只在 4PX 商家后台的「发件人/寄件人信息」里，API 和历史导出都不返回。
- US/CA/GB/NZ：4PX 账号默认发件人可用，试验建单成功（已取消）；
- AU：必须逐单提供发件人姓名/电话/地址/邮编/省州/城市，否则报错。
→ 需要从 4PX 后台截图/导出「发件人管理」，或让 4PX 业务把 AU 默认发件人配好。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`3d99bb9ec50b`），已重启 web/worker/beat/各 worker；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261031f/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag c00d0d45c232 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 二次更新：配置发件人/退件地址，AU 解锁

## 发件人 / 退件地址（按 4PX 后台实际配置）

用户提供的 4PX 后台信息：

- 发件人：姓名 `MAI`、电话 `1111111111`、国家 CN、省/州 Guangdong、城市 Shenzhen、邮编 518000、
  地址 `SHENZHENSHIHUANYUXINGCHENG GFY TINGYUJU`
- 退件地址：收件人 `LIU`、电话 `15814459815`、国家 CN、省/州 Guangdong、城市 Shenzhen、
  区 Longgang、地址 `Buji Street Wenjing Community Bulong Road 31 Leshun Building 317`

注意：4PX 校验要求发件人 **省/州、城市只能英文和符号**，填中文会报
`sender.state 只能输入英文和符号`，因此代码里使用 Guangdong / Shenzhen；
退件地址同样用英文/拼音写入（4PX 建单已验证通过）。原始中文信息保留在本记录里备查。

## 改动

- `fourpx_declaration_service` 新增 `SENDER_INFO` / `RETURN_ADDRESS` / `RETURN_INFO`，建单时写入；
  `deliver_type_info` 仍为 `{"deliver_type": "2"}`（上门揽收，走 4PX 账号默认）。
- 去掉 AU 的“缺发件人”拦截，US/CA/GB/AU/NZ 全量可自动建单。
- `fourpx_client` 现在把 4PX 返回的 `errors[].error_msg` 挂在 `FourPxError.details` 上，
  建单失败时页面直接显示 4PX 原文（原来只显示“未确认操作成功”）。

## 验证

- 试探：AU 建单成功（省/市中文报错后改英文通过），已取消；
- 真实订单验证：AU 订单 **16313** 建单成功（`4PX3003334666543CN`，source=4px），随后取消并清理本地包裹；
- 失败样例：订单 **13483** 建单被 4PX 拒绝，返回
  `收件人地址必须包含数字和英文字母`——该订单 `address1` 只有 `81`，缺街道名，需要修正地址后重试；
- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101b/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`aaf5caf0e22a`），已重启；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag 3d99bb9ec50b caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 三次更新：地址校验 + 「预申报人工处理」便签页

## 地址校验（建单前就拦）

`_blockers` 增加 4PX 实际校验规则：

- 收件地址（address1+address2）必须**同时包含数字和字母**（4PX 报错原文：`收件人地址必须包含数字和英文字母`）；
  缺失时提示「收货地址需同时包含门牌号和街道名（4PX 要求数字+字母），请核对」；
- 英国（GB）订单必须有收件人邮箱（4PX 要求电话+邮箱），缺失时提示；
- 原有校验保留：缺电话、缺邮编、国家不在规则内、商品件数为 0。

每个被拦订单多返回 `reason_group`（address / contact / country / items / other）；
`summary.groups` 返回各组数量，`check` / `check.csv` 支持 `blocked_only` 和 `reason_group` 过滤。

## 新增「预申报人工处理」页（便签页）

- 菜单：仓储作业 → **预申报人工处理**（`/declaration-exceptions`）；
- 只列不能自动预申报的订单，按原因分 Tab（地址问题 / 联系方式缺失 / 国家不在规则内 / 商品为 0 / 其他），
  支持订单号搜索、服务端分页、导出 CSV；
- 每行显示收件人/电话/地址和被拦原因，提供「复制信息」（方便发给客服/客户补地址）和「去订单详情」；
- 检查页汇总处也会显示「待人工处理 N 单」并可跳转。

## 线上验证（2026-11-01 15:5x）

- 全量 390 单：可预申报 386、被拦 4（地址问题 3、国家不支持 1）；
- 地址问题样例：13483、16375、16560（address1 只有门牌号，无街道名）；
- 13483 实测 4PX 建单报 `收件人地址必须包含数字和英文字母`，与页面拦截一致；
- `/declaration-exceptions/` 返回 200；被拦 CSV 850 字节、4 单；后端 pytest 全量通过。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101c/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`a20eb3b94700`），已重启；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101b/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag aaf5caf0e22a caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 四次更新：地址真实性校验（邮编库/州一致性/占位识别）

## 做了什么

在原有「门牌号+街道名」「英国邮箱」检查之外，新增真实地址校验：

- **美国**：ZIP 必须在邮编库中存在（GeoNames，40,979 个 ZIP）；ZIP 与州必须一致；
  ZIP+4、海外属地（006-009）、军邮（APO/FPO）单独处理；个别未被库收录的新 ZIP 用 3 位前缀兜底判断州。
- **澳大利亚**：4 位邮编必须在邮编库中（3,170 个）；邮编与州一致；
  边境共享邮编（如 3644 同时属于 NSW/VIC）允许任一州。
- **加拿大**：邮编格式 A1A 1A1；按首字母校验省（X 允许 NT/NU）。
- **英国/新西兰/荷兰**：邮编格式和英国区号存在性。
- **可疑占位信息**：电话全同数字/过短/常见占位号、地址含 NA/test/xxx/fake 等占位词、
  缺少收件人姓名，都会进「预申报人工处理」。

数据来源：GeoNames postal code datasets（US/AU/CA，CC BY 4.0），已生成
`app/data/postal_regions.json`（约 578KB），随镜像发布，不依赖外部接口。

## 误报验证（用近 60 天 9,352 个真实订单回放）

- 全量只有 **4 单**命中：都是 address2 填了 `na/NA` 占位，属于正确拦截；
- 修复了初版的误报：`na` 子串误匹配（Manassas 等）、美国 201xx（VA）被当成 DC、
  澳洲边境邮编 3644、加拿大 GeoNames FSA 不全等。

## 线上结果（部署后）

- 394 单待预申报：可预申报 **389**、被拦 **5**（地址 3、国家 1、商品件数 0）；
- 地址拦截样例：13483 / 16375 / 16560（address1 只有门牌号）；
- `/declaration-exceptions/` 正常，前端/后端测试通过。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101d/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`2e51088e7ee2`），已重启；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101c/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag a20eb3b94700 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 五次更新：人工处理页改成物流轨迹同款 Tabs（带全量计数）

## 改动

- 「预申报人工处理」页原来用 Segmented 小分段，改成与「物流轨迹」页一致的 antd `Tabs`：
  顶部一行「全部（N） / 地址问题（N） / 联系方式缺失（N） / 国家不在规则内（N） / 商品件数为0（N） / 其他（N）」，
  当前 Tab 下划线、点击切换筛选。
- 后端 `check` 的 `summary.groups` 现在按**全部被拦订单**统计，切到某个 Tab 时其他 Tab 的数量不会变小/消失；
  分页 `total` 仍按当前 Tab 过滤后的数量。
- 每个 Tab 内保留：订单号搜索、导出 CSV、复制订单信息、去订单详情。

## 验证

- 线上：被拦 5 单 = 地址问题 3（13483/16375/16560）+ 国家不在规则 1 + 商品件数为 0 的 1；
- 切到「地址问题」Tab：行只剩 3 单，Tab 上其他数量保持 5 的总分布不变；
- `/declaration-exceptions/` 200；前端页面测试、tsc/build 通过。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101e/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`4a28e605dcc7`），已重启；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101d/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag 2e51088e7ee2 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 六次更新：合并成一个页面 + 去掉页面废话

## 合并

- 「4PX 预申报检查」和「预申报人工处理」合并为一个页面：菜单「4PX 预申报」；
- 页面顶部 Tabs：**可预申报（429） / 待人工处理（9）**，数量来自全量统计，切 Tab 不丢；
- 可预申报 Tab：勾选 → 生成清单 / 真实预申报（仍按每 50 单分批）、导出、查看请求体；
- 待人工处理 Tab：显示收件信息 + 拦截原因 + 复制信息/订单详情，不可勾选建单；
- 旧地址 `/declaration-exceptions` 重定向到合并后的页面；菜单里的「预申报人工处理」已删除。

## 精简文案

- 删掉原来堆在页面顶部的 6 条 Alert 说明（只读说明、发件人、重量、申报口径、地址校验…）；
- 产品代码/申报口径/拦截类型收进标题旁的 ? 图标（Tooltip），需要时再看；
- 后端 `notices` 返回空列表，页面不再渲染说明区；子标题压成一行「只读检查；确认后才向 4PX 建单，不做定时自动」。

## 后端

- `check` / `check.csv` 新增 `status=ready|blocked`；
- `summary` 始终按全量统计（candidates/ready/blocked/groups），分页 total 按当前 Tab 过滤；
- 线上实测：候选 438、可预申报 429、待人工处理 9（地址 4、商品件数为 0 的 4、国家 1）。

## 部署

- 前端：`/opt/caseledger-copy/releases/declaration-check-20261101f/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`9698115dd94b`），已重启；无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101e/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
docker tag 4a28e605dcc7 caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 七次更新：已退款订单不参与预申报

## 规则

- 候选订单排除**已全额退款**的订单：
  - Shopify 退款交易（`payments.payment_method='shopify_refund'`，状态 pending/success）金额合计 ≥ 订单金额；
  - 或订单金额已为 0 但存在退款流水（Shopify 全退后常见 `financial_status` 仍是 paid、total 变 0）；
- **部分退款**（退款金额小于订单金额）仍保留，避免误伤「退一件、发剩余」的订单；
- 防御：即使手动选中已全额退款订单去建单，也会被拦下并提示「订单已全额退款，无需申报」。

实现：`_refund_amounts()` 批量查退款流水 → `_candidate_orders()` 过滤；
`_blockers()` 增加退款拦截，建单/清单/CSV 全部生效。

## 线上验证

- 修复前候选 438（可预申报 429、待人工处理 9，其中 4 单是「商品件数为 0」的全退单）；
- 修复后候选 **434**（可预申报 429、待人工处理 5：地址 4、国家 1）；
- 已退款订单 16388 / 16413 / 16554 / 16666 已从预申报列表消失；
- 部分退款订单仍正常显示。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`de945d6edcdb`），已重启；无前端改动、无数据库迁移。

## 回滚

```bash
docker tag 9698115dd94b caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-01 八次更新：全站页面去掉冗长文案

## 范围

按「保留必要警告、删掉解释性废话」的标准，逐页检查并精简了 18 个页面：

- 物流轨迹：删除增量核查规则长说明、4 段 Tab 说明；Popconfirm 只留「只扣现有库存，缺口记账实差异」。
- 装箱核对：两段长说明合并成短提示；出库引导改成「要出库请到扫码出库页」。
- 扫码出库：保留扫码/错误/撤回提示，长段 Explanation 删除，队列提示只留「队列清空前请勿离开页面」。
- 发货中心：批量 PDF / CF 对单大段解释删除，Alert 改成一句话；子标题改为「Shopify 已发货 ≠ 仓库已出库」。
- 采购管理：勾选说明、收货提示、补采说明等长文案缩短；动态数量/状态保留。
- 售后管理：Tab 说明段落删除，顶部说明与删除确认缩短。
- 库存管理 / 库存流水：子标题、汇总括号说明、入库/调整提示缩短。
- 邮件工作台 / 草稿任务 / 售后审阅：Zoho/DeepSeek 设置长说明删除，只留关键限制；折扣、核实提示一句话。
- 店铺管理 / 设置：Shopify Client 凭据的长说明改为一句，Form 提示缩短。
- 订单列表 / 订单详情：tooltip、Alert、Popconfirm、弹窗段落精简；删除「已进采购清单」提示。
- 商品管理 / 费用：子标题、tooltip、Form extra 精简。
- 仪表板 / 财务报表：无超长文案，未改。

长中文文案数量 54 → 22（剩下的都是动态状态、错误信息、必要警告）。

## 验证

- `npx tsc --noEmit` 通过；
- 相关页面 Jest：16 个套件 / 56 个测试通过，补充精简后再跑 MailDesk+Purchases 6 套件 / 16 测试通过；
- 前端构建通过；生产页面全部 200/301 正常。

## 部署

- 前端：`/opt/caseledger-copy/releases/copy-trim-20261101/frontend/dist`；
- 无后端改动、无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/declaration-check-20261101f/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
```

---

# 2026-11-01 九次更新：预申报「建单后自动回填 Shopify」开关

## 行为

- 4PX 预申报页新增店铺级开关：**建单后自动回填 Shopify**（默认关闭）；
- 开启后：真实预申报成功的订单，建单结束自动调用 `writeback_tracking`：
  - 回填成功 → Shopify 标记发货并请求客户发货通知；
  - 已回填过 → 跳过；
  - 多包裹单包裹不齐 → 记为「待补包裹」，补齐后再回填；
  - 失败只记录在结果里，不回滚已建单；
- 关闭后行为不变：只能从发货中心「4PX 对单」或订单详情手动回填。

## 存储与接口

- 新表 `fourpx_declaration_settings`（shop_id 主键、auto_writeback、updated_at），
  已用 SQL 在生产创建（未跑 alembic，避免与现有 revision 冲突）：

```sql
CREATE TABLE IF NOT EXISTS fourpx_declaration_settings (
  shop_id integer PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
  auto_writeback boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

- 接口：`GET/POST /api/shipping/4px/declaration/settings`（按 shop_id 隔离，权限 fulfillment）；
- 建单结果新增 `auto_writeback` 统计 + 每条订单的 `shopify_status`/`shopify_error`，页面结果弹窗展示。

## 验证

- 后端测试：开关默认关闭、店铺隔离、开启后才调用回填（36 项通过）；
- 前端测试：8 项通过（含开关保存）；
- 生产接口验证：GET 默认 false，开启/关闭往返正常，**当前保持关闭**；
- 容器已用新镜像 `b94eab3863bf` 重启，`/declaration-check/` 200。

## 回滚

- 前端：`ln -sfn /opt/caseledger-copy/releases/copy-trim-20261101/frontend/dist .../current/frontend/dist`
- 后端镜像：`docker tag de945d6edcdb caseledger-copy-app:local` 后 `up -d`；
- 开关关闭即可停用功能，无需删表（表可保留）。

---

# 2026-11-01 十次更新：product_list 统一成申报口径

## 问题

4PX 请求体里 `product_list[].product_name` / `product_unit_price` 抄的是订单商品原名和原价
（例如 `Dopamine Contrast Color iPhone Case` / 22.6），而 `parcel_value` 和
`declare_product_info` 用的是申报口径（1.2 / Phone case），两处对不上。

## 修复

- 新增 `_declared_product_list()`：按申报行生成 product_list
  - `product_name` = 申报英文品名（Phone case / Wallet / Mobile phone protective film / phone chain / Magnetic support bracket）
  - `product_unit_price` = 申报单价（US/AU 1.20、CA/GB/NZ 1.00、手机链 1.00、保护膜 1.20）
  - `qty` = 申报数量；`currency` = USD
  - `sku_code` 仍保留订单实际 SKU（对货用）
- 真实建单和页面「查看请求体」预览都改用同一套数据；
- `parcel_value` = product_list 金额合计，保持一致。

## 验证

- 线上预览（订单 16312 GB）：`product_list = [{sku_code: 实际SKU, product_name: "Phone case", product_unit_price: 1.0}]`，
  `parcel_value: 1.0` = 合计；
- 4PX 真实试探建单 + 取消成功（新 product_list 被接受）；
- 新增 2 项测试（单品类、手机壳+钱包两类），37 项通过；后端全量 pytest 通过；
- 后端镜像：`caseledger-copy-app:local`（`688a2c469203`），已重启；无前端改动、无数据库迁移。

## 回滚

```bash
docker tag b94eab3863bf caseledger-copy-app:local
cd /opt/caseledger-copy/current && docker compose \
  --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d web worker beat mail-worker tracking-worker tracking-history-worker
```

---

# 2026-11-02 更新：预申报不再预取面单（避免 4PX 提前显示「已打印」）

## 原因

`create_declarations` 建单后会自动调用 `ds.xms.label.get` 预取实体面单条码（为加拿大智选 GV 条码），
4PX 后台会把这次面单获取记为「已打印」，导致刚申报就显示已打印。

## 修复

- 预申报建单**不再调用面单接口**：`barcode_sync.updated` 恒为 0；
- 建单返回的 `label_barcode` 若等于运单号，只当占位处理，不落库（`label_barcode=None`）；
- 实体面单条码在**实际打印**时回填：
  - 发货中心「批量下载面单 PDF」— 现在允许把 4px 包裹的占位条码更新为真实条码；
  - 订单详情「获取面单」— 同样允许占位条码被真实条码覆盖；
- 扫码出库逻辑不变：打印后条码已回填，扫 GV/运单号都能命中。

## 验证

- 新增/更新测试：建单不触发 label.get、不存占位条码、占位条码在打印时可被真实条码替换；
- 相关 61 项测试通过，后端全量 pytest 通过；
- 后端镜像：`caseledger-copy-app:local`（`4db6f2264dc7`），已重启；无前端改动、无数据库迁移。

## 注意

- 历史已预取过面单的订单，4PX 上的「已打印」状态不会自动恢复（状态已发生）；
- 发货中心「批量绑定并回填」时仍会为对单包裹预取一次条码（发货准备阶段），如需要「只有真正打印才取」可再调整。

---

# 2026-11-02 更新：带支架/手机链套装改走标准挂号（QC）

## 规则变化

`_has_special_pair` 扩展为：

1. 沿用原规则：两件都是 `Gradient Magnetic iPhone Case with Matching Stand` → QC；
2. 新增：同一订单同时包含
   - `Silicone iPhone Case with Stand & Camera Control`
   - `Gradient Magnetic iPhone Case with Matching Stand`
   且**每条商品的变体**都带 `+ Stand` / `+stand` / `手机链` / `Wrist Chain` → QC。

不符合（如变体没有支架/手机链、或同单还有其它无标记商品）仍按件数走轻小件。

## 验证

- 订单 16314（GB，两件：Silicone +Stand + Gradient +Wrist Chain）：新规则判定 special_pair=True、产品 QC，
  原因「带支架/手机链的套装按标准挂号」；
- 16314 数据核对：4PX 上已有 QC 委托单 `4PX3003335347489CN`，但本地包裹登记的是已失效的
  `4PX3003335324411CN`（4PX 查询为空），已把本地包裹单号修正为实际 4PX 单号并清掉占位条码；
- 16484、16744 等同类订单（其它店铺）同样适用新规则；
- 新增 3 项测试：成对走 QC、同标题无标记仍轻小件、单件支架款仍轻小件；后端全量 pytest 通过；
- 后端镜像：`caseledger-copy-app:local`（`cca3755032eb`），已重启；无前端改动、无数据库迁移。

---

# 2026-11-02 更新：权限分配看得清 + 补齐功能说明

## 问题

- 权限分配弹窗只有一行行名称，不说明每项覆盖哪些功能；
- 账号列表「功能权限」只显示前 3 个标签，看不到完整已分配清单；
- 有些功能（4PX 预申报、物流轨迹、装箱核对、扫码出库）其实都在「履约作业」下，但标签没写，看起来像没有；
- 仪表板、强制补记出库等依赖关系没有提示。

## 改动

后端 `app/utils/permissions.py`：

- 每个权限增加 `group`（经营概览/仓储作业/商品与库存/售后与客服/财务/系统管理）、
  `description`（覆盖的功能）、`requires`（依赖权限）、`note`；
- 16 项权限重新命名与说明，例如「仓储履约 → 采购管理、发货中心、4PX 预申报、物流轨迹、装箱核对、扫码出库」；
- `GET /api/permissions` 返回上述字段（不改鉴权逻辑，纯展示增强）。

前端「系统管理 → 子账户」：

- 分配弹窗按分组展示，每项下方写明覆盖功能，高危项/依赖项有蓝色提示；
- 顶部显示「已选 N 项」，支持「全选可分配 / 清空」；
- 勾了仪表板但没勾订单/报表、勾了强制补记但没勾仓储履约时，弹窗内红黄提示缺什么；
- 账号列表「功能权限」列展示前 3 个 + 「查看全部（N 项）」，点开抽屉按分组列出已分配/未分配及说明；
- 新建/编辑子账户的权限勾选同步改成同款分组说明。

## 验证

- 后端权限目录测试新增断言（分组、4PX 预申报描述、仪表板依赖、强制补记依赖、管理员专属）；
- 前端 tsc/build 通过，页面`/system/sub-accounts/` 200；
- 无数据库迁移，鉴权逻辑不变。

## 部署

- 前端：`/opt/caseledger-copy/releases/permissions-20261102/frontend/dist`；
- 后端镜像：`caseledger-copy-app:local`（`0a908afa035b`），已重启。

---

# 2026-11-02 更新：预申报改为后台任务（有进度、关页面不丢）+ 加拿大规则

## 一、真实预申报改成异步任务

之前 159 单点「确认建单」后前端分 50 单循环调用，没有进度；关掉弹窗就看不到，刷新/关页面更会中断剩余批次。

现在：

- 新增 `fourpx_declaration_jobs` 表 + Celery 任务 `run_fourpx_declaration_job_task`：
  - 点确认后只创建任务并立即返回；
  - worker 按 50 单一批真实建单，每批完成后回写进度（已处理/成功/失败）；
  - 页面每 2 秒轮询任务进度，**关弹窗、刷新页面、重新进页面都会恢复**（自动取最近任务）；
- 页面顶部显示进度条和「已处理 60/159 · 成功 58 · 失败 2」，完成后显示「查看结果/清除」；
- 任务失败会保留已建结果和错误信息，不会重复建单（任务幂等，重复调用直接跳过）；
- 接口：`POST/GET /api/shipping/4px/declaration/jobs`、`GET /api/shipping/4px/declaration/jobs/<id>`；
- worker 内以任务创建者身份通过店铺权限校验（fix：之前 Celery 里无登录用户会 403「无权操作该店铺的发货订单」）。

生产验证：用不存在的订单号创建一个验证任务 → worker 执行完成、结果记录正确；测试任务已清理。

## 二、加拿大（CA）产品规则调整

`select_product` 增加 `stand_set`：

- **两件以上、且每条商品都是支架款/手机链款**（Silicone iPhone Case with Stand & Camera Control / Gradient Magnetic iPhone Case with Matching Stand，变体带 + Stand / 手机链）→ **QC 标准挂号**（不再走智选）；
- 智选不可达邮编：
  - 1 件、或 2 件无支架 → **S5058 轻小件**；
  - 3 件以上 → QC；
- 智选可达且不是支架套装 → 仍优先智选 S5191。

测试覆盖：加拿大可达/不可达的支架套装 → QC；单件支架款 → 轻小件；2 件普通款 → 轻小件/智选。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`05690e3536e7` 后的权限修复版本），已重启 web/worker；
- 前端：`/opt/caseledger-copy/releases/declaration-job-20261102/frontend/dist`；
- 新表：`fourpx_declaration_jobs`（生产已用 SQL 创建，未跑 alembic）；无其它数据库迁移。

---

# 2026-11-02 更新：同步「已在 4PX 人工申报」的订单

## 场景

批量预申报有部分失败，员工直接去 4PX 后台手工建了委托单；系统里没有包裹记录，
导致发货中心/扫码对不上，也不知道怎么补回来。

## 新增同步能力

- 接口：`POST /api/shipping/4px/declaration/sync`，参数 `shop_id` + `order_ids` 或 `job_id`；
- 逻辑：按**订单号**回查 4PX（`ds.xms.order.get` 的 ref_no）：
  - 查到委托单 → 写成包裹（`source=4px`、4PX 单号），不调用面单接口，所以不会让 4PX 显示已打印；
  - 已有包裹 → 跳过；
  - 4PX 查不到 → 返回「未找到，可能客户单号不一致或还没申报」；
  - 单号冲突/退款/取消 → 拒绝并说明原因；
  - 若店铺开了「建单后自动回填 Shopify」，同步成功后也会回填。
- 页面：
  - 勾选订单后点顶部「同步 4PX 已有单」；
  - 任务失败时，进度条上会出现「同步失败的 N 单」，一键按失败清单补同步；
  - 结果弹窗显示：已绑定 / 已有包裹 / 4PX 未找到 / 失败。

## 本次生产同步结果

扫描近 4 天 4PX 委托单与本地候选订单，发现 6 单已在 4PX 有委托单但本地无包裹，已全部补绑定：

| 订单 | 4PX 单号 |
|---|---|
| 16329 | 4PX3003335388307CN |
| 16358 | 4PX3003335388871CN |
| 16377 | 4PX3003335389168CN |
| 16401 | 4PX3003335392182CN |
| 16461 | 4PX3003335392717CN |
| 16548 | 4PX3003335397248CN |

- 6 单均已生成包裹（source=4px），实体面单条码待打印时回填；
- 可预申报候选数相应减少 6 单；
- 新增同步测试（命中/未找到/已有包裹），预申报测试 49 项通过。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`ee12cf10563c`），web/worker 已重启；
- 前端：`/opt/caseledger-copy/releases/manual-sync-20261102/frontend/dist`；
- 无数据库迁移。

---

# 2026-11-02 更新：收件人姓名打印顺序修正（姓/名倒序）

## 原因

4PX 面单按 `last_name + first_name` 顺序打印。旧代码把完整姓名按空格拆成
`first_name=名, last_name=姓`，于是面单打印成「姓 名」（例：Helen Berry → Berry Helen）。
而 4PX 后台人工建单时收件人姓名是整串填在 `first_name`、`last_name` 为空，
所以人工单打印是正常的「名 姓」。

生产用可取消试探确认：
- first=Helen / last=Berry → 面单 `Berry Helen`；
- first=`Helen Berry` / last 空 → 面单 `Helen Berry`（US/AU/GB 建单均通过）。

## 修复

`_recipient()` 改为：`first_name` = 完整姓名，`last_name` = 空，与 4PX 后台人工单一致；
新增测试断言 payload 里姓名字段。

## 已存在订单的影响

- 2026-11-01 16:00–18:00 UTC（北京时间 11-02 00:00–02:00）由系统自动建单的 165 个包裹，
  4PX 上的收件人姓名是倒序，面单会打成「姓 名」（样例：16323 Coll Adriana、16324 Walker Claire、16325 Michalczuk Elwira）；
- 其中 102 个已扫码出库，63 个未扫码；
- 4PX 接口没有「修改收件人」能力，只能取消委托单后重建（会换新单号），不自动处理；
- 姓名顺序不影响派送，建议已出库的不动，未出库的如需纠正再人工取消重建。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`24909c9a6c92`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-02 更新：待人工处理 Tab 也支持同步 4PX

- 「待人工处理」Tab 现在可以勾选订单（之前只有可预申报 Tab 能勾），
  勾选后顶部「同步 4PX 已有单（N）」可直接批量同步；
- 每行新增「同步 4PX」按钮，单笔一键回查 4PX 客户单号并绑定包裹；
- 同步成功后订单从待人工处理列表消失、进入发货中心（面单已齐全待扫码）；
- 之前已建过的姓名倒序订单按用户确认**不处理**（不影响派送）。

部署：前端 `/opt/caseledger-copy/releases/manual-sync-20261102b/frontend/dist`；
后端未改动；前端测试 11 项通过。

---

# 2026-11-02 更新：退款订单已发货却不自动出库

## 现象

物流轨迹「未出库 · 已有运输轨迹」里订单 15706 一直不自动出库，4PX 轨迹已有 9 条
（Customs Data processed · shipment on route）。

## 原因（两层）

1. 订单 15706 已被**全额退款**（`financial_status=refunded`），原自动补记逻辑直接拒绝
   「未付清/已取消/退款」的订单，所以一直停在待处理；
2. 退款同步时 Shopify 的 `current_quantity` 变成 0，本地商品行数量被同步成 0，
   导致即使放开退款限制也报「订单没有可出库商品」。

## 修复

- `_apply`：允许**已退款/已取消**的订单在「有实际运输轨迹」时补记出库（实物已出仓，
  库存必须扣减）；未付款（pending/unpaid/authorized）仍禁止。
- 新增 `restore_refunded_item_quantities()`：出库前若本地商品数量为 0，
  从 Shopify GraphQL 回源恢复原始下单数量再扣库存；拉取失败才按无商品处理。
- 新增测试：退款订单有轨迹自动出库；未付款订单不自动出库；数量为 0 的退款单恢复后出库。

## 生产处理

- 部署后现场执行 `auto_reconcile(shop_id=1)`：15706 已补记出库（shipped），
  轨迹待办列表清空；
- 该 SKU 当前公共库存为 0，扣减记录为「账实差异 1 件」（`deducted=0, shortages=1`），
  与系统既有「库存不足只扣现有库存、缺口留记录」策略一致；
- 之后同类订单会自动处理。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`422443bc7d27`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-02 更新：采购清单确认时自动处理「售后需求已变化」

## 现象

采购清单 #66（草稿）发起（确认）时报 `售后商品需求已变化，请核对采购清单`，无法发起。

## 原因

清单里包含 6 条售后补采分配，其中售后单 CF12120 在清单生成后已经由现货满足
（`cf_uncovered=0`），但清单还保留着这条采购需求；确认时的校验发现「计划采购量 > 当前未覆盖需求」，
于是整张清单被拒绝。

## 修复

`confirm_purchase_list` 在确认时自动对账售后分配：

- 售后单/商品行已删除、变体已变化 → 移除该条分配；
- 当前现货/其它草稿清单已覆盖该需求 → 按剩余可用量裁剪，减到 0 就移除；
- 已发货的售后单仍保留（允许继续备货）；
- 被裁剪/移除后同步调整清单商品数量、总件数，并写 `purchase_list_edits` 审计快照
  （记录被移除的售后分配明细）；
- 全部被移除时才提示重新生成清单。

## 生产处理

- 清单 #66 已成功发起：状态 `ordered`，总件数 213 → 212；
- 自动移除售后单 CF12120 的 1 件需求（已被现货满足），其余 5 条售后分配保留；
- 审计快照（edit #5）记录了被移除的分配。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`8bf9f9d2c013`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-02 修正：采购清单按原单发起，不自动裁剪售后需求

## 调整原因

上一步的实现遇到「售后需求已变化」会自动移除/裁剪清单里的售后分配。用户确认：
**不应该自动处理变化的需求，应该直接按清单原样发起采购，多采购的数量到货后当作库存。**

## 最终行为

- 确认采购时**不再校验**售后需求是否已被现货/其它清单满足，也不再自动裁剪；
- 清单里的商品行、数量、总件数全部按草稿原样发起（`status=ordered`）；
- 只有结构性失效（售后单被删除、店铺归属变化）仍提示核对；
- 到货入库时多出的数量按正常采购入库进入公共库存，之后可用作其它订单/售后。

## 数据修正

- 采购清单 #66 之前被自动移除了 CF12120 的 1 件，现已从编辑快照恢复：
  总件数 **213**、分配 **213** 条、售后分配保留，售后单 CF12120 恢复为 `ready`；
- 那条自动裁剪的审计记录已删除（避免误导）。

## 部署

- 后端镜像：`caseledger-copy-app:local`（`7a002b2318f7`），web/worker 已重启；无前端改动、无数据库迁移；
- 测试更新：售后需求被现货满足后，清单仍按原数量成功发起。

---

# 2026-11-03 新增：AI 只读/操作接口（第一版：客服邮件 + API Key）

## 能力

- 管理员在「系统管理 → AI 接口」申请 API Key：
  - 选名称、可访问店铺、接口范围（mail_read / mail_send）、有效期（30/90/180/365 天或长期）；
  - Key 只在创建时显示一次（`cai_...`），数据库只存 SHA-256 哈希，可随时撤销；
  - 列表显示前缀、店铺、范围、最近调用时间、状态。
- AI 调用：请求头 `X-API-Key: cai_xxx`（也支持 `Authorization: Bearer cai_xxx`），前缀 `/api/ai`；
- 全部调用写审计表 `ai_api_audit_logs`（key、接口、状态码、耗时、IP）。
- 鉴权与网页登录完全隔离：`/api/ai/` 走 API Key；`/api/ai-keys` 仍走管理员 JWT。

## 客服邮件接口（第一版）

| 方法 | 路径 | 范围 |
|---|---|---|
| GET | `/api/ai/health` | 任意 key |
| GET | `/api/ai/mail/status` | mail_read |
| GET | `/api/ai/mail/messages?shop_id=&status=todo&search=&page=&per_page=` | mail_read |
| GET | `/api/ai/mail/messages/<id>` | mail_read |
| GET | `/api/ai/mail/messages/<id>/attachments` | mail_read |
| GET | `/api/ai/mail/messages/<id>/attachments/<attachment_id>/download` | mail_read |
| POST | `/api/ai/mail/messages/<id>/reply` `{reply}`（只存草稿） | mail_send |
| POST | `/api/ai/mail/messages/<id>/send` `{reply?}`（保存并立即发送） | mail_send |
| POST | `/api/ai/mail/messages/<id>/draft` `{guidance?, order_id?, facts?}` | mail_send |
| POST | `/api/ai/mail/messages/<id>/ignore` `{ignored}` | mail_send |
| POST | `/api/ai/mail/sync` `{history?}` | mail_send |
| POST | `/api/ai/mail/translate` `{id, kind, reply}` | mail_read |

发送沿用现有 mail_service 的全部保护：只能回复客户来信、已回复/发送结果不明/Nothing 会拒绝、旧草稿过期会拒绝、不会自动重发。

## 数据库

```sql
CREATE TABLE ai_api_keys (
  id serial PRIMARY KEY, name varchar(100) NOT NULL, key_prefix varchar(20) NOT NULL,
  key_hash varchar(64) NOT NULL UNIQUE, shop_ids json NOT NULL DEFAULT '[]',
  scopes json NOT NULL DEFAULT '[]', is_active boolean NOT NULL DEFAULT true,
  created_by integer, last_used_at timestamptz, expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ai_api_audit_logs (
  id serial PRIMARY KEY, key_id integer REFERENCES ai_api_keys(id) ON DELETE SET NULL,
  method varchar(10) NOT NULL, path varchar(500) NOT NULL, status_code integer NOT NULL,
  duration_ms integer, ip varchar(64), created_at timestamptz NOT NULL DEFAULT now()
);
```
生产已用 SQL 创建。

## 验证

- 生产用临时 key 实测：`/api/ai/health` 200、`/api/ai/mail/messages` 200（读到 2 封待处理）、`/api/ai/mail/status` 200、审计记录 3 条；无效 key 401；测试 key 已删除；
- 网页登录/预申报/订单等原有页面 200，鉴权链路未受影响；
- 新增测试：仅管理员可建 key、key 鉴权/撤销、范围校验、店铺隔离、邮件列表与审计；
- 后端镜像：`caseledger-copy-app:local`（`4195528e4e7d` + 时区修复），前端 `ai-api-20261103`。

---

# 2026-11-04 新增：4PX 真实出账费用同步（计费重 + 明细 + 总价）

## 结论

4PX 有真实出账费用接口：`ds.xms.order.getFreight`（查询订单费用信息，4PX 开放平台 2023-06-15 上线，不在旧基础文档里）。
返回：4PX 单号、计费重（kg）、币种、费用明细（挂号费/速递运费等）、总费用、出账时间；客户单号和 4PX 单号都能查。

## 实现

- `FourPxClient.get_freight()`：新增 `ds.xms.order.getFreight`（v1.0.0）；
- 新表 `fourpx_freights`：order_id 唯一，存 tracking/ref、charge_weight_kg、currency、total_fee、subs 明细、billed_at、raw、synced_at；
- `fourpx_freight_service`：
  - 单笔 `sync_order_freight(order_id)`：校验店铺权限与包裹，调用 4PX 并 upsert；
  - 批量 `sync_order_freights({order_ids})`：最多 50 单，单笔失败不影响其它；
  - 订单详情自动带 `fourpx_freight`；
- 接口：
  - `POST /api/shipping/4px/freight/sync`（履约权限）
  - `GET /api/orders/<id>/4px-freight`（只读已存数据）
- 订单详情页新增「4PX 真实费用」卡片：4PX 单号、计费重、总费用、费用明细、出账/同步时间，带「同步 4PX 费用」按钮；
- 当前只展示，不改订单物流费用/利润（后续可配置是否自动写入成本）。

## 生产验证

已同步 11471 / 11475 / 11488：

| 订单 | 计费重 | 明细 | 总费用 |
|---|---|---|---|
| 11471 | 0.087 kg | 挂号费 ¥16.00 + 速递运费 ¥4.70 | ¥20.70 |
| 11475 | 0.092 kg | 挂号费 ¥21.00 + 速递运费 ¥5.80 | ¥26.80 |
| 11488 | 0.084 kg | 挂号费 ¥16.00 + 速递运费 ¥4.54 | ¥20.54 |

- 订单详情接口已返回 `fourpx_freight`；新增测试覆盖真实费用保存、重复同步更新、未出账/无包裹提示；
- 后端镜像 `caseledger-copy-app:local`（`763e0c677e9f`），前端 `fourpx-freight-20261104`；无既有表结构改动。

---

# 2026-11-04 扩展：AI API 全量功能（邮件补全 + 订单/物流/采购/库存/售后只读 + 平台安全）

## Scope（每把 Key 可分别勾选）

`mail_read`、`mail_send`、`orders_read`、`logistics_read`、`procurement_read`、`inventory_read`、`after_sales_read`、`purchase_draft`

## 新增接口

- **客服邮件补全**：草稿读取/保存/删除、回复关键点、整段会话加载、带附件发送（multipart）、批量忽略、按邮箱查订单、只读审阅、列表日期/客户/附件/增量筛选；发送支持 `dry_run=true`。
- **订单**：列表（状态/单号/邮箱/日期/增量）、详情（商品/支付/包裹/售后/4PX 真实费用）、时间线。
- **物流**：轨迹列表（pending/preinfo/overdue/搜索）、详情、异常汇总。
- **采购**：候选、采购清单列表/详情、创建**采购草稿**（scope `purchase_draft`，不能确认下单）。
- **库存**：汇总（在库/在途/缺口 Top50）、流水。
- **售后**：列表、详情、待补发/待采购。
- **OpenAPI**：`GET /api/ai/openapi.json`（公开，31 个路径，可直接导入 Grok/工具）。

## 平台安全

- API Key 新增：IP 白名单、每日发送上限（默认 100，可配置 1–10000）；
- 每 Key 每分钟 120 次限流；
- 审计查询：管理页「AI 接口 → 最近调用」，或 `GET /api/ai-keys/audit`；
- 文档更新：`docs/AI-API-生产接口文档.md`（扩展版 v1.1），含 scope 表、全部接口、curl 示例、提示词注入提醒；
- `/api/ai/*` 使用 API Key 鉴权，不占用网页模块权限；权限覆盖测试已排除 API Key 路由。

## 验证

- 生产临时 Key 实测：health / 邮件列表 / 订单列表与按邮箱 / 物流汇总 / 采购候选 / 采购清单 / 库存汇总与流水 / 售后列表 全部 200；
- OpenAPI 200（31 paths）；dry-run 发送 200 且未真实发送；测试 Key 已删除；
- 新增测试：多 scope 读接口、OpenAPI、dry-run、IP 白名单、审计列表；
- 后端镜像 `caseledger-copy-app:local`（`bca0f923e302`），前端 `ai-api-20261104`；`ai_api_keys` 增加两列（已 ALTER）。

---

# 2026-11-04 完善：AI API 自描述、用量、字段裁剪、Key 管理

- 新增 `/api/ai/me`、`/api/ai/shops`、`/api/ai/capabilities`、`/api/ai/usage`、`/api/ai/mail/folders`；
- 列表接口支持 `fields=` 字段裁剪（orders/messages/logistics）、`updated_since` 增量；
- 管理端新增 Key 编辑（PATCH）、轮换（rotate）、今日调用/发送用量列、审计查询；
- 限流改为标准 `429 RATE_LIMITED`（带 retry_after）；
- 生产验证：临时 Key 实测 me/shops/capabilities/usage/fields 均 200，轮换后旧 Key 401、新 Key 200；测试 Key 已删除；
- 新增测试：discovery、fields、update/rotate、rate-limit；
- 后端镜像 `caseledger-copy-app:local`（`98bc94f44026`），前端 `ai-api-20261104b`；文档已更新到 v1.2。

---

# 2026-11-04 修复：售后补发 4PX 对单搜不到已出库的 CF 单

## 现象

「发货中心 → 售后补发 · 4PX 对单」查 2026-11-01 显示 0 条，但 4PX 官网能看到 CF11694（状态已出库）。

## 原因

- 查询接口 `ds.xms.order.get` 被固定传了 `consignment_status="P"`（只查已预报），
  已交接/库内作业/已出库的委托单（状态 V/H/C）全被过滤；
- 对单逻辑又要求状态必须为 "P"，否则标为 invalid_4px_status。
  CF11694 在 4PX 是 **C（已出库）**，所以两处都被排除。

## 修复

- 去掉 4PX 查询里的状态过滤，按创建时间取全部委托单；
- 对单可用状态放宽为 **P（已预报）/ V（已交接）/ H（库内作业）/ C（已出库）**，
  只有 X（已关闭）等状态仍视为无效；
- 订单 4PX 对单与售后补发 4PX 对单两处逻辑同步放宽。

## 生产验证

- 2026-11-01 的售后 4PX 对单现在返回 2 条：
  - CF11694 → 4PX3003334219169CN，status=ready_to_bind（原订单 11694）
  - CF13361 → 4PX3003334214065CN，status=ready_to_bind
- 前端刷新「售后补发 · 4PX 对单」后即可勾选绑定/覆盖回填。

## 部署

后端镜像 `caseledger-copy-app:local`（`12040d0ab880`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-04 修复：售后 4PX 对单把已完成(F)的 CF 单误标异常

## 现象

售后补发 · 4PX 对单（2026-10-27）里 CF10041 显示「异常：invalid_4px_status」，
但该售后单其实早已绑定（4PX 单号 4PX3003315985175CN、面单条码已回填、10-27 已出库）。

## 原因

- CF10041 的 4PX 委托单状态是 **F（已完成/已签收）**，不在之前允许的 P/V/H/C 里；
- 对单逻辑先判断状态、后判断「已绑定」，所以已绑定的老单也被标成异常。

## 修复

- 允许状态扩展为 **P（已预报）/ V（已交接）/ H（库内作业）/ C（已出库）/ F（已完成）**；
- 调整判断顺序：**先判断是否已绑定**（同一 4PX 单号已绑定 → `already_bound`），
  再判断状态是否可绑定；只有 X（已关闭）/D（草稿）等才标无效状态；
- 订单 4PX 对单同步调整。

## 生产验证

2026-10-27 售后 4PX 对单现在 4 条全部为 `already_bound`，不再有红色异常：
CF12230 / CF11499 / CF10041 / CF9082。

## 部署

后端镜像 `caseledger-copy-app:local`（`a9445ef40b2e`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-04 新增：AI API 售后写操作

- 新 scope `after_sales_write`：发起售后补发、修改备注、登记售后费用、手动绑定补发物流；
- 新接口：`GET /api/ai/after-sales/lookup`、`POST /api/ai/after-sales`、`PUT /api/ai/after-sales/<id>/note`、`POST /api/ai/after-sales/<id>/cost`、`POST /api/ai/after-sales/<id>/tracking`；
- 仍不开放：删除售后、退款/优惠、扫码出库；
- 生产验证：临时 Key 调用 lookup 200；测试 Key 已清理；
- 新增测试覆盖完整写入链路（lookup → 创建 → 备注 → 费用 → 绑定物流 → 读取）；
- 后端镜像 `caseledger-copy-app:local`（`76490f5196e8`），文档已更新到 v1.3。没有前端改动（scope 列表从接口动态读取）。

---

# 2026-11-04 新增：加拿大智选邮编不可达时自动回退轻小件

## 现象

预申报检查页对部分加拿大订单建议 S5191 智选（例如 16913 V1L6M6、16925 V2J5R5、
16949 P6C2V3、16960 X0E0R2、17000 V0J1E1），但 4PX 建单会报
`该邮编所在地区无法提供派送服务`（错误码 010109008）。

## 原因

我们本地的「智选不可达邮编」清单（8,262 个）没有覆盖这些邮编，
所以检查页误判为可服务。

## 修复

1. **建单自动回退**：加拿大订单用 S5191 建单时，如果 4PX 返回
   「该邮编所在地区无法提供派送服务」，自动用 **S5058 轻小件**重试一次；
   成功则结果标记 `created_fallback`，备注「智选邮编不可达，已自动回退轻小件(S5058)」；
2. **邮编缓存**：被拒邮编写入新表 `fourpx_smart_rejections`；
   之后检查页对同一店铺/同一邮编直接按「智选不可达」处理：
   - 1–2 件 → S5058 轻小件；
   - 3 件以上 → QC 标准挂号；
   - 支架/手机链套装 → QC；
3. 已把本次确认被拒的邮编写入缓存：V1L6M6、V2J5R5、P6C2V3、X0E0R2、V0J1E1；
   L6A4Z8（16916）4PX 可正常建智选，不在缓存里。

## 验证

- 4PX 真实试探：上述 5 个邮编 S5191 均返回派送服务不可用，L6A4Z8 可通过；
- 线上确认：16913、16925 在 4PX 已是 S5058，16916 是 S5191；
- 新增测试：智选被拒 → 自动回退 S5058、缓存落库、下次检查直接建议轻小件；
- 后端镜像 `caseledger-copy-app:local`（`e61d6b342f60`），web/worker 已重启；无前端改动。

---

# 2026-11-04 修复：物流轨迹核查列表翻页被重置

## 现象

物流轨迹核查列表点「下一页」又回到第 1 页（翻页点不了）。

## 原因

Table 的 `onChange` 在分页变化时也会触发，并且带着上一次的排序状态；
代码里只要排序列是「订单/包裹」就无条件 `setPage(1)`，所以**排序过订单号后**，
每次翻页都会被拉回第 1 页。

## 修复

- 分页触发时（`pagination.current !== page`）直接跳过排序处理；
- 只有排序真的变化时才重置页码，避免翻页被重置。

## 部署

前端 `/opt/caseledger-copy/releases/logistics-pager-20261104/frontend/dist`；
无后端改动、无数据库迁移；前端测试 5 项通过。

---

# 2026-11-04 调整：库存页同颜色不同配置合并成一个颜色框

## 现象

库存管理里 Gradient Magnetic 手机壳，同一颜色「蓝橙」和「蓝橙 + Wrist Chain」被拆成两个颜色框，
看起来像两个不同款。

## 原因

`_group_stock_products` 用 `颜色 + 配置` 作为颜色分组键，把 +Wrist Chain/+Stand 当成了独立颜色。

## 调整

- 颜色分组只按「基础颜色」，不再带配置后缀；
- 同颜色的普通款与 +Wrist Chain/+Stand 变体合并到同一个颜色框；
- 型号行仍保留配置 Tag（Wrist Chain / Stand），SKU、库存、需求仍按变体分开计算；
- 颜色图片优先用普通款图片。

## 验证

- 线上 Gradient Magnetic：Aqua Orange / Blue Sky / Pink Lime 等颜色现在各 1 个框，
  框内同时包含普通款和 +Wrist Chain 型号行；
- 测试更新：同颜色两个配置合并为 1 个颜色组，variant_count=2，配置在型号行区分；
- 后端镜像 `caseledger-copy-app:local`（`a89adf148e13`），web/worker 已重启；无前端改动。

---

# 2026-11-05 调整：库存页移除「结构错配」展示

- 库存页不再显示「结构错配 N」标签（颜色/型号行和汇总提示里的结构错配件数都移除）；
- 后端仍保留 `structural_gap_quantity` 字段（API/AI 可用），只是页面不展示；
- 同颜色普通款/+Stand/+Wrist Chain 合并成一个颜色框的改动保持生效；
- 前端 release `inventory-cleanup-20261105`；无后端改动。

---

# 2026-11-05 调整：库存页同型号 +Wrist Chain 与普通款合并一行

## 需求

链子是单独采购的，库存页同一颜色、同一型号的「+Wrist Chain」和普通款不应拆成两行。

## 实现

- `_group_stock_products` 合并同颜色、同型号、仅差 +Wrist Chain 的变体：
  - 数量、需求、已采购未到、缺货、占单明细全部合并；
  - 合并行保留 `group_keys` 与 `components`（底层 SKU/配置）；
  - SKU 显示为两个 SKU（` / ` 分隔）；
- 前端合并行的「调整 / 记录」改为下拉选择具体 SKU，避免调整错配置；
- 仅合并 Wrist Chain；+Stand 等其它配置仍单独保留。

## 验证

- 线上 Gradient Magnetic「绿紫色」：iPhone 18 Pro Max 的普通款与 +Wrist Chain 已合并为一行（在库 1）；
- 前后端测试通过；
- 后端镜像 `caseledger-copy-app:local`（`493d949fd84b`），前端 `inventory-merge-chain-20261105`。

---

# 2026-11-05 新增：AI API 主动发邮件

- 新接口 `POST /api/ai/mail/send`：给任意邮箱主动发信（不要求客户先来信），支持 to/cc/bcc、主题正文、附件、`dry_run`；
- `mail_service.send_new_mail` 走 Zoho `accounts/{account}/messages`，发送后补拉已发送；
- 仍受 Key 的 `mail_send` 权限、IP 白名单、每分钟 120 次、每日发送上限约束；
- 生产验证：dry-run 200、非法邮箱 400、测试 Key 已清理；OpenAPI 已包含（41 个路径）；
- 后端镜像 `caseledger-copy-app:local`（`9ded8b668e9e`）；无前端改动。

---

# 2026-11-05 修复：ERANOIR 未出库已有轨迹无法自动补出库

## 原因

ERANOIR 有 100 单是 `legacy_unverified`（历史迁移订单）。物流补记 `_apply`
对这类订单直接报「历史出库状态待核实，请人工处理」，所以即使 4PX 已有真实轨迹，
自动任务也不会处理；另有正常 pending 单因每轮只处理 20 单而积压。

## 修复

- 历史迁移订单（legacy_unverified / legacy_partial_unverified）有 4PX 实际运输轨迹时：
  - 自动核实为已出库、补登包裹/出库事件；
  - **不追溯扣库存**（这些单发生在库存上线前，现在扣会把当前库存扣错）；
  - 审计记录标记 `historical=true, stock_deducted=false`；
- 正常 pending 单仍按原逻辑扣库存、记账实差异；
- 自动补记每店铺每轮上限从 20 提到 50，清积压更快。

## 生产处理结果

ERANOIR 待补记队列：176 单成功补出库、1 单跳过、0 单失败，**pending 列表已清零**。

## 部署

后端镜像 `caseledger-copy-app:local`（`a6239303fea6`），web/worker 已重启；无前端改动、无数据库迁移。

---

# 2026-11-06 优化：AI 接口管理页布局

- 顶部「接口一览（第一版：客服邮件）」换成「接口能力」卡片：列出全部模块（邮件/主动发信/订单/物流/采购/库存/售后），显示 Base URL、OpenAPI、curl 示例（真实地址，不再是 `<域名>`）；
- Key 表格：
  - Key 前缀并入「名称 / Key」列，不再被挤成竖排；
  - 店铺、范围限制宽度；范围标签用短名 + Tooltip，超过 4 个显示「+N」弹出全部；
  - 今日调用/发送分两行显示，最近调用/有效期不再换行；
  - 操作列固定右侧，表格横向滚动；
- 新建 Key 后的示例 curl 使用真实 Base URL；
- 前端 release `ai-api-layout-20261106`；无后端改动。

---

# 2026-11-06 新增：4PX 物流费自动归集（按订单日期）

## 规则

- 切换日：**2026-11-01**；只处理订单日期 >= 切换日的已出库订单（含 ERANOIR，按各店铺 4PX 凭据）；
- 订单没有物流费时，自动写 `order_costs`：
  - `shipping_cost = 4PX 真实 total_fee`
  - `shipping_source='4px'`、`status='pending'`
  - **成本日期 = 订单日期（北京时间）**（例：11-01 的订单 11-03 出库，费用记 11-01）
  - 记录 4PX 单号/同步时间；
- 不覆盖已有手工/账单费用；不改已确认费用；不自动确认、不扣账户；
- 历史订单（订单日期 < 2026-11-01）不自动补，视为历史已记。

## 定时任务

- `sync-logistics-costs`：每 10 分钟，每店铺最多 100 单，补同步 4PX 费用并生成 pending 订单费用；
- `daily-logistics-expense`：每天北京时间 03:10，按订单日期汇总 4PX 真实费用，自动写/更新一条物流费日账单：
  - 描述 `4PX自动-YYYY-MM-DD`，一天一条；
  - 当天已有人工账单则跳过，绝不重复；
  - 随着 4PX 费用陆续同步，自动账单金额会更新。

## 配置

`config.py`：
- `LOGISTICS_COST_AUTO=true`（默认开启）
- `LOGISTICS_COST_CUTOVER=2026-11-01`
- `LOGISTICS_COST_BATCH=100`

## 生产验证

- 首次运行自动建了 6 笔 pending 费用，成本日期与订单日期一致（如 10-31 UTC 订单 → 11-01 北京时间）；
- 批量补同步 3 批：CASEEASE 累计 288 笔、¥9,334.95；ERANOIR 13 笔、¥512.78；
- 自动日账单已生成并随同步更新（例如 CASEEASE 11-01 从 ¥83.70 更新到 ¥6,720.08）；
- 后端镜像 `caseledger-copy-app:local`（`189cf14c4078`），web/worker/beat 已重启。

---

# 2026-11-06 调整：费用管理移除「4PX 物流费对账」按钮

- 按需求去掉费用管理页右上角的「4PX 物流费对账」入口及对应抽屉；
- 后端对账接口保留（/api/order-costs/fourpx/reconcile、/apply），需要时仍可通过接口使用；
- 前端 release `expenses-cleanup-20261106`；无后端改动。

---

# 2026-11-06 修复：自动物流费日账单滞后 + 未出账订单反复重试

- 问题：费用管理的 `4PX自动-日期` 日账单每天只在 03:10 更新一次，后面新同步的订单费用没立刻反映；
- 修复：每次 10 分钟同步任务结束后，立刻重算最近 14 天的自动日账单金额（一天仍只有一条，已有手工账单跳过）；
- 另加 4PX 出账延迟保护：出库不满 24 小时且还没查到费用的订单先跳过，避免每 10 分钟反复请求未出账订单；
- 验证：CASEEASE 11-01 自动账单 ¥6,720.08 保持不变，11-02 从 ¥2,614.87 更新到 ¥4,669.51，11-03 新增 ¥2,523.68；订单费用累计 432 笔/¥13,913.27；
- 后端镜像 `996980e4b245`（后续部署为最新），web/worker/beat 已重启。

---

# 2026-11-06 修复：订单列表/详情不显示 4PX 自动物流费

- 原因：订单列表「物流费用」读的是旧账单分摊字段 `allocated_shipping_cost`，订单详情读的是旧字段 `shipping_cost`；自动归集的费用在 `order_costs` 里，但两个页面都没用 `shipping_cost_cny`；
- 修复：
  - 后端 `Order.to_dict()` 增加 `shipping_cost_source`（4px/manual/bill）；
  - 订单列表「物流费用」改用 `shipping_cost_cny`，来源 4PX 时显示标签；
  - 订单详情「物流费用」改用 `shipping_cost_cny`，显示 ¥ 金额 + 4PX 标签；
- 验证：订单 16545（11-01 归属）接口返回 `shipping_cost_cny=28.18`、`source=4px`；
- 后端镜像 `ae65b7512b50`，前端 `order-cost-display-20261106`。

---

# 2026-11-06 修复：4PX 预申报异步任务入队失败（Redis 版本不兼容）

## 现象

点「确认建单」后弹「Response status:400」，提示：
`Redis transport requires redis-py versions 6.1.0 or later. You have 4.6.0`

## 原因

- 镜像里 celery 5.3.1 + redis 4.6.0，但 pip 未锁定 kombu，装到了 **kombu 5.7.0**；
- kombu 5.7 要求 redis-py >= 6.1，导致 Celery 往 Redis 投递任务时直接抛 VersionMismatch；
- 预申报任务入队失败，接口返回 400（没有真正向 4PX 建单）。

## 修复

- `requirements.txt` 增加 `kombu==5.3.7`（与 Celery 5.3.1 / redis 4.6.0 兼容）；
- 重建镜像并重启 web/worker/beat/mail-worker/tracking-worker/tracking-history-worker；
- 验证：容器内 celery 5.3.1 + redis 4.6.0 + kombu 5.3.7，`apply_async` 入队成功。

## 部署

后端镜像 `caseledger-copy-app:local`（`ce080d81a9c7`）；失败的那次任务没有创建任何 4PX 委托单，重新点确认建单即可。

---

# 2026-11-08 修复：采购清单因订单商品变化无法确认

## 现象

采购清单 #72（页面显示 #73）点「确认发起采购」报 400：
`订单商品变体在生成清单后发生变化，请重新生成采购清单`

## 原因

清单里有一条分配（订单 16917 / order_item 19528）在生成后，Shopify 同步把该订单商品的
`shopify_variant_id` 同步成了空，`group_key` 从 `["variant", ...]` 变成描述键；
确认逻辑严格比对 group_key，一条不一致就拒绝整单。

## 规则调整（按需求）

从现在起：**采购清单是人工确认过的快照，确认时不再因订单/售后变化拦截**。

- 不修改清单商品、颜色、型号、数量；
- 关联订单已删除、商品变体变化、售后已关闭等都不再阻止确认；
- 确认后按清单原样发起采购，多采购的数量到货后计入库存；
- 只保留最基本的校验：清单必须是草稿状态。

## 处理结果

采购清单 #72 已成功确认：status=`ordered`，167 个商品、257 件，**清单内容与确认前完全一致**。

## 部署

后端镜像 `caseledger-copy-app:local`（`57b1394891ba`），web/worker 已重启；无前端改动。

---

# 2026-11-09 新增：合并发货（一个运单号对应多个订单）自动出库

## 现象

订单 17410（Me Me）和 17411（Kassidy Bryan）是同一客户合并发货，Shopify 两单都填了同一个
4PX 运单号 `4PX3003357517495CN`；4PX 委托单 ref_no 只对应 17411。
物流轨迹自动补记检测到「一个物流号对应多个订单」直接拒绝，导致两单都卡在「未出库·已有运输轨迹」。

## 规则调整

- 一个物流号同时绑定多个**原订单**时，视为合并发货，允许自动补记；
- 每个订单分别扣自己的商品库存、分别标记已出库、分别记出库事件；
- 包裹只登记在其中一个订单（数据库标签号唯一），其他订单共用该运单号，不重复建包裹；
- 若物流号同时对应订单和售后，仍保持人工处理（内容可能不同）。

## 处理结果

- 17410：已出库，扣库存 1 件（无重复包裹，共用运单号）；
- 17411：已出库，扣库存 1 件（包裹 4PX3003357517495CN 已扫码）；
- 「未出库·已有运输轨迹」列表已清空这两单。

## 部署

后端镜像 `caseledger-copy-app:local`（`a51b3bbf9a30`），web/worker 已重启；无前端改动。

---

# 2026-11-09 修复：商品管理只显示部分颜色（Carbon/Coffee 未识别）

## 现象

商品 `Men's Slim RFID Bifold Wallet with Pop-Up Card Holder` 实际有 Black/Brown/Carbon/Coffee 4 个变体，
商品管理中只显示 Black、Brown 两个颜色。

## 原因

颜色提取正则只认常见颜色词，`Carbon`、`Coffee` 不在列表里，被当成「未标注颜色」；
商品管理/采购/库存的颜色分组会跳过未标注颜色，所以只看到两个颜色。

## 修复

颜色词表补充 `carbon`、`coffee`；该商品现在 4 个颜色都会显示。

## 部署

后端镜像 `caseledger-copy-app:local`（`333ff9ff1da4`），web/worker 已重启；无前端改动。

---

# 2026-11-10 修复：订单列表「物流标签单号」与「订单日期」重叠

- 物流标签单号列宽只有 180，「仅 Shopify」标签 + 运单号 + 复制图标超出列宽，溢出盖住订单日期列；
- 修复：该列加宽到 260、固定表格布局、运单号单行省略 + tooltip，复制按钮固定不换行；
- 订单日期列 150 → 160，表格横向滚动 1840 → 2000；
- 前端 release `orders-columns-20261110`；无后端改动。

---

# 2026-11-10 调整：订单列表去掉「仅 Shopify」标签

- 物流标签单号列只显示运单号（可复制），不再显示「仅 Shopify」标签；
- 前端 release `orders-columns-20261110b`；无后端改动。

---

# 2026-11-10 新增：CF 售后补发 4PX 物流费记录（2026-11-01 起）

- 新增表 `fourpx_after_sale_freights`：按售后单存 4PX 真实费用（计费重/明细/总费用/出账时间）；
- `after_sale_costs` 增加 `source`（manual/4px）、4PX 单号、同步时间；
- 自动任务每 10 分钟对「2026-11-01 起出库、有 4PX 运单号、还没有 4px 物流费」的 CF 售后：
  - 用客户单号（CF…）调 `ds.xms.order.getFreight`；
  - 写 `fourpx_after_sale_freights`；
  - 自动生成/更新一条 `after_sale_costs`：`cost_type=shipping`、`source=4px`、金额=4PX 总费用；
  - 不覆盖人工登记的费用；
- 费用管理每日自动账单 `4PX自动-日期` 现在同时包含订单运费和 CF 售后运费（CF 按售后出库日期归集）；
- 首次回填：CASEEASE 11-01 起 54 张 CF，成功 52 张，合计 ¥1,555.07；2 张（CF13315、CF14889）4PX 尚未出账，自动重试；
- 后端镜像 `caseledger-copy-app:local`（`875d8f296718`），web/worker/beat 已重启。
