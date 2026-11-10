# 2026-10-20 更新记录：回复邮件支持图片附件

## 功能

回复客户邮件时可以添加图片一起发送：

- 支持 PNG / JPG / GIF / WebP；
- 一次最多 5 张，单张 ≤10MB，总计 ≤25MB；
- 发送前可在回复框下方预览、删除；
- 图片只在点击「发送回复」时上传到 Zoho，并随回复一起发出。

## 后端

- `mail_client.upload_attachment()`：调用 Zoho `POST /api/accounts/{id}/messages/attachments?uploadType=multipart`，
  使用 multipart 字段 `attach`，返回 `attachmentName / attachmentPath / storeName`。
- `mail_service.send_reply_with_attachments()`：先保存手写正文，再逐张上传，最后调用 `send_reply(attachments=...)`。
- `send_reply()` 在 Zoho 发信 body 里增加 `attachments` 数组；发送记录 `MailSend.result.attachments` 保留文件名，便于审计。
- 校验图片 MIME、文件头（防止改扩展名）、空文件、单张/总大小、数量。
- 新接口 `POST /api/mail/messages/<id>/send-with-attachments`（multipart：`reply` + `files`）。

## 前端

- 回复草稿下方新增图片上传区（picture-card），支持预览和删除，最多 5 张。
- 发送时若有图片，调用新的 multipart 接口；没有图片仍走原来的纯文本接口。
- 请求拦截器识别 `FormData`，不再把表单数据展开成 JSON，只追加 `shop_id`。

## 已知边界

- 图片不写入「保存草稿」：草稿只保存正文（界面已明确提示）。重新打开邮件后需要重新选择图片。
- 如果图片上传成功但发信失败，服务端会保留正文草稿，图片需要重新选择后重试。

## 验证

- 生产 Zoho 账号实际调用上传接口返回 200，拿到 `attachmentName/attachmentPath/storeName`。
- 生产 Zoho 账号实际发送了一封带图片的回复（收件人也是 support_agent@caseease.com），接口返回 200 并在响应中回显 `attachments`，确认回复接口支持附件。
  测试邮件主题为 `[CaseLedger TEST] attachment reply`；当前 OAuth scope 没有 update 权限，无法通过 API 移入垃圾箱，可在 Zoho 网页端手动删除，未发给任何客户。
- 新增后端测试：multipart 接口、附件元数据写入发信 body、发送记录保存附件名、非图片/超数量拒绝。
- 前端 MailDesk 9 项测试、`tsc`、生产构建通过。
- 生产页面实测：选择图片后出现预览，回复框和发送按钮正常；未在生产环境发送测试邮件。

## 部署

- 后端镜像已重建，web/worker/beat 等容器已重启。
- 前端目录：`/opt/caseledger-copy/releases/mail-attachments-20261020/frontend/dist`。
- 后端备份：`/opt/caseledger-copy/backups/pre-mail-attachments-20261020.tgz`。
- 无数据库迁移。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/mail-manual-draft-20261019/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-mail-attachments-20261020.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
