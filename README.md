# 🎬 MovieSync

MovieSync 是一个面向 Docker、NAS 和家庭服务器的自托管影视管理平台。它将影视信息、资源搜索、网盘转存、自动追剧和播放入口整合在一个 Web 界面中，并通过卡片按需扩展第三方服务。

## 主要功能

- **影视信息**：通过豆瓣等卡片获取影视信息；可选接入 TMDB。
- **资源搜索**：通过 PanSou、Telegram 等资源卡片查找影视资源。
- **网盘转存**：选择资源、存储目标和保存目录，集中查看任务状态与日志。
- **自动追剧**：设置起追集数，跟进后续内容并避免重复转存。
- **播放中心**：从影视卡片直达在线点播；也可通过兼容的播放卡片播放已转存文件。在线点播需配置可用的兼容资源站。
- **卡片扩展**：按需安装和配置影视信息、资源来源、存储及播放卡片。

MovieSync 核心可以独立运行，不要求预装特定资源源或网盘。OpenList 是可选的独立服务，不包含在 MovieSync 镜像中。

## Docker 部署

需要已安装 Docker Engine 和 Docker Compose。创建一个目录，并在其中创建 `compose.yaml`：

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

启动服务：

```bash
docker compose up -d
```

浏览器打开 `http://服务器IP:8099`，按页面提示创建管理员账号。若通过 HTTPS 反向代理提供服务，请按实际部署调整 Cookie 安全设置，并确保管理界面不会暴露给不可信的访问者。

### 数据与更新

- 所有运行数据保存在宿主机的 `./data` 目录中；更新镜像时保留该目录。
- 更新前建议备份数据目录。里面可能包含 Cookie、Token 和其他敏感配置，不要上传到公开仓库。
- 拉取最新镜像并重建容器：

```bash
docker compose pull
docker compose up -d
```

### 可选服务

- **OpenList**：仅在需要 OpenList 网盘入口或 OpenList 播放卡片时单独部署，并在对应卡片中配置可访问的服务地址和凭据。
- **在线点播**：如需搜索并播放外部资源站中的影视，请在 MovieSync 中配置兼容的可用资源站；不要求为此单独部署 OpenList。
- **其他集成**：在「卡片管理」中按需安装和配置对应卡片。部分卡片需要自行提供 Cookie、Token 或 API 密钥。

## 相关文档

- [在线点播说明](ONLINE_PLAYBACK.md)
- [架构说明](ARCHITECTURE.md)
- [卡片开发规范](CARD_DEVELOPMENT.md) · [卡片接口约定](CARD_CONTRACT.md)
- [智能追剧规则](SMART_FOLLOW_RULES.md) · [更新记录](CHANGELOG.md)

## 许可证与第三方声明

MovieSync 以 [MIT License](LICENSE) 发布。使用、修改和再分发时，请保留许可证及版权声明。

如启用 TMDB 卡片，需遵守 [TMDB API 使用条款](https://www.themoviedb.org/api-terms-of-use) 和[官方署名要求](https://www.themoviedb.org/about/logos-attribution)，并保留以下声明：

> This product uses the TMDB API but is not endorsed or certified by TMDB.

第三方卡片、数据源及服务可能适用各自的许可证和使用条款，请在启用前自行确认。MovieSync 不提供或托管影视资源；使用者应自行确认资源来源与使用方式符合当地法律、版权要求及第三方平台规则。
