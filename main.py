import os
import sys
from pathlib import Path

# ----------------------------
# 解析命令行参数（简单手写即可）
# ----------------------------
argv = sys.argv[1:]
skip_user = None
scale_mode = "pix"  # 默认策略：只高清像素，不放大布局

i = 0
while i < len(argv):
    a = argv[i]
    if a in ("--user", "-u") and i + 1 < len(argv):
        skip_user = argv[i + 1]
        i += 2
    elif a == "--scale" and i + 1 < len(argv):
        scale_mode = argv[i + 1].lower().strip()
        i += 2
    else:
        i += 1

# ---------------------------------
# 设置 Qt 缩放策略（需在 QApplication 前）
# ---------------------------------
def apply_scale(mode: str):
    """
    none : 完全禁用自动缩放（最不容易“变大”）
    pix  : 仅启用高清位图（图片清晰，不放大控件）
    full : 启用自动缩放 + 高清位图（若项目内部也做了 dpr 乘法，可能双重放大）
    """
    # 先清理潜在的环境变量影响
    for k in ("QT_AUTO_SCREEN_SCALE_FACTOR", "QT_SCALE_FACTOR", "QT_ENABLE_HIGHDPI_SCALING"):
        if k in os.environ:
            os.environ.pop(k)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    if mode == "none":
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        # 不设置任何 Qt Attribute
    elif mode == "pix":
        # 仅高清像素
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    elif mode == "full":
        # 全量高分屏（可能导致“变大”）
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    else:
        # 未知值回退为默认 pix
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

# ---------------------------------
# 捕获未处理异常，打印到控制台
# ---------------------------------
def _install_excepthook():
    def _hook(exctype, value, tb):
        import traceback
        traceback.print_exception(exctype, value, tb)
        # 交还给默认钩子，避免 Qt 吃掉异常
        sys.__excepthook__(exctype, value, tb)
    sys.excepthook = _hook

# ---------------------------------
# 准备模块搜索路径（保险起见把 root 和 view 都塞进 sys.path）
# ---------------------------------
ROOT = Path(__file__).resolve().parent
VIEW = ROOT / "view"
for p in (str(ROOT), str(VIEW)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------
# 兜底一些运行必需的环境变量（比如 Google Maps Key）
# ---------------------------------
os.environ.setdefault("GOOGLE_MAPS_KEY", "DUMMY_KEY")  # 没 key 也能先跑 UI

# ---------------------------------
# 入口函数
# ---------------------------------
def run() -> int:
    _install_excepthook()

    # 在创建 QApplication 之前设置缩放策略
    apply_scale(scale_mode)

    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("Smart Driving")

    # ===== 封装后续 UI 启动逻辑（与你原逻辑一致）:contentReference[oaicite:1]{index=1}=====
    def _start_ui():
        try:
            # 你的主窗口：MainWindow(username: str)
            from view.app import MainWindow
        except Exception as e:
            print("[FATAL] 导入 view.app 失败：", repr(e))
            return 1

        try:
            # 登录窗口：LoginWindow，需设置 login_success_callback
            from view.load_win import LoginWindow
        except Exception as e:
            print("[FATAL] 导入 view.load_win 失败：", repr(e))
            return 1

        # 开发/演示：跳过登录:contentReference[oaicite:2]{index=2}
        if skip_user:
            w = MainWindow(skip_user)
            w.show()
            return 0

        # 正常流程：先登录，成功后进入主窗体:contentReference[oaicite:3]{index=3}
        login = LoginWindow()

        def on_login_success(username: str):
            w = MainWindow(username)
            w.show()
            login.close()

        login.login_success_callback = on_login_success
        login.resize(760, 680)
        login.show()
        return 0

    # ===== 开机动画（OpenCV 解码，不用 QMediaPlayer）=====
    try:
        from view.splash_video import SplashVideoCV  # 见下说明：需新增该文件
        splash_path = str((ROOT / "view" / "splash.mp4").resolve())  # 绝对路径
        if os.path.exists(splash_path):
            splash = SplashVideoCV(splash_path, next_callback=_start_ui)
            splash.show()

            # 保险：最长 15s 后一定进入 UI，避免坏帧阻塞
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(15000, getattr(splash, "_finish"))
        else:
            print("[INFO] 未找到开机动画：", splash_path, "，直接进入 UI")
            _start_ui()
    except Exception as e:
        print("[WARN] 开机动画异常：", repr(e), "，直接进入 UI")
        _start_ui()

    return app.exec_()


# ---------------------------------
# 程序入口
# ---------------------------------
if __name__ == "__main__":
    sys.exit(run())
