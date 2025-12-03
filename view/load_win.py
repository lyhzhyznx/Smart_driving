# -*- coding: utf-8 -*-
import sys, os, random, hashlib
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap, QIcon, QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QHBoxLayout, QVBoxLayout, QGridLayout, QFrame, QMessageBox, QDialog,
    QSizePolicy, QSpacerItem
)
from PyQt5.QtCore import QRegularExpression
from PyQt5.QtGui import QRegularExpressionValidator
from pathlib import Path
import sys

def resource_path(*parts) -> str:
    """
    返回资源的绝对路径：
    - 打包后：使用 PyInstaller 的临时目录 _MEIPASS
    - 开发时：以项目根（view 的上一级 smart_driving）为 base
    """
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parents[1]  # smart_driving/
    return str(base.joinpath(*parts))


# ----------------- 内联 MySQL 逻辑（不依赖 DB_util） -----------------
import pymysql


import os, pymysql

class InlineDB2:
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

    def db_user_login(self, acc: str, pwd: str):
        try:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    sql = ("SELECT uid, uname, upwd, createtime, imgpath, ustate "
                           "FROM users WHERE uname=%s AND upwd=MD5(%s) AND ustate=1")
                    cur.execute(sql, (acc, pwd))
                    rows = cur.fetchall()
                    return list(rows) if rows else 0
            finally:
                conn.close()
        except Exception as e:
            print("[DB] login error:", repr(e))
            return 0

    def db_user_reg(self, acc: str, pwd: str):
        try:
            conn = self._conn()  # 1) 先拿连接
            try:
                with conn.cursor() as cur:  # 2) 只对游标用 with
                    sql = ("INSERT INTO users(uname, upwd, createtime, imgpath, ustate) "
                           "VALUES(%s, MD5(%s), NOW(), %s, 1)")
                    cur.execute(sql, (acc, pwd, None))
                conn.commit()  # 3) 在连接关闭之前 commit
                return 1
            finally:
                conn.close()  # 4) 确保连接被关闭
        except pymysql.err.IntegrityError as e:
            print("[DB] duplicate user:", repr(e))
            return 0
        except Exception as e:
            print("[DB] reg error:", repr(e))
            return 0


TIPS_IMG = resource_path("image", "icons", "tips.jpg")
REG_IMG = resource_path("image", "icons", "register.png")

# ----------------- 主题样式 -----------------
LIGHT_QSS = """
QWidget { background:#f5f6fa; color:#2b2b2b; }
QLineEdit {
    background:#fff; border:1px solid #cfd3dc; border-radius:8px;
    padding:6px 10px; font-size:16px;
}
QLineEdit[readOnly="true"] { background:#f4f5f7; font-weight:bold; }
QPushButton {
    background:#4062ff; color:#fff; border:none; border-radius:10px;
    min-height:40px; padding:0 16px; font-size:16px;
}
QPushButton:hover { filter:brightness(1.06); }
QLabel[hint="true"] { color:#999999; }
"""

DARK_QSS = """
QWidget { background:#1f2430; color:#e6e6e6; }
QLineEdit {
    background:#2a3140; border:1px solid #3b455a; border-radius:8px;
    padding:6px 10px; font-size:16px; color:#e6e6e6;
}
QLineEdit[readOnly="true"] { background:#232a39; font-weight:bold; }
QPushButton {
    background:#4062ff; color:#fff; border:none; border-radius:10px;
    min-height:40px; padding:0 16px; font-size:16px;
}
QPushButton:hover { filter:brightness(1.08); }
QLabel[hint="true"] { color:#a8b0c0; }
"""


# ----------------- 主题混入：提供 apply_theme / current_theme -----------------
class ThemeMixin:
    current_theme = "light"

    def apply_theme(self, target):
        target.setStyleSheet(LIGHT_QSS if self.current_theme == "light" else DARK_QSS)

    def toggle_theme(self, *targets):
        self.current_theme = "dark" if self.current_theme == "light" else "light"
        for t in targets:
            if hasattr(t, "setStyleSheet"):
                self.apply_theme(t)


