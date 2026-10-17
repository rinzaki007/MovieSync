# 🎬 MovieSync

> 自托管影视信息、开放资源源与存储任务管理平台

面向 Docker、NAS 和家庭服务器，支持影视浏览、资源检索、网盘转存、任务管理与自动追剧。资源来源和存储目标采用卡片扩展，新装后可按需安装，不依赖固定来源。

**基本流程：** 搜索影视 → 选择资源 → 选择兼容的存储目标 → 查看转存任务；也可创建自动追剧任务。

## ✨ 主要功能

- 影视浏览与搜索：支持豆瓣，可选启用 TMDB
- 资源检索：支持 PanSou、Telegram，并可扩展其他来源
- 存储卡片：按兼容的资源类型选择网盘和保存目录
- 自动追剧：定期检查并跟进后续内容
- 任务中心：查看进度、日志、结果并按需重试
- 基础安全：管理员登录、CSRF 防护、登录保护和敏感配置脱敏

新安装默认不自动安装卡片。PanSou 搜索 API 已随镜像提供，无需另建容器。

## 🐳 Docker 部署

创建 \`docker-compose.yml\`：

\`\`\`yaml
services:
  moviesync:
    image: ghcr.io/rinzaki007/moviesync:latest
    container_name: MovieSync
    restart: unless-stopped
    ports:
      - "8099:5000"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
      - MOVIESYNC_COOKIE_SECURE=0
      # 可选：首次启动时安装镜像内置的全部卡片
      # - MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS=1
\`\`\`

启动：

\`\`\`bash
docker compose up -d
\`\`\`

浏览器访问 \`http://服务器IP:8099\`，首次启动按页面提示创建管理员账号。

### iStoreOS / NAS

将持久化目录挂载到容器的 \`/app/data\`，例如：

\`\`\`text
/mnt/sata1-1/moviesync -> /app/data
\`\`\`

更新容器时保持该挂载不变，账号、配置和任务记录即可保留。也可以直接使用 Docker：

\`\`\`bash
docker run -d \\
  --name MovieSync \\
  --restart unless-stopped \\
  -p 8099:5000 \\
  -v /你的目录/moviesync:/app/data \\
  -e TZ=Asia/Shanghai \\
  -e MOVIESYNC_COOKIE_SECURE=0 \\
  ghcr.io/rinzaki007/moviesync:latest
\`\`\`

## ⚙️ 初次配置

1. 创建管理员账号并登录。
2. 在「卡片管理」安装需要的卡片；也可将单个 \`.py\` 卡片文件放入 \`/app/data/cards/\` 后重启。
3. 安装夸克存储卡片，并填写 Cookie 和保存目录。
4. 安装 PanSou 卡片即可使用内置搜索服务；默认地址已配置。
5. 如需 TMDB，安装对应卡片并填写 API Read Access Token；豆瓣与 TMDB 可分别启用或停用。
6. 如需 Telegram 资源，安装 Telegram 卡片并配置频道。
7. 搜索影视、选择资源和文件后提交转存；需要时创建自动追剧任务。

如需首次自动安装镜像内置的全部卡片，可设置 \`MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS=1\`。

## 🧩 卡片开发与扩展

卡片作者请先阅读 [卡片开发规范（CARD_DEVELOPMENT.md）](CARD_DEVELOPMENT.md)，了解 manifest、能力声明、配置版本迁移、安装安全边界和提交前验证要求。

## 💾 数据与备份

运行数据保存在 \`/app/data/\`，包括账号、配置、已安装卡片、任务和追剧记录。不要将这些数据提交到公开仓库。

**更新前建议备份整个宿主机挂载目录。** 以下以 iStoreOS 的 \`/mnt/sata1-1/moviesync\` 为例，请按实际路径调整。备份文件包含 Cookie 等敏感信息，请妥善保管。

\`\`\`bash
docker stop MovieSync
mkdir -p /mnt/sata1-1/moviesync-backups
tar -czf "/mnt/sata1-1/moviesync-backups/moviesync-$(date +%Y%m%d-%H%M%S).tar.gz" -C /mnt/sata1-1 moviesync
docker start MovieSync
\`\`\`

### 恢复备份

先确认备份文件存在且可读取，再解压到临时目录检查；确认无误后才替换现有目录。请将示例文件名改为实际备份文件名。

\`\`\`bash
docker stop MovieSync
mkdir -p /mnt/sata1-1/moviesync-restore
tar -tzf "/mnt/sata1-1/moviesync-backups/实际备份文件名.tar.gz" | head
tar -xzf "/mnt/sata1-1/moviesync-backups/实际备份文件名.tar.gz" -C /mnt/sata1-1/moviesync-restore
# 确认临时目录中存在 moviesync/ 后，再替换现有目录
mv /mnt/sata1-1/moviesync "/mnt/sata1-1/moviesync-before-restore-$(date +%Y%m%d-%H%M%S)"
mv /mnt/sata1-1/moviesync-restore/moviesync /mnt/sata1-1/moviesync
docker start MovieSync
\`\`\`

如果解压或检查失败，不要执行两条 \`mv\` 命令；可先运行 \`docker start MovieSync\` 恢复原服务。恢复后检查登录状态、卡片配置和任务记录。

## 🔧 常用命令

\`\`\`bash
docker ps | grep MovieSync        # 查看容器
docker logs -f MovieSync          # 查看日志
docker restart MovieSync          # 重启容器
docker compose pull && docker compose up -d  # 更新镜像
\`\`\`

## 🧩 扩展与可靠性

- 资源来源和存储目标相互独立，可按卡片接口扩展。
- 存储卡片声明支持的资源类型；不兼容的分享不会直接提交转存。
- 任务和追剧状态会持久化；遇到无法确认的转存结果时会保留待核实状态，避免盲目重试。
- 健康检查帮助区分外部来源故障与平台问题，但不代表第三方服务始终可用。

智能追剧的识别规则与测试约定见 [SMART_FOLLOW_RULES.md](SMART_FOLLOW_RULES.md)。

## 📚 第三方数据来源

TMDB 为可选卡片。启用后请保留项目「关于」页面中的官方署名，并遵守 [TMDB API 使用条款](https://www.themoviedb.org/api-terms-of-use) 和[官方署名要求](https://www.themoviedb.org/about/logos-attribution)。

> This product uses the TMDB API but is not endorsed or certified by TMDB.

## ⚠️ 免责声明

MovieSync 不提供、托管或分发影视资源。资源来源、账号、链接及具体使用方式均由使用者自行配置；使用者应确认内容合法，并遵守所在地法律法规、版权要求及第三方平台规则。

## 📄 License

MIT License
