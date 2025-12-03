import math
import os,datetime
import sys

import cv2,time
import pymysql
from datetime import date as _date
from PyQt5.QtCore import Qt, QDate, QSize, pyqtSignal, QRectF, QThread, QTimer, pyqtSlot
from PyQt5.QtGui import QImageReader, QFont
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QAction, QToolBar, QApplication, QScrollArea, QProgressBar
)

from PyQt5.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QConicalGradient, QIcon, QImage, QTransform
)
from PyQt5.QtWidgets import (
    QWidget, QLabel, QPushButton, QFileDialog, QVBoxLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QFormLayout, QDialog, QDialogButtonBox, QFrame,
    QGridLayout, QGraphicsDropShadowEffect, QListWidget, QListWidgetItem, QDateEdit, QSlider
)
try:
    # 当从项目根目录运行 main.py 时（推荐方式）
    from .road_scene_ultra import RoadSceneAnalyzer, AnalyzerConfig
except ImportError:
    # 当直接在 view 目录里跑 app.py 时（老习惯）
    from road_scene_ultra import RoadSceneAnalyzer, AnalyzerConfig


# ==============================
# 路径 & 打包工具
# ==============================
def is_frozen() -> bool:
    """判断当前是否为 PyInstaller 打包环境"""
    return hasattr(sys, "_MEIPASS")

