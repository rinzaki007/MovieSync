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

如果需要添加或管理 OpenList 网盘驱动，请在 Dockge/Compose 中把端口 `5245:5244` 映射到可信的局域网，再访问 `http://服务器IP:5245`。宿主机默认用 5245，避免与可能仍在运行的独立 OpenList（常用宿主机端口 5244）冲突；容器内部端口仍为 5244。没有发布该端口时，OpenList 仍能在容器内部向 MovieSync 提供 API，但浏览器无法直接打开原生管理界面。

OpenList 是管理面板，不应未经保护直接暴露到公网。不要为了让视频可播放就随意关闭 OpenList 的认证。容器端口映射属于宿主机部署配置，`EXPOSE 5244` 本身不会自动向宿主机发布端口。

## MovieSync 原生 OpenList 存储卡片

MovieSync 提供独立的「OpenList 存储卡片」，用于在 MovieSync 后台直接管理 OpenList 官方驱动挂载。卡片通过内置 OpenList 的管理 API 读取驱动清单与参数定义、创建和编辑挂载、启停或删除挂载；不需要用户进入 OpenList 管理后台，也不要求 MovieSync 为每个网盘重写一套驱动协议。

### 配置流程

1. 确认 MovieSync 镜像包含内置 OpenList，并且没有设置 `MOVIESYNC_BUNDLED_OPENLIST_ENABLED=false`。
2. 打开 MovieSync「设置 → 卡片管理」，从内置卡片模板安装「OpenList 存储卡片」（模板文件为 `card_templates/openlist_storage.py`）。现有安装默认不会覆盖用户的卡片目录；卡片仍按 MovieSync 的可选卡片机制安装、卸载。
3. 打开「OpenList 存储卡片」，点「添加网盘」。存储类型来自当前 OpenList 引擎的官方驱动清单，驱动授权参数也会动态读取官方定义。
4. 按需填写显示名称、挂载路径、根目录和该驱动要求的授权参数，然后点「保存并创建挂载」。例如可以将显示名称设为「夸克网盘」、挂载路径设为 `/夸克网盘`、根目录设为 `/`。这些只是配置字段示例；只有填写真实有效的授权信息并且驱动初始化成功，网盘才算连接成功。
5. 在卡片的挂载列表查看状态。后续可在 MovieSync 内编辑授权参数、启用/停用挂载或删除挂载。删除只移除 OpenList 的挂载配置，不会删除网盘中的文件。

### 授权参数与安全

- MovieSync 只在服务端调用内置 OpenList 管理 API；浏览器不会直接接触 OpenList 管理 Token。
- 授权字段根据官方驱动的定义动态生成，Cookie、Token、密码、密钥等敏感字段以密码框显示。列表接口会清除敏感字段的实际值，只返回是否已有凭据；编辑时将已保存的敏感字段留空会保留原值。
- 新建挂载时必须填写驱动声明的必填授权参数。MovieSync 不会把示例字段当成已连接状态，也不会在未提交配置时自动创建示例网盘。
- 对需要 `root_folder_id` 的驱动，根目录 `/` 会映射为该驱动的根 ID（通常为 `0`）；若要挂载子目录，应按该驱动要求提供目录 ID。使用 `root_folder_path` 的驱动则按官方定义传入路径。
- 建议不要将 OpenList 原生管理端口映射到公网。MovieSync 原生存储卡片通过容器内部的 `http://127.0.0.1:5244` 操作，不需要向浏览器开放 5244 端口。

### OpenList 原生管理界面（可选）

OpenList 管理界面仍随引擎保留，主要用于高级排障或直接操作 OpenList 自身设置，不是 MovieSync 存储卡片的必经步骤。如确有需要，可在 Dockge/Compose 中将宿主机端口 `5245` 映射到容器端口 `5244`，然后从可信的局域网访问 `http://服务器IP:5245`。不要把这个管理端口未经保护地暴露到公网。

原有「OpenList 播放卡片」仍负责从 OpenList 已挂载的目录列出视频和解析播放链接；它与「OpenList 存储卡片」职责不同。前者可继续连接内置或外部 OpenList，后者负责通过 MovieSync 管理 OpenList 挂载。

## 故障排查

- **MovieSync 正常，但 OpenList 无法访问：** 查看容器日志中是否出现内置服务启动失败，确认没有把 `MOVIESYNC_BUNDLED_OPENLIST_ENABLED` 设为 `false`，并确认 5244 端口未被容器内其他服务占用。
- **网页无法访问：** 确认 Compose 已发布 `5245:5244`，防火墙允许可信客户端访问，使用正确的服务器 IP。
- **播放中心无法列出文件：** 检查卡片服务地址、Token、OpenList 挂载路径和目标驱动权限。
- **列表可见但视频无法播放：** 检查「浏览器播放地址前缀」是否使用浏览器可达的地址，并考虑链接有效期、上游限制、Range 请求与浏览器编码兼容性。
- **丢失初始管理员密码：** 可在容器终端执行 `/usr/local/bin/openlist --data /app/data/openlist admin random` 重置密码。该操作会更改内置实例的管理员密码，请先确认命令使用的是内置数据目录。

## 许可证与来源

内置程序来自 [OpenList v4.2.7](https://github.com/OpenListTeam/OpenList/tree/v4.2.7)，上游使用 **AGPL-3.0** 许可证。MovieSync 镜像包含对应许可证文本及指向对应源代码归档的声明：

- 镜像内许可证：`/usr/share/licenses/moviesync/OpenList-LICENSE`
- 镜像内来源声明：`/usr/share/licenses/moviesync/OpenList-SOURCE.txt`
- 官方源代码：[OpenList v4.2.7](https://github.com/OpenListTeam/OpenList/tree/v4.2.7)

OpenList 在本原型中作为未修改的独立可执行程序随镜像分发。若后续修改 OpenList 本身、改变它的分发方式或深度整合其代码，需重新评估并履行适用的 AGPL-3.0 义务。
