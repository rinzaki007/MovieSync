# MovieSync 内置 OpenList 引擎

## 目的与边界

MovieSync 镜像随附 OpenList 官方 **v4.2.7** Linux musl 发布二进制。OpenList 作为独立子进程运行在 MovieSync 的同一个容器内；它仍然是一个独立的 OpenList 服务，只是不再需要用户再创建第二个 Docker 容器。

此实现优先保留 OpenList 原有的网盘驱动和管理界面，作为后续增加更多 MovieSync 存储卡片的基础。它不是把 Go 代码直接嵌入 Python 进程，也不意味着 MovieSync 的所有业务都依赖 OpenList。

目前内置二进制仅支持 `amd64` 和 `arm64`。Docker 构建时下载固定版本的官方发布包，并校验写入 Dockerfile 的官方发布资产 SHA-256 摘要。升级 OpenList 时必须同时更新固定版本与对应架构的 SHA-256 摘要并检查兼容性，不要在生产构建里无版本地追踪 latest。

## 端口与启动

- MovieSync Web：容器端口 `5000`，示例宿主机端口 `8099`。
- OpenList 原生管理界面与 API：容器端口 `5244`。
- MovieSync 内部默认连接地址：`http://127.0.0.1:5244`。
- 默认启动内置 OpenList；设置 `MOVIESYNC_BUNDLED_OPENLIST_ENABLED=false` 可关闭它。
- OpenList 自身的数据目录为 `/app/data/openlist/`，其中包含配置、数据库和索引，跟随 MovieSync 的 `/app/data` 卷持久化。

如果需要添加或管理 OpenList 网盘驱动，请在 Dockge/Compose 中把端口 `5244:5244` 映射到可信的局域网，再访问 `http://服务器IP:5244`。没有发布该端口时，OpenList 仍能在容器内部向 MovieSync 提供 API，但浏览器无法直接打开原生管理界面。

OpenList 是管理面板，不应未经保护直接暴露到公网。不要为了让视频可播放就随意关闭 OpenList 的认证。容器端口映射属于宿主机部署配置，`EXPOSE 5244` 本身不会自动向宿主机发布端口。

## 首次配置与播放卡片

1. 打开 OpenList 管理界面，完成管理员初始化。
2. 在 OpenList 的存储管理里添加需要的网盘驱动，并设置挂载路径。例如，可将挂载路径设为 `/quark` 或 `/movies`。
3. 在 MovieSync「设置 → 卡片管理」安装 OpenList 播放卡片。
4. 服务地址默认使用容器内部地址 `http://127.0.0.1:5244`。填写 API Token，并将媒体根目录设为 OpenList 的挂载路径或其子目录。可在容器终端运行 ` /usr/local/bin/openlist --data /app/data/openlist admin token` 查看内置管理员 Token；也可在 OpenList 的用户/权限设置中按需建立权限更小的账号。
5. 将「浏览器播放地址前缀」填写为浏览器能访问的宿主机地址，例如 `http://192.168.1.2:5244`。不要在这个字段中填写 `127.0.0.1`，否则在另一台设备上看视频时，浏览器会把它解析为观看设备自身。
6. 使用播放中心前，确认所配置的 OpenList 媒体根目录内确实可以列出目标文件。

播放卡片可能提供删除已选文件/目录的能力，因此 Token 必须有与用户预期一致的权限。管理员 Token 权限很高，应当仅在充分信任 MovieSync 服务和网络环境的情况下使用，并把它当作密码保护。

如果以前使用独立 OpenList Docker，原实例仍可继续使用：把播放卡片的服务地址填写成 MovieSync 容器能够访问的外部地址即可。内置实例会使用全新的 `/app/data/openlist/` 数据目录，不会自动导入另一个容器的配置和数据库；切换实例前应在 OpenList 中重新建立存储配置或按 OpenList 支持的方式迁移数据。

## 故障排查

- **MovieSync 正常，但 OpenList 无法访问：** 查看容器日志中是否出现内置服务启动失败，确认没有把 `MOVIESYNC_BUNDLED_OPENLIST_ENABLED` 设为 `false`，并确认 5244 端口未被容器内其他服务占用。
- **网页无法访问：** 确认 Compose 已发布 `5244:5244`，防火墙允许可信客户端访问，使用正确的服务器 IP。
- **播放中心无法列出文件：** 检查卡片服务地址、Token、OpenList 挂载路径和目标驱动权限。
- **列表可见但视频无法播放：** 检查「浏览器播放地址前缀」是否使用浏览器可达的地址，并考虑链接有效期、上游限制、Range 请求与浏览器编码兼容性。
- **丢失初始管理员密码：** 可在容器终端执行 `/usr/local/bin/openlist --data /app/data/openlist admin random` 重置密码。该操作会更改内置实例的管理员密码，请先确认命令使用的是内置数据目录。

## 许可证与来源

内置程序来自 [OpenList v4.2.7](https://github.com/OpenListTeam/OpenList/tree/v4.2.7)，上游使用 **AGPL-3.0** 许可证。MovieSync 镜像包含对应许可证文本及指向对应源代码归档的声明：

- 镜像内许可证：`/usr/share/licenses/moviesync/OpenList-LICENSE`
- 镜像内来源声明：`/usr/share/licenses/moviesync/OpenList-SOURCE.txt`
- 官方源代码：[OpenList v4.2.7](https://github.com/OpenListTeam/OpenList/tree/v4.2.7)

OpenList 在本原型中作为未修改的独立可执行程序随镜像分发。若后续修改 OpenList 本身、改变它的分发方式或深度整合其代码，需重新评估并履行适用的 AGPL-3.0 义务。
