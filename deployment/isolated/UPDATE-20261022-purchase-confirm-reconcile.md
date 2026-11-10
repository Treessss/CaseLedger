# 2026-10-22 更新记录：草稿采购单确认按快照直接发起

## 业务口径

采购清单是给供应商的采购单，**不是订单发货状态的派生单**。

草稿生成后，即使订单已经出库、退款、取消，或售后已经出库，确认时也仍然按草稿保存的商品和数量直接发起采购。到货后正常入库；如果订单已经出库，这批货就成为额外库存。这是预期行为，不再自动删订单、删售后或裁数量。

## 现在的确认行为

确认草稿时：

1. **不再校验订单是否已出库 / 退款 / 取消**；已出库订单也保留在采购单里，正常买、正常入库。
2. **不再裁剪被其他采购单或库存覆盖的数量**；草稿里写了多少就采购多少，多出来的就是库存。
3. 已出库的售后补发也允许保留，采购到货后作为额外库存，不会把售后单重新改回“采购中”。
4. 仍保留基本完整性校验：
   - 订单必须存在且属于当前店铺；
   - 订单商品变体必须和保存时一致；
   - 售后单不能已删除、归属变化或不存在；
   - 清单商品数量必须和分配明细一致。
5. 纯库存采购（无订单/售后）仍可正常确认。

前端不再出现“自动移除订单/自动裁剪数量”的提示，因为这些自动清理已取消。

## #53 处理结果

之前按旧逻辑确认时，系统删除了 3 个已出库订单、1 个已出库售后和 1 件被覆盖商品。按新口径已恢复为完整快照：

- 状态：采购中
- 订单：187
- 件数：209
- 已重新包含：订单 14285、14393、14525，售后 CF9859，以及订单 14679 的 1 件

到货清点时按 209 件正常入库；已出库订单对应的数量会形成额外库存。

备份：

- `/opt/caseledger-copy/backups/purchase_53_before_confirm_20261022.json`
- `/opt/caseledger-copy/backups/purchase_53_after_confirm_before_full_restore_20261022.json`
- `/opt/caseledger-copy/backups/purchase_53_before_confirm_retry_20261022.json`

## 验证

- 采购测试：已出库/已取消订单保留、已被其他清单覆盖的数量保留、已出库售后保留并保持 shipped 状态、纯库存采购确认，全部通过。
- 采购与售后测试全量通过；`ruff` 通过。
- 后端镜像已重建重启；无数据库迁移。
- 后端备份：`/opt/caseledger-copy/backups/pre-purchase-confirm-skip-20261022.tgz`（旧逻辑）；当前代码仅改 confirm 行为。

## 回滚

```bash
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-purchase-confirm-skip-20261022.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
