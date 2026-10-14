"""MovieSync 包入口，兼容导出 create_app。"""
def create_app(*args, **kwargs):
    from .app import create_app as factory
    return factory(*args, **kwargs)


__all__ = ["create_app"]
