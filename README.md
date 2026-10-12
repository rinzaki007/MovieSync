# 🎬 MovieSync

> 豆瓣 / TMDB 影视发现 · 可插拔资源检索 · 多存储目标接口 · 自动追剧

MovieSync 是一个面向家庭服务器、NAS 和 Docker 环境的轻量级自托管影视资源管理工具。它以任务中心串联影视搜索、资源筛选、兼容的存储卡片、转存与自动追更，让日常操作和后台执行状态更清晰、可追踪。

**核心流程：** 选择影视元数据来源并搜索 → 通过已启用的资源来源检索分享 → 按资源类型选择兼容的存储卡片与目录 → 提交转存任务 → 通过自动追剧任务持续检查后续集数。

项目采用可扩展的资源来源抽象和独立的存储目标设计；当前内置 PanSou、Telegram 资源来源与夸克存储卡片，后续可以按同一接口增加其他网盘卡片。Docker 镜像已将 PanSou 搜索 API 一并打包，无需额外创建 PanSou 容器。不同网盘分享不能默认跨平台转存：目标卡片必须声明支持对应资源类型，MovieSync 不会把不兼容的分享强行交给其他网盘。MovieSync 本身不提供、托管或分发影视文件，具体资源及第三方服务均由使用者自行配置。

## ✨ 当前能力

- 🎬 浏览和搜索豆瓣影视；可选配置 TMDB 卡片作为独立元数据来源
- 🔌 资源来源抽象层：内置 PanSou 搜索 API 与 Telegram，可扩展其他来源
- ❤️ 资源源健康检查与最近状态记录
- 🔎 从已配置的资源来源检索候选资源
- ☁️ 搜索方向跟随用户选择的存储卡片：自动按卡片声明的分享类型筛选 PanSou 网盘结果，并只把匹配的资源交给对应存储卡片
- 📁 从兼容的存储卡片中选择保存目标与目录
- 🤖 自动追剧：周期性检查并跟进后续内容
- 📋 独立任务中心：进度、阶段、成功/跳过/失败、日志、重试
- 🔐 管理员认证、CSRF、防暴力登录、敏感配置脱敏
- 💾 JSON 持久化，适合家庭服务器 / NAS / iStoreOS / Docker
- 🐳 提供 GitHub Actions 自动测试与 Docker 镜像构建

## 🛡️ 可靠性与异常恢复

MovieSync 的后台任务不仅关注“执行成功”，也会尽量避免异常情况下重复转存或丢失进度：

- **同一订阅单次执行**：手动检查与定时调度共享运行标记，避免同一订阅同时进入两次检查。
- **转存进度持久化**：追剧记录保存在 `/app/data/subscriptions.json`；成功记录与待处理状态在同一次存储提交中更新。
- **不确定结果保护**：如果远程转存请求可能已到达网盘、但客户端无法确认结果，会保留待核实信息，不直接盲目重试。
- **重启恢复**：服务启动时检查上次中断的任务；存在待确认转存记录时要求先核实网盘结果。
- **任务状态可追踪**：任务中心记录状态、阶段、运行历史与错误摘要，便于定位问题。

这些机制用于降低重复操作和状态丢失的风险，并不替代定期备份 `/app/data`。更新容器前仍建议备份重要配置与任务记录。

智能追剧的起追集数、跨季比较、规则卡片职责和回归测试约定见 [SMART_FOLLOW_RULES.md](SMART_FOLLOW_RULES.md)。

## 🧩 开放资源源架构

MovieSync 不把任何单一资源来源写死在核心业务里。

当前结构：

```text
MovieSync
├── 影视信息
│   └── Douban
│
├── ResourceSource
│   ├── PanSou（内置搜索 API）
│   ├── Telegram（内置）
│   └── 其他来源（可扩展）
│
├── 资源候选
│
├── Storage / Transfer
│   ├── Quark（当前内置存储卡片）
│   └── 其他网盘（可按接口扩展）
│
└── Task Center
```

资源源统一位于：

```text
moviesync/services/resource_sources.py
```

核心接口为 `ResourceSource`。

以后增加新的资源来源时，原则上只需要实现：

- 资源搜索
- 资源源健康检查
- 统一的候选资源字段

而不需要重新修改首页、资源选择页、任务中心和转存流程。

这意味着 Telegram 即使未来不可用，也不会影响 MovieSync 的整体架构；只需要增加或替换一个资源源实现即可。

