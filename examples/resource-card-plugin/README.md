# MovieSync 示例资源卡片

这是一个可独立安装的 Python 包，用来验证 MovieSync 3.0 的资源来源插件入口。它只演示加载、Manifest、搜索接口与健康检查，**不会连接真实资源服务，也不会返回可转存链接**。

## 安装

在 MovieSync 所使用的同一 Python 环境中执行：

```bash
python -m pip install ./examples/resource-card-plugin
```

然后重启 MovieSync。应用启动时会发现 `moviesync.resource_sources` entry point，并将卡片注册到统一卡片注册表。

## 开发自己的资源卡片

复制这个包并修改 `pyproject.toml` 中的 entry point 名称与工厂路径。卡片 ID 必须唯一，且与 Manifest 的 ID 一致。实现 `search(movie, config)` 和 `check(config)`，返回 MovieSync 约定的资源字段；资源源只负责发现资源，不能假设某个存储目标一定能解析它。

Python 插件会以 MovieSync 进程权限执行，只安装自己审查过的可信代码。插件依赖必须预先安装到同一环境。
