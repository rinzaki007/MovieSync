# 卡片扩展约定

本文定义 MovieSync 当前代码中的资源来源卡片与存储目标卡片边界，作为后续接入新平台时的实现清单。卡片注册表只管理已经由应用加载的实例。MovieSync 支持可信的单文件卡片：文件存放于持久化目录 `/app/data/cards/`，后台可上传加载或卸载删除。Python 卡片不是沙箱，只有在审查并信任代码后才能安装。

## 资源来源卡片（`resource_source`）

实现应继承 `ResourceSourceCard`，并提供唯一、稳定的小写 `manifest.id`。核心搜索流程依赖的标准资源字段如下：

- `pwd_id`：来源资源的稳定标识，必须能交给所选存储目标解析。
- `source_id` / `source_name`：来源卡片 ID 与展示名称。
- `storage_target_id`：当资源只能由特定网盘解析时明确指定；可通用解析时可留空。
- `channel` / `channel_id`：可选的频道或来源位置，用于展示和追剧定位。

最小能力是 `search(movie, config)` 与 `check(config)`。如果卡片支持按频道扫描新内容，还应实现 `search_channel(channel, title, config)`；不支持时基类默认返回空列表，不能将其视为已支持自动追剧扫描。

返回的资源应为字典列表；单个坏结果不应破坏其他结果。异常由管理器隔离并记录。卡片配置放在 `config.cards[manifest.id].config`，启用状态由同一条卡片配置的 `enabled` 控制。

## 存储目标卡片（`storage_target`）

实现应继承 `StorageTargetCard`，并提供以下能力：

- `resolve_resource(resource)`：返回标准结果，至少包含 `files`、`token`、`error`。
- `list_files(resource)`：返回统一文件列表。
- `destination_options()`：返回可供界面选择的目录选项。
- `create_folder(name, parent_id)`：返回创建或复用后的目录 ID。
- `transfer(resource, files, target_id)`：只处理经过服务端重新解析和校验的文件。

标准文件记录至少应包含 `fid`、`file_name` 和可选的 `size`。资源来源与存储目标不是任意可互换的：`pwd_id` 必须被目标平台理解，文件 ID 与分享 Token 也必须属于同一个资源。不能因为两张卡片都已注册，就假定它们可以组合。

## 文件名与集数识别

共享基础解析器位于 `moviesync/services/filename_rules.py`，统一入口为 `parse_tv_episode()`。所有资源搜索来源（例如 PanSou、Telegram 及未来新增的资源卡片）都必须交给统一的文件名识别流程；来源卡片负责发现资源，不应自行维护一套必需的集数识别规则。

- 「文件名识别」卡片集中维护全局规则，按配置顺序尝试；规则默认对所有资源来源生效。
- 只有遇到确实无法用全局规则合理覆盖的特殊命名时，才可选填 `source_ids` 限定规则适用范围。来源限定是例外，不是每张资源卡片的必需实现。
- 旧卡片只实现 `parse_episode(file_name, config)` 的接口仍然兼容；`FilenameProcessorCard` 的优先级机制也继续保留给有明确需求的扩展识别器。
- 旧的单条 `magic_regex` 配置和来源卡片历史规则暂时保留兼容回退，避免升级后已有配置突然失效；新规则统一添加到全局规则列表，不要再把新规则写入资源来源卡片。
- 识别结果统一为 `(season, episode)`；无法识别时返回 `(None, None)` 并继续回退。规则卡片只负责识别，起追基线、跨季比较、去重、任务队列与转存仍由核心统一负责。

不要在平台客户端、订阅服务和各资源卡片中重复实现互不一致的正则。测试必须覆盖：同一条全局规则跨来源生效、按顺序回退、可选来源限定、停用识别卡片、坏规则隔离及跨季季集结果。

## 接入检查清单

1. 为卡片定义唯一的 `CardManifest` 与明确的 `capabilities`。
2. 对无结果、异常、重复资源 ID、无效资源 ID 和停用卡片编写测试。
3. 若支持频道扫描，覆盖新集发现、跨季比较和空搜索回退。
4. 若支持存储操作，测试资源解析、文件字段标准化、Token 不泄露与转存白名单。
5. 通过 pytest、Ruff 和 Docker 构建后再合并到 `main`。


## 声明式配置字段