def resource_path(*relative_parts) -> str:
    """
    读取只读资源（图片/图标/模型等）路径：
    - 开发期：smart_driving/ 根目录
    - 打包后：_MEIPASS 临时目录
    用法：resource_path('image','icons','home.png')
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        # 本文件通常位于 smart_driving/view/ 或 smart_driving/ 下
        base = Path(__file__).resolve().parent.parent
    return str(base.joinpath(*relative_parts))

def writable_root() -> str:
    """
    返回可写根目录：
    - 开发期：smart_driving/image/
    - 打包后：系统用户目录（Windows: %APPDATA%，macOS: ~/Library/Application Support，Linux: ~/.local/share）/smart_driving
    """
    if is_frozen():
        home = Path.home()
        if sys.platform.startswith("win"):
            base = Path(os.getenv("APPDATA", home / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support"
        else:
            base = home / ".local" / "share"
        root = base / "smart_driving"
    else:
        root = Path(resource_path("image"))
    root.mkdir(parents=True, exist_ok=True)
    return str(root)

def resolve_avatar_abs(rel_path: str) -> str:
    """把数据库中的相对头像路径（例如 imgpath/26.png）解析为绝对路径"""
    rel_path = rel_path.replace("\\", "/")
    return str(Path(writable_root()).joinpath(rel_path))

def avatar_rel(uid: int) -> str:
    """统一头像相对路径：imgpath/<uid>.png"""
    return f"imgpath/{uid}.png"

def ensure_avatar_dir():
    """确保头像目录存在"""
    Path(resolve_avatar_abs("imgpath")).mkdir(parents=True, exist_ok=True)

# ==============================
# MySQL 访问（与登录注册保持一致，MD5）
# ==============================
class InlineDB:
    def __init__(self,
                 host=os.environ.get("DB_HOST", "127.0.0.1"),
                 port=int(os.environ.get("DB_PORT", "3306")),
                 user=os.environ.get("DB_USER", "root"),
                 password=os.environ.get("DB_PASS", "123456"),
                 database=os.environ.get("DB_NAME", "ai250822")):
        self.cfg = dict(host=host, port=port, user=user, password=password, database=database,
                        charset="utf8mb4", cursorclass=pymysql.cursors.Cursor)

    def _conn(self):
        return pymysql.connect(**self.cfg)

    def get_user(self, uname):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT uid, uname, imgpath, createtime FROM users WHERE uname=%s", (uname,))
                return cur.fetchone()

    def update_avatar(self, uname, rel_path):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET imgpath=%s WHERE uname=%s", (rel_path, uname))
            conn.commit()

    def check_old_password(self, uname, oldpwd) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE uname=%s AND upwd=MD5(%s)", (uname, oldpwd))
                return cur.fetchone() is not None

    def update_password(self, uname, newpwd):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET upwd=MD5(%s) WHERE uname=%s", (newpwd, uname))
            conn.commit()

# ==============================
# 小工具：头像裁圆（基础版）
# ==============================
def circle_pixmap(pix: QPixmap, size: int = 128) -> QPixmap:
    """把方形头像裁成圆形，带抗锯齿"""
    if pix.isNull():
        out = QPixmap(size, size)
        out.fill(Qt.transparent)
        return out
    scaled = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out

# ==============================
# 用户中心（美化版）
# ==============================
class UserPage(QWidget):
    """
    用户中心（美化＋路径兼容）：
      - 显示头像（圆形/渐变环）、用户名、注册时间
      - 上传头像（DB 存相对：imgpath/<uid>.png；文件写到可写目录）
      - 修改密码（MD5）
    """
    def __init__(self, username, parent=None):
        super().__init__(parent)
        self.username = username
        self.db = InlineDB()
        self.uid = None

        self._apply_theme()
        self._build_ui()
        self._load_user_info()

    # ---------- 主题样式 ----------
    def _apply_theme(self):
        # 使用 [class="..."] 选择器匹配动态属性
        self.setStyleSheet("""
            QWidget { background:#0f1115; color:#e6eaf2; font-size:15px; }
            QLabel[class="title"] { font-size:20px; font-weight:800; letter-spacing:0.3px; }
            QLabel[class="subtitle"] { font-size:13px; color:#9aa4b2; }

            /* 顶部条 */
            QFrame[class="header"] {
                background:#0a0c10; border:1px solid #20242d; border-radius:10px;
            }
            QLabel[class="headerText"] {
                font-size:16px; font-weight:700; padding:8px 12px;
            }

            /* 主卡片 */
            QFrame[class="card"] {
                background:#11151c; border:1px solid #1e2430; border-radius:18px;
            }

            /* 主按钮 / 次按钮 */
            QPushButton[class="primary"] {
                background:#2f6cea; color:#ffffff; border:none; border-radius:10px;
                padding:10px 16px; font-weight:700;
            }
            QPushButton[class="primary"]:hover { background:#3a79ff; }
            QPushButton[class="primary"]:pressed { background:#2962d3; }

            QPushButton[class="ghost"] {
                background:transparent; color:#e6eaf2;
                border:1px solid #2a3140; border-radius:10px; padding:10px 16px;
            }
            QPushButton[class="ghost"]:hover { background:#171c25; }
            QPushButton[class="ghost"]:pressed { background:#141923; }
        """)

    # ---------- 渐变环头像 ----------
    def _circle_with_ring(self, pix: QPixmap, size=120) -> QPixmap:
        base = QPixmap(size, size)
        base.fill(Qt.transparent)
        p = QPainter(base)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 外环渐变
        ring = QPainterPath()
        ring.addEllipse(0, 0, size, size)
        p.setPen(Qt.NoPen)
        grad = QConicalGradient(size/2, size/2, 0)
        grad.setColorAt(0.00, QColor("#3a79ff"))
        grad.setColorAt(0.25, QColor("#60d6ff"))
        grad.setColorAt(0.50, QColor("#7df9d0"))
        grad.setColorAt(0.75, QColor("#60d6ff"))
        grad.setColorAt(1.00, QColor("#3a79ff"))
        p.setBrush(grad)
        p.drawPath(ring)

        # 内圆（头像内容）
        inner = QPixmap(size-8, size-8)
        inner.fill(Qt.transparent)
        ip = QPainter(inner)
        ip.setRenderHint(QPainter.Antialiasing, True)
        mask = QPainterPath()
        mask.addEllipse(0, 0, size-8, size-8)
        ip.setClipPath(mask)
        scaled = pix.scaled(size-8, size-8, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        ip.drawPixmap(0, 0, scaled)
        ip.end()

        p.drawPixmap(4, 4, inner)
        p.end()
        return base

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # 顶部标题条
        header = QFrame()
        header.setProperty("class", "header")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 8, 12, 8)
        title = QLabel("用户中心（User Center）")
        title.setProperty("class", "headerText")
        subtitle = QLabel("Profile • Security • Personalization")
        subtitle.setProperty("class", "subtitle")
        hbox.addWidget(title)
        hbox.addStretch(1)
        hbox.addWidget(subtitle)
        root.addWidget(header)

        # 主卡片
        card = QFrame()
        card.setProperty("class", "card")
        c = QGridLayout(card)
        c.setContentsMargins(20, 20, 20, 20)
        c.setHorizontalSpacing(20)
        c.setVerticalSpacing(12)

        # 左：头像
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(132, 132)
        self.avatar_label.setAlignment(Qt.AlignCenter)
        shadow = QGraphicsDropShadowEffect(self.avatar_label)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.avatar_label.setGraphicsEffect(shadow)
        c.addWidget(self.avatar_label, 0, 0, 3, 1, Qt.AlignTop)

        # 右：用户名/时间
        self.name_label = QLabel("用户名：—")
        self.name_label.setProperty("class", "title")
        self.time_label = QLabel("注册时间：—")
        self.time_label.setProperty("class", "subtitle")
        infoBox = QVBoxLayout()
        infoBox.setSpacing(6)
        infoBox.addWidget(self.name_label)
        infoBox.addWidget(self.time_label)
        infoWrap = QWidget()
        infoWrap.setLayout(infoBox)
        c.addWidget(infoWrap, 0, 1, 1, 2)

        # ===== 使用说明（仅用户中心显示） =====
        self.instruction_label = QLabel(
            "欢迎使用智能座舱系统（Smart Driving Cockpit System）\n"
            "• 主要功能（Main Features）:\n"
            "  1) 疲劳驾驶检测（Fatigue Detection）：眨眼频率、打哈欠、闭眼时长等实时监测；\n"
            "  2) 分心/危险行为监测（Distraction Monitoring）：如看手机、低头、东张西望；\n"
            "  3) 语音与可视化提醒（Voice & Visual Alerts）：发现风险立即播放警报并在界面提示；\n"
            "  4) 证据留存（Evidence Capture）：在摄像头/视频页面可手动截图保存；\n"
            "  5) 本地化运行（On-device）：离线或弱网环境下也能稳定工作。\n"
            "\n"
            "• 使用建议（How to Use）:\n"
            "  - 驾驶前请确保前置摄像头无遮挡、光线适中；\n"
            "  - 打开【车辆/道路/摄像头】后可用底部功能栏快速切换；\n"
            "  - 如出现误报（False Alarm），先检查口罩/墨镜/遮挡等影响识别的因素；\n"
            "  - 若需暂时关闭警报，请扫描右侧二维码联系工程师（进入工程模式）。\n"
            "\n"
            "为保障驾驶安全（Safety First），当系统检测到疲劳驾驶、使用手机等高风险情景时，"
            "将自动播放警报提示驾驶员。若要关闭警报，请扫描右侧二维码联系工程师。"
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setStyleSheet(
            "QLabel { background:#0f1218; border:1px solid #1e2430; border-radius:12px;"
            "padding:12px; font-size:14px; line-height:1.5; }"
        )

        # 右侧二维码（优先项目资源路径，其次可写目录兼容）
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        qr_pix = QPixmap(resource_path("image", "imgpath", "pay.jpg"))
        if qr_pix.isNull():
            # 若打包时被复制到可写目录，也兼容此路径
            alt_path = resolve_avatar_abs("imgpath/pay.jpg")
            qr_pix = QPixmap(alt_path)
        if not qr_pix.isNull():
            self.qr_label.setPixmap(
                qr_pix.scaled(460, 460, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.qr_label.setText("二维码未找到：image/imgpath/pay.jpg")
            self.qr_label.setStyleSheet("color:#ff8a8a;")

        # 放置说明 + 二维码
        c.addWidget(self.instruction_label, 1, 1, 1, 1)
        c.addWidget(self.qr_label, 1, 2, 1, 1, Qt.AlignRight | Qt.AlignTop)

        # 底部按钮（上传头像 / 修改密码）
        self.btn_upload = QPushButton("上传头像  Upload Avatar")
        self.btn_upload.setProperty("class", "primary")
        self.btn_upload.clicked.connect(self.upload_avatar)
        self.btn_pwd = QPushButton("修改密码  Change Password")
        self.btn_pwd.setProperty("class", "ghost")
        self.btn_pwd.clicked.connect(self.change_password)

        btnRow = QHBoxLayout()
        btnRow.setSpacing(12)
        btnRow.addWidget(self.btn_upload)
        btnRow.addWidget(self.btn_pwd)
        btnRow.addStretch(1)
        c.addLayout(btnRow, 2, 1, 1, 2)  # 注意：rowSpan=1, colSpan=2（修正后的签名）

        root.addWidget(card)
        root.addStretch(1)

    # ---------- 数据 ----------
    def _load_user_info(self):
        row = self.db.get_user(self.username)
        if not row:
            QMessageBox.warning(self, "提示", "未找到该用户")
            return
        self.uid, uname, img_rel, createtime = row
        try:
            create_str = createtime.strftime('%Y-%m-%d %H:%M:%S') if createtime else '-'
        except Exception:
            create_str = str(createtime) if createtime else '-'
        self.name_label.setText(f"用户名：{uname}")
        self.time_label.setText(f"注册时间：{create_str}")

        # 显示头像：优先用户头像，否则默认图标
        pix = QPixmap()
        if img_rel:
            abs_path = resolve_avatar_abs(img_rel)
            if os.path.exists(abs_path):
                pix = QPixmap(abs_path)
        if pix.isNull():
            default_path = resource_path("image", "icons", "user_default.png")
            pix = QPixmap(default_path)

        # 渐变环（失败时退化为普通圆形）
        try:
            shown = self._circle_with_ring(pix, 120)
        except Exception:
            shown = circle_pixmap(pix, 120)
        self.avatar_label.setPixmap(shown)

    # ---------- 上传头像 ----------
    def upload_avatar(self):
        if self.uid is None:
            QMessageBox.warning(self, "提示", "用户未初始化")
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择头像", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not f:
            return

        ensure_avatar_dir()
        rel = avatar_rel(self.uid)          # DB：imgpath/<uid>.png
        abs_path = resolve_avatar_abs(rel)  # 可写绝对路径

        pix = QPixmap(f)
        if pix.isNull():
            QMessageBox.warning(self, "提示", "无法读取图片")
            return
        ok = pix.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(abs_path, "PNG")
        if not ok:
            QMessageBox.warning(self, "提示", "保存头像失败")
            return

        self.db.update_avatar(self.username, rel.replace("\\", "/"))
        self._load_user_info()
        QMessageBox.information(self, "成功", "头像已更新！")

    # ---------- 修改密码 ----------
    def change_password(self):
        dlg = PasswordDialog(self.username, self.db, self)
        dlg.exec_()

# ==============================
# 修改密码弹窗（MD5）
# ==============================
class PasswordDialog(QDialog):
    def __init__(self, uname, db, parent=None):
        super().__init__(parent)
        self.db, self.uname = db, uname
        self.setWindowTitle("修改密码")
        self.setModal(True)

        # ======== 窗口大小与字体 ========
        self.resize(500, 260)  # 更大窗口
        font = QFont("Microsoft YaHei", 14)  # 更大更清晰的字体
        self.setFont(font)

        # ======== 表单布局 ========
        form = QFormLayout(self)
        form.setVerticalSpacing(20)  # 调整行距
        form.setLabelAlignment(Qt.AlignRight)

        # ======== 输入框样式 ========
        self.old = QLineEdit()
        self.old.setEchoMode(QLineEdit.Password)
        self.old.setPlaceholderText("当前密码")
        self.old.setMinimumHeight(40)
        self.old.setStyleSheet("""
            QLineEdit {
                padding: 6px;
                border: 1px solid #999;
                border-radius: 6px;
                font-size: 18px;
            }
            QLineEdit:focus {
                border-color: #3a79ff;
            }
        """)

        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.Password)
        self.new.setPlaceholderText("新密码（6-16位）")
        self.new.setMinimumHeight(40)
        self.new.setStyleSheet("""
            QLineEdit {
                padding: 6px;
                border: 1px solid #999;
                border-radius: 6px;
                font-size: 18px;
            }
            QLineEdit:focus {
                border-color: #3a79ff;
            }
        """)

        form.addRow("旧密码：", self.old)
        form.addRow("新密码：", self.new)

        # ======== 按钮 ========
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确认修改")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.button(QDialogButtonBox.Ok).setMinimumHeight(36)
        btns.button(QDialogButtonBox.Cancel).setMinimumHeight(36)
        btns.setStyleSheet("""
            QPushButton {
                font-size: 18px;
                padding: 6px 14px;
                border-radius: 6px;
            }
        """)
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    # ======== 验证与更新逻辑 ========
    def _submit(self):
        old, new = self.old.text().strip(), self.new.text().strip()
        if not old or not new:
            QMessageBox.warning(self, "提示", "请填写完整")
            return
        if len(new) < 6 or len(new) > 16:
            QMessageBox.warning(self, "提示", "新密码长度 6~16 位")
            return
        if not self.db.check_old_password(self.uname, old):
            QMessageBox.warning(self, "提示", "旧密码错误")
            return
        self.db.update_password(self.uname, new)
        QMessageBox.information(self, "成功", "密码已更新")
        self.accept()


# ------------------ 视频播放页面（改动后的完整代码） ------------------
from pathlib import Path
# ====================== 视频播放（页面版，线程安全、不阻塞UI） ======================
class _VideoReader(QThread):
    """
    后台读取视频帧的线程：
    - 发出 BGR ndarray 帧（由页面在 GUI 线程里转 QImage/QPixmap）
    - 支持暂停、改变播放速度、停止
    """
    frame = pyqtSignal(object)   # np.ndarray (BGR)
    ended = pyqtSignal()         # 到达文件末尾或异常

    def __init__(self, path: str, speed: float = 1.0, parent=None):
        super().__init__(parent)
        self.path = path
        self.speed = max(0.25, float(speed))
        self._stop = False
        self._paused = False

    def stop(self):
        self._stop = True

    def set_paused(self, v: bool):
        self._paused = v

    def set_speed(self, s: float):
        self.speed = max(0.25, float(s))

    def run(self):
        cap = None
        try:
            cap = cv2.VideoCapture(self.path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            delay_ms = max(5.0, 1000.0 / fps)

            while not self._stop:
                if self._paused:
                    self.msleep(30)
                    continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                self.frame.emit(frame)
                self.msleep(int(delay_ms / self.speed))
        except Exception:
            pass
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
            self.ended.emit()


class VideoPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#111; color:#eee;")

        # ========= 顶部标题：显示文件名 =========
        self.lab_title = QLabel("（未选择视频）")
        self.lab_title.setStyleSheet("background:#1e1e1e; color:#ffffff; padding:6px 10px; font-size:14px;")
        self.lab_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # ========= 视频画面区 =========
        self.label = QLabel("未开始播放")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background:#000; color:#ddd; font-size:16px;")

        # ========= 底部控制条 =========
        self.btn_play = QPushButton("开始")     # 点击后在“开始/暂停”之间切换
        self.btn_speed05 = QPushButton("0.5×")
        self.btn_speed10 = QPushButton("1.0×")
        self.btn_speed20 = QPushButton("2.0×")
        self.btn_shot = QPushButton("截屏")
        self.btn_back = QPushButton("返回")

        for b in (self.btn_play, self.btn_speed05, self.btn_speed10, self.btn_speed20, self.btn_shot, self.btn_back):
            b.setFixedHeight(30)
            b.setStyleSheet("QPushButton{background:#2b2c30; color:#fff; border:1px solid #555; border-radius:6px; padding:0 12px;} QPushButton:hover{background:#3a3b40;}")

        # 进度（显示即可，不拖动）
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:#222; border:1px solid #444; border-radius:4px;} QProgressBar::chunk{background:#3f7fff;}")

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.addWidget(self.btn_play)
        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("速度"))
        ctrl.addWidget(self.btn_speed05)
        ctrl.addWidget(self.btn_speed10)
        ctrl.addWidget(self.btn_speed20)
        ctrl.addSpacing(10)
        ctrl.addWidget(self.btn_shot)
        ctrl.addStretch(1)
        ctrl.addWidget(self.progress, 3)
        ctrl.addSpacing(10)
        ctrl.addWidget(self.btn_back)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        root.addWidget(self.lab_title, 0)
        root.addWidget(self.label, 1)
        root.addLayout(ctrl)

        # ========= 状态 =========
        self._is_playing = False
        self._duration_ms = None   # 若你的播放器里已有时长，可在 play() 里回填
        self._fps = 25.0                  # NEW: 记录 fps
        self._pos_ms = 0.0                # NEW: 当前位置估计（毫秒）
        self.reader = None                # NEW: 后台读帧线程
        self._speed = 1.0                 # NEW: 当前倍速
        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._tick_progress)
        self._progress_timer.start(200)  # 200ms 更新一次进度
        self._last_frame = None  # 最新一帧（BGR），专供截图用

        # ========= 事件 =========
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_speed05.clicked.connect(lambda: self._set_speed(0.5))
        self.btn_speed10.clicked.connect(lambda: self._set_speed(1.0))
        self.btn_speed20.clicked.connect(lambda: self._set_speed(2.0))
        self.btn_shot.clicked.connect(self._capture_and_prompt)
        self.btn_back.clicked.connect(self._go_back_to_browser)

    # ========= 播放入口（CHG：真正启动线程） =========
    def play(self, path: str):
        import os, cv2
        self.lab_title.setText(os.path.basename(path) if path else "（未选择视频）")
        if (not path) or (not os.path.exists(path)):
            QMessageBox.warning(self, "提示", f"找不到视频文件：\n{path}")
            return

        self.stop(quiet=True)  # 先停旧的

        # 预取 fps/时长（仅用于进度估计）
        try:
            cap = cv2.VideoCapture(path)
            self._fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            self._duration_ms = int(frames / max(1e-6, self._fps) * 1000) if frames > 0 else None
        except Exception:
            self._fps, self._duration_ms = 25.0, None

        # 启动后台读帧线程
        self.reader = _VideoReader(path=path, speed=self._speed)
        self.reader.frame.connect(self._on_frame)   # NEW: 接收一帧并显示
        self.reader.ended.connect(self._on_reader_end)
        self.reader.start()

        self._is_playing = True
        self.btn_play.setText("暂停")
        self.progress.setValue(0)
        self.label.setText("")  # 清“未开始播放”
        self._pos_ms = 0.0

    # ========= 收帧并显示（NEW） =========
    @pyqtSlot(object)
    def _on_frame(self, frame):
        # frame 是 BGR
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(pix)

        # 估计当前位置（按 fps 累加）
        self._pos_ms += (1000.0 / max(1e-6, self._fps)) / max(1e-6, self._speed)
        self._last_frame = frame.copy()

    # ========= 线程结束（NEW） =========
    @pyqtSlot()
    def _on_reader_end(self):
        # 播放结束或异常
        self._is_playing = False
        self.btn_play.setText("开始")
        # 不强制清屏，保留最后一帧
        # 可根据需要：self.progress.setValue(1000) if self._duration_ms else 0


    # ========= 播放/暂停 =========
    def _toggle_play(self):
        # 这里调用你现有的开始/暂停控制
        # 例如：self.reader.pause() / self.reader.resume() 或 self.timer.stop()/start()
        self._is_playing = not self._is_playing
        self.btn_play.setText("暂停" if self._is_playing else "开始")
        try:
            if hasattr(self, "reader"):   # 示例：如果你有 reader 线程
                if self._is_playing and hasattr(self.reader, "resume"): self.reader.resume()
                if not self._is_playing and hasattr(self.reader, "pause"): self.reader.pause()
        except Exception:
            pass

    # ========= 倍速（CHG：调用线程接口） =========
    def _set_speed(self, s: float):
        self._speed = float(s)
        try:
            if self.reader is not None:
                self.reader.set_speed(self._speed)
        except Exception:
            pass

    # ========= 截图并提示 =========
    def _capture_and_prompt(self):
        if self._last_frame is None:
            QMessageBox.warning(self, "提示", "暂无可保存的画面（视频未开始或还未收到帧）。")
            return

        save_dir = resource_path("image", "captures")
        try:
            os.makedirs(save_dir, exist_ok=True)
            fn = os.path.join(save_dir, f"shot_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
            # _last_frame 是 BGR，cv2.imwrite 直接可用
            ok = cv2.imwrite(fn, self._last_frame)
            if ok:
                QMessageBox.information(self, "提示", f"已保存当前视频帧：\n{fn}")
            else:
                QMessageBox.warning(self, "提示", "截图失败（写入文件失败）。")
        except Exception as e:
            QMessageBox.warning(self, "提示", f"截图失败：{e}")

    # ========= 返回列表 =========
    def _go_back_to_browser(self):
        self.stop(quiet=True)
        main = QApplication.activeWindow()
        if hasattr(main, "video_browser"):
            main.stack.setCurrentWidget(main.video_browser)
        elif hasattr(main, "page_main"):
            main.stack.setCurrentWidget(main.page_main)

    # ========= 进度条刷新（CHG：用累加的 _pos_ms） =========
    def _tick_progress(self):
        if self._duration_ms:
            v = max(0, min(1000, int(self._pos_ms / self._duration_ms * 1000)))
            self.progress.setValue(v)
        else:
            self.progress.setValue(0)

    # ========= stop（NEW：提供给 closeEvent / 切页调用） =========
    def stop(self, quiet=False):
        try:
            if self.reader is not None:
                self.reader.stop()
                self.reader.wait(300)
        except Exception:
            pass
        self.reader = None
        self._is_playing = False
        self._pos_ms = 0.0
        self.progress.setValue(0)
        self.btn_play.setText("开始")
        if not quiet:
            self.label.setText("未开始播放")


class Video1DetectPage(QWidget):
    """
    仅用于“视频1”的检测播放页：
    - 播放本地视频并叠加：车辆距离(BEV)、红绿灯/停牌识别
    - 去掉倍速相关控件
    - 保留 capture_frame() 供底部截图按钮使用
    """
    def __init__(self, default_path: str = None, parent=None):
        super().__init__(parent)
        self._path = default_path
        self._reader: _VideoReader = None
        self._current_frame = None
        self._playing = False

        # === Ultralytics + BEV 分析器 ===
        self.analyzer = RoadSceneAnalyzer(
            AnalyzerConfig(model_path="yolov8n.pt")  # 可换 yolov8s.pt 等
        )

        self.setStyleSheet("background:#18191d; color:#e6e6e6;")

        # 画面区域
        self.video_label = QLabel("未开始播放", alignment=Qt.AlignCenter)
        self.video_label.setStyleSheet("background:#000; color:#fff; font-size:14px;")
        self.video_label.setMinimumSize(640, 360)

        # 控件区（仅 播放/打开）
        self.btn_play = QPushButton("播放")
        self.btn_play.setFixedHeight(34)
        self.btn_play.clicked.connect(self._toggle_play)

        self.btn_open = QPushButton("打开文件…")
        self.btn_open.setFixedHeight(34)
        self.btn_open.clicked.connect(self._open_file)

        ctl = QHBoxLayout()
        ctl.addWidget(self.btn_play)
        ctl.addStretch(1)
        ctl.addWidget(self.btn_open)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.video_label, 1)
        layout.addLayout(ctl)

    # ============ 对外API ============
    def play(self, path: str):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "提示", f"找不到视频文件：\n{path}")
            return
        self.stop()
        self._path = path
        self.video_label.setText("正在加载…")
        # 线程启动（不需要速度参数）
        self._reader = _VideoReader(self._path, parent=self)
        self._reader.frame.connect(self._on_frame)
        self._reader.ended.connect(self._on_ended)
        self._reader.start()
        self._playing = True
        self.btn_play.setText("暂停")

    def stop(self):
        self._playing = False
        try:
            if self._reader is not None:
                self._reader.stop()
                self._reader.wait(800)
        except Exception:
            pass
        self._reader = None
        self._current_frame = None
        self.btn_play.setText("播放")

    def capture_frame(self, save_dir: str) -> str:
        """保存当前叠加后的画面为PNG，返回路径；供 FunctionBar 截图按钮使用"""
        if self._current_frame is None:
            QMessageBox.information(self, "提示", "没有可截取的画面。")
            return ""
        try:
            os.makedirs(save_dir, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            fname = f"video1_detect_{now}.png"
            path = os.path.join(save_dir, fname)
            rgb = cv2.cvtColor(self._current_frame, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            qimg = QImage(rgb.data, w, h, w*3, QImage.Format_RGB888)
            pm = QPixmap.fromImage(qimg)
            pm.save(path, "PNG")
            return path
        except Exception as e:
            QMessageBox.warning(self, "提示", f"保存失败：{e}")
            return ""

    # ============ 槽函数 ============
    def _on_frame(self, bgr):
        """
        每帧调用分析器，得到 overlay（叠加框与文字），
        并把 overlay 同步到 QLabel。_current_frame 保存 overlay 以便截图。
        """
        try:
            overlay, info = self.analyzer.update(bgr)
            self._current_frame = overlay  # 保存叠加后的画面
            rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch*w, QImage.Format_RGB888)
            pm = QPixmap.fromImage(qimg).scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.video_label.setPixmap(pm)
        except Exception as e:
            print("Video1DetectPage 分析错误:", e)

    def _on_ended(self):
        self._playing = False
        self.btn_play.setText("播放")
        # 播放结束时保留最后一帧（如需自动重播可在此处调用 self.play(self._path)）

    # ============ UI操作 ============
    def _toggle_play(self):
        if self._reader is None:
            if self._path and os.path.exists(self._path):
                self.play(self._path)
            else:
                self._open_file()
            return
        if self._playing:
            self._playing = False
            self.btn_play.setText("播放")
            self._reader.set_paused(True)
        else:
            self._playing = True
            self.btn_play.setText("暂停")
            self._reader.set_paused(False)

    def _open_file(self):
        start_dir = resource_path("videos") if 'resource_path' in globals() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频", start_dir,
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.flv *.wmv);;所有文件 (*.*)"
        )
        if path:
            self.play(path)

    # 自适应窗口：下次到帧时按 label 尺寸缩放
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._current_frame is not None:
            # 触发一次重绘：把缓存帧重新塞回管道
            self._on_frame(self._current_frame)

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)


# ------------------ 视频库（预览 + 点击播放） ------------------
# ================== 按日期查询的视频库 ==================
class VideoBrowserPage(QWidget):
    """
    视频浏览页：显示缩略图 + 日期筛选 + 双击播放
    """
    def __init__(self, target_page: QWidget, parent=None):
        super().__init__(parent)
        self.target_page = target_page
        self.video_dir = Path(resource_path("videos"))
        self.video_dir.mkdir(parents=True, exist_ok=True)

        # === 顶部筛选栏 ===
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(10)

        label_date = QLabel("选择日期：")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())

        btn_query = QPushButton("查询")
        btn_query.setFixedWidth(80)
        btn_query.setStyleSheet("background:#3a79ff;color:white;border:none;border-radius:6px;")

        btn_open = QPushButton("打开目录")
        btn_open.setFixedWidth(90)
        btn_open.setStyleSheet("background:#3a79ff;color:white;border:none;border-radius:6px;")

        bl.addWidget(label_date)
        bl.addWidget(self.date_edit)
        bl.addWidget(btn_query)
        bl.addStretch(1)
        bl.addWidget(btn_open)

        # === 缩略图区域 ===
        self.grid = QGridLayout()
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(10)
        container = QWidget()
        container.setLayout(self.grid)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setStyleSheet("background:#f5f5f5;")

        # === 主布局 ===
        layout = QVBoxLayout(self)
        layout.addWidget(bar)
        layout.addWidget(scroll, 1)

        btn_query.clicked.connect(self.refresh_thumbnails)
        btn_open.clicked.connect(self.open_folder)

        # 首次加载当天
        self.refresh_thumbnails()

    def refresh_thumbnails(self):
        """加载符合日期的视频缩略图"""
        import datetime, os, cv2
        d = self.date_edit.date().toPyDate()
        prefix = d.strftime("%Y-%m-%d") + "_"
        exts = (".mp4", ".avi", ".mkv", ".mov")

        # 清空旧缩略图
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()

        files = []
        for p in sorted(self.video_dir.glob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue

            # 文件名日期或修改日期匹配
            by_name = p.name.startswith(prefix)
            mdate = datetime.date.fromtimestamp(p.stat().st_mtime)
            by_mtime = (mdate == d)

            if by_name or by_mtime:
                files.append(p)

        if not files:
            self.grid.addWidget(QLabel("没有找到该日期的视频。"))
            return

        # 生成缩略图
        for i, p in enumerate(files):
            thumb = self.make_thumbnail_widget(p)
            row, col = divmod(i, 4)
            self.grid.addWidget(thumb, row, col)

    def make_thumbnail_widget(self, path: Path):
        """生成单个缩略图卡片"""
        import cv2
        cap = cv2.VideoCapture(str(path))
        ret, frame = cap.read()
        cap.release()

        label = QLabel()
        label.setFixedSize(260, 160)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("background:#000; border-radius:6px;")
        if ret:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _ = frame.shape
            qimg = QImage(frame.data, w, h, 3 * w, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg).scaled(260, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label.setPixmap(pix)
        else:
            label.setText("无缩略图")
            label.setStyleSheet("color:white;background:#000;")

        title = QLabel(path.name)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:12px;color:#222;")

        card = QVBoxLayout()
        card.addWidget(label)
        card.addWidget(title)

        widget = QWidget()
        widget.setLayout(card)
        widget.setCursor(Qt.PointingHandCursor)
        widget.mouseDoubleClickEvent = lambda e, p=path: self.play_video(p)
        return widget

    def play_video(self, path: Path):
        """双击播放"""
        if hasattr(self.target_page, "play"):
            self.target_page.play(str(path))
            main = QApplication.activeWindow()
            if hasattr(main, "stack") and hasattr(main, "page_video2"):
                main.stack.setCurrentWidget(main.page_video2)

    def open_folder(self):
        """打开 videos 文件夹"""
        import os, sys
        d = str(self.video_dir.resolve())
        if sys.platform.startswith("win"): os.startfile(d)
        elif sys.platform == "darwin": os.system(f'open "{d}"')
        else: os.system(f'xdg-open "{d}"')


# --------------------图片浏览---------------------------------------
# 小工具：取文件的“修改日期”（本地时区按天）
def _file_mdate(path: str):
    try:
        t = os.path.getmtime(path)
        return _date.fromtimestamp(t)
    except Exception:
        return None


class ImageViewerDialog(QDialog):
    """弹出式图片预览，可缩放/旋转/平移，带工具条（修复 _act_fit 初始化顺序）"""
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.setModal(True)
        # 顶层对话框：不要覆盖式 flags
        self.setWindowFlag(Qt.Dialog, True)
        self.setWindowFlag(Qt.WindowTitleHint, True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.CustomizeWindowHint, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.resize(1000, 700)

        # ====== 场景/视图 ======
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing
        )
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        self._min_scale, self._max_scale = 0.05, 40.0
        self._current_scale = 1.0
        self._rotation = 0.0
        self._fit_mode = True  # 初次适配

        self.pix_item = QGraphicsPixmapItem()
        self.pix_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pix_item)

        # ====== 工具条======
        tb = QToolBar(self)
        tb.setMovable(False)
        act_zoom_in  = QAction("放大", self, shortcut="+", triggered=lambda: self._zoom(1.15))
        act_zoom_out = QAction("缩小", self, shortcut="-", triggered=lambda: self._zoom(1/1.15))
        act_fit      = QAction("适配窗口", self, checkable=True, shortcut="F", triggered=self._toggle_fit)
        act_actual   = QAction("实际大小", self, shortcut="1", triggered=self._actual_size)
        act_rot_l    = QAction("左旋90°", self, shortcut="L", triggered=lambda: self._rotate(-90))
        act_rot_r    = QAction("右旋90°", self, shortcut="R", triggered=lambda: self._rotate(+90))
        act_reset    = QAction("复位", self, shortcut="0", triggered=self._reset)
        for a in (act_zoom_in, act_zoom_out, act_fit, act_actual, act_rot_l, act_rot_r, act_reset):
            tb.addAction(a)
        self._act_fit = act_fit
        self._act_fit.setChecked(True)

        # ====== 布局 ======
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(tb, 0)
        lay.addWidget(self.view, 1)

        # ====== 事件代理（滚轮缩放 / 中键拖拽） ======
        self.view.wheelEvent = self._wheelEvent_proxy
        self.view.mousePressEvent = self._mousePressEvent_proxy
        self.view.mouseReleaseEvent = self._mouseReleaseEvent_proxy

        # 最后再设置图像
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._set_pixmap(pixmap)

    # ---------- 图像与变换 ----------
    def _set_pixmap(self, pm: QPixmap):
        self.pix_item.setPixmap(pm)
        self.pix_item.setOffset(-pm.width() / 2, -pm.height() / 2)  # 图像中心在原点
        self._rotation = 0.0
        self.pix_item.setRotation(self._rotation)
        self.view.resetTransform()
        self._current_scale = 1.0
        self._fit_mode = True
        if hasattr(self, "_act_fit") and self._act_fit is not None:
            self._act_fit.setChecked(True)
        self._fit_to_window()

    def _zoom(self, factor: float):
        if self.pix_item.pixmap().isNull():
            return
        self._fit_mode = False
        if hasattr(self, "_act_fit") and self._act_fit is not None:
            self._act_fit.setChecked(False)
        new_scale = self._current_scale * factor
        if not (self._min_scale <= new_scale <= self._max_scale):
            return
        self.view.scale(factor, factor)
        self._current_scale = new_scale

    def _rotate(self, delta_deg: float):
        if self.pix_item.pixmap().isNull():
            return
        self._rotation = (self._rotation + delta_deg) % 360
        self.pix_item.setRotation(self._rotation)
        if self._fit_mode:
            self._fit_to_window()

    def _reset(self):
        if self.pix_item.pixmap().isNull():
            return
        self._rotation = 0.0
        self.pix_item.setRotation(self._rotation)
        self.view.resetTransform()
        self._current_scale = 1.0
        self._fit_mode = True
        if hasattr(self, "_act_fit") and self._act_fit is not None:
            self._act_fit.setChecked(True)
        self._fit_to_window()

    def _actual_size(self):
        if self.pix_item.pixmap().isNull():
            return
        self._fit_mode = False
        if hasattr(self, "_act_fit") and self._act_fit is not None:
            self._act_fit.setChecked(False)
        self.view.resetTransform()
        self._current_scale = 1.0

    def _toggle_fit(self, checked: bool):
        self._fit_mode = checked
        if checked:
            self._fit_to_window()

    def _fit_to_window(self):
        pm = self.pix_item.pixmap()
        if pm.isNull():
            return
        br = QTransform().rotate(self._rotation).mapRect(QRectF(0, 0, pm.width(), pm.height()))
        vw = max(1.0, self.view.viewport().width())
        vh = max(1.0, self.view.viewport().height())
        s = min(vw / br.width(), vh / br.height()) * 0.98
        self.view.resetTransform()
        self._current_scale = 1.0
        if s > 0:
            self.view.scale(s, s)
            self._current_scale = s

    # ---------- 交互代理 ----------
    def _wheelEvent_proxy(self, e):
        if e.angleDelta().y() == 0:
            return
        self._zoom(1.15 if e.angleDelta().y() > 0 else 1/1.15)

    def _mousePressEvent_proxy(self, e):
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            fake = type(e)(e.type(), e.localPos(), e.screenPos(),
                           Qt.LeftButton, e.buttons() | Qt.LeftButton, e.modifiers())
            QGraphicsView.mousePressEvent(self.view, fake)
        else:
            QGraphicsView.mousePressEvent(self.view, e)

    def _mouseReleaseEvent_proxy(self, e):
        QGraphicsView.mouseReleaseEvent(self.view, e)
        self.view.setDragMode(QGraphicsView.NoDrag)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit_mode:
            self._fit_to_window()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape,):
            self.reject()
        else:
            super().keyPressEvent(e)


class ImagePage(QWidget):
    """
    图片浏览（分页版）
    - 顶部：日期选择 + 查询
    - 中部：缩略图网格（自适应每页数量）
    - 底部：分页控件
    - 双击缩略图进入放大预览
    """
    def __init__(self, parent=None, image_dir="captures"):
        super().__init__(parent)
        self.image_dir = image_dir
        self.all_files, self.filtered = [], []
        self.thumb_w, self.thumb_h = 240, 180
        self.page_rows = 3
        self.thumb_gap = 10
        self.page_size = 12
        self.page = 1

        # 顶部 —— 日期 + 查询
        self.date_edit = QDateEdit(calendarPopup=True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.btn_query = QPushButton("查询")
        self.btn_query.setStyleSheet("background:#3a79ff;color:#fff;border-radius:8px;font-weight:600;height:34px;")

        top = QHBoxLayout()
        top.addWidget(QLabel("选择日期："))
        top.addWidget(self.date_edit)
        top.addWidget(self.btn_query)
        top.addStretch(1)

        # 中部 —— 缩略图网格
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWrapping(True)
        self.grid.setSpacing(self.thumb_gap)
        self.grid.setStyleSheet("QListWidget{background:#dcdcdc; border:1px solid #999; }")
        self.grid.setIconSize(QSize(self.thumb_w, self.thumb_h))

        # 底部 —— 分页控件
        self.btn_prev = QPushButton("上一页")
        self.btn_next = QPushButton("下一页")
        for b in (self.btn_prev, self.btn_next):
            b.setFixedHeight(32)
            b.setStyleSheet("background:#2f6cea;color:#fff;border-radius:8px;")
        self.page_info = QLabel("第 0 / 0 页")
        self.page_edit = QLineEdit()
        self.page_edit.setFixedWidth(60)
        self.page_edit.setPlaceholderText("页码")
        self.btn_go = QPushButton("跳转")
        self.btn_go.setFixedHeight(32)
        self.btn_go.setStyleSheet("background:#3a79ff;color:#fff;border-radius:8px;")

        pager = QHBoxLayout()
        pager.addWidget(self.btn_prev)
        pager.addWidget(self.btn_next)
        pager.addSpacing(12)
        pager.addWidget(self.page_info)
        pager.addStretch(1)
        pager.addWidget(QLabel("跳转到："))
        pager.addWidget(self.page_edit)
        pager.addWidget(self.btn_go)

        # 总布局
        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.grid, 1)
        v.addLayout(pager)

        # 事件
        self.btn_query.clicked.connect(self.on_query)
        self.btn_prev.clicked.connect(self.on_prev)
        self.btn_next.clicked.connect(self.on_next)
        self.btn_go.clicked.connect(self.on_jump)
        self.grid.itemDoubleClicked.connect(self.on_open_viewer)

        # 自适应每页数
        self.page_size = self._calc_page_size()

    def _calc_page_size(self):
        gw = max(1, self.grid.viewport().width())
        cell_w = self.thumb_w + self.thumb_gap * 2 + 12
        cols = max(2, gw // cell_w)
        return int(cols) * int(self.page_rows)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        new_size = self._calc_page_size()
        if new_size != self.page_size:
            self.page_size = new_size
            if self.filtered:
                self._render_page()

    # 进入页面时调用
    def load_from_dir(self, dir_path=None):
        if dir_path is not None:
            self.image_dir = dir_path
        self.grid.clear()

        if not os.path.isdir(self.image_dir):
            self.grid.addItem(QListWidgetItem("图片目录不存在：" + self.image_dir))
            self.page_info.setText("第 0 / 0 页")
            return

        exts = (".jpg", ".jpeg", ".png", ".bmp")
        files = [os.path.join(self.image_dir, f) for f in os.listdir(self.image_dir) if f.lower().endswith(exts)]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        MAX_SCAN = 2000
        if len(files) > MAX_SCAN:
            files = files[:MAX_SCAN]

        self.all_files = files
        self.filtered = self.all_files
        self.page = 1

        if not self.filtered:
            self.grid.addItem(QListWidgetItem("未找到任何图片。"))
            self.page_info.setText("第 0 / 0 页")
            return

        self.page_size = self._calc_page_size()
        self._render_page()

    def _show_tip(self, text):
        self.grid.clear()
        self.grid.addItem(QListWidgetItem(text))
        self.page_info.setText("第 0 / 0 页")

    # 查询（按日期过滤）
    def on_query(self):
        if not self.all_files:
            self._show_tip("目录为空或尚未加载。")
            return
        target = self.date_edit.date().toPyDate()
        self.filtered = [p for p in self.all_files if _file_mdate(p) == target]
        self.page = 1
        if not self.filtered:
            self._show_tip(f"所选日期（{target}）无图片。")
            return
        self.page_size = self._calc_page_size()
        self._render_page()

    # 渲染当前页
    def _render_page(self):
        total = len(self.filtered)
        pages = max(1, math.ceil(total / self.page_size))
        self.page = max(1, min(self.page, pages))
        self.page_info.setText(f"第 {self.page} / {pages} 页")

        self.grid.clear()
        start = (self.page - 1) * self.page_size
        end = min(total, start + self.page_size)

        THUMB_W, THUMB_H = self.thumb_w, self.thumb_h

        for path in self.filtered[start:end]:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(THUMB_W, THUMB_H))
            img = reader.read()
            if img.isNull():
                continue
            icon = QPixmap.fromImage(img)

            it = QListWidgetItem(QIcon(icon), os.path.basename(path))
            it.setFlags(it.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            it.setData(Qt.UserRole, str(path))
            self.grid.addItem(it)

        self.btn_prev.setEnabled(self.page > 1)
        self.btn_next.setEnabled(self.page < pages)

    def on_prev(self):
        if self.page > 1:
            self.page -= 1
            self._render_page()

    def on_next(self):
        pages = max(1, math.ceil(len(self.filtered) / self.page_size))
        if self.page < pages:
            self.page += 1
            self._render_page()

    def on_jump(self):
        text = self.page_edit.text().strip()
        if not text.isdigit():
            QMessageBox.information(self, "提示", "请输入合法的页码（正整数）。")
            return
        p = int(text)
        pages = max(1, math.ceil(len(self.filtered) / self.page_size))
        if 1 <= p <= pages:
            self.page = p
            self._render_page()
        else:
            QMessageBox.information(self, "提示", f"页码范围：1 ~ {pages}")

    # 双击放大
    def on_open_viewer(self, item: QListWidgetItem = None):
        try:
            if item is None:
                item = self.grid.currentItem()
            if item is None:
                QMessageBox.information(self, "提示", "没有选中任何图片项。")
                return

            path = item.data(Qt.UserRole)
            if not path:
                QMessageBox.information(self, "提示", "该项未保存图片路径。")
                return
            path = str(path)
            if not os.path.exists(path):
                QMessageBox.information(self, "提示", f"文件不存在：\n{path}")
                return

            reader = QImageReader(path)
            reader.setAutoTransform(True)
            img = reader.read()
            if img.isNull():
                QMessageBox.warning(self, "提示", "无法加载图片。")
                return

            MAX_SIDE = 6000
            if img.width() > MAX_SIDE or img.height() > MAX_SIDE:
                img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)

            pix = QPixmap.fromImage(img)
            dlg = ImageViewerDialog(pix, self)
            dlg.resize(900, 600)
            dlg.exec_()  # PyQt5: exec_；若为 PyQt6 请改为 exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开失败：\n{e}")

    def showEvent(self, e):
        super().showEvent(e)
        new_size = self._calc_page_size()
        if new_size != self.page_size:
            self.page_size = new_size
            if self.filtered:
                self._render_page()


# ------------------ 底部 7 个功能键组件 ------------------
class FunctionBar(QWidget):
    """
    底部统一功能条（无论当前右侧是哪个页面，都显示这一条）

    按钮顺序（从左到右）：
    1. openCamera        打开摄像头
    2. playVideo1        播放视频 1
    3. screenshot        截图（仅在 1 / 2 时有效）
    4. closeCurrent      关闭当前功能，回到主界面
    5. playVideo2        播放视频 2
    6. showImages        查看图片
    7. showUser          用户界面
    """
    openCamera = pyqtSignal()
    playVideo1 = pyqtSignal()
    screenshot = pyqtSignal()
    closeCurrent = pyqtSignal()
    playVideo2 = pyqtSignal()
    showImages = pyqtSignal()
    showUser = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setStyleSheet("background-color:#171717;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(160, 8, 160, 12)
        layout.setSpacing(18)
        layout.addStretch(1)

        # 按钮工厂
        def make_btn(text, icon_name=None):
            btn = QPushButton()
            if icon_name:
                pix = QPixmap(resource_path("image", "icons", icon_name))
                btn.setIcon(QIcon(pix))
                btn.setIconSize(QSize(40, 40))
            btn.setText(text)
            btn.setStyleSheet("""
                QPushButton {
                    background: #edeff5;
                    border-radius: 18px;
                    border: none;
                    padding: 6px 10px;
                }
                QPushButton:hover {
                    background: #34353b;
                    color:white;
                }
            """)
            btn.setFixedSize(130, 70)
            return btn

        # 1~7 个按钮
        self.btn_cam  = make_btn("", "Car.png")
        self.btn_v1   = make_btn("", "road.png")
        self.btn_cap  = make_btn("", "camera.png")
        self.btn_cls  = make_btn("", "home.png")
        self.btn_v2   = make_btn("", "video.png")
        self.btn_img  = make_btn("", "photo.png")
        self.btn_user = make_btn("", "people.png")

        for b in [self.btn_cam, self.btn_v1, self.btn_cap,
                  self.btn_cls, self.btn_v2, self.btn_img, self.btn_user]:
            layout.addWidget(b)
        layout.addStretch(1)

        # 信号连接
        self.btn_cam.clicked.connect(self.openCamera.emit)
        self.btn_v1.clicked.connect(self.playVideo1.emit)
        self.btn_cap.clicked.connect(self.screenshot.emit)
        self.btn_cls.clicked.connect(self.closeCurrent.emit)
        self.btn_v2.clicked.connect(self.playVideo2.emit)
        self.btn_img.clicked.connect(self.showImages.emit)
        self.btn_user.clicked.connect(self.showUser.emit)


# ------------------ 管理当前功能状态的帮助类（可选） ------------------
class FunctionManager:
    """
    底部 1~7 键调度器
    """
    MODE_IDLE   = 0
    MODE_CAMERA = 1
    MODE_VIDEO1 = 2
    MODE_VIDEO2 = 3

    def __init__(self, main_window, func_bar: FunctionBar,
                 video1_page: 'Video1DetectPage', video2_page: 'VideoPage',
                 image_page: 'ImagePage', user_page: 'UserPage',
                 video1_path="drive.mp4",
                 video2_path="",
                 capture_dir="captures"):
        import os
        self.os = os

        self.w = main_window
        self.bar = func_bar

        self.video1_page = video1_page
        self.video2_page = video2_page
        self.image_page = image_page
        self.user_page = user_page

        # 统一成绝对路径；允许传 ""（无默认）
        self.video1_path = self._abs(video1_path)
        self.video2_path = self._abs(video2_path)
        self.capture_dir = self._abs(capture_dir)

        self.mode = self.MODE_IDLE

        # 连接底部栏信号
        self.bar.openCamera.connect(self.do_open_camera)
        self.bar.playVideo1.connect(self.do_play_video1)
        self.bar.screenshot.connect(self.do_screenshot)
        self.bar.closeCurrent.connect(self.do_close_current)
        self.bar.playVideo2.connect(self.do_play_video2)   # ← 确保存在该方法
        self.bar.showImages.connect(self.do_show_images)
        self.bar.showUser.connect(self.do_show_user)

    # ---------- 工具 ----------
    def _abs(self, p: str) -> str:
        """把相对路径转为 resource_path 的绝对路径；传空串原样返回"""
        if not p:
            return ""
        if self.os.path.isabs(p):
            return p
        # 允许 "a/b/c.mp4" 这种写法
        parts = p.replace("\\", "/").split("/")
        try:
            return resource_path(*parts)
        except Exception:
            return p  # 兜底

    def _stop_all_video(self):
        try:
            self.video1_page.stop()
        except Exception:
            pass
        try:
            self.video2_page.stop()
        except Exception:
            pass

    # ---------- 1~7 按键 ----------
    def do_open_camera(self):
        self._stop_all_video()
        if self.w.stack.currentWidget() is not self.w.page_cam:
            self.w.stack.setCurrentWidget(self.w.page_cam)
        self.w.page_cam.start_camera()
        self.mode = self.MODE_CAMERA

    def do_play_video1(self):
        self._stop_all_video()
        self.w.page_cam.stop_camera()
        self.w.stack.setCurrentWidget(self.video1_page)
        # 无默认则弹窗提示，而不是传空串
        if not self.video1_path or not self.os.path.exists(self.video1_path):
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self.w, "提示", "视频1未配置或文件不存在，请先在视频库/打开文件中选择。")
            return
        self.video1_page.play(self.video1_path)
        self.mode = self.MODE_VIDEO1

    def do_play_video2(self):
        """
        打开‘视频2’：有默认且存在 -> 直接播放；
        否则如果主窗体有 video_browser -> 进入视频库；
        再不行 -> 切到播放页并触发“打开文件…”
        """
        self._stop_all_video()
        self.w.page_cam.stop_camera()

        # 1) 默认文件存在 -> 直接播
        if self.video2_path and self.os.path.exists(self.video2_path):
            self.w.stack.setCurrentWidget(self.video2_page)
            self.video2_page.play(self.video2_path)   # play() 内部也会做 exists 检查:contentReference[oaicite:1]{index=1}
            self.mode = self.MODE_VIDEO2
            return

        # 2) 进视频库（如果你在 MainWindow 里创建了 video_browser）
        if hasattr(self.w, "video_browser") and self.w.video_browser is not None:
            self.w.stack.setCurrentWidget(self.w.video_browser)
            self.mode = self.MODE_IDLE
            return

        # 3) 兜底：切到播放页并弹“打开文件…”
        from PyQt5.QtWidgets import QMessageBox
        self.w.stack.setCurrentWidget(self.video2_page)
        try:
            self.video2_page._open_file()  # 内部会弹 QFileDialog
        except Exception:
            QMessageBox.warning(self.w, "提示", "请点击右下角“打开文件…”选择一个视频。")

    def do_screenshot(self):
        if self.mode == self.MODE_CAMERA:
            self.w.page_cam.take_photo()
        elif self.mode == self.MODE_VIDEO1:
            path = self.video1_page.capture_frame(self.capture_dir)
            if path:
                print("已保存视频截图:", path)
        else:
            print("当前模式不支持截图（仅摄像头/视频1）。")

    def do_close_current(self):
        self.w.page_cam.stop_camera()
        self._stop_all_video()
        self.w.stack.setCurrentWidget(self.w.page_main)
        self.mode = self.MODE_IDLE

    def do_show_images(self):
        self.w.page_cam.stop_camera()
        self._stop_all_video()
        self.image_page.load_from_dir(self.capture_dir)
        self.w.stack.setCurrentWidget(self.image_page)
        self.mode = self.MODE_IDLE

    def do_show_user(self):
        self.w.page_cam.stop_camera()
        self._stop_all_video()
        self.w.stack.setCurrentWidget(self.user_page)
        self.mode = self.MODE_IDLE

