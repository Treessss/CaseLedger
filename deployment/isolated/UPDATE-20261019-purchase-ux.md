# 2026-10-19 更新记录：采购清单 / 到货入库 / 缺货补采 / 库存口径

- 服务器：192.3.236.57，HTTPS 8443；Compose 项目 `caseledger-copy`。
- 后端镜像 `caseledger-copy-app:local` 已重建，web/worker/beat/mail/tracking 容器已重启。
- 新前端目录：`/opt/caseledger-copy/releases/purchase-ux-20261019-v2/frontend/dist`；
  `current/frontend/dist` 已切换，旧版本目录保留。
- 无数据库结构变更、无迁移、无数据清理。生产数据未修改（补采按钮只在本地环境点过）。

## 改了什么

### 1. 清单状态与进度

- 后端 `purchase_lists` 序列化新增 `received_quantity / damaged_quantity / shortage_quantity / pending_quantity`，
  草稿的未到数量固定为 0。
- 列表改为四段口径：到货进度条（合格绿 / 破损缺货橙 / 未到灰）、异常列、未到列；
  状态改为「待发起 / 采购中 / 到货中 / 已结束 · 有异常 / 已清点入库」。
- 详情页增加「合格 / 破损 / 缺货 / 未到」标签与三段进度条；`received` 但有异常时不再显示绿色「已清点入库」。

### 2. 清点界面

- 每个变体默认只填「实到件数」，破损/少发点「记录破损/少发」再展开，避免一行三个数字。
- 先填实到再填破损/少发时，超出部分自动从其它数量回退，不再把输入悄悄夹回 0。
- 清点草稿按清单保存在 localStorage，关闭弹窗 24 小时内重新打开会提示恢复，可「清空重填」；
  提交成功后自动清除。
- 「全部到齐」二次确认中显示将填满的总件数；文案统一为「实到 / 破损 / 少发」。

### 3. 缺货 / 破损后续

- 新增 `POST /api/purchase-lists/<id>/replenish`：按已确认的缺货 + 破损生成补采草稿，
  并按这些订单/售后当前仍未覆盖的数量封顶，避免重复采购。
- 新清单的 allocations 带 `source_purchase_id`，重复点击返回同一张草稿，不会重复建单。
- 清单详情出现「有 N 件需要补采」提示，可直接「生成补采清单」；生成后保留原清单并显示「查看补采单 #N」。

### 4. 库存数字解释

- 指标改为「总需求 / 已采购未到 / 在库 / 按型号还差」，并给出公式说明。
- 新增 `net_shortage_quantity`（总量差）与 `structural_gap_quantity`（型号结构错配）：
  生产当前总需求 822 − 在库 206 − 已采购未到 544 = 总量差 72，按型号还需采购 122，
  其中 50 件为结构错配（有富余的型号不能抵扣缺货型号）。
- 每个商品行的数字旁直接显示「结构错配 N」标签。

## 改动文件

后端：`app/services/purchase_service.py`、`app/api/purchases.py`、`app/services/inventory_service.py`。
前端：`frontend/src/pages/Purchases/index.tsx`、`frontend/src/pages/Purchases/Receipt.test.tsx`、
`frontend/src/services/purchases/api.ts`、`frontend/src/components/InventoryQuantities.tsx`、
`frontend/src/components/InventoryQuantities.test.tsx`、`frontend/src/pages/Inventory/index.tsx`、
`frontend/src/services/inventory/api.ts`、`frontend/src/global.less`。
测试：`tests/test_purchases.py` 新增清单口径与补采用例。

## 验证

- 后端全量 `pytest -q`：通过（约 773 项）；`ruff check app tests`：通过。
- 前端 `jest --runInBand`：31 个套件、86 项通过；`tsc --noEmit`：通过；生产构建通过。
- 本地实例按真实流程走通：生成清单 → 确认采购 → 清点（实到/破损）→ 结束到货 → 生成补采草稿 → 重复点击不重复建单；
  清点草稿关闭重开可恢复。
- 生产只读复测：清单列表 #40 `170 合格 / 33 缺货 / 已结束·有异常`、#44 `0 合格 / 22 缺货 / 264 未到`、
  #45 `未到 280`；详情补采提示、库存公式与结构错配标签均正常。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/packing-address-20261019/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist

cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-purchase-ux-20261019.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