卡片可通过 CardManifest 的 config_fields 声明后台表单字段。每个字段至少包含 key，可选 label、type、required、default、placeholder、description 与 secret。支持 string、password、textarea、boolean、number、json 类型；复杂配置可声明为 json 对象或数组。字段是卡片配置对象的顶层键。

敏感字段应设置 secret: true。服务端还会根据 cookie、token、password、secret、api_key、authorization、credential 等字段名做兜底识别。读取接口不会返回凭据原文；前端留空提交时保留已有值。配置接口只接受 Manifest 声明的字段，未知键会被拒绝。连接测试使用 POST /api/cards/<card_id>/check，卡片必须声明 *.health_check 或 *.check 能力。

## 已安装资源卡片插件入口

MovieSync 支持通过 Python 包的 entry point 加载可信资源来源插件，组名为 `moviesync.resource_sources`。每个入口必须指向一个工厂函数，接收单个 `context` 字典并返回 `ResourceSourceCard` 实例。当前上下文包含 `telegram`、`config_store` 和 `logger`，插件可以按需使用这些共享服务。

例如，插件包的 `pyproject.toml` 可声明：

```toml
[project.entry-points."moviesync.resource_sources"]
example = "my_moviesync_plugin:create_card"
```

工厂函数形状：

```python
from moviesync.cards import CardManifest, ResourceSourceCard

class ExampleSource(ResourceSourceCard):
    manifest = CardManifest(
        id="example",
        name="Example",
        type="resource_source",
        capabilities=("resource.search", "resource.health_check"),
    )

    def search(self, movie, config):
        return []

    def check(self, config):
        return {"status": "healthy", "message": "ok"}

def create_card(context):
    return ExampleSource()
```

插件包方式仍适合需要额外依赖的卡片。除此之外，可信的单文件卡片可以由后台上传并立即加载；文件名用于定位、上传和卸载插件文件，不要求与 `CardManifest.id` 一致；`CardManifest.id` 才是注册表、配置和业务分发使用的稳定卡片 ID。插件必须导出 `create_card(context)` 工厂函数。单文件插件最多 256 KiB，依赖必须已经存在于 MovieSync 的 Python 环境。安装后的卡片会进入统一注册表，并出现在 `GET /api/cards`；资源搜索、集数配置和健康检查由资源来源管理器按卡片 ID 分发。


## 卡片业务配置校验

通用配置 API 在字段白名单、字段类型和必填项检查后，会调用卡片的 validate_config(config) 钩子。该方法接收合并后的完整配置，应返回校验通过且规范化后的配置字典；无效配置应抛出 ValueError（包括 ConfigValidationError），不得在校验阶段产生持久化副作用。通用接口只保存本次提交字段对应的规范化值，因此不会意外覆盖卡片内部的健康状态等未声明字段。

未实现该钩子的卡片继承 Card 默认实现，只进行对象形状检查。声明了复杂配置的卡片应自行验证其业务约束。内置 Quark 会校验默认/分类目录 FID，Telegram 会校验频道数组、频道 ID、正则语法与长度限制。校验失败返回 HTTP 400，且不会改变已保存配置或启用状态。


## 存储卡片停用与任务状态

停用存储目标不会撤回已经发出的网盘请求，也不会强制终止 Python 中正在执行的卡片调用；但每个后续存储操作都会重新检查卡片启用状态，因此同一任务的后续步骤可能因卡片已停用而失败。任务中心会保留失败结果，重新启用卡片后可手动重试。若任务显示“结果待核实”，必须先核对网盘，不能盲目重试。删除排队任务会阻止其 worker 开始执行；运行中的任务不能删除。任务历史上限只裁剪终态记录，不会驱逐排队或运行中的任务。



## 已安装存储卡片插件入口（3.0 首期）

MovieSync 支持通过 Python 包的 entry point 加载可信存储目标插件，组名为
`moviesync.storage_targets`。每个入口必须指向一个工厂函数，接收单个
`context` 字典并返回 `StorageTargetCard` 实例。当前上下文包含
`config_store` 与 `logger`，插件可按需使用这些共享服务。

例如，插件包的 `pyproject.toml` 可声明：

```toml
[project.entry-points."moviesync.storage_targets"]
example = "my_storage_plugin:create_card"
```

工厂函数形状：

