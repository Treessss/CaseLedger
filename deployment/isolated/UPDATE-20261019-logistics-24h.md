# 2026-10-19 更新记录：仅一条预报按出库 24 小时拆分/标记处理

## 需求

「已出库 · 仅一条预报」里把刚出库和超过 24 小时的混在一页，无法区分。改为：

- 出库 24 小时内一组；
- 出库 24 小时以外一组；
- 24 小时以内可人工标记「已处理」（只记录跟进，不改物流/出库/库存）。

## 后端

`app/services/logistics_service.py`：

- 新增 `_outbound_at()` SQL 表达式，与列表返回的 `outbound_at` 口径一致（售后用 `shipped_at`，否则包裹扫描时间，再退回订单发货时间）。
- `/api/logistics?tab=preinfo&age=within24h|beyond24h|all` 新增 `age` 筛选；不传保持旧行为（all），旧客户端不受影响。
- 返回 `preinfo_age_counts`（两组件数）和 `preinfo_handling_counts`（24 小时内已/未处理）。
- `handling` 筛选在 `preinfo + within24h` 时生效，默认未处理；`beyond24h` 不显示处理筛选。
- `set_handling` 允许两类包裹：预报满 15 天未送达；或仅一条预报且出库 24 小时内。其余仍拒绝，已送达拒绝。
- 两组计数合并成一次聚合查询，列表 SQL 预算不增加。

## 前端

`frontend/src/pages/Logistics/index.tsx`、`frontend/src/services/logistics/api.ts`：

- 仅一条预报页增加「出库24小时内（N）/ 出库24小时以外（N）」分组筛选。
- 24 小时内显示「未处理 / 已处理 / 全部」筛选与「标记已处理 / 恢复未处理」按钮。
- 24 小时以外不显示处理筛选和按钮；仍可查看轨迹、强制刷新。
- 文案说明标记只记录跟进，不改变签收、出库或库存。

## 部署

- 后端镜像已重建并重启 web/worker/beat 等容器。
- 前端目录：`/opt/caseledger-copy/releases/logistics-24h-20261019/frontend/dist`。
- 后端备份：`/opt/caseledger-copy/backups/pre-logistics-24h-20261019.tgz`。
- 无数据库迁移、无历史数据修改。

## 验证

- 生产接口：仅一条预报共 228 条，其中 24 小时内 225、24 小时以外 3；处理计数 0/225。
- 生产页面：两组筛选、处理筛选、标记按钮显示正确；切到 24 小时以外后处理筛选和标记按钮消失。
- 新增后端用例覆盖：分组计数、24 小时内可标记、超过 24 小时且未满 15 天不可标记。
- 新增前端用例覆盖：分组筛选参数、标记处理、切换到 24 小时以外后按钮消失。
- 物流后端测试全部通过；前端 31 个套件 / 87 项通过；`tsc` 与生产构建通过。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/mail-manual-draft-20261019/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-logistics-24h-20261019.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```

## 补充（同日）：24 小时以外也允许标记处理

现场反馈：24 小时以外主要就是要处理的（其中 7830 已全额退款、11399 部分退款），不能标记等于无法清掉。

- `show_handling` 改为覆盖全部「仅一条预报」，不再限制 24 小时内。
- `set_handling` 允许所有 `only_preinfo` 且未送达的运单标记；已送达仍拒绝。
- `handling_counts` 按当前分组返回（24 小时内 / 24 小时以外各自统计未处理、已处理）。
- 前端两组都显示「未处理 / 已处理 / 全部」和「标记已处理 / 恢复未处理」。
- 生产复核：24 小时以外 3 条，处理计数 0/3，三个「标记已处理」按钮均可用；标记后默认从未处理列表消失，可在「已处理」恢复。
- 前端目录更新为 `/opt/caseledger-copy/releases/logistics-beyond-20261019/frontend/dist`；
  后端备份 `pre-logistics-beyond-handling-20261019.tgz`。
