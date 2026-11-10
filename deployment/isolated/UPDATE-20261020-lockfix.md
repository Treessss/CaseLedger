# 2026-10-20 售后创建卡顿：数据库锁排查与修复

## 现象

15:20–15:27（北京时间）创建售后单持续卡住，页面一直转圈。

## 根因

`sync_gateway_fees_task`（每小时第 20 分钟执行）当时正在跑 3 个店铺的手续费结算同步，持续 **373 秒**，在一个事务里更新订单/支付记录：

- 写入 `payments/orders` 时，外键会取得 `shops` 行的 `FOR KEY SHARE` 锁；
- 售后创建要 `SELECT shops ... FOR UPDATE`，两者冲突；
- 该任务一直不提交，售后请求排队等待，其中一个已等 2 分 22 秒，后面的请求继续排队；
- 本次同步因为窗口内出现退款，触发了全历史重算，处理量远大于平时的增量同步。

证据：`pg_stat_activity` 中 `pg_blocking_pids` 链
`售后创建(1106417) -> 结算同步(1106426)`，后者 `idle in transaction`，`xact_start` 已 4 分 35 秒，worker 日志显示该任务 `succeeded in 373.6s`。

## 已处理

1. 终止了卡住的结算同步数据库连接（`pg_terminate_backend`），事务回滚，锁立即释放，排队的售后请求恢复；worker 正常，后续任务无异常。
2. `sync_gateway_fees_task` 每个店铺结束都 `commit`（异常先 `rollback`），不再跨店铺持有事务。
3. `sync_shopify_payments_charges` 全历史回放时每 100 个订单提交一次，锁最多只持有很短一批。
4. `refresh_fees_for_shops` 同样按店铺提交。
5. `create_after_sale` 在 PostgreSQL 上设置 `SET LOCAL lock_timeout = 8s`：再遇到长锁会在 8 秒内返回「系统正在处理另一笔仓库或结算任务，请稍后重试；本次未创建售后单」，不会无限转圈。

## 验证

- 当前 `pg_stat_activity` 中锁等待为 0；售后无效请求往返 1.4 秒（含 TLS/鉴权）。
- `tests/test_gateway_graphql.py`、`tests/test_after_sales.py` 等通过；`ruff` 通过。
- 后端镜像已重建，web/worker/beat 等容器已重启；无数据库迁移。
- 备份：`/opt/caseledger-copy/backups/pre-lockfix-20261020.tgz`。

## 仍需关注（不是本次锁的原因）

- 售后商品搜索 `/api/after-sales/catalog/products` 仍需 1–2 秒（10 万+ 商品变体、按商品分组），输入搜索时会感觉慢；可以下一步做分组缓存/索引优化。
- 服务器 2GB 内存，swap 已用约 580MB，根分区使用率 96%；建议清理旧 release/日志或升配，避免整体响应变慢。

## 回滚

```bash
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-lockfix-20261020.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
