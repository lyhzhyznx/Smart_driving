# ---- Standard Library 标准库 ----
import os
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime
# ---- Third-Party 第三方库 ----
import requests
import cv2

# 静默导入 pygame（抑制控制台输出）
_devnull = open(os.devnull, "w")
_stdout_backup = sys.stdout
try:
    sys.stdout = _devnull
    import pygame  # noqa: F401
finally:
    sys.stdout = _stdout_backup
    _devnull.close()

# mutagen 可选导入（缺失时不报错）
try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

# ---- PyQt5 ----
from PyQt5.QtCore import (
    Qt, QTimer, QDateTime, QSize, QUrl
)
from PyQt5.QtGui import (
    QFont, QPixmap, QImage, QIcon, QPainter, QPen, QColor, QFontMetrics
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QStackedWidget, QGridLayout,
    QLineEdit, QComboBox, QListWidget, QListWidgetItem,
    QMessageBox, QSlider, QFileDialog, QSpacerItem, QSizePolicy
)
from PyQt5.QtMultimedia import (
    QSoundEffect
)

# ---- Project Modules 项目内模块 ----
from view.functions import (
    FunctionBar, FunctionManager, VideoPage, ImagePage, UserPage, Video1DetectPage, VideoBrowserPage
)
from view.driving_detect import driving_detect
from view.functions import resource_path

def get_weather_kl():
    # 吉隆坡的经纬度：3.1390, 101.6869
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=3.1390&longitude=101.6869"
        "&current=temperature_2m,relative_humidity_2m,weather_code"
        "&timezone=auto"
    )
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json().get("current", {})
        temp = data.get("temperature_2m")
        rh = data.get("relative_humidity_2m")
        code = data.get("weather_code", 0)
        desc = {
            0: "晴", 1: "多云间晴", 2: "多云", 3: "阴",
            61: "小雨", 63: "中雨", 65: "大雨",
            71: "小雪", 73: "中雪", 75: "大雪",
            95: "雷阵雨"
        }.get(code, "未知")
        return f"{desc} {temp:.1f}°C · 湿度 {rh}%"
    except Exception:
        return "天气：获取失败"


