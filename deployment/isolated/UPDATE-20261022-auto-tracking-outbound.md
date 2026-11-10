# 2026-10-22 更新记录：未出库且有运输轨迹自动补记出库

## 功能

物流轨迹页「未出库 · 已有运输轨迹」里的记录，现在会**自动补记出库**，不需要再手动勾选「一键批量补记出库」。

- 每 2 分钟执行一次；
- 只处理：仍有未出库订单/售后 + 4PX 已核实有实际运输轨迹（`has_movement=true`）；
- 使用系统已缓存的可靠轨迹证据，不额外调用 4PX；
- 仍然走原补记逻辑：锁店铺、校验绑定唯一、只扣当前实际库存、缺口记账实差异、写补记审计；
- 已出库的订单/包裹自动跳过，不会重复扣库存；
- 未付款、已取消、退款、历史待核实订单不会被自动出库，仍留在列表里人工处理。

## 安全边界

- 不使用客户端提交的轨迹；只用后台已保存的 `TrackingRecord` 证据；
- 每个绑定重新校验：单号不能对应多个订单、绑定不能失效；
- 库存不足时不会虚增库存或负库存，缺口进入 `tracking_outbound_audits.stock_result.shortages`；
- 操作人记为空（系统自动），审计来源为 `auto_reconcile`；
- 可通过环境变量关闭：

```bash
TRACKING_AUTO_OUTBOUND=0
```

修改后重启 worker/beat。

## 配置与调度

- `config.py` 新增 `TRACKING_AUTO_OUTBOUND`，默认开启；
- `celery_app.py` 新增 beat：`tracking-auto-outbound`，每 120 秒；
- 任务路由到 `tracking` 队列；
- `app/tasks.py` 新增 `auto_reconcile_tracking_outbound_task`；
- `app/services/logistics_outbound_service.py` 新增 `auto_reconcile()`，单店铺每轮最多处理 20 条，避免一次占用过久。

## 生产结果

2026-10-22 17:09 首次自动任务运行：

- 处理 11 条成功，9 条失败（失败记录当前均已不在待处理列表，未产生库存变动）；
- 「未出库 · 已有运输轨迹」从有记录变为 **0**；
- 补记审计 ID 45–56，来源 `auto_reconcile`；
- 这 11 单当时库存均为 0，所以实际扣减 0、账实差异各 1 件，和手动补记口径一致。

## 验证

- 新增测试：pending 且已有轨迹的订单自动出库；重复执行不重复扣减、不重复写审计。
- 物流、采购、售后相关测试全部通过；`ruff` 通过；前端构建通过。
- 后端镜像已重建重启；前端目录 `releases/auto-outbound-20261022/frontend/dist`。
- 后端备份：`/opt/caseledger-copy/backups/pre-auto-outbound-20261022.tgz`。

## 回滚

```bash
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-auto-outbound-20261022.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
