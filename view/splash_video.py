# -*- coding: utf-8 -*-
import os, cv2
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication

class SplashVideoCV(QWidget):
    """
    开机动画（OpenCV 解码 + QLabel 渲染）
    - 固定窗口 1280x720
    - 绝对路径加载视频
    - 播放完自动回调 next_callback
    """
    def __init__(self, video_path_abs: str, next_callback):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet("background:black;")
        self.setFixedSize(1280, 720)

        self.label = QLabel(alignment=Qt.AlignCenter)
        self.label.setStyleSheet("background:black;")
        v = QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.addWidget(self.label)

        self.next_callback = next_callback

        self.video_path_abs = os.path.abspath(video_path_abs)
        if not os.path.exists(self.video_path_abs):
            print("[SplashCV] not found:", self.video_path_abs)
            QTimer.singleShot(200, self._finish)
            return

        self.cap = cv2.VideoCapture(self.video_path_abs)
        if not self.cap.isOpened():
            print("[SplashCV] open failed:", self.video_path_abs)
            QTimer.singleShot(200, self._finish)
            return

        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.interval = max(15, int(500 / (fps if fps and fps > 1e-3 else 30)))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.timer.start(self.interval)

        self._center_on_screen()

    def _center_on_screen(self):
        screen = QApplication.desktop().screenGeometry()
        self.move((screen.width() - self.width()) // 2,
                  (screen.height() - self.height()) // 2)

    def _next_frame(self):
        ok, frame = self.cap.read()
        if not ok:
            self._finish(); return

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]
        scale = min(1280 / w, 720 / h)
        nw, nh = int(w * scale), int(h * scale)
        frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)

        qimg = QImage(frame.data, nw, nh, frame.strides[0], QImage.Format_RGB888)
        self.label.setPixmap(QPixmap.fromImage(qimg))

    def _finish(self):
        try:
            if hasattr(self, "timer") and self.timer: self.timer.stop()
            if hasattr(self, "cap") and self.cap: self.cap.release()
        finally:
            self.close()
            if self.next_callback:
                try: self.next_callback()
                except Exception as e: print("[SplashCV] next_callback error:", e)