### 存储卡片扩展约定

- 资源来源优先输出通用的 `resource_id` 与 `resource_type`；旧的 `pwd_id` 仍会兼容，但新卡片不需要伪造这个夸克字段。
- 存储卡片在 `CardManifest.capabilities` 中声明支持的分享类型，例如 `storage.accepts.quark_share`。只有声明兼容的卡片才会被用于解析与转存。
- 同一个资源 ID 在不同网盘之间可能相同，因此缓存、任务去重和追剧记录会区分资源类型。
- 增加新网盘时，应实现对应的资源解析、文件列表、目录与转存接口，并为该卡片声明能力；不需要在核心任务流程中添加该网盘的专用分支。
- 资源类型不兼容时会阻止提交。只有网盘卡片确实实现了跨平台搬运能力并明确声明支持时，才可以接收其他平台的分享。


> PanSou API 已随 MovieSync Docker 镜像一起启动；默认地址为容器内的 `http://127.0.0.1:8888`。无需单独部署 PanSou。若已有自己的 PanSou 服务，仍可在 PanSou 卡片里改用外部地址。搜索时会根据当前已安装存储卡片声明的 `storage.accepts.<resource_type>` 能力映射到 PanSou 的 `cloud_types`；切换存储卡片后会重新搜索，不会继续沿用上一个网盘的结果。未安装对应存储卡片时，不会把该网盘分享当成可转存资源。

## 🩺 资源源健康状态

后台的“资源来源”区域可以手动检查来源状态。

状态包括：

- 🟢 正常：来源可用
- 🟡 部分可用：例如部分 Telegram 频道不可用
- 🔴 不可用：来源整体不可用
- ⚪ 未检查：尚未执行健康检查

健康信息会持久化保存，包括最近检查时间、最近成功时间和失败次数。

健康检查用于帮助判断“是 MovieSync 出问题，还是外部资源来源出了问题”，不会把某个来源视为系统永久依赖。

## 🏗️ 项目结构

```text
MovieSync/
├── moviesync/
│   ├── clients/
│   │   ├── douban.py
│   │   ├── http.py
│   │   ├── quark.py
│   │   └── telegram.py
│   │
│   ├── services/
│   │   ├── resource_sources.py  # 开放资源源抽象与健康检查
│   │   ├── search.py            # 候选资源处理与转存
│   │   ├── subscriptions.py     # 自动追剧
│   │   └── tasks.py             # 任务中心
│   │
│   ├── web/
│   │   └── routes.py
│   ├── app.py
│   ├── auth.py
│   ├── config_store.py
│   ├── logging_setup.py
│   ├── settings.py
│   └── storage.py
│
├── static/
├── templates/
├── tests/
├── Dockerfile
├── main.py
└── README.md
```

## 🐳 Docker 部署

### Docker Compose

新建 `docker-compose.yml`：

```yaml
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
      # 可选：首次启动时安装镜像内置的全部卡片；默认不自动安装任何卡片
      # - MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS=1
```

启动：

```bash
docker compose up -d
```

访问：

```text
http://你的服务器IP:8099
```

第一次启动会进入管理员初始化页面。MovieSync 容器会同时启动内置 PanSou 搜索 API；PanSou 缓存写入 `/app/data/pansou/cache`，随现有 `/app/data` 挂载持久化。

### iStoreOS / NAS

例如：

```text
/mnt/sata1-1/moviesync -> /app/data
```

只要 `/app/data` 挂载目录保持不变，更新容器不会丢失账号、配置和追剧任务。

### 直接 Docker

```bash
docker run -d \
  --name MovieSync \
  --restart unless-stopped \
  -p 8099:5000 \
  -v /你的目录/moviesync:/app/data \
  -e TZ=Asia/Shanghai \
  -e MOVIESYNC_COOKIE_SECURE=0 \
  ghcr.io/rinzaki007/moviesync:latest
```

## ⚙️ 初次配置

