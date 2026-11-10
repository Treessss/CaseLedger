# 2026-10-19 更新记录：库存按“颜色 + 配置”分组（腕链独立）

## 事实核对

在 CASEEASE 店铺（`efb409-00.myshopify.com`）查 Shopify Admin API：

- `Gradient Magnetic iPhone Case with Matching Stand` 是**一个产品**（`gid://shopify/Product/16102788366639`），共 240 个变体。
- 「带腕链」不是第二个产品，而是该产品下的变体选项：
  - `iPhone 17 Pro / Pink Lime` → `CE-GMS-17-PRO-PINK-LIME`
  - `iPhone 17 Pro / Pink Lime + Wrist Chain` → `CE-GMS-17-PRO-PINK-LIME-WRIST`
- 两个变体有各自的 `variant_id`、SKU、图片，库存也各自独立。

## 为什么之前“混在一起”

库存页只按 `_color_name(variant_title)` 提取基础颜色（`Pink Lime`）分组，`+ Wrist Chain` 被当成颜色后缀丢掉了，于是两个变体落进同一个「粉黄晕」颜色组：

- 组名相同；
- 组图取该组第一个变体的图（通常是不带腕链的图）；
- 两个变体的型号都显示 `iPhone 17 Pro`，只有 SKU 不同。

## 本次改动

- `_group_stock_products` 的颜色分组键从 `color` 改为 `color + configuration`。
- 腕链组显示为「粉黄晕 + Wrist Chain」，使用腕链变体自己的图片；普通款仍是「粉黄晕」。
- 变体行保留 `Wrist Chain` 标签；在库、采购中、缺口仍按变体分别计算。
- 采购清单本来就把 bundle 配置拆成独立颜色组，本次让库存与采购口径一致。
- 没有改 Shopify 数据，没有合并或拆分库存，也没有数据库迁移。

## 部署

- 只改后端 `app/services/inventory_service.py`，镜像已重建并重启 web/worker/beat 等容器。
- 前端沿用 `releases/inventory-config-20261019`，无需重新发布静态资源。
- 备份：`/opt/caseledger-copy/backups/pre-inventory-bundle-groups-20261019.tgz`。

## 验证

- 生产接口：该商品返回 2 个颜色组——
  - `粉黄晕` / `Pink Lime` / 图 `pink-lime-gradient-phone-case-stand.webp` / 在库 1
  - `粉黄晕 + Wrist Chain` / `+ Wrist Chain` / 图 `pink-lime-wrist-chain-bundle.webp` / 在库 0
- 生产页面：两个颜色组各有自己的图片和变体行。
- 新增分组断言，`tests/test_inventory.py`、`test_inventory_pool.py`、`test_inventory_visibility.py`、`test_quantity_only_inventory.py`、`test_purchases.py` 全部通过；`ruff` 通过。

## 回滚

```bash
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-inventory-bundle-groups-20261019.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
