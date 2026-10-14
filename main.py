"""MovieSync 启动入口：创建应用并注册退出清理。"""
import atexit

from moviesync.app import create_app

app = create_app()
atexit.register(app.extensions["moviesync"]["shutdown"])


if __name__ == "__main__":
    settings = app.extensions["moviesync_settings"]
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