1. 创建管理员账号并登录后台
2. 新装默认不自动安装任何卡片；在「卡片管理」上传你需要的卡片文件，或将单个 `.py` 卡片文件挂载到 `/app/data/cards/` 后重启
3. 如需一次性安装镜像内置的全部卡片，可设置环境变量 `MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS=1`。已有安装中的卡片文件会继续保留并按原规则升级
4. 安装夸克存储卡片后，在该卡片的配置中填写 Cookie 和保存目录
5. 安装 PanSou 资源卡片即可使用内置搜索 API，默认地址已配置，无需单独部署或填写地址
6. 如需使用 TMDB，安装 TMDB 卡片并填写 API Read Access Token；豆瓣与 TMDB 可分别启用或停用
7. 首页搜索框左侧可以选择影视数据源；自动来源模式不会拼接多个来源的结果
8. 如需额外搜索 Telegram，安装 Telegram 卡片后再配置对应频道
9. 使用首页搜索影视，选择候选资源及文件并提交转存任务
10. 需要时创建自动追剧任务

## 💾 数据目录

运行数据保存在：

```text
/app/data/
├── auth.json
├── config.json
├── subscriptions.json
├── tasks.json
├── .secret_key
└── logs/
```

这些内容属于运行数据，不应提交到 GitHub。

配置文件会自动维护 schema version，并兼容旧版本配置。

### 备份与恢复（建议更新前执行）

MovieSync 的账号、Cookie、卡片配置、已安装卡片、任务和追剧记录都可能位于挂载的数据目录中。**更新容器前建议备份整个数据目录，而不是只备份某一个 JSON 文件。** 备份文件包含敏感信息，请妥善保管，不要上传到公开仓库。

以下以 iStoreOS 上的 `/mnt/sata1-1/moviesync` 为例；如果你的宿主机挂载路径不同，请替换为实际路径。

备份时先停止容器，避免任务运行期间只复制到一部分状态：

```bash
docker stop MovieSync
mkdir -p /mnt/sata1-1/moviesync-backups
tar -czf "/mnt/sata1-1/moviesync-backups/moviesync-$(date +%Y%m%d-%H%M%S).tar.gz" -C /mnt/sata1-1 moviesync
docker start MovieSync
```

确认压缩包已生成后，再执行镜像更新。备份文件建议保存到与数据目录不同的位置；有条件时可再复制一份到另一块磁盘。

恢复前先确认备份文件路径正确。下面的操作会先将当前数据目录改名保留，再解压备份：

```bash
docker stop MovieSync
mv /mnt/sata1-1/moviesync "/mnt/sata1-1/moviesync-before-restore-$(date +%Y%m%d-%H%M%S)"
tar -xzf "/mnt/sata1-1/moviesync-backups/替换为实际备份文件名.tar.gz" -C /mnt/sata1-1
docker start MovieSync
```

请将示例中的备份文件名替换为实际文件名。恢复后检查容器日志、登录状态、卡片配置和任务记录；不要在容器运行时覆盖正在使用的数据目录。

## 🔧 常用命令

查看容器：

```bash
docker ps | grep MovieSync
```

查看日志：

```bash
docker logs -f MovieSync
```

重启：

```bash
docker restart MovieSync
```

更新：

```bash
docker compose pull
docker compose up -d
```

## 🛡️ 设计原则

MovieSync 的核心设计目标不是绑定某一个资源网站，而是：

1. **来源可替换**：ResourceSource 与核心业务解耦
2. **存储可替换**：资源发现与存储目标解耦
3. **失败可解释**：区分来源、解析、转存和任务执行错误
4. **状态可追踪**：统一任务中心记录后台任务
5. **数据可持久化**：容器更新不依赖容器内部文件
6. **最小信任**：浏览器提交的资源参数在服务端重新验证
7. **易于维护**：尽量保持单一职责和明确的 Provider 边界

## 📚 第三方数据来源与署名

TMDB 是可选的独立元数据卡片。启用后，请在「关于」页面查看 TMDB 标志及官方署名声明。该页面包含 TMDB 要求的声明：

> This product uses the TMDB API but is not endorsed or certified by TMDB.

TMDB 数据和图片的使用应遵守 [TMDB API 使用条款](https://www.themoviedb.org/api-terms-of-use) 及 [官方标志与署名要求](https://www.themoviedb.org/about/logos-attribution)。非商业开发者 API 使用也必须正确署名；商业用途应先向 TMDB 确认所需许可。

## ⚠️ 免责声明

MovieSync 是一个通用的软件平台，不提供、托管或分发影视资源内容。

资源来源、账号、链接、文件以及具体使用方式均由使用者自行配置和决定。使用者应自行确认相关内容的合法性，并遵守所在地法律法规、版权要求以及相关服务的平台规则和服务条款。

项目作者不对使用者配置的第三方服务、外部资源来源、账号状态、数据损失或具体使用行为承担责任。

## 📄 License

MIT License
