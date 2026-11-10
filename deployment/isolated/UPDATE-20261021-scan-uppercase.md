# 2026-10-21 更新记录：扫码出库 / 装箱核对兼容大小写

## 改动

所有扫描输入统一按大写处理，前后端都兼容：

- 前端 `normalizePackingScan` 统一 `trim().toUpperCase()`；扫码枪按键和手动输入框也实时转大写。
- 装箱核对、扫码出库共用同一套规范化逻辑：`4px...cn`、`4Px...Cn`、`CF...`、`GV...` 都会变成大写。
- 后端新增 `normalize_label_code()`，以下查询改为大小写不敏感：
  - `packing-lookup` 的包裹条码 / 运单号 / 订单发货单号 / Shopify fulfillments 里的运单号；
  - 扫码出库 `find_parcel_by_code()`、`parcel_matches_code()`；
  - 售后补发单号 `resolve_after_sale_label()` / `matches_after_sale_label()`；
  - CF 回写记录、售后面单冲突检查。
- 新登记/保存的物流标签统一存大写；历史小写数据仍能匹配，不需要清洗数据。
- 订单号本身是数字，不受影响；`#` 前缀仍会去掉。

## 验证

- 生产实测包裹 `4PX3003288411299CN`，分别用全大写、全小写、混合大小写请求 `packing-lookup`，三次都返回同一订单 `14412 / id 13819`。
- 新增后端测试：小写标签/条码的装箱查询与 `parcel_matches_code`。
- 前端 `packingScan` 测试新增大小写规范化断言；装箱/出库相关 27 项测试通过。
- 后端 shipping / parcels / inventory_pool / after_sales 测试全部通过；`ruff` 通过。
- 后端镜像已重建重启；前端目录 `releases/scan-uppercase-20261021/frontend/dist`；无数据库迁移。
- 后端备份：`/opt/caseledger-copy/backups/pre-scan-uppercase-20261021.tgz`。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/aftersales-search-20261021/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-scan-uppercase-20261021.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