class CameraPage(QWidget):
    """
    摄像头界面：实时视频 + 截图按钮 + YOLOv7 检测 + 每30s自动保存视频片段 + 右上角倒计时 + 异常报警
    Camera with live preview, capture, YOLOv7 overlay, rolling 30s MP4 segments,
    top-right countdown, and abnormal-behavior beeps.
    """

    def __init__(self):
        super().__init__()

        # ====== 画面区域 ======
        self.label = QLabel("摄像头未开启")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background:#000; color:white; font-size:20px;")

        # 顶部工具条（预留占位）
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addStretch(1)

        # 主布局
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(top_bar, 0)
        v.addWidget(self.label, 1)

        # ====== 运行态 ======
        self.cap = None
        self.timer = QTimer(self)          # 刷帧计时器（~30fps）
        self.timer.timeout.connect(self.update_frame)

        self.detector = None               # driving_detect 实例
        self.detect_enabled = True         # 是否启用检测
        self.last_bgr_shown = None         # 上次显示的 BGR 帧（含框）

        # ====== 自动保存 & 倒计时 ======
        self.autosave_interval = 30        # 秒
        self.seconds_left = self.autosave_interval
        self.autosave_timer = QTimer(self) # 每秒走一个刻度，用于倒计时和定时保存
        self.autosave_timer.timeout.connect(self._tick_autosave)

        # —— 视频保存（30s切片）——
        self.video_dir = Path(resource_path('videos'))
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.writer = None                 # cv2.VideoWriter
        self.segment_start_ts = None       # 当前片段开始时间戳
        self.target_fps = 30.0             # 目标帧率（fallback）
        self.frame_size = None             # (w, h) for VideoWriter

        # 图片保存目录（用于手动截图）
        self.capture_dir = Path(resource_path('image', 'captures'))
        self.capture_dir.mkdir(parents=True, exist_ok=True)

        # ====== 截图“已截图”提示（Toast）=====
        self._shot_msg_until = 0.0         # 提示截止时间戳（time.time()）
        self._shot_msg_text = "已截图"       # 提示文案
        self._shot_msg_ms = 1200           # 提示显示时长（毫秒）

        # ====== 异常报警设置 ======
        # 你的类别清单：Normal / CloseEyes / Yawn / Phone / LookRightAndLeft
        # 将以下四类视为异常（不包含 Normal）
        self.abnormal_set = {
            "closeeyes", "yawn", "phone", "lookrightandleft"
        }
        self.alarm_cooldown_secs = 3.0     # 报警冷却（秒）
        self._last_alarm_ts = 0.0
        self._alarm_enabled = True
        self._alarm = QSoundEffect(self)
        self._alarm.setSource(QUrl.fromLocalFile(resource_path("music", "Alarm.wav")))
        self._alarm.setVolume(1.0)

        # ====== 公共控制 ======
    def start_camera(self):
        """打开摄像头 + 懒加载检测器 + 开始计时器"""
        if self.cap is None:
            # Windows下 CAP_DSHOW 可减少初始化卡顿；其他平台会忽略
            try:
                self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            except Exception:
                self.cap = cv2.VideoCapture(0)
            # 如需固定分辨率可放开：
            # self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            # self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        if not self.cap or not self.cap.isOpened():
            self.label.setText("无法打开摄像头")
            return

        if self.detector is None:
            try:
                self.detector = driving_detect()
            except Exception as e:
                print("Detector init failed:", e)
                self.detector = None

        # 读取摄像头 FPS；拿不到则回退 30
        try:
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps < 5 or fps > 120:
                fps = 30.0
            self.target_fps = float(fps)
        except Exception:
            self.target_fps = 30.0

        if not self.timer.isActive():
            self.timer.start(30)  # 约 33 fps
        if not self.autosave_timer.isActive():
            self.seconds_left = self.autosave_interval
            self.autosave_timer.start(1000)  # 每秒

        self.segment_start_ts = time.time()

    def stop_camera(self):
        """停止摄像头与计时器"""
        if self.timer.isActive():
            self.timer.stop()
        if self.autosave_timer.isActive():
            self.autosave_timer.stop()
        self._close_segment()
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    # ====== 定时器回调 ======
    def _tick_autosave(self):
        """每秒钟调用：更新倒计时；归零时轮转视频片段"""
        self.seconds_left -= 1
        if self.seconds_left <= 0:
            # 轮转视频片段（新片段在下一帧自动打开）
            self._close_segment()
            self.seconds_left = self.autosave_interval

    # ====== 片段启动/关闭 ======
    def _open_new_segment(self, first_frame_bgr):
        """开始一个新的视频片段"""
        h, w = first_frame_bgr.shape[:2]
        self.frame_size = (w, h)
        dt_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{dt_str}.mp4"
        path = self.video_dir / filename
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # .mp4
        self.writer = cv2.VideoWriter(str(path), fourcc, self.target_fps, self.frame_size)
        self.segment_start_ts = time.time()
        if not self.writer or not self.writer.isOpened():
            print("VideoWriter 打开失败 / failed to open:", path)

    def _close_segment(self):
        """关闭当前视频片段"""
        if self.writer is not None:
            try:
                self.writer.release()
            except Exception:
                pass
            self.writer = None

    # ====== 核心帧循环 ======
    def update_frame(self):
        if not self.cap:
            return

        ret, frame_bgr = self.cap.read()
        if not ret or frame_bgr is None:
            return

        draw_bgr = frame_bgr.copy()
        detected_labels = None  # 用于异常判定

        # —— 检测与画框 ——
        if self.detect_enabled and self.detector is not None:
            try:
                labels, boxes = self.detector.detect(frame_bgr)
            except Exception:
                labels, boxes = None, None

            detected_labels = labels

            if boxes:
                h, w = draw_bgr.shape[:2]
                for lab, box in zip(labels, boxes):
                    x1, y1, x2, y2 = [int(max(0, v)) for v in box]
                    x1, y1 = min(x1, w - 1), min(y1, h - 1)
                    x2, y2 = min(x2, w - 1), min(y2, h - 1)
                    # 异常红框 / 正常绿框
                    is_abn = self._is_abnormal_label(lab)
                    color = (0, 0, 255) if is_abn else (0, 255, 0)
                    cv2.rectangle(draw_bgr, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(draw_bgr, str(lab), (x1, max(0, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

        # —— 异常行为 → 报警（带冷却）——
        if self._alarm_enabled and detected_labels:
            if self._any_abnormal(detected_labels):
                self._trigger_alarm()

        # —— 写入视频（30s切片）——
        if self.writer is None:
            self._open_new_segment(draw_bgr)
        elif self.frame_size != (draw_bgr.shape[1], draw_bgr.shape[0]):
            # 容错：尺寸变化时重新打开
            self._close_segment()
            self._open_new_segment(draw_bgr)

        if self.writer is not None and self.writer.isOpened():
            try:
                self.writer.write(draw_bgr)
            except Exception as e:
                print("写入视频帧失败:", e)

        # —— 构造用于 QLabel 显示的叠加（倒计时 & 截图提示）——
        rgb = cv2.cvtColor(draw_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)

        try:
            painter = QPainter()
            painter.begin(img)
            painter.setRenderHint(QPainter.Antialiasing, True)

            # 右上角：自动保存倒计时
            text = f"自动保存：{self.seconds_left:02d} 秒"
            font = QFont("Microsoft YaHei", 12)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            tw = metrics.horizontalAdvance(text)
            th = metrics.height()
            pad = 8
            x2, y1 = w - 10, 10
            x1, y2 = x2 - tw - 2 * pad, y1 + th + 2 * pad
            painter.fillRect(x1, y1, x2 - x1, y2 - y1, Qt.white)
            painter.setPen(QPen(Qt.black))
            painter.drawText(x1 + pad, y1 + pad + metrics.ascent(), text)

            # 中央：截图提示 toast（在 _shot_msg_until 之前显示）
            if time.time() < self._shot_msg_until:
                toast_font = QFont("Microsoft YaHei", 16, QFont.DemiBold)
                painter.setFont(toast_font)
                tmetrics = QFontMetrics(toast_font)
                t = self._shot_msg_text
                t_w = tmetrics.horizontalAdvance(t)
                t_h = tmetrics.height()

                box_w = t_w + 40
                box_h = t_h + 24
                cx, cy = w // 2, h // 2
                bx, by = cx - box_w // 2, cy - box_h // 2

                painter.fillRect(bx, by, box_w, box_h, QColor(0, 0, 0, 140))
                painter.setPen(QPen(Qt.white))
                painter.drawText(
                    bx + (box_w - t_w) // 2,
                    by + (box_h - t_h) // 2 + tmetrics.ascent(),
                    t
                )

            painter.end()

            pix = QPixmap.fromImage(img).scaled(
                self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.label.setPixmap(pix)
        except Exception as e:
            print("绘制叠加层失败：", e)

        self.last_bgr_shown = draw_bgr

    # ====== 截图相关 ======
    def take_photo(self):
        """手动截图：优先保存带框的当前可视帧"""
        self._save_current_frame(auto=False)

    def _save_current_frame(self, auto: bool):
        ts = int(time.time())
        name = f"auto_{ts}.jpg" if auto else f"photo_{ts}.jpg"
        path = self.capture_dir / name

        if self.last_bgr_shown is not None:
            ok = cv2.imwrite(str(path), self.last_bgr_shown)
        else:
            ok = False
            if self.cap:
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    ok = cv2.imwrite(str(path), frame)

        if ok:
            print(("自动保存" if auto else "已保存截图"), "->", path)
            if not auto:  # 仅手动截图显示提示；如需自动也显示，去掉此判断
                self._shot_msg_until = time.time() + self._shot_msg_ms / 1000.0
        else:
            print("保存失败 / Save failed")

    # ====== 事件与清理 ======
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.label.pixmap():
            self.label.setPixmap(
                self.label.pixmap().scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def closeEvent(self, event):
        """关闭页面时释放资源"""
        try:
            self.stop_camera()
        finally:
            event.accept()

    # ====== 工具：解析/判断/报警 ======
    def _parse_class_name(self, label_with_conf: str) -> str:
        """
        支持 "ClassName" 或 "ClassName:0.87" 两种格式，统一返回小写 class 名
        """
        if not label_with_conf:
            return ""
        s = str(label_with_conf).strip()
        pos = s.find(":")
        cls = s[:pos] if pos > 0 else s
        return cls.lower()

    def _is_abnormal_label(self, label_with_conf: str) -> bool:
        cls = self._parse_class_name(label_with_conf)
        if not cls:
            return False
        # Normal 视为正常，其余四类视为异常
        if cls == "normal":
            return False
        return cls in self.abnormal_set

    def _any_abnormal(self, labels) -> bool:
        try:
            return any(self._is_abnormal_label(lab) for lab in (labels or []))
        except Exception:
            return False

    def _trigger_alarm(self):
        if time.time() - self._last_alarm_ts < self.alarm_cooldown_secs:
            return
        self._last_alarm_ts = time.time()
        self._alarm.play()


# ========== Google API Key 加载 ==========
def load_google_key():
    k = os.getenv("GOOGLE_MAPS_KEY")
    if k:
        return k
    # 允许同目录放 config.json
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    cfg = base / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("GOOGLE_MAPS_KEY")
        except Exception:
            pass
    raise RuntimeError("缺少 GOOGLE_MAPS_KEY：请设置环境变量或放 config.json")


GOOGLE_MAPS_KEY = load_google_key()

# ========== 工具函数 ==========
_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG.sub("", s).replace("&nbsp;", " ").replace("&amp;", "&")


# 吉隆坡中心点 + 拼写修正表
KL_CENTER = (3.1390, 101.6869)
SPELL_FIX = {
    "kila": "klia",
    "kila 1": "klia",
    "kila1": "klia",
    "twin towers": "Petronas Twin Towers",
    "xiamen university": "Xiamen University Malaysia",
}


# ========== 导航==========
class NavigationPage(QWidget):
    """基于 Google Maps API 的智能车载导航页面"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        # ---------- 左侧输入区 ----------
        lab_from = QLabel("起点：");
        lab_from.setStyleSheet("color:white; font-size:13px;")
        self.edit_from = QLineEdit();
        self.edit_from.setPlaceholderText("例如：Xiamen University Malaysia")
        self.edit_from.setStyleSheet(
            "QLineEdit{background-color:#33343a;border-radius:6px;border:1px solid #555;color:white;padding:4px 8px;}")

        lab_to = QLabel("终点：");
        lab_to.setStyleSheet("color:white; font-size:13px;")
        self.edit_to = QLineEdit();
        self.edit_to.setPlaceholderText("例如：Petronas Twin Towers")
        self.edit_to.setStyleSheet(
            "QLineEdit{background-color:#33343a;border-radius:6px;border:1px solid #555;color:white;padding:4px 8px;}")

        lab_mode = QLabel("方式：");
        lab_mode.setStyleSheet("color:white; font-size:13px;")
        self.combo_mode = QComboBox();
        self.combo_mode.addItems(["驾车", "步行"])
        self.combo_mode.setStyleSheet(
            "QComboBox{background-color:#33343a;border-radius:6px;border:1px solid #555;color:white;padding:2px 6px;}")

        self.btn_swap = QPushButton("⇄ 互换起终点")
        self.btn_nav = QPushButton("开始导航")
        for b in (self.btn_swap, self.btn_nav):
            b.setFixedHeight(32)
            b.setStyleSheet("""QPushButton{
                background-color:#3a3b40;color:white;border-radius:16px;border:1px solid #666;
            }QPushButton:hover{background-color:#4c4d52;}""")

        # 布局
        row1 = QHBoxLayout();
        row1.addWidget(lab_from);
        row1.addWidget(self.edit_from)
        row2 = QHBoxLayout();
        row2.addWidget(lab_to);
        row2.addWidget(self.edit_to)
        row3 = QHBoxLayout();
        row3.addWidget(lab_mode);
        row3.addWidget(self.combo_mode)
        row3.addStretch(1);
        row3.addWidget(self.btn_swap);
        row3.addWidget(self.btn_nav)
        panel = QWidget();
        lay = QVBoxLayout(panel);
        lay.addLayout(row1);
        lay.addLayout(row2);
        lay.addLayout(row3)

        self.lab_summary = QLabel("请先输入起点和终点，然后点击“开始导航”。")
        self.lab_summary.setStyleSheet("color:#dddddd; font-size:13px;");
        self.lab_summary.setWordWrap(True)

        self.list_steps = QListWidget();
        self.list_steps.setStyleSheet("""
            QListWidget{background-color:#2b2c30;color:white;font-size:13px;border:1px solid #555;}
            QListWidget::item{padding:6px 8px;}
            QListWidget::item:selected{background-color:#3f7fff;}""")

        left = QWidget();
        l = QVBoxLayout(left)
        l.setContentsMargins(16, 16, 16, 16);
        l.addWidget(QLabel("<b style='color:white;font-size:18px;'>智能导航</b>"))
        l.addWidget(panel);
        l.addWidget(self.lab_summary);
        l.addWidget(self.list_steps, 1)

        # ---------- 右侧地图 ----------
        self.map_label = QLabel("路线地图会显示在这里")
        self.map_label.setAlignment(Qt.AlignCenter)
        self.map_label.setMinimumSize(500, 400)
        self.map_label.setStyleSheet("background-color:#1f2024;color:#888;")

        right = QWidget();
        r = QVBoxLayout(right)
        r.setContentsMargins(12, 16, 12, 16)
        r.addWidget(QLabel("<b style='color:white;font-size:14px;'>路线示意图</b>"))
        r.addWidget(self.map_label, 1)

        # ---------- 总体布局 ----------
        root = QHBoxLayout(self)
        root.addWidget(left, 3);
        root.addWidget(right, 2)
        self.setStyleSheet("background-color:#18191d;")

        # 信号
        self.btn_nav.clicked.connect(self.start_navigation)
        self.btn_swap.clicked.connect(self.swap_locations)
        self.edit_to.returnPressed.connect(self.start_navigation)

    # ---------- 方法 ----------
    def swap_locations(self):
        a, b = self.edit_from.text(), self.edit_to.text()
        self.edit_from.setText(b);
        self.edit_to.setText(a)

    def start_navigation(self):
        start_text = self.edit_from.text().strip()
        end_text = self.edit_to.text().strip()
        if not start_text or not end_text:
            QMessageBox.warning(self, "输入不完整", "请同时输入起点和终点。");
            return

        mode = self.combo_mode.currentText()
        profile = "driving" if mode == "驾车" else "walking"

        try:
            s_lat, s_lon, s_name = self.geocode_address(start_text)
            e_lat, e_lon, e_name = self.geocode_address(end_text)
            total_dist, total_time, steps, polyline = self.request_route(s_lat, s_lon, e_lat, e_lon, profile)
            self.update_route_ui(total_dist, total_time, steps, s_name, e_name, mode)
            self.render_route_map(polyline, (s_lat, s_lon), (e_lat, e_lon))
        except Exception as e:
            print("导航请求失败:", e)
            QMessageBox.warning(self, "导航失败", f"请求导航服务失败：{e}")

    # ---------- 地理编码 ----------
    def geocode_address(self, text):
        if not text: raise ValueError("地址为空")
        q = SPELL_FIX.get(text.lower(), text.strip())

        # Google Places
        try:
            url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
            params = {
                "input": q, "inputtype": "textquery", "language": "zh-CN", "region": "my",
                "fields": "geometry,formatted_address,name",
                "locationbias": f"circle:50000@{KL_CENTER[0]},{KL_CENTER[1]}",
                "key": GOOGLE_MAPS_KEY
            }
            r = requests.get(url, params=params, timeout=8)
            js = r.json()
            if (c := js.get("candidates")):
                loc = c[0]["geometry"]["location"]
                return loc["lat"], loc["lng"], c[0].get("name", q)
        except Exception as e:
            print("Find Place失败:", e)

        # Geocoding
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {"address": q, "key": GOOGLE_MAPS_KEY, "language": "zh-CN", "region": "my"}
            r = requests.get(url, params=params, timeout=8)
            js = r.json()
            if js.get("status") == "OK" and js.get("results"):
                res = js["results"][0];
                loc = res["geometry"]["location"]
                return loc["lat"], loc["lng"], res.get("formatted_address", q)
        except Exception as e:
            print("Geocoding失败:", e)

        # OSM fallback
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {"q": q, "format": "json", "limit": 1, "countrycodes": "my"}
            headers = {"User-Agent": "XMUM-Intelligent-Cabin/1.0"}
            r = requests.get(url, params=params, headers=headers, timeout=8)
            arr = r.json()
            if arr:
                it = arr[0];
                return float(it["lat"]), float(it["lon"]), it.get("display_name", q)
        except Exception as e:
            print("Nominatim失败:", e)
        raise ValueError(f"找不到地点：{text}")

    # ---------- 路线 ----------
    def request_route(self, s_lat, s_lon, e_lat, e_lon, profile="driving"):
        mode = "driving" if profile == "driving" else "walking"
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": f"{s_lat},{s_lon}",
            "destination": f"{e_lat},{e_lon}",
            "mode": mode, "language": "zh-CN", "region": "my", "key": GOOGLE_MAPS_KEY
        }
        r = requests.get(url, params=params, timeout=10)
        js = r.json()
        if js.get("status") != "OK":
            raise ValueError(js.get("error_message", js.get("status")))
        route = js["routes"][0];
        leg = route["legs"][0]
        steps = [{"distance": s["distance"]["value"], "duration": s["duration"]["value"],
                  "instruction": s.get("html_instructions", "")} for s in leg.get("steps", [])]
        return leg["distance"]["value"], leg["duration"]["value"], steps, route["overview_polyline"]["points"]

    # ---------- 静态地图 ----------
    def render_route_map(self, polyline, start=None, end=None):
        if not polyline:
            self.map_label.setText("未获取到路线几何信息。");
            return
        width, height = 600, 400
        parts = [f"https://maps.googleapis.com/maps/api/staticmap?size={width}x{height}",
                 f"&path=enc:{polyline}", f"&key={GOOGLE_MAPS_KEY}"]
        if start: parts.append(f"&markers=color:green|label:S|{start[0]},{start[1]}")
        if end: parts.append(f"&markers=color:red|label:E|{end[0]},{end[1]}")
        url = "".join(parts)
        try:
            img = QImage.fromData(requests.get(url, timeout=10).content)
            if img.isNull(): raise ValueError("静态地图加载失败")
            self.map_label.setPixmap(QPixmap.fromImage(img))
        except Exception as e:
            print("加载谷歌静态地图失败:", e)
            self.map_label.setText("无法加载谷歌静态地图，请检查网络或配额。")

    # ---------- UI 更新 ----------
    def update_route_ui(self, total_dist, total_time, steps, s_name, e_name, mode):
        km = total_dist / 1000;
        minutes = total_time / 60
        self.lab_summary.setText(
            f"从：{s_name}\n到：{e_name}\n方式：{mode}，总距离约 {km:.2f} 公里，预计用时 {minutes:.1f} 分钟。")
        self.list_steps.clear()
        if not steps:
            self.list_steps.addItem("未获取到详细步骤。");
            return
        for i, st in enumerate(steps, 1):
            t = _strip_html(st.get("instruction", "")) or "按路标行驶"
            self.list_steps.addItem(
                QListWidgetItem(f"{i}. {t}（约 {st['distance']:.0f} 米，{st['duration'] / 60:.1f} 分钟）"))


# ========== 音乐页面 ==========
class MusicPage(QWidget):
    """
    音乐页面（pygame 播放版）：
    1. 自动读取 ./music 里的音频文件作为“我的音乐”
    2. 左侧列表 + 双击播放 / 按钮播放
    3. 支持上一首、下一首、播放/暂停
    4. 添加本地音乐到 music 文件夹
    """

    def __init__(self, music_dir=None):
        super().__init__()

        # ---------- 播放核心 ----------
        pygame.mixer.init()
        self.music_dir = (resource_path("music") if music_dir is None else music_dir)
        os.makedirs(self.music_dir, exist_ok=True)

        self.track_paths = []  # 每一首歌的完整路径
        self.track_durations = []  # 每一首歌的时长(ms)，可能为 None
        self.current_index = -1
        self.is_paused = False

        # ---------- 左侧：标题 + 列表 ----------
        title = QLabel("我的音乐")
        title.setFont(QFont("Microsoft YaHei", 14))
        title.setStyleSheet("color: white; margin: 6px 0;")

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #2b2c30;
                color: white;
                font-size: 18px;
                border: 1px solid #555555;
            }
            QListWidget::item {
                padding: 6px 8px;
            }
            QListWidget::item:selected {
                background-color: #3f7fff;
            }
        """)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)

        # ---------- 底部控制：按钮 + 进度条 ----------
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderMoved.connect(self.on_slider_moved)

        self.label_time = QLabel("00:00 / 00:00")
        self.label_time.setStyleSheet("color: white; font-size: 12px;")

        self.btn_prev = QPushButton("上一首")
        self.btn_play = QPushButton("播放")
        self.btn_next = QPushButton("下一首")
        self.btn_add = QPushButton("添加本地音乐")

        for b in [self.btn_prev, self.btn_play, self.btn_next, self.btn_add]:
            b.setFixedHeight(32)
            b.setStyleSheet("""
                QPushButton {
                    background-color: #3a3b40;
                    color: white;
                    border-radius: 16px;
                    padding: 0 12px;
                    border: 1px solid #555555;
                }
                QPushButton:hover {
                    background-color: #4c4d52;
                }
            """)

        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next.clicked.connect(self.play_next)
        self.btn_add.clicked.connect(self.add_local_music)

        # 用定时器刷新“进度条 + 时间”
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_position)
        self.timer.start(500)  # 每 0.5s 刷新一次

        # ---------- 右侧推荐区（静态） ----------
        right_panel = QWidget()
        right_panel.setStyleSheet("background-color: #1f2024;")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(12)

        lab_sel = QLabel("精选歌单")
        lab_sel.setStyleSheet("color: white; font-size: 13px;")
        right_layout.addWidget(lab_sel)

        def make_card(title, desc):
            w = QWidget()
            w.setStyleSheet("""
                QWidget {
                    background-color: #ff6f3c;
                    border-radius: 12px;
                }
            """)
            v = QVBoxLayout(w)
            v.setContentsMargins(10, 10, 10, 10)
            name = QLabel(title)
            name.setStyleSheet("color:white; font-weight:bold;")
            d = QLabel(desc)
            d.setStyleSheet("color:white; font-size:12px;")
            d.setWordWrap(True)
            v.addWidget(name)
            v.addWidget(d)
            return w

        right_layout.addWidget(make_card("睡眠气氛组", "助眠白噪音，给你舒适的入睡环境。"))
        right_layout.addWidget(make_card("学习专注", "提升专注力的 BGM，在路上也能保持高效。"))
        right_layout.addStretch(1)

        # ---------- 左侧整体布局 ----------
        left_main = QWidget()
        left_layout = QVBoxLayout(left_main)
        left_layout.setContentsMargins(12, 12, 12, 8)
        left_layout.setSpacing(8)
        left_layout.addWidget(title)
        left_layout.addWidget(self.list_widget, 1)

        bottom_controls = QHBoxLayout()
        bottom_controls.setSpacing(10)
        bottom_controls.addWidget(self.btn_prev)
        bottom_controls.addWidget(self.btn_play)
        bottom_controls.addWidget(self.btn_next)
        bottom_controls.addStretch(1)
        bottom_controls.addWidget(self.btn_add)

        left_layout.addLayout(bottom_controls)
        left_layout.addWidget(self.slider)
        left_layout.addWidget(self.label_time, 0, Qt.AlignRight)

        # ---------- 整体左右布局 ----------
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(left_main, 3)
        main_layout.addWidget(right_panel, 2)

        self.setStyleSheet("background-color:#18191d;")

        # 初始化：加载 music 目录里的歌曲
        self.load_music_from_folder()

    # ====== 加载音乐文件 ======
    def load_music_from_folder(self):
        exts = (".mp3", ".m4a", ".wav", ".flac", ".ogg")
        self.list_widget.clear()
        self.track_paths.clear()
        self.track_durations.clear()

        for fname in sorted(os.listdir(self.music_dir)):
            if not fname.lower().endswith(exts):
                continue
            full_path = os.path.abspath(os.path.join(self.music_dir, fname))
            self.track_paths.append(full_path)

            duration_ms = None
            if MutagenFile is not None:
                try:
                    audio = MutagenFile(full_path)
                    if audio and audio.info:
                        duration_ms = int(audio.info.length * 1000)
                except Exception as e:
                    print("读取时长失败:", e)
            self.track_durations.append(duration_ms)

            item = QListWidgetItem(fname)
            self.list_widget.addItem(item)

        if self.track_paths:
            self.current_index = 0
            self.list_widget.setCurrentRow(0)
        else:
            self.current_index = -1

    # ====== 播放/暂停/切歌 ======
    def play_index(self, idx):
        if idx < 0 or idx >= len(self.track_paths):
            return
        path = self.track_paths[idx]
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            self.current_index = idx
            self.is_paused = False
            self.btn_play.setText("暂停")
        except Exception as e:
            print("播放失败:", e)

    def on_item_double_clicked(self, item):
        row = self.list_widget.row(item)
        self.play_index(row)

    def play_prev(self):
        if not self.track_paths:
            return
        idx = (self.current_index - 1) % len(self.track_paths)
        self.list_widget.setCurrentRow(idx)
        self.play_index(idx)

    def play_next(self):
        if not self.track_paths:
            return
        idx = (self.current_index + 1) % len(self.track_paths)
        self.list_widget.setCurrentRow(idx)
        self.play_index(idx)

    def toggle_play(self):
        if not self.track_paths:
            return
        if pygame.mixer.music.get_busy():
            # 当前在播放 → 暂停
            pygame.mixer.music.pause()
            self.is_paused = True
            self.btn_play.setText("播放")
        else:
            if self.is_paused:
                # 暂停状态 → 继续
                pygame.mixer.music.unpause()
                self.is_paused = False
                self.btn_play.setText("暂停")
            else:
                # 没有在播（或第一次）→ 播放当前曲目
                if self.current_index < 0:
                    self.current_index = 0
                    self.list_widget.setCurrentRow(0)
                self.play_index(self.current_index)

    # ====== 进度与时间 ======
    def update_position(self):
        if self.current_index < 0 or not self.track_paths:
            self.slider.setValue(0)
            self.label_time.setText("00:00 / 00:00")
            return

        pos_ms = pygame.mixer.music.get_pos()  # 从本次 play/unpause 起的时长
        if pos_ms < 0:
            pos_ms = 0

        dur_ms = self.track_durations[self.current_index]
        # 进度条
        if dur_ms and dur_ms > 0:
            percent = min(100, int(pos_ms * 100 / dur_ms))
            self.slider.blockSignals(True)
            self.slider.setValue(percent)
            self.slider.blockSignals(False)
        else:
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)

        # 时间标签
        def fmt(ms):
            s = int(ms // 1000)
            m = s // 60
            s = s % 60
            return f"{m:02d}:{s:02d}"

        cur_text = fmt(pos_ms)
        total_text = fmt(dur_ms) if dur_ms else "00:00"
        self.label_time.setText(f"{cur_text} / {total_text}")

    def on_slider_moved(self, value):
        """拖动进度条：按百分比跳转（大概），需要有总时长信息才有效"""
        if self.current_index < 0:
            return
        dur_ms = self.track_durations[self.current_index]
        if not dur_ms:
            return
        new_pos = int(dur_ms * value / 100)
        pygame.mixer.music.set_pos(new_pos / 1000.0)

    # ====== 添加本地音乐 ======
    def add_local_music(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要添加的音乐文件",
            "",
            "音频文件 (*.mp3 *.m4a *.wav *.flac *.ogg);;所有文件 (*.*)"
        )
        if not files:
            return

        import shutil
        for f in files:
            fname = os.path.basename(f)
            dst_path = os.path.join(self.music_dir, fname)
            if not os.path.exists(dst_path):
                try:
                    shutil.copy2(f, dst_path)
                except Exception as e:
                    print("复制失败:", e)

        self.load_music_from_folder()


# ========== 电话页面 ==========
class PhonePage(QWidget):
    """
    车载电话页面：
    - 顶部：标题 + 信号/网络占位
    - 左侧：三大功能按钮（联系人 / 通话记录 / 拨号盘）
    - 中间：列表或数字键盘（通过内部 QStackedWidget 切换）
    - 右侧：当前号码 / 当前联系人信息
    - 底部：呼叫 / 挂断 按钮（模拟）
    """

    def __init__(self):
        super().__init__()

        self.setStyleSheet("background-color:#18191d;")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ========== 顶部栏 ==========
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(4, 4, 4, 4)
        top_layout.setSpacing(8)

        title = QLabel("电话")
        title.setStyleSheet("color:white; font-size:18px; font-weight:bold;")

        lab_signal = QLabel("信号：▂ ▃ ▄ ▅ ▆")
        lab_signal.setStyleSheet("color:#cccccc; font-size:12px;")
        lab_network = QLabel("4G  已连接")
        lab_network.setStyleSheet("color:#cccccc; font-size:12px;")

        top_layout.addWidget(title)
        top_layout.addStretch(1)
        top_layout.addWidget(lab_signal)
        top_layout.addWidget(lab_network)

        main_layout.addWidget(top_bar)

        # ========== 中间主区域：左按钮 + 中内容 + 右侧信息 ==========
        center = QWidget()
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        # ------ 左侧：功能切换按钮 ------
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        self.btn_contacts = QPushButton("联系人")
        self.btn_history = QPushButton("通话记录")
        self.btn_dial = QPushButton("拨号盘")

        for b in (self.btn_contacts, self.btn_history, self.btn_dial):
            b.setFixedHeight(40)
            b.setStyleSheet("""
                QPushButton {
                    background-color:#2a2b30;
                    color:white;
                    border-radius:8px;
                    border:1px solid #555;
                }
                QPushButton:hover {
                    background-color:#3a3b40;
                }
            """)
            left_layout.addWidget(b)

        left_layout.addStretch(1)

        # ------ 中间：三个子页面（内部 QStackedWidget） ------
        self.middle_stack = QStackedWidget()

        # 1) 联系人列表
        self.list_contacts = QListWidget()
        self.list_contacts.setStyleSheet("""
            QListWidget {
                background-color:#222329;
                color:white;
                font-size:14px;
                border:1px solid #555;
            }
            QListWidget::item {
                padding:6px 8px;
            }
            QListWidget::item:selected {
                background-color:#3f7fff;
            }
        """)
        # 模拟一些联系人
        demo_contacts = [
            ("张三", "138 0000 0001"),
            ("李四", "138 0000 0002"),
            ("王老师", "010-8888 0000"),
            ("XMUM教务处", "03-8888 6666"),
        ]
        for name, phone in demo_contacts:
            item = QListWidgetItem(f"{name}    {phone}")
            item.setData(Qt.UserRole, phone)
            item.setData(Qt.UserRole + 1, name)
            self.list_contacts.addItem(item)

        contacts_page = QWidget()
        c_layout = QVBoxLayout(contacts_page)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(4)
        lab_c = QLabel("联系人")
        lab_c.setStyleSheet("color:#dddddd; font-size:14px;")
        c_layout.addWidget(lab_c)
        c_layout.addWidget(self.list_contacts, 1)

        # 2) 通话记录
        self.list_history = QListWidget()
        self.list_history.setStyleSheet("""
            QListWidget {
                background-color:#222329;
                color:white;
                font-size:13px;
                border:1px solid #555;
            }
            QListWidget::item {
                padding:5px 7px;
            }
            QListWidget::item:selected {
                background-color:#3f7fff;
            }
        """)
        # 初始可以为空，拨打电话时写入

        history_page = QWidget()
        h_layout = QVBoxLayout(history_page)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)
        lab_h = QLabel("通话记录")
        lab_h.setStyleSheet("color:#dddddd; font-size:14px;")
        h_layout.addWidget(lab_h)
        h_layout.addWidget(self.list_history, 1)

        # 3) 拨号盘
        dial_page = QWidget()
        dial_layout = QVBoxLayout(dial_page)
        dial_layout.setContentsMargins(0, 0, 0, 0)
        dial_layout.setSpacing(8)

        self.edit_number = QLineEdit()
        self.edit_number.setPlaceholderText("请输入电话号码")
        self.edit_number.setReadOnly(True)
        self.edit_number.setStyleSheet("""
            QLineEdit {
                background-color:#222329;
                border-radius:6px;
                border:1px solid #555;
                color:white;
                padding:6px 8px;
                font-size:18px;
                letter-spacing:2px;
            }
        """)

        grid = QGridLayout()
        grid.setSpacing(8)
        buttons = [
            "1", "2", "3",
            "4", "5", "6",
            "7", "8", "9",
            "*", "0", "#"
        ]
        positions = [(i, j) for i in range(4) for j in range(3)]
        for pos, text in zip(positions, buttons):
            btn = QPushButton(text)
            btn.setFixedSize(70, 50)
            btn.setStyleSheet("""
                QPushButton {
                    background-color:#2f3035;
                    color:white;
                    border-radius:8px;
                    font-size:16px;
                    border:1px solid #444;
                }
                QPushButton:hover {
                    background-color:#44454b;
                }
            """)
            btn.clicked.connect(self.on_keypad_clicked)
            grid.addWidget(btn, *pos)

        # 删除按键
        self.btn_backspace = QPushButton("删除")
        self.btn_backspace.setFixedHeight(36)
        self.btn_backspace.setStyleSheet("""
            QPushButton {
                background-color:#3a3b40;
                color:white;
                border-radius:8px;
                border:1px solid #666;
            }
            QPushButton:hover {
                background-color:#4c4d52;
            }
        """)
        self.btn_backspace.clicked.connect(self.on_backspace)

        dial_layout.addWidget(QLabel("拨号盘", styleSheet="color:#dddddd; font-size:14px;"))
        dial_layout.addWidget(self.edit_number)
        dial_layout.addLayout(grid)
        dial_layout.addWidget(self.btn_backspace, 0, Qt.AlignRight)

        # 将三页加入 stack
        self.middle_stack.addWidget(contacts_page)
        self.middle_stack.addWidget(history_page)
        self.middle_stack.addWidget(dial_page)

        # 默认显示联系人页
        self.middle_stack.setCurrentIndex(0)

        # ------ 右侧：当前通话信息 ------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        self.lab_status = QLabel("未在通话")
        self.lab_status.setWordWrap(True)
        self.lab_status.setStyleSheet("color:#dddddd; font-size:14px;")

        self.lab_current_name = QLabel("联系人：-")
        self.lab_current_name.setStyleSheet("color:#ffffff; font-size:16px; font-weight:bold;")
        self.lab_current_number = QLabel("号码：-")
        self.lab_current_number.setStyleSheet("color:#cccccc; font-size:14px;")

        right_layout.addWidget(self.lab_status)
        right_layout.addSpacing(10)
        right_layout.addWidget(self.lab_current_name)
        right_layout.addWidget(self.lab_current_number)
        right_layout.addStretch(1)

        # 把三个区域塞到 center_layout
        center_layout.addWidget(left_panel, 1)
        center_layout.addWidget(self.middle_stack, 3)
        center_layout.addWidget(right_panel, 2)

        main_layout.addWidget(center, 1)

        # ========== 底部：呼叫 / 挂断 ==========
        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(12)

        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        bottom_layout.addItem(spacer)

        self.btn_call = QPushButton("呼叫")
        self.btn_hang = QPushButton("挂断")

        for b, color in [(self.btn_call, "#00aa55"), (self.btn_hang, "#cc3333")]:
            b.setFixedSize(96, 40)
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color:{color};
                    color:white;
                    border-radius:20px;
                    border:none;
                    font-size:15px;
                    font-weight:bold;
                }}
                QPushButton:hover {{
                    background-color:#ffffff22;
                }}
            """)
            bottom_layout.addWidget(b)

        main_layout.addWidget(bottom)

        # ========== 信号连接 ==========
        self.btn_contacts.clicked.connect(lambda: self.middle_stack.setCurrentIndex(0))
        self.btn_history.clicked.connect(lambda: self.middle_stack.setCurrentIndex(1))
        self.btn_dial.clicked.connect(lambda: self.middle_stack.setCurrentIndex(2))

        self.list_contacts.itemClicked.connect(self.on_contact_clicked)
        self.list_history.itemClicked.connect(self.on_history_clicked)

        self.btn_call.clicked.connect(self.start_call)
        self.btn_hang.clicked.connect(self.end_call)

        # 当前选择
        self.current_name = ""
        self.current_number = ""

    # ---------------- 按键/事件处理 ----------------
    def on_keypad_clicked(self):
        sender = self.sender()
        if sender:
            self.edit_number.setText(self.edit_number.text() + sender.text())

    def on_backspace(self):
        txt = self.edit_number.text()
        self.edit_number.setText(txt[:-1])

    def on_contact_clicked(self, item: QListWidgetItem):
        phone = item.data(Qt.UserRole)
        name = item.data(Qt.UserRole + 1)
        self.current_name = name
        self.current_number = phone
        self.update_current_display()
        # 点击联系人时，顺便把号码放到拨号框
        self.edit_number.setText(phone)

    def on_history_clicked(self, item: QListWidgetItem):
        phone = item.data(Qt.UserRole)
        name = item.data(Qt.UserRole + 1)
        self.current_name = name
        self.current_number = phone
        self.update_current_display()
        self.edit_number.setText(phone)

    def update_current_display(self):
        self.lab_current_name.setText(f"联系人：{self.current_name or '-'}")
        self.lab_current_number.setText(f"号码：{self.current_number or '-'}")

    def start_call(self):
        # 读取号码：优先当前号码，其次拨号框
        number = self.current_number or self.edit_number.text().strip()
        if not number:
            self.lab_status.setText("请先选择联系人或输入电话号码。")
            return

        if not self.current_name:
            self.current_name = "未知号码"

        self.current_number = number
        self.update_current_display()
        self.lab_status.setText(f"正在呼叫……\n{self.current_name}（{self.current_number}）")

        # 写入通话记录（简单插入到顶部）
        item_text = f"{self.current_name}    {self.current_number}    出去呼叫"
        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, self.current_number)
        item.setData(Qt.UserRole + 1, self.current_name)
        self.list_history.insertItem(0, item)

    def end_call(self):
        if self.current_number:
            self.lab_status.setText(f"通话结束：{self.current_name}（{self.current_number}）")
        else:
            self.lab_status.setText("未在通话")


# ========== 主界面右侧内容（不含左侧4个按钮，也不含底部功能栏） ==========
class MainUI(QWidget):
    """
    主界面：右侧内容区
    包含：顶部时间+天气 + 中间（三按钮+车+三按钮）
    不再包含底部功能栏
    """

    def __init__(self):
        super().__init__()
        self.dark_mode = True  # 默认深色模式

        # ------- 顶部信息栏（你好 + 时间 + 天气） -------
        self.label_info = QLabel("正在获取时间和天气…")
        self.label_info.setAlignment(Qt.AlignCenter)
        self.label_info.setFont(QFont("Microsoft YaHei", 13))
        self.label_info.setStyleSheet("color: white;")

        self.top_bar = QWidget()
        self.top_bar.setStyleSheet("background-color: #202126; border-radius: 0px;")
        top_layout = QVBoxLayout(self.top_bar)
        top_layout.setContentsMargins(0, 8, 0, 8)
        top_layout.addWidget(self.label_info)

        # ------- 中间：左三按钮 + 车 + 右三按钮 -------
        self.center_panel = QWidget()
        self.center_panel.setStyleSheet("background-color: #23262B;")
        center_h = QHBoxLayout(self.center_panel)
        center_h.setContentsMargins(40, 20, 40, 20)
        center_h.setSpacing(80)

        # 左侧三项
        left_func_col = QVBoxLayout()
        left_func_col.setContentsMargins(0, 40, 0, 40)
        left_func_col.setSpacing(25)
        left_func_col.addWidget(self.make_side_item("中控锁", "lock.png"))
        left_func_col.addWidget(self.make_side_item("油箱", "Gas-Pump.png"))
        left_func_col.addWidget(self.make_side_item("主题", "主题.png"))
        left_func_col.addStretch(1)

        # 右侧三项
        right_func_col = QVBoxLayout()
        right_func_col.setContentsMargins(0, 40, 0, 40)
        right_func_col.setSpacing(25)
        right_func_col.addWidget(self.make_side_item("空调", "空调.png"))
        right_func_col.addWidget(self.make_side_item("灯光", "灯光.png"))
        right_func_col.addWidget(self.make_side_item("座椅", "前后座椅通风.png"))
        right_func_col.addStretch(1)

        # 中间汽车
        self.car_label = QLabel()
        self.car_label.setAlignment(Qt.AlignCenter)

        car_pix = QPixmap(resource_path('image', 'icons', 'Xiaomi-SU73.png'))
        if not car_pix.isNull():
            car_pix = car_pix.scaled(520, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.car_label.setPixmap(car_pix)

        car_wrap = QWidget()
        car_v = QVBoxLayout(car_wrap)
        car_v.setContentsMargins(0, 0, 0, 0)
        car_v.addStretch(1)
        car_v.addWidget(self.car_label, 0, Qt.AlignCenter)
        car_v.addStretch(1)

        # 把左列 / 车 / 右列放到一行
        center_h.addLayout(left_func_col, 0)
        center_h.addWidget(car_wrap, 1)
        center_h.addLayout(right_func_col, 0)

        # ------- 右侧整体布局（顶部+中间） -------
        center_all = QVBoxLayout()
        center_all.setContentsMargins(8, 12, 12, 12)
        center_all.setSpacing(10)
        center_all.addWidget(self.top_bar)
        center_all.addWidget(self.center_panel, 1)

        # 这个 MainUI 只负责右侧内容区域（不含底部功能栏）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(center_all)

        # 整体背景
        self.setStyleSheet("""
            QWidget {
                background-color: #E6E6E6;
            }
        """)

        self.apply_theme()

    # ================== 下面是工具方法（和 __init__ 平级缩进） ==================

    def make_side_item(self, text: str, icon_filename: str) -> QWidget:
        """中间左右两侧：圆形按钮 + 图标 + 文字"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(6)
        v.setContentsMargins(0, 0, 0, 0)

        # 用 QLabel 当按钮，后面加 mousePressEvent
        circle = QLabel()
        circle.setFixedSize(64, 64)
        circle.setStyleSheet("""
            QLabel {
                background-color: #e1e4f0;
                border-radius: 32px;
            }
        """)
        circle.setAlignment(Qt.AlignCenter)

        # 加载图标（统一加 icons/ 前缀）
        icon = QPixmap(resource_path('image', 'icons', icon_filename))
        if not icon.isNull():
            icon = icon.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            circle.setPixmap(icon)

        lab = QLabel(text)
        lab.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        lab.setStyleSheet("color: #DDDDDD;")
        lab.setFont(QFont("Microsoft YaHei", 11))

        v.addWidget(circle, 0, Qt.AlignHCenter)
        v.addWidget(lab, 0, Qt.AlignHCenter)
        v.addStretch(1)

        # ============== 点击触发主题切换功能 ==============
        if text == "主题":
            # 点整块区域都能切换
            w.mousePressEvent = lambda e: self.toggle_theme()

        return w

    def set_header_text(self, text: str):
        """供 MainWindow 调用，更新顶部时间+天气文字"""
        self.label_info.setText(text)

    def toggle_theme(self):
        """主题切换：深色 <-> 浅色"""
        self.dark_mode = not self.dark_mode
        self.apply_theme()

    def apply_theme(self):
        """根据 self.dark_mode 设置整套深/浅主题"""
        if self.dark_mode:
            # ===== 深色模式 =====
            self.setStyleSheet("""
                QWidget { background-color: #101014; }
            """)
            self.top_bar.setStyleSheet("background-color: #202126; border-radius: 0px;")
            self.center_panel.setStyleSheet("background-color: #23262B;")
            self.label_info.setStyleSheet("color: white;")
        else:
            # ===== 浅色模式 =====
            self.setStyleSheet("""
                QWidget { background-color: #F4F4F4; }
            """)
            self.top_bar.setStyleSheet("background-color: #f0f0f0; border-radius: 0px;")
            self.center_panel.setStyleSheet("background-color: #ffffff;")
            self.label_info.setStyleSheet("color: #222222;")


# ========== 主窗口 ==========
class MainWindow(QMainWindow):
    def __init__(self, username: str):
        super().__init__()

        self.setWindowTitle("智能座舱系统")
        self.resize(1280, 720)  # 初始窗口大小调整为 16:9，更接近设计图
        self.setWindowIcon(QIcon("image/icons/xiaomi.png"))

        # 用户名 / 城市（天气用）
        self.user_name = username
        self.city = "Kuala Lumpur"
        self.weather_text = "天气：获取中…"

        # ========= 右侧页面堆叠：主界面 + 摄像头 + 导航 + 音乐 + 电话 + 新的几个页面 =========
        self.stack = QStackedWidget()
        self.page_main = MainUI()
        self.page_cam = CameraPage()
        self.page_nav = NavigationPage()
        self.page_music = MusicPage()
        self.page_phone = PhonePage()

        # 视频页面 / 图片浏览 / 用户中心
        self.page_video1 = Video1DetectPage()
        self.page_video2 = VideoPage()
        self.video_browser = VideoBrowserPage(self.page_video2)  # 预览页，双击后让 video2_page 播放
        self.page_images = ImagePage()
        self.page_user = UserPage(self.user_name)

        # 加入 stack
        self.stack.addWidget(self.page_main)
        self.stack.addWidget(self.page_nav)
        self.stack.addWidget(self.page_music)
        self.stack.addWidget(self.page_phone)
        self.stack.addWidget(self.page_cam)
        self.stack.addWidget(self.page_video1)
        self.stack.addWidget(self.video_browser)
        self.stack.addWidget(self.page_video2)
        self.stack.addWidget(self.page_images)
        self.stack.addWidget(self.page_user)

        # ========= 左侧 4 个固定按钮（导航栏） =========
        self.left_bar = QWidget()
        left_layout = QVBoxLayout(self.left_bar)
        left_layout.setContentsMargins(15, 20, 15, 20)
        left_layout.setSpacing(25)

        self.btn_home = self.make_left_btn("home.png")
        self.btn_music = self.make_left_btn("music.png")
        self.btn_phone = self.make_left_btn("phone.png")
        self.btn_car = self.make_left_btn("guide.png")

        left_layout.addWidget(self.btn_home)
        left_layout.addWidget(self.btn_music)
        left_layout.addWidget(self.btn_phone)
        left_layout.addWidget(self.btn_car)
        left_layout.addStretch(1)

        # ========= 底部全局功能栏（1~7 功能键） =========
        self.func_bar = FunctionBar()

        # ========= 右侧整体：stack + 功能栏 =========
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.stack, 1)
        right_layout.addWidget(self.func_bar)

        # ========= 根布局：左侧栏 + 右侧（stack+功能栏） =========
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.left_bar)
        root_layout.addWidget(right_container, 1)

        self.setCentralWidget(root)

        # ========= 连接左侧按钮事件 =========
        self.btn_home.clicked.connect(self.goto_main_page)
        self.btn_music.clicked.connect(self.goto_music)
        self.btn_phone.clicked.connect(self.goto_phone)
        self.btn_car.clicked.connect(self.goto_navigation)

        # ========= 创建 FunctionManager，负责底部 1~7 功能键 =========
        self.func_manager = FunctionManager(
            main_window=self,
            func_bar=self.func_bar,
            video1_page=self.page_video1,
            video2_page=self.page_video2,
            image_page=self.page_images,
            user_page=self.page_user,

            video1_path=resource_path("drive.mp4"),
            video2_path="",
            capture_dir=resource_path("image", "captures")
        )

        # ========= 定时器：更新时间 =========
        self.timer_clock = QTimer(self)
        self.timer_clock.timeout.connect(self.update_datetime_only)
        self.timer_clock.start(1000)  # 每秒

        # ========= 定时器：更新天气（30 分钟一次） =========
        self.timer_weather = QTimer(self)
        self.timer_weather.timeout.connect(self.update_weather)
        self.timer_weather.start(30 * 60 * 1000)

        # 启动时先获取一次天气 & 时间
        self.update_weather()
        self.update_header()

    # ------- 左侧按钮外观 -------
    def make_left_btn(self, icon_name: str) -> QPushButton:
        """左侧竖直导航按钮（全局固定）"""
        btn = QPushButton()
        pix = QPixmap(resource_path("image", "icons", icon_name))
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(44, 44))  # 左侧图标大小
        btn.setFixedSize(78, 78)  # 左侧按钮大小
        btn.setStyleSheet("""
            QPushButton {
                background: #f7f2f2;
                border-radius: 14px;
                border: none;
            }
            QPushButton:hover {
                background: #151515;
            }
        """)
        return btn

    # ------- 顶部时间 + 天气 -------
    def make_header_text(self) -> str:
        now = QDateTime.currentDateTime().toString("yyyy.MM.dd HH:mm:ss")
        return f"你好，{self.user_name}\n{now}\n{self.weather_text}"

    def update_header(self):
        self.page_main.set_header_text(self.make_header_text())

    def update_datetime_only(self):
        """每秒刷新时间（天气不变）"""
        self.update_header()

    def update_weather(self):
        """从 Open-Meteo 获取吉隆坡天气，更稳定、无需 Key"""
        try:
            # 吉隆坡经纬度
            lat, lon = 3.1390, 101.6869
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,weather_code"
                "&timezone=auto"
            )
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json().get("current", {})
            temp = data.get("temperature_2m")
            rh = data.get("relative_humidity_2m")
            code = data.get("weather_code", 0)

            # 简单中文天气描述映射
            desc_map = {
                0: "晴", 1: "多云间晴", 2: "多云", 3: "阴",
                45: "雾", 48: "霜雾", 51: "小毛毛雨", 53: "中毛毛雨",
                55: "大毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
                66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪",
                75: "大雪", 95: "雷阵雨", 99: "雷阵雨伴冰雹"
            }
            desc = desc_map.get(code, "未知")
            self.weather_text = f"天气：{desc}，温度：{temp:.1f}°C，湿度：{rh}%"
        except Exception as e:
            print("获取天气失败：", e)
            self.weather_text = "天气：获取失败"

        self.update_header()

    # ------- 页面切换 & 摄像头控制 -------
    def goto_main_page(self):
        """返回主界面（右侧显示主界面内容）"""
        # 回主界面时顺便关掉摄像头
        if self.stack.currentWidget() is self.page_cam:
            self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        self.stack.setCurrentWidget(self.page_main)

    def open_camera_page(self):
        """
        如果你在别的地方想打开摄像头，可以直接调用这个，
        内部直接复用底部“摄像头”按钮的逻辑。
        """
        self.func_manager.do_open_camera()

    def goto_navigation(self):
        """右侧切换到导航页"""
        if self.stack.currentWidget() is self.page_cam:
            self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        self.stack.setCurrentWidget(self.page_nav)

    def goto_music(self):
        """右侧切换到音乐页"""
        if self.stack.currentWidget() is self.page_cam:
            self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        self.stack.setCurrentWidget(self.page_music)

    def goto_phone(self):
        """右侧切换到电话页"""
        if self.stack.currentWidget() is self.page_cam:
            self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        self.stack.setCurrentWidget(self.page_phone)

    def goto_photos_placeholder(self):
        """以前底部照片按钮用的，现在有 ImagePage 可以不用了"""
        print("照片按钮：当前版本使用底部功能键中的“图片”查看。")

    def goto_video_library(self):
        if self.stack.currentWidget() is self.page_cam:
            self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        self.stack.setCurrentWidget(self.video_browser)

    def closeEvent(self, event):
        """关闭程序时，确保释放摄像头"""
        self.page_cam.stop_camera()
        self.page_video1.stop()
        self.page_video2.stop()
        event.accept()


# # app.py 末尾
# def main():
#     app = QApplication(sys.argv)
#     app.setFont(QFont("Microsoft YaHei", 10))
#     username = 'zzz'
#     w = MainWindow(username)
#     w.show()
#     sys.exit(app.exec_())
#
#
# if __name__ == "__main__":
#     main()
