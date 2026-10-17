# MovieSync 卡片开发规范（3.0 阶段 D）

MovieSync 的卡片是独立能力单元。核心程序负责统一配置、调度、状态和生命周期；卡片负责具体资源源、网盘、影视资料或通知平台的实现。

## 1. 支持的卡片类型

| `type` | 用途 | 建议的能力标识 |
| --- | --- | --- |
| `resource_source` | 搜索和发现资源 | `resource.search`、`resource.health_check` |
| `storage_target` | 解析分享、列出文件、创建目录和转存 | `storage.check`、`storage.resolve_resource`、`storage.transfer` |
| `metadata_provider` | 影视资料和搜索 | `metadata.list`、`metadata.search`、`metadata.detail` |
| `filename_processor` | 解析文件名和季集信息 | `filename.parse`、`filename.normalize` |
| `notification` | 发送通知 | `notification.send` |
| `playback_provider` | 提供播放相关解析能力 | `playback.list_files`、`playback.resolve` |
| `automation` | 可选自动化能力 | 按功能定义 |
| `custom` | 其他不属于上述类型的能力 | 使用有命名空间的自定义能力 |

能力标识应使用小写字母、数字、点、下划线或连字符；`storage.accepts.quark_share` 这样的能力用于声明目标卡片支持的资源类型，`storage.accepts.*` 表示卡片明确声明兼容多种资源类型。不要仅为了通过选择流程而声明实际不支持的能力。

## 2. 文件和工厂约定

- 每张单文件卡片放在持久化目录 `/app/data/cards/`，文件名使用小写字母、数字、下划线和连字符，例如 `my_storage.py`。
- 文件名（不含 `.py`）必须与 manifest 的卡片 ID 一致。
- 模块必须提供 `create_card(context)`，并返回对应类型的 MovieSync `Card` 子类实例。
- 使用 Python 包入口点安装时，入口点名称必须与 manifest 的 `id` 完全一致；例如 `example-storage = "moviesync_example_storage:create_card"` 对应 `id="example-storage"`。名称不一致的存储卡片会被拒绝注册，避免入口别名造成卡片身份混乱。
- `context` 是依赖注入入口。只读取卡片需要的依赖，不要假设可访问其他卡片的内部对象。
- 卡片应提供 `close()` 清理会话、线程和其他资源；清理失败不得影响其他卡片。
- 一个卡片加载失败不应阻断 MovieSync 或其他卡片启动。

最小示例：

```python
from moviesync.cards import CardManifest, ResourceSourceCard


class ExampleSource(ResourceSourceCard):
    manifest = CardManifest(
        id="example_source",
        name="Example Source",
        version="1.0.0",
        type="resource_source",
        description="示例资源来源",
        capabilities=("resource.search", "resource.health_check"),
    )

    def __init__(self, context):
        self.logger = context.get("logger")

    def search(self, movie, config):
        return []

    def check(self, config):
        return {"status": "healthy", "message": "示例卡片已加载"}


def create_card(context):
    return ExampleSource(context)
```

## 3. Manifest 和版本规则

- `id`：稳定且唯一；发布后不要随意更改。
- `version`：使用语义化版本号，例如 `1.2.0`、`2.0.0-rc.1`。
- `type`：使用上表中的固定类型。
- `capabilities`：列出卡片实际实现的能力；不得重复或使用无效标识。
- `config_fields`：声明配置表单字段。字段 `key` 使用小写字母开头的英文标识；`required` 和 `secret` 必须是布尔值。
- `config_version`：配置结构版本，独立于卡片代码的 `version`。只有配置结构改变时才递增。
- `migrate_config(config, from_version)`：需要升级旧配置的卡片应显式实现迁移。迁移失败时应保留旧配置并报告错误，不能把未迁移的数据直接标记为新版本。
- 不要在 manifest、错误消息或日志中放 Cookie、Token、密码或其他凭据。

示例配置字段：

```python
config_fields=(
    {
        "key": "cookie",
        "label": "平台 Cookie",
        "type": "password",
        "secret": True,
        "required": True,
        "description": "只在本地保存，不在界面回显。",
    },
)
```

## 4. 配置与兼容性

- 通过传入的 `config_store` 访问本卡片配置；不要直接编辑其他卡片的配置。
- 旧配置迁移必须尽量保持原有行为；新增字段应提供安全默认值。
- 不能读取配置时应失败关闭，尤其不能静默切换到另一家存储目标。
- 检查连接失败、未配置、权限不足、限流和未知结果应返回不同且易懂的状态。
- 转存请求超时不代表失败或成功；结果不确定时应要求核实，避免盲目重复提交。
- 任何外部输入都应进行长度、类型和格式校验；错误响应不得包含凭据。

## 5. 安装和安全边界

单文件 Python 卡片是**可执行代码，不是沙箱内的声明文件**。安装不可信卡片可能执行任意代码并读取 MovieSync 进程有权访问的数据。

- 只安装自己编写或来自可信来源、经过检查的卡片。
- 不要将 Cookie、API Token 或密码写进卡片源码；通过卡片配置界面填写。
- 当前卡片安装采用本地单文件方式；在线卡片目录、自动更新和自动执行远程代码不属于本阶段范围。
- 卸载前先检查默认存储目标和追剧订阅是否仍引用该卡片。
- 卡片升级应保留管理员自定义文件；只有确认未被修改的内置文件才可以自动替换。

## 6. 提交前验证

新增或修改卡片时，至少检查：

1. manifest 的 ID、版本、类型、能力和配置字段符合规范。
2. 缺失可选依赖或单张卡片初始化失败时，主程序仍可启动。
3. 启用、停用、配置保存、连接检查和卸载行为符合预期。
4. 敏感配置不会在 API 响应、错误或日志中明文泄露。
5. 配置版本迁移失败会保留原数据。
6. 运行仓库 CI：pytest、Ruff、前端语法检查和 Docker 构建。

MovieSync 核心不应直接判断卡片 ID 来决定平台特有行为；优先依赖卡片类型、能力声明和统一接口。


## 7. 配置安全契约（阶段 D）

所有卡片（包括第三方卡片）的配置接口都必须遵守同一组安全边界：

- 只向管理界面返回 manifest 声明的字段，不把内部配置键一并返回。
- secret 字段及名称中含 Cookie、Token、密码、API Key、Authorization、Credential 或 Private Key 等敏感标记的字段，读取时不得回显原值；仅返回是否已有保存值。
- 对密钥字段提交空字符串、纯空白或 null 表示保留已有凭据，避免用户保存普通设置时意外清空 Cookie。
- 未声明的字段必须拒绝写入；配置校验失败时不能部分覆盖已有设置。
- 测试应使用虚构凭据，并检查响应正文、保存结果和失败路径，不能只验证 HTTP 状态码。

这些规则是平台级契约，不应要求每张卡片重复实现。新增配置 API 或改变字段脱敏逻辑时，必须更新通用契约测试。
