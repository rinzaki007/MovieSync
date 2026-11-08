# MovieSync 数据备份与恢复

## 备份内容

备份整个 `MOVIESYNC_DATA_DIR`（默认 `/app/data`），保留隐藏文件。脚本默认排除可重新生成的 `logs/`、`cache/`、`pansou/cache/` 和 `backups/`。

重要数据包括：

- `.secret_key`：Flask Session 签名密钥，必须与认证数据一起恢复
- `auth.json`、`setup.token`（若首次初始化尚未完成）
- `config.json`、`subscriptions.json`、`tasks.json`
- `cards/`、`cards/.seeded/`
- `pansou/pansou.env`

## 执行备份

在宿主机有权读取数据卷的位置运行：

```sh
MOVIESYNC_DATA_DIR=/mnt/sata1-1/moviesync sh scripts/backup-data.sh /path/to/off-device-backups
```

也可以先将数据卷只读挂载到备份环境，再运行脚本。备份文件包含 Cookie、密钥和账号配置，脚本默认以仅当前用户可读写的权限创建归档。请将备份加密保存并限制访问，建议复制到另一块磁盘或远端位置；不要只把备份留在 MovieSync 自己的数据卷中。

## 恢复步骤

1. 停止 MovieSync 容器，避免备份期间 JSON 文件仍在变化。
2. 先将当前数据目录整体复制一份，作为回滚点。
3. 将备份归档解压到原数据目录，保留隐藏文件。
4. 确认数据目录归属与容器运行 UID/GID 一致（官方镜像默认 `10001:10001`；可设置 `MOVIESYNC_UID` / `MOVIESYNC_GID`）。
5. 启动容器并检查 `/healthz`、登录、卡片状态、订阅和任务记录。

**不要只恢复 `auth.json` 而遗漏 `.secret_key`，也不要从不同时间点拼接认证文件和密钥。**恢复完整的数据目录比选择性恢复单个 JSON 文件更可靠。
