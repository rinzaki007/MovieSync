# 🎬 MovieSync

> 自托管影视检索、资源选择与网盘任务管理平台

MovieSync 面向 Docker、NAS 和家庭服务器。它把影视信息、资源搜索、网盘转存和自动追剧整合在一个 Web 界面中，并通过卡片扩展资源来源和存储目标。

**部署边界：** MovieSync 镜像不再内置 OpenList。MovieSync 可独立运行；需要使用 OpenList 播放卡片或 OpenList 网盘入口时，再单独部署 OpenList 并在 MovieSync 中配置连接。

**基本流程：** 搜索影视 → 选择资源 → 选择兼容的存储卡片 → 查看任务结果。转存完成后，可以从任务卡片进入播放中心；也可以从豆瓣影视卡片直接进入播放中心，使用已配置的 MacCMS 资源站搜索在线资源。自动追剧任务则可持续跟进后续内容。

## 主要功能

- **影视信息：** 豆瓣；TMDB 可按需安装和配置
- **资源搜索：** PanSou、Telegram 等资源来源卡片
- **网盘转存：** 按资源类型选择兼容的存储卡片和保存目录
- **统一播放中心：** 在同一播放页面浏览已转存文件，或通过 MacCMS 兼容资源站搜索在线影视。豆瓣卡片可直接带入片名与年份；电影会优先匹配并尝试播放，电视剧、动漫和综艺可选择线路与具体集数。
- **自动追剧：** 选择起追集数、跟进后续内容，并避免重复转存
- **任务中心：** 查看任务状态、进度、日志和结果；成功转存后，任务卡片直接显示「打开播放中心」，并可显示存储卡声明的网盘入口
- **卡片管理：** 按需安装、启用、停用和配置卡片
- **基础安全：** 管理员登录、CSRF 防护、登录保护及敏感配置脱敏

MovieSync 核心不要求必须安装某个资源源或网盘卡片。新安装默认可以没有卡片，用户按需选择。PanSou 搜索服务已随 Docker 镜像提供，无需另外部署容器；使用它仍需在 MovieSync 中安装对应卡片。

## 播放中心：网盘文件与 MacCMS 在线资源

播放中心统一承载媒体文件播放和 MacCMS 在线点播，但两者使用不同的资源来源与配置。

- **网盘文件播放：** 如需浏览已转存文件并获取播放信息，请在「设置 → 卡片管理」安装并配置 OpenList 播放卡片。也可以使用未来提供的其他兼容播放卡片。存储卡声明的 `web_url` 只是跳转到外部网盘主页，不会自动提供文件列表或播放地址。
- **MacCMS 在线点播：** 在 MacCMS 源管理中配置可用的兼容资源站后，可在播放中心搜索影片与剧集。豆瓣卡片上的「立即观看」会将片名和年份带入播放中心，减少重复搜索。在线点播依赖已配置的 MacCMS 资源站，不要求先配置 OpenList 播放卡片。
- **电影：** 系统优先使用匹配度较高的资源并尝试播放；页面会同步显示实际选中的资源。其他候选资源可按需展开。
- **电视剧、动漫与综艺：** 先查看资源站线路和原始选集名称，再点击目标集数播放；不会为了自动播放而随意猜测一集。切换线路只更新选集列表，不会暗中切换正在播放的视频；选择新线路中的集数后才会切换。
- **备用线路：** 播放失败时，只在能确认是同一集的情况下尝试其他线路，避免跳到不同集数。无法可靠匹配的集数不会被当作同一集。
- **播放限制：** 真实播放仍取决于资源站可用性、播放地址有效期、浏览器网络、上游访问策略、HLS/视频编码兼容性等。自动化测试和 Docker 构建通过不等于每个第三方资源站都已实播验证。

转存成功不代表网盘文件一定能播放；资源站搜索到结果也不代表播放地址一定有效。更详细的在线点播行为见 [MacCMS 在线点播说明](MACCMS_PLAYBACK.md)。

## Docker 部署

### 只部署 MovieSync

创建 `compose.yaml`。默认配置只启动 MovieSync，不会安装或启动 OpenList：

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

启动 MovieSync：

```bash
docker compose up -d
```

浏览器访问 `http://服务器IP:8099`，按页面提示创建管理员账号。

### 可选：单独部署 OpenList

OpenList 不属于 MovieSync 镜像的一部分。只有需要 OpenList 相关功能时，才在同一目录额外创建 `compose.openlist.yaml`：

```yaml
services:
  openlist:
    image: openlistteam/openlist:latest
    container_name: OpenList
    restart: unless-stopped
    ports:
      - "5244:5244"
    volumes:
      - ./openlist-data:/opt/openlist/data
    environment:
      - TZ=Asia/Shanghai
```

在两个文件所在目录运行以下命令，即可让两个独立容器加入同一个 Compose 网络：

```bash
docker compose -f compose.yaml -f compose.openlist.yaml up -d
```

OpenList 管理界面可通过浏览器访问 `http://服务器IP:5244`。在 MovieSync 的 OpenList 播放卡片中，服务地址填写容器间通信地址 `http://openlist:5244`，并配置自己的 API Token 和媒体根目录；「浏览器播放地址前缀」填写浏览器可访问的地址，例如 `http://服务器IP:5244`。

如果 OpenList 已经由其他 Compose 项目或其他设备独立运行，不必重复部署；只需把 MovieSync 卡片的服务地址设置为 MovieSync 容器能够访问的 OpenList 地址。不要把管理端口直接暴露到不可信的公网。

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
4. 如需浏览并播放已转存的网盘文件，在「设置 → 卡片管理」安装 OpenList 播放卡片并完成配置。若使用上方可选的 OpenList 容器，服务地址填写 `http://openlist:5244`，再填写 OpenList API Token、媒体根目录和浏览器可访问的播放地址前缀。若只使用 MacCMS 在线点播，请配置 MacCMS 兼容资源站即可，不要求配置 OpenList。
5. 如需 PanSou 搜索，安装 PanSou 卡片；镜像已内置对应搜索服务。
6. 如需 TMDB 或 Telegram，分别安装对应卡片并按页面提示配置。
7. 搜索影视、选择资源与文件，提交转存；转存成功后可从任务卡片打开播放中心。需要时也可以创建自动追剧任务。

也可以将可信的单文件 Python 卡片放入持久化目录 `/app/data/cards/` 后按卡片机制加载。卡片是可执行代码，只安装来自可信来源且经过检查的文件。

## 数据与备份

MovieSync 的运行数据保存在 `/app/data/`，包括管理员信息、配置、已安装卡片、任务和追剧记录。请勿把真实 Cookie、Token、密码或运行数据提交到公开仓库。

更新前建议停止容器并备份宿主机挂载目录。恢复备份前先检查压缩包内容，并确认恢复目标路径正确；不要在未核实的情况下覆盖现有数据。

## 文档

- [架构说明](ARCHITECTURE.md)：核心模块、播放中心与卡片边界
- [MacCMS 在线点播说明](MACCMS_PLAYBACK.md)：电影自动匹配、剧集选集、线路切换与备用线路规则
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