# =========================================================
# 登录窗口
# =========================================================
class LoginWindow(QWidget, ThemeMixin):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("疲劳驾驶检测系统")
        self.setWindowIcon(QIcon(REG_IMG))
        self._captcha_text = ""

        self.build_ui()
        self.refresh_captcha()
        self.apply_theme(self)  # 默认套主题
        self.db = InlineDB2()  # 使用内联的 MySQL 逻辑

    # ---------- UI ----------
    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶部工具条（右上角：主题切换）
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_theme = QPushButton("深/浅 主题")
        self.btn_theme.clicked.connect(lambda: self.toggle_theme(self, getattr(self, "_register_dlg", None)))
        bar.addWidget(self.btn_theme)
        root.addLayout(bar)

        # ===== 顶部 banner（自适应缩放） =====
        self.banner = QLabel()
        self.banner.setMinimumHeight(160)
        self.banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.banner.setAlignment(Qt.AlignCenter)
        self._tips_pix = QPixmap(TIPS_IMG)
        self.banner.setPixmap(self._tips_pix.scaled(self.banner.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        root.addWidget(self.banner)

        # ===== 中部内容区（网格） =====
        grid = QGridLayout()
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        # 左下角插图（自适应）
        self.left_pic = QLabel()
        self.left_pic.setAlignment(Qt.AlignCenter | Qt.AlignBottom)
        self.left_pic.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._reg_pix = QPixmap(REG_IMG)
        self.left_pic.setPixmap(self._reg_pix.scaled(QSize(160, 160), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        grid.addWidget(self.left_pic, 0, 0, 6, 1)

        title = QLabel("疲劳驾驶检测系统")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        grid.addWidget(title, 0, 1, 1, 3, Qt.AlignLeft | Qt.AlignVCenter)

        # 用户名
        lab_user = QLabel("用户名")
        lab_user.setFont(QFont("Microsoft YaHei", 18))
        grid.addWidget(lab_user, 1, 1)
        self.edit_user = QLineEdit()
        self.edit_user.setPlaceholderText("长度 6-10 的数字或英文字母")
        self.edit_user.setMaxLength(10)
        self.edit_user.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grid.addWidget(self.edit_user, 1, 2, 1, 2)

        # 密码
        # 密码
        lab_pwd = QLabel("密　码")
        lab_pwd.setFont(QFont("Microsoft YaHei", 18))
        grid.addWidget(lab_pwd, 2, 1)
        self.edit_pwd = QLineEdit()
        self.edit_pwd.setPlaceholderText("长度 6-10 位数字或英文字母")
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        self.edit_pwd.setMaxLength(10)
        grid.addWidget(self.edit_pwd, 2, 2)

        self.btn_toggle = QPushButton("显/隐")
        self.btn_toggle.clicked.connect(self.toggle_password)
        self.btn_toggle.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid.addWidget(self.btn_toggle, 2, 3)

        # 验证码
        lab_cap = QLabel("验证码")
        lab_cap.setFont(QFont("Microsoft YaHei", 18))
        grid.addWidget(lab_cap, 3, 1)

        self.edit_cap = QLineEdit()
        self.edit_cap.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        grid.addWidget(self.edit_cap, 3, 2)

        self.cap_box = QLineEdit()
        self.cap_box.setReadOnly(True)
        self.cap_box.setAlignment(Qt.AlignCenter)
        self.cap_box.setProperty("hint", True)  # 用于主题里浅色文字
        size_pol = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.cap_box.setSizePolicy(size_pol)
        grid.addWidget(self.cap_box, 3, 3)

        # 登录/注册按钮行（自适应）
        btn_row = QHBoxLayout()
        self.btn_login = QPushButton("登录")
        self.btn_register = QPushButton("注册")

        # 关键：让按钮水平可拉伸（Expanding）
        pol = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_login.setSizePolicy(pol)
        self.btn_register.setSizePolicy(pol)

        # 按钮最小高度（保持美观）
        self.btn_login.setMinimumHeight(48)
        self.btn_register.setMinimumHeight(48)

        # 两个按钮之间保持间距
        btn_row.setSpacing(20)

        # 两端留白 + 两个按钮平分宽度
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_login)
        btn_row.addWidget(self.btn_register)
        btn_row.addStretch(1)

        # 让两个按钮在 layout 内平分宽度
        btn_row.setStretchFactor(self.btn_login, 1)
        btn_row.setStretchFactor(self.btn_register, 1)

        root.addLayout(grid)
        root.addLayout(btn_row)
        root.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # 事件
        self.btn_login.clicked.connect(self.on_login_clicked)
        self.btn_register.clicked.connect(self.open_register)

    # ---------- 行为 ----------
    def resizeEvent(self, e):
        """跟随窗口缩放 banner 与左图"""
        if not self._tips_pix.isNull():
            self.banner.setPixmap(
                self._tips_pix.scaled(self.banner.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if not self._reg_pix.isNull():
            # 左下图给个合适的目标高度
            h = max(120, min(220, self.height() // 4))
            self.left_pic.setPixmap(self._reg_pix.scaled(QSize(h, h), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(e)

    def toggle_password(self):
        self.edit_pwd.setEchoMode(
            QLineEdit.Normal if self.edit_pwd.echoMode() == QLineEdit.Password else QLineEdit.Password)

    def refresh_captcha(self):
        pool = "abcdefghjkmnpqrstuvwxyz23456789"
        self._captcha_text = "".join(random.choice(pool) for _ in range(4))
        color = random.choice(["#ff4d4f", "#52c41a", "#1677ff", "#fa8c16", "#722ed1"])
        self.cap_box.setText(self._captcha_text)
        # 每次刷新给验证码不同颜色
        self.cap_box.setStyleSheet(f"QLineEdit{{ font-size:20px; font-weight:bold; color:{color}; }}")

    def _ok(self, s):  # 6-10 位字母或数字
        return 6 <= len(s) <= 10 and s.isalnum()

    def on_login_clicked(self):
        acc = self.edit_user.text().strip()
        pwd = self.edit_pwd.text()

        rows = self.db.db_user_login(acc, pwd)  # -> list of tuples or 0
        if rows == 0:
            QMessageBox.warning(self, "登录失败", "账号或密码错误，或账户被禁用")
            return

        first_row = rows[0]  # (uid, uname, upwd, createtime, imgpath, ustate)
        username = first_row[1] if len(first_row) > 1 else acc

        if hasattr(self, "login_success_callback") and callable(self.login_success_callback):
            self.login_success_callback(username)
        self.close()

    def open_register(self):
        self._register_dlg = RegisterWindow(theme=self.current_theme, parent=self)
        self._register_dlg.exec_()

    def fill_username(self, username: str):
        self.edit_user.setText(username)
        self.edit_pwd.clear()
        self.edit_user.setFocus()


# =========================================================
# 注册对话框（自适应 + 主题）
# =========================================================
class RegisterWindow(QDialog, ThemeMixin):
    def __init__(self, theme="light", parent=None):
        super().__init__(parent)
        self.current_theme = theme
        self.setWindowTitle("用户注册")
        self._captcha = ""
        self._tips_pix = QPixmap(TIPS_IMG)

        self._build_ui()
        self._refresh_captcha()
        self.apply_theme(self)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 顶部工具条：主题同步切换
        bar = QHBoxLayout()
        bar.addStretch(1)
        btn_theme = QPushButton("深/浅 主题")
        btn_theme.clicked.connect(lambda: self.toggle_theme(self))
        bar.addWidget(btn_theme)
        root.addLayout(bar)

        # 自适应 banner
        self.banner = QLabel()
        self.banner.setMinimumHeight(140)
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.banner.setPixmap(self._tips_pix.scaled(self.banner.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        root.addWidget(self.banner)

        form = QGridLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)

        title = QLabel("注册新用户")
        title.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        form.addWidget(title, 0, 0, 1, 4)

        # 账号
        form.addWidget(QLabel("账  号"), 1, 0)
        self.edit_acc = QLineEdit()
        self._apply_regex(self.edit_acc)
        self._set_placeholder(self.edit_acc, "英文大小写、数字，长度 6~10")
        self.edit_acc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        form.addWidget(self.edit_acc, 1, 1, 1, 3)

        # 昵称
        form.addWidget(QLabel("昵  称"), 2, 0)
        self.edit_nick = QLineEdit()
        self._set_placeholder(self.edit_nick, "中文/英文/数字，长度 ≤ 10")
        self.edit_nick.setMaxLength(10)
        form.addWidget(self.edit_nick, 2, 1, 1, 3)

        # 密码 + 显隐
        form.addWidget(QLabel("密  码"), 3, 0)
        self.edit_pwd = QLineEdit()
        self._apply_regex(self.edit_pwd)
        self._set_placeholder(self.edit_pwd, "英文大小写、数字，长度 6-10")
        self.edit_pwd.setEchoMode(QLineEdit.Password)
        form.addWidget(self.edit_pwd, 3, 1, 1, 2)
        btn_t1 = QPushButton("显/隐");
        btn_t1.clicked.connect(lambda: self._toggle(self.edit_pwd))
        form.addWidget(btn_t1, 3, 3)

        # 确认密码 + 显隐
        form.addWidget(QLabel("确认密码"), 4, 0)
        self.edit_pwd2 = QLineEdit()
        self._apply_regex(self.edit_pwd2)
        self._set_placeholder(self.edit_pwd2, "再次输入密码")
        self.edit_pwd2.setEchoMode(QLineEdit.Password)
        form.addWidget(self.edit_pwd2, 4, 1, 1, 2)
        btn_t2 = QPushButton("显/隐");
        btn_t2.clicked.connect(lambda: self._toggle(self.edit_pwd2))
        form.addWidget(btn_t2, 4, 3)

        # 验证码：输入框 + 展示框 + 换一张
        form.addWidget(QLabel("验 证 码"), 5, 0)
        self.edit_cap = QLineEdit()
        form.addWidget(self.edit_cap, 5, 1)
        self.cap_box = QLineEdit();
        self.cap_box.setReadOnly(True);
        self.cap_box.setAlignment(Qt.AlignCenter)
        self.cap_box.setProperty("hint", True)
        form.addWidget(self.cap_box, 5, 2)
        self.btn_refresh = QPushButton("换一张")
        self.btn_refresh.clicked.connect(self._refresh_captcha)
        form.addWidget(self.btn_refresh, 5, 3)

        # 按钮
        row = QHBoxLayout()
        self.btn_ok = QPushButton("确 定")
        self.btn_cancel = QPushButton("取 消")
        row.addStretch(1);
        row.addWidget(self.btn_ok);
        row.addWidget(self.btn_cancel);
        row.addStretch(1)

        root.addLayout(form)
        root.addLayout(row)
        root.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.btn_ok.clicked.connect(self._on_submit)
        self.btn_cancel.clicked.connect(self.close)

    def resizeEvent(self, e):
        if not self._tips_pix.isNull():
            self.banner.setPixmap(
                self._tips_pix.scaled(self.banner.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(e)

    # helpers
    def _apply_regex(self, edit: QLineEdit):
        rx = QRegularExpression(r"^[A-Za-z0-9]{6,10}$")
        edit.setValidator(QRegularExpressionValidator(rx))

    def _set_placeholder(self, edit: QLineEdit, text: str):
        edit.setPlaceholderText(text)

    def _toggle(self, edit: QLineEdit):
        edit.setEchoMode(QLineEdit.Normal if edit.echoMode() == QLineEdit.Password else QLineEdit.Password)

    def _refresh_captcha(self):
        pool = "abcdefghjkmnpqrstuvwxyz23456789"
        self._captcha = "".join(random.choice(pool) for _ in range(4))
        color = random.choice(["#ff4d4f", "#52c41a", "#1677ff", "#fa8c16", "#722ed1"])
        self.cap_box.setText(self._captcha)
        self.cap_box.setStyleSheet(f"QLineEdit{{ font-size:20px; font-weight:bold; color:{color}; }}")

    def _on_submit(self):
        acc = self.edit_acc.text().strip()
        nick = self.edit_nick.text().strip()
        pwd1 = self.edit_pwd.text().strip()
        pwd2 = self.edit_pwd2.text().strip()
        cap = self.edit_cap.text().strip()

        self.setStyleSheet(self.styleSheet() + """
            QMessageBox QPushButton {
                min-width: 80px; min-height: 32px; font-size:16px;
                background:#4062ff; color:#fff; border-radius:8px;
            }
        """)

        if not acc or not pwd1:
            QMessageBox.warning(self, "提示", "账号或密码不能为空");
            return
        if pwd1 != pwd2:
            QMessageBox.warning(self, "提示", "两次输入的密码不一致");
            return
        if cap.lower() != self._captcha.lower():
            QMessageBox.information(self, "提示", "验证码错误，已刷新")
            self._refresh_captcha();
            self.edit_cap.clear();
            return

        # 使用父窗口已创建的 InlineDB（没有就临时建一个）
        parent = self.parent()
        db = getattr(parent, "db", None) if parent else None
        if db is None:
            db = InlineDB2()

        ok = db.db_user_reg(acc, pwd1)  # 成功=1 失败=0
        if ok == 1:
            if self.parent() and hasattr(self.parent(), "fill_username"):
                self.parent().fill_username(acc)
            QMessageBox.information(self, "注册成功", f"用户：{acc}\n昵称：{nick or '-'}")
            self.close()
        else:
            QMessageBox.warning(self, "注册失败", "用户名已存在，或数据库异常")


# # ----------------- 直接运行（仅调试） -----------------
# if __name__ == "__main__":
#     db = InlineDB()
#
#     QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
#     QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
#
#     app = QApplication(sys.argv)
#     w = LoginWindow()
#     w.resize(760, 680)  # 允许拉伸，自适应
#     w.show()
#     sys.exit(app.exec_())
