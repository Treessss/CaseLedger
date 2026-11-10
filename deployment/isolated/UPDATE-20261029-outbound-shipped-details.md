# 2026-10-29 更新记录：扫码出库展示已出库商品图片 + 收货信息核对

- 服务器：192.3.236.57，HTTPS 8443；Compose 项目 `caseledger-copy`。
- 新前端目录：`/opt/caseledger-copy/releases/outbound-shipped-details-20261029/frontend/dist`。
- `current/frontend/dist` 由 `releases/mail-video-20261022c/frontend/dist` 切换到上述目录。
- 上一版仍完整保留，可随时回滚：
  ```bash
  ln -sfn /opt/caseledger-copy/releases/mail-video-20261022c/frontend/dist \
    /opt/caseledger-copy/current/frontend/dist
  ```

## 改动内容

1. **已出库的包裹重新扫码，照样展示商品图片。**
   以前扫一张已经出库的面单只会提示「该包裹已出库，本次重复扫码已忽略」，商品区是空的。
   现在扫到重复出库后，前端会再查一次 `packing-lookup`（只读，不改状态、不写异常记录），
   把订单的应装商品（含图片）、面单和收货信息显示出来，方便现场对照实物。
2. **扫码出库页新增「收货信息核对」面板。**
   与装箱核对同款：商品图片在左，收件人 / 联系电话 / 公司 / 地址 / 城市 / 省州 / 邮编 / 国家在右；
   宽度 ≤740px 时面板自动落到商品下方。数据取本单解析到的包裹地址（售后补发单展示补发地址）。
3. **部分出库的提示更正。**
   只扫了部分包裹时提示「这张面单已经扫过 · 已扫 x/y 个包裹」，不再笼统说「这单已经出库了」。
4. `RecipientInfo` 从 `frontend/src/pages/PackingCheck/RecipientInfo.tsx` 提升为
   `frontend/src/components/RecipientInfo/index.tsx`，装箱核对和扫码出库共用。

## 后端

本次没有后端改动，也未重建/重启容器。`packing-lookup` 原本就返回
`items[].image_url`、`shipping_address` 和 `outbound.already_scanned`，直接复用。

## 验证

- `npx jest src/pages/ShippingOutbound src/pages/PackingCheck src/components/RecipientInfo --runInBand`：4 个套件、22 项用例通过；
  新增用例覆盖「重复扫码后仍补查详情并展示收货地址」和「部分扫描提示剩余包裹」。
- 全量 `npx jest --ci`：31 个套件、89 项用例通过。
- `npx tsc --noEmit` 通过；`npx @biomejs/biome lint` 无错误；`npm run build` 通过（29 个 JS 资源，Safari 13 语法检查通过）。
- 部署后从公网校验：`/`、`/login/`、`/shipping-outbound/`、`/packing-check/` 均返回 200；
  新构建的 chunk 返回 200 且包含本次新增文案；旧版独立资源经符号链接仍返回 200（缓存旧 index.html 的浏览器不受影响）；
  未登录调用 `/api/` 仍返回 401（鉴权未受影响）。
- 旧版 dist 中缺失的 695 个文件已在新目录建立指向上一版的符号链接。

## 现场复测建议

1. 扫一张已经出库的面单：应看到「这单已经出库了（或这张面单已经扫过）」+ 商品图片 + 收货信息核对；
   不会重复扣库存，也不会新增出库异常记录。
2. 扫一张未出库的面单：出库成功后同样显示商品图片和收货信息核对，地址应与面单一致。
3. 多包裹订单只扫一张：提示「已扫 1/N 个包裹」，把剩余面单扫完才提示整单已出库。

## 备注

- 本次只替换静态前端目录，不涉及数据库、迁移、后台任务或容器；刷新页面即可生效。
- 服务器根分区使用率约 56%（新 release 约 20MB）。
