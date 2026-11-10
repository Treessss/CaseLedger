# 2026-10-19 更新记录：库存同型号多配置显示

## 问题

库存管理里出现两行完全相同的「iPhone 17 Pro」：

- `CE-GMS-17-PRO-PINK-LIME`（`shopify_variant_id=58259075268911`）
- `CE-GMS-17-PRO-PINK-LIME-WRIST`（`shopify_variant_id=58286065778991`）

两者是 Shopify 的两个独立变体：后者是 **+ Wrist Chain（带腕链）**，SKU、库存和订单都分开。
不是重复数据，但库存页只显示 `model`，把 `+ Wrist Chain` 后缀省掉了，导致现场无法区分。

## 改动

- 后端 `inventory_service` 的行数据新增 `configuration` 字段，从 `variant_title` 中提取腕链/支架/挂绳等物理配置后缀；
  新增 `purchase_service._accessory_configuration()`，采购分组也复用同一段逻辑。
- 库存总览的变体行、手动入库选择器的变体行都显示蓝色配置标签（例如「Wrist Chain」）。
- 数据、分组键、库存数量均未改动；两个变体仍然分别计算在库、采购中和缺口。

## 部署

- 前端目录：`/opt/caseledger-copy/releases/inventory-config-20261019/frontend/dist`。
- 后端镜像已重建，web/worker/beat 等容器已重启。
- 后端备份：`/opt/caseledger-copy/backups/pre-inventory-config-20261019.tgz`。

## 验证

- 生产接口实测：两个变体 `model` 都是 `iPhone 17 Pro`，`configuration` 分别为 `""` 和 `"+ Wrist Chain"`。
- 生产页面实测：带腕链的一行显示「Wrist Chain」标签，另一行不显示。
- 新增后端用例 `test_inventory_rows_expose_bundle_configuration`；前端 86 项测试、`tsc`、构建通过。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/purchase-ux-20261019-v2/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-inventory-config-20261019.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