```python
from moviesync.cards import CardManifest, StorageTargetCard

class ExampleStorage(StorageTargetCard):
    manifest = CardManifest(
        id="example-storage",
        name="Example Storage",
        type="storage_target",
        capabilities=(
            "storage.check",
            "storage.resolve_resource",
            "storage.list_files",
            "storage.create_folder",
            "storage.transfer",
        ),
    )

    def resolve_resource(self, resource):
        return {"files": [], "token": None, "error": None}

    def list_files(self, resource):
        return []

    def create_folder(self, name, parent_id="0"):
        return parent_id

    def transfer(self, resource, files, target_id="0"):
        return True, "ok"

def create_card(context):
    return ExampleStorage()
```

存储插件可以通过可信 Python 包 entry point 加载，也可以使用单文件卡片方式。单文件方式必须返回 `StorageTargetCard` 并导出 `create_card(context)`；文件名用于文件管理，卡片 ID 以 `CardManifest.id` 为准，两者可以不同。启动时会隔离单个文件的加载错误；后台上传会先尝试加载，失败则删除本次上传文件。卸载会注销卡片并删除文件，但保留历史配置。插件依赖必须预先安装，单文件代码以 MovieSync 服务权限运行，不应安装来源不明的代码。


## 可运行的独立存储插件示例

仓库提供了完整的外置示例包：`examples/storage-card-plugin/`。它声明
`moviesync.storage_targets` entry point，可用
`python -m pip install ./examples/storage-card-plugin` 安装到 MovieSync 的同一
Python 环境，再重启应用即可自动发现。该示例只演示插件加载、配置与检查，明确不执行真实网盘操作。
CI 中的 `tests/test_example_storage_plugin_package.py` 会将示例包安装到临时目录，检查安装生成的 entry point 元数据，并通过真实的 `StorageTargetManager.load_plugins()` 加载它。


## 后台单文件卡片生命周期

- 上传：管理员在「卡片管理 → 单文件卡片插件」选择可信的 `.py` 文件，系统验证文件名、UTF-8 编码、大小上限（256 KiB）、工厂入口、卡片类型与 Manifest ID 后加载。
- 加载：已有但未加载的文件可通过后台再次加载；启动时会扫描 `/app/data/cards/*.py`。单个文件失败只记录错误，不影响其他卡片和核心平台启动。
- 卸载：后台卸载会注销该卡片并删除对应 `.py` 文件；卡片配置和历史任务记录不会被自动清除。若它是默认存储目标，应先切换默认目标。
- 内置卡片迁移：PanSou 已作为独立单文件卡片放入持久化目录。首次启动时从镜像中的 `card_templates/pansou.py` 初始化；初始化标记会保留，因此用户主动删除后，后续重启不会自动恢复该卡片。
- 信任边界：加载 Python 文件等价于执行代码。后台入口受管理员会话与 CSRF 保护，但这不是代码沙箱；只安装自己审查过的文件。


## Built-in single-file cards

The built-in Telegram resource source, PanSou resource source, Quark storage target, and Douban metadata provider live as independent files under `card_templates/`. On first startup, `CardFilePluginManager` copies each valid template into the persistent `/app/data/cards/` directory and loads it through `create_card(context)`. The core resource, storage, and metadata managers only discover registered cards by their declared interface; they do not instantiate a specific built-in implementation.

- `telegram.py`: Telegram channel search and health checks.
- `pansou.py`: PanSou resource search.
- `quark.py`: Quark link resolution, destination folders, and transfers.
- `douban.py`: Douban metadata lookup and search.
- `vod_source_manager.py`: MacCMS-compatible VOD source management and source health checks; its stable card ID remains `maccms`.

Uninstalling one of these files removes that card from the registry and persistent plugin directory, while other cards and core services continue running. Configuration and history are intentionally retained. Re-upload the same reviewed file to restore it. Shared low-level clients remain platform services injected through the factory context; card-specific behavior stays in the card file.


## 存储卡的可选网盘入口

存储卡可通过 `web_url` 属性声明面向浏览器的网盘主页地址。该字段仅用于任务成功后的“打开网盘”快捷入口，不参与转存、认证或目录解析；不应包含 Cookie、Token、用户名、密码或其他凭据。核心只向前端公开格式有效的 HTTP/HTTPS URL，并使用新标签页和 `noopener noreferrer` 打开。未声明该属性的卡片仍完整支持标准存储接口，只是不显示外部跳转按钮。
