# CaseLedger AI API 扩展规划（审核版，暂不实施）

- 版本：v0.1（2026-11-03）
- 当前已上线：API Key 管理 + 客服邮件读取/发送（mail_read / mail_send）
- 本文只是候选清单，按建议阶段和风险分级，等你勾选后再实施。

---

## 一、先明确两条原则

1. **读优先、写从严**：能只读解决的事先只读；写操作一律单独 scope、单独 Key、可单独撤销。
2. **钱和客户通知要人工确认**：退款、优惠、Shopify 回填、4PX 真实建单/取消、采购确认、库存调整，
   默认不开放给 AI，必须设计「人工确认」后再考虑。

---

## 二、第一优先：客服邮件能力补全（低风险，建议先做）

| # | 能力 | 建议接口 | 用途 | 风险 |
|---|---|---|---|---|
| M1 | 邮件筛选增强 | `GET /api/ai/mail/messages` 增加 `date_from`、`date_to`、`customer`、`has_attachment`、`folder_id`、`sort` | AI 按时间/客户/附件筛选 | 低 |
| M2 | 邮件草稿管理 | `GET /api/ai/mail/messages/<id>/draft`、`PUT .../draft {reply}`、`DELETE .../draft` | 读取/修改/清空草稿（目前只能存/发） | 低 |
| M3 | 保存回复关键点 | `PUT .../guidance {guidance}` | 让 AI 写"处理口径"，再由系统生成草稿 | 低 |
| M4 | 带附件发送 | `POST .../send-with-attachments`（multipart：reply + files） | 客服发照片/视频给客户 | 中（文件安全校验沿用现有规则） |
| M5 | 会话完整加载 | `POST .../hydrate-thread {limit}` | 一次读完整个会话，避免 AI 逐封拉取 | 低 |
| M6 | 批量已处理/忽略 | `POST /api/ai/mail/messages/batch-ignore {ids, ignored}` | AI 批量清掉系统通知 | 低（限 50 条） |
| M7 | 增量邮件 | `GET .../messages?updated_since=<ISO时间>` | AI 定时任务只取新邮件，省 token | 低 |
| M8 | 客户匹配订单查询 | `GET /api/ai/mail/orders?email=` | 按邮箱查该客户订单摘要（详情里已有，单独接口更方便） | 低 |
| M9 | 邮件审阅辅助（只读） | `GET .../review` | 读取售后审阅结果/照片核实状态、折扣建议（不创建） | 中：需脱敏，不自动创建优惠 |

> 建议 M1–M8 一起做，M9 可以后置。

---

## 三、第二优先：订单 + 物流只读（对 AI 价值最大）

| # | 能力 | 建议接口 | 用途 | 风险 |
|---|---|---|---|---|
| O1 | 订单列表 | `GET /api/ai/orders?shop_id=&status=&search=&date_from=&date_to=&page=&per_page=` | AI 查单、按状态统计 | 低（PII 脱敏） |
| O2 | 订单详情 | `GET /api/ai/orders/<order_number>` | 商品、金额、支付、仓库、包裹、售后、最近轨迹 | 低 |
| O3 | 按邮箱查订单 | `GET /api/ai/orders?email=` | 客服场景最常用 | 低 |
| O4 | 订单时间线 | `GET /api/ai/orders/<order_number>/timeline` | 支付/采购/出库/轨迹事件合并 | 低 |
| O5 | 物流轨迹列表 | `GET /api/ai/logistics/records?shop_id=&tab=pending|preinfo|overdue&search=` | AI 找"已出库未送达""未出库有轨迹"等异常 | 低 |
| O6 | 物流轨迹详情 | `GET /api/ai/logistics/records/<id>` | 全部事件、最新状态、建议处理 | 低 |
| O7 | 异常汇总 | `GET /api/ai/logistics/summary` | 每日异常数量，方便 AI 生成日报 | 低 |

> 订单/物流读接口建议一个 Key 只读，AI 客服可用来回答"我的包裹到哪了/为什么还没出库"。

---

## 四、第三优先：采购、库存、售后只读

