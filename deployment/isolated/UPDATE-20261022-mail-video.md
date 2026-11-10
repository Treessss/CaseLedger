# 2026-10-22 更新记录：邮件支持视频附件

## 功能

邮件回复现在支持图片和视频附件：

- 图片：PNG、JPG、GIF、WebP（沿用严格文件头校验）
- 视频：MP4、MOV、WebM、AVI、MKV、M4V、3GP、3G2、MPEG/MPG、FLV、WMV、ASF、VOB、F4V、OGV、TS/M2TS 等；
- 浏览器未上报 MIME 的视频（如 MKV/AVI）会按扩展名识别；
- 单个附件最大 **25MB**，整封合计最大 **50MB**，最多 **5 个**；
- 前端上传区显示视频图标和文件名，不再尝试用图片预览视频；
- 发送成功后提示“含 N 个图片/视频附件”。

## 基础设施调整

- 宿主机 nginx 与仓库 `deployment/isolated/nginx.conf`：`client_max_body_size 60m`，`proxy_send_timeout 300s`；
- gunicorn `--timeout 300`，前端发送请求超时 300 秒；
- 仍使用 Zoho 附件上传与回复接口，附件本身不写入草稿，只在发送时上传。

## 限制说明

应用允许到 50MB，但实际能否送达还取决于 Zoho 邮箱和收件方邮箱的单封大小限制（常见为 25MB）。超过 25MB 的视频如果被 Zoho/收件方拒收，会返回发送失败，建议压缩到 25MB 内或改用网盘链接。

## 验证

- 新增测试：MP4 上传并附加到 Zoho 回复、伪装成 MP4 的 HTML 被拒绝；原有图片/数量校验回归通过。
- 邮件测试全部通过；`ruff` 通过；前端 `tsc` 与生产构建通过。
- 后端镜像已重建重启；前端目录 `releases/mail-video-20261022c/frontend/dist`；无数据库迁移。
- 后端备份：`/opt/caseledger-copy/backups/pre-mail-video-20261022.tgz`。

## 回滚

```bash
ln -sfn /opt/caseledger-copy/releases/auto-outbound-20261022/frontend/dist \
  /opt/caseledger-copy/current/frontend/dist
cd /opt/caseledger-copy/current
tar -xzf /opt/caseledger-copy/backups/pre-mail-video-20261022.tgz
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml build
docker compose --env-file /opt/caseledger-copy/config/compose.env \
  -f deployment/isolated/compose.yaml up -d
```
