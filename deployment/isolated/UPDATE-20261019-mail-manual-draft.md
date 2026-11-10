# 2026-10-19 更新记录：手写回复草稿可直接保存/恢复

## 问题

客服邮件页的「保存回复」调用 `POST /api/mail/messages/<id>/reply`，后端 `save_reply` 里有一道限制：

```python
if draft is None or not draft.reply:
    raise ValidationError("请先生成回复草稿")
```

所以员工自己手写回复时，必须先点「生成草稿」，否则保存一定失败。刷新/切换邮件后自然也无法恢复手写内容。

## 改动

- 后端 `save_reply` 改为首次保存时创建 `mail_drafts` 行（`_draft_row`），不再要求已有 AI 草稿。
- 手写/修改后统一标记 `edited=True`、`safe=False`，发送仍需人工确认。
- 前端按钮文案改为「保存草稿」，保存成功提示「回复草稿已保存，下次打开可继续编辑」，并刷新列表里的「有草稿」标记。
- 重新打开邮件时沿用原有逻辑，从 `detail.draft.reply` 恢复正文。

## 部署

- 后端镜像已重建并重启 web/worker/beat 等容器。
- 前端目录：`/opt/caseledger-copy/releases/mail-manual-draft-20261019/frontend/dist`。
- 后端备份：`/opt/caseledger-copy/backups/pre-mail-manual-draft-20261019.tgz`。
- 无数据库迁移、无历史数据修改。

## 验证

- 新增 `test_manual_reply_saves_and_restores_without_generated_draft`：无草稿时保存成功、`generated_at` 为空、详情接口能读回。
- 本地浏览器实走：打开邮件 → 手写正文 → 保存草稿 → 刷新 → 重新打开，正文完整恢复。
- 生产只读确认「保存草稿」按钮已上线；未在生产写入测试草稿。
- 全部邮件后端测试通过；前端 MailDesk 9 项测试、`tsc`、生产构建通过。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/inventory-config-20261019/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-mail-manual-draft-20261019.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
