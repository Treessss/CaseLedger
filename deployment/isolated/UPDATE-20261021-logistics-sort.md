# 2026-10-21 更新记录：物流轨迹订单列支持升降序

## 功能

物流轨迹核查（三个 Tab 都生效，主要用于「预报满15天 · 未送达」）的「订单 / 包裹」表头现在可点击排序：

- 第一次点击：订单号升序（默认）；
- 再点：订单号降序；
- 表头显示排序箭头，切换后回到第 1 页；
- 排序在数据库分页前完成，不是只排当前页。

## 实现

- 后端 `GET /api/logistics` 新增 `order=asc|desc`，只翻转订单号排序方向；非数字单号仍排最后，CF 售后包裹仍紧跟同号原单，避免售后和原单被拆散。
- 前端 Table 的「订单 / 包裹」列改为 `sorter`，受控 `sortOrder`，`onChange` 时更新排序并重置页码。
- 切换店铺时恢复默认升序。

## 验证

- 生产接口：同一批 113 条未处理记录，升序首条 `1859`，降序首条 `11892`，方向正确。
- 生产页面：点击表头后箭头变为降序，列表按订单号从大到小展示。
- 新增后端测试验证降序顺序 `100, #11, 10, 09, 2` 和非法方向 400。
- 物流页前端测试通过；`tsc` 和生产构建通过。
- 后端镜像已重建重启；前端目录 `releases/logistics-sort-20261021/frontend/dist`；无数据库迁移。
- 后端备份 `pre-logistics-sort-20261021.tgz`。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/scan-uppercase-20261021/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-logistics-sort-20261021.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
