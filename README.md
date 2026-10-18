# 🎬 MovieSync

<img width="1920" height="945" alt="99ecaf9aca6fa251b3fc9ecc3d8a911e" src="https://github.com/user-attachments/assets/381f42b5-b998-4538-8405-e9657fda3a78" />


> 自托管影视检索、资源选择与网盘任务管理平台

MovieSync 面向 Docker、NAS 和家庭服务器。它把影视信息、资源搜索、网盘转存和自动追剧整合在一个 Web 界面中，并通过卡片扩展资源来源和存储目标。

**基本流程：** 搜索影视 → 选择资源 → 选择兼容的存储卡片 → 查看任务结果。转存完成后，可以从任务卡片直接进入在线播放中心；也可以创建自动追剧任务，持续跟进后续内容。

## 主要功能

- **影视信息：** 豆瓣；TMDB 可按需安装和配置
- **资源搜索：** PanSou、Telegram 等资源来源卡片
- **网盘转存：** 按资源类型选择兼容的存储卡片和保存目录
- **在线播放：** 在播放中心浏览已转存的视频并尝试通过浏览器在线播放；播放能力取决于已接入的播放来源、可用播放信息、浏览器网络可达性、视频编码及 Range 请求支持
- **自动追剧：** 选择起追集数、跟进后续内容，并避免重复转存
- **任务中心：** 查看任务状态、进度、日志和结果；成功转存后，任务卡片直接显示「打开播放中心」，并可显示存储卡声明的网盘入口
- **卡片管理：** 按需安装、启用、停用和配置卡片
- **基础安全：** 管理员登录、CSRF 防护、登录保护及敏感配置脱敏

MovieSync 核心不要求必须安装某个资源源或网盘卡片。新安装默认可以没有卡片，用户按需选择。PanSou 搜索服务已随 Docker 镜像提供，无需另外部署容器；使用它仍需在 MovieSync 中安装对应卡片。

## 在线播放与 OpenList 的关系

**MovieSync 自带的播放中心不要求必须部署 OpenList。** 播放中心使用当前已接入播放来源提供的预览或播放信息；当前能否播放，取决于对应存储卡及上游平台实际提供的能力，而不是仅仅取决于是否配置了 OpenList。

OpenList 是可选的外部网盘浏览/访问入口，不是 MovieSync 播放中心的必需后端：

- **不使用 OpenList：** 仍可使用 MovieSync 的播放中心尝试播放已接入播放来源提供的视频；不保证每个文件或每个网盘都支持在线播放。
- **使用 OpenList：** 可以按自己的部署方式配置 OpenList，用于通过其 Web 界面访问或浏览网盘内容。MovieSync 任务卡片仅在存储卡声明了有效的网盘主页地址时显示对应跳转入口。
- **不要混淆两种播放路径：** 从 MovieSync 播放中心播放，与跳转到 OpenList 后通过 OpenList 访问或播放，是两条不同的路径。配置 OpenList 不会自动让 MovieSync 获得原本不存在的播放地址，也不能保证上游受限的视频一定能播放。

## 在线播放说明

1. 完成转存并确认任务状态为成功后，在任务卡片点击「打开播放中心」。
2. 在播放中心选择已列出的视频并尝试播放。当前播放列表取决于已实现并启用的播放来源；不能据此推断任意网盘中的所有文件都可播放。
3. 如果视频无法播放，可能是上游平台没有提供可用的预览地址、播放地址过期、服务端或浏览器无法访问该地址、上游限制请求（例如 Range 请求），或视频编码与浏览器不兼容。转存成功不等于在线播放一定成功。
4. 如果更习惯通过 OpenList 浏览网盘，请单独部署并配置 OpenList，再使用对应的 OpenList 访问入口；这不是 MovieSync 播放中心的前置条件。
5. 仍可通过任务详情查看执行日志和诊断信息；如果存储卡声明了安全有效的网盘主页入口，任务卡片也会提供对应跳转。

## Docker 部署

创建 `compose.yaml`：

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
```

启动容器：

```bash
docker compose up -d
```

浏览器访问 `http://服务器IP:8099`，按页面提示创建管理员账号。

### iStoreOS / NAS

将宿主机目录持久化挂载到容器的 `/app/data`。例如：

```yaml
volumes:
  - /mnt/sata1-1/moviesync:/app/data
```

更新镜像时保留原有数据挂载，不要删除数据目录或使用会清除数据卷的命令。建议更新前备份整个挂载目录；其中可能包含 Cookie、Token 和其他敏感配置，请妥善保管。

## 初次配置

1. 创建管理员账号并登录。
2. 在「卡片管理」安装需要的影视信息、资源来源或存储卡片。
3. 安装夸克存储卡片，并填写自己的 Cookie 和保存目录。
4. 如需 PanSou 搜索，安装 PanSou 卡片；镜像已内置对应搜索服务。
5. 如需 TMDB 或 Telegram，分别安装对应卡片并按页面提示配置。
6. 搜索影视、选择资源与文件，提交转存；转存成功后可从任务卡片打开播放中心。需要时也可以创建自动追剧任务。

也可以将可信的单文件 Python 卡片放入持久化目录 `/app/data/cards/` 后按卡片机制加载。卡片是可执行代码，只安装来自可信来源且经过检查的文件。

## 数据与备份

MovieSync 的运行数据保存在 `/app/data/`，包括管理员信息、配置、已安装卡片、任务和追剧记录。请勿把真实 Cookie、Token、密码或运行数据提交到公开仓库。

更新前建议停止容器并备份宿主机挂载目录。恢复备份前先检查压缩包内容，并确认恢复目标路径正确；不要在未核实的情况下覆盖现有数据。

## 文档

- [架构说明](ARCHITECTURE.md)：核心模块与卡片边界
- [卡片开发规范](CARD_DEVELOPMENT.md)：开发、配置、安全和测试要求
- [卡片接口约定](CARD_CONTRACT.md)：卡片与核心之间的契约
- [智能追剧规则](SMART_FOLLOW_RULES.md)：起追集数、去重与回归测试约定
- [更新记录](CHANGELOG.md)
- [历史重构清单](REFACTORING.md)
- [历史代码审查记录](CODE_REVIEW.md)

## 开发与验证

提交代码前建议运行：

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
node --check static/js/main.js
node --check static/js/resource-select.js
node --check static/js/task-center.js
```

Pull Request 会运行自动化测试、Ruff 和前端语法检查，并验证 Docker 镜像能否构建。正式的 `latest` 镜像只在手动触发主分支发布工作流时推送，不会因每次普通提交自动发布。

## 第三方数据来源

TMDB 为可选卡片。启用后请保留项目「关于」页面中的官方署名，并遵守 [TMDB API 使用条款](https://www.themoviedb.org/api-terms-of-use) 与[官方署名要求](https://www.themoviedb.org/about/logos-attribution)。

> This product uses the TMDB API but is not endorsed or certified by TMDB.

## 免责声明

MovieSync 不提供、托管或分发影视资源。资源来源、账号、链接和具体使用方式均由使用者自行配置。使用者应自行确认内容合法，并遵守所在地法律法规、版权要求及第三方平台规则。

## License

[MIT License](LICENSE)