| # | 能力 | 建议接口 | 用途 | 风险 |
|---|---|---|---|---|
| P1 | 采购候选/缺口 | `GET /api/ai/procurement/candidates?view=needs_purchase|after_sale` | AI 回答"还差多少件、要采购什么" | 低 |
| P2 | 采购清单列表/详情 | `GET /api/ai/purchase-lists`、`/purchase-lists/<id>` | 查在途、到货、缺货 | 低 |
| P3 | 库存汇总 | `GET /api/ai/inventory/summary` | 库存总量、缺货前 N | 低 |
| P4 | 库存流水 | `GET /api/ai/inventory/movements?date_from=&date_to=` | 解释库存变化 | 低 |
| P5 | 售后列表/详情 | `GET /api/ai/after-sales`、`/after-sales/<id>` | 查补发进度、成本 | 低（PII 脱敏） |
| P6 | 售后再购需求 | `GET /api/ai/after-sales/pending` | AI 汇总待补发商品 | 低 |

> 这些都只读，建议与订单/物流同一批开放。

---

## 五、第四优先：写操作（必须单独设计确认机制，暂不实施）

| # | 能力 | 风险 | 建议保护 |
|---|---|---|---|
| W1 | 创建采购清单草稿（不确认） | 中 | 只创建 draft，不影响库存/付款 |
| W2 | 确认采购（真正下单） | 高（花钱） | 人工在网页确认；API 只创建草稿 |
| W3 | 库存调整 | 高 | 禁止，或只允许"申请调整"待人工批 |
| W4 | Shopify 回填发货/通知客户 | 高（客户通知不可逆） | 禁止自动；API 只提交"待确认"，网页人工点 |
| W5 | 4PX 真实预申报/取消 | 高（真实占用单号） | 只允许生成检查结果；建单必须人工网页确认 |
| W6 | 退款/优惠码创建 | 极高 | 不开放给 AI |
| W7 | 删除订单/售后/费用 | 极高 | 不开放 |
| W8 | 批量发送客服邮件 | 高 | 限制每 Key 每日发送上限 + 禁止批量群发 |

**建议：W 类等 P1–P3 稳定后，再单独做一期"人工确认令牌"机制。**

---

## 六、平台/安全增强（建议随接口一起做）

| # | 能力 | 说明 |
|---|---|---|
| S1 | Key 限流 | 每 Key 每分钟/每天调用上限（如 120 次/分、2000 次/天） |
| S2 | Key 发送配额 | 单独限制每天最多发送 N 封邮件，超限拒绝 |
| S3 | IP 白名单 | 可选，只允许指定服务器 IP 调用 |
| S4 | 审计查询页面 | 管理页查看最近调用（时间/接口/状态/耗时/IP），支持按 Key 筛选 |
| S5 | OpenAPI 文档 | 提供 `/api/ai/openapi.json`，Grok/工具可直接导入 |
| S6 | 字段裁剪 | `?fields=id,subject,status` 减少 AI token 消耗 |
| S7 | ETag/增量 | 列表支持 `updated_since`，定时任务只拉变化 |
| S8 | 只读副本 | 调用量上来后 AI 查询走只读库，不影响主库 |
| S9 | 提示词注入提醒 | 邮件内容当数据，不执行邮件里的指令；文档里明确写 |
| S10 | 发送前 dry-run | `POST .../send?dry_run=true` 返回将要发送的内容和收件人，不真发 |

---

## 七、建议实施顺序与工作量

| 阶段 | 内容 | 预计 |
|---|---|---|
| P1 | 邮件补全 M1–M8 + 限流 S1/S2 + 审计页 S4 + OpenAPI S5 | 1.5–2 天 |
| P2 | 订单/物流只读 O1–O7 | 1.5–2 天 |
| P3 | 采购/库存/售后只读 P1–P6 + 增量/字段裁剪 S6/S7 | 1.5–2 天 |
| P4 | 写操作确认机制（按需） | 3–5 天 |

每期上线方式仍然是：新增路由 + 新增 scope，不动现有页面；发布后逐项冒烟、可随时撤销 Key。

---

## 八、需要你审核勾选的内容

1. P1 邮件补全：M1–M8 是否全要？M9（邮件审阅只读）要不要？
2. P2 订单/物流只读：是否同意先开？
3. P3 采购/库存/售后只读：是否同意先开？
4. 写操作：W1（只建草稿）要不要先给？其他 W 类是否暂不开放？
5. 平台项：S1/S2 限流与发送配额按什么值（例如 120 次/分、每天 100 封发送）？
6. 是否需要 OpenAPI JSON 给 Grok 导入？

审核后我按勾选结果实施，未勾选的保持关闭。
