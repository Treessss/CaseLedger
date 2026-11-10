# 2026-10-21 更新记录：售后管理支持搜索

## 功能

售后管理页顶部新增搜索框，支持按以下内容模糊搜索（不区分大小写）：

- 售后单号（如 `CF12619`，可带 `#`）
- 原订单号（如 `12619`）
- 售后物流单号 / 面单条码
- 客户姓名、客户邮箱

搜索与现有四个 Tab（全部 / 未处理 / 未采购 / 已扫码出库）叠加；搜索时 Tab 数量按搜索结果统计。清空搜索后恢复全部列表。

## 实现

- 后端 `GET /api/after-sales` 新增 `search` 参数，使用 `ilike` + 转义 `%`/`_`，通过 EXISTS 关联原订单，不产生重复行。
- 前端售后管理页增加受控 `Input.Search`，回车或点搜索执行；点清空立即恢复全部。
- 切换店铺时清空搜索词。

## 验证

- 生产实测：搜索 `CF12619` 返回 1 条；原订单号 `12619` 同样返回该售后单；不存在的关键词返回 0 条。
- 新增后端测试覆盖售后单号、原订单号（含 `#`）、物流单号、客户邮箱及无结果场景。
- 售后页面前端测试通过；`tsc` 与生产构建通过。
- 后端镜像已重建重启；前端目录 `releases/aftersales-search-20261021/frontend/dist`。
- 无数据库迁移；后端备份 `pre-aftersales-search-20261021.tgz`。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/mail-attachments-20261020/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-aftersales-search-20261021.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
