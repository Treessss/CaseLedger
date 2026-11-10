# 2026-10-20 服务器清理：停用宝塔栈、删除遗留程序、增加 swap

## 已停用并禁用开机启动

- 宝塔面板 `bt`、站点监控 `monitor`
- MySQL `mysqld` / `mysql`
- PHP-FPM `php-fpm-80`
- Pure-FTPd `pure-ftpd` / `pureftpd`
- 宝塔自带 PostgreSQL `pgsql`（只有默认库、无连接）
- 孤儿 node 进程（`/www/wwwroot/insdm` 已删除，仍监听 127.0.0.1:8787）
- 清空 root 旧 crontab（宝塔和已删除项目的任务，其中一条每分钟执行）；备份在 `/root/crontab-backup-20261020.txt`

主机 nginx 保留，因为 CaseLedger 8443 的入口仍由它提供。

## nginx 配置迁移

删除宝塔目录前，已把 CaseLedger vhost 从
`/www/server/panel/vhost/nginx/caseledger-copy.conf`
迁移到
`/www/server/nginx/conf/vhost/caseledger-copy.conf`，
并修改 `nginx.conf` 的 include 指向新目录；原配置备份为
`/www/server/nginx/conf/nginx.conf.bak-20261020`。

`nginx -t` 通过，`https://127.0.0.1:8443/` 返回 200。

## 已删除

`/www/server` 下除 `nginx` 外的遗留目录已删除：
`panel`、`monitor`、`mysql`、`data`、`php`、`pgsql`、`nodejs`、`nvm`、`pure-ftpd`、
`cron`、`stop`、`rar`、`go_project`、`pyporject_evn`、`python_project`。

`/www/server` 从约 4.0GB 降到 **17MB**（仅 nginx）。

## swap

新增 2GB swap 文件：

- `/swapfile2`，权限 0600，已 `mkswap` + `swapon`；
- `/etc/fstab` 已加入 `/swapfile2 none swap sw 0 0`；
- 当前 swap：1GB 分区 + 2GB 文件，共 3GB，使用约 130MB。

## 结果

| 指标 | 清理前 | 现在 |
|---|---:|---:|
| 根分区 | 96%，剩 1.3G | **53%，剩 13G** |
| `/www/server` | 约 4.0GB | **17MB** |
| Swap 总量/使用 | 1GB / 661MB | **3GB / 130MB** |
| 可用内存 | 约 470MB | **约 475MB，swap 余量 2.9GB** |

## 验证

- `nginx -t` 通过，CaseLedger 8443 返回 200（约 35ms）。
- 售后 API 返回 200；8 个容器全部运行。
- 3306/21/8787/34541/5432 等旧端口已关闭。
- `nginx.service`、`docker.service` 开机启用；证书续期和每日备份 timer 正常。
- 数据库锁等待 0。

## 说明

- 宝塔面板、MySQL、PHP、FTP 及其数据已删除；如果以后需要，只能重新安装，不再保留旧数据。
- CaseLedger 自身不受影响；nginx 仍是主机上的 `nginx.service`，配置在 `/www/server/nginx`。
