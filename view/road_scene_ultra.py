#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
road_scene_ultra.py
Ultralytics YOLO + BEV 距离估计 + 红绿灯/停牌检测（可在 PyQt5 CameraPage 中直接调用）
"""
from dataclasses import dataclass, asdict
import json
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

@dataclass
class AnalyzerConfig:
    model_path: str = "yolov8n.pt"
    lane_width_m: float = 3.6
    num_lanes_in_roi: float = 2.0
    ahead_m: float = 35.0
    scale: float = 10.0
    conf_thres: float = 0.30
    iou_thres: float = 0.45
    alarm_dist_m: float = 12.0
    alarm_hold_frames: int = 15
    redlight_alert_dist_m: float = 25.0
    stopsign_alert_dist_m: float = 20.0

class RoadSceneAnalyzer:
    VEHICLE_CLS = {2, 3, 5, 7}
    TLIGHT_CLS  = {9}
    STOPSIGN_CLS = {11}

    def __init__(self, cfg: AnalyzerConfig):
        self.cfg = cfg
        self._load_model(cfg.model_path)
        self.src_pts: Optional[np.ndarray] = None
        self.dst_pts: Optional[np.ndarray] = None
        self.H: Optional[np.ndarray] = None
        self.Hinv: Optional[np.ndarray] = None
        self.bev_w: int = 0
        self.bev_h: int = 0
        self._ema_val: Optional[float] = None
        self._ema_alpha: float = 0.4
        self._alarm_frames: int = 0

    def _load_model(self, model_path: str):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    def set_src_pts(self, src_pts: np.ndarray, frame_shape: Optional[Tuple[int,int]]=None):
        src_pts = np.float32(src_pts)
        assert src_pts.shape == (4, 2), "src_pts 必须是 (4,2)"
        if frame_shape is not None:
            h, w = frame_shape
            assert (src_pts[:,0].min() >= 0 and src_pts[:,0].max() <= w and
                    src_pts[:,1].min() >= 0 and src_pts[:,1].max() <= h), "src_pts 越界"
        self.src_pts = src_pts
        self._rebuild_homography()

    def _default_src_pts(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        return np.float32([
            [0.18*w, 0.96*h],
            [0.81*w, 0.96*h],
            [0.66*w, 0.63*h],
            [0.33*w, 0.63*h],
        ])

    def _build_bev(self):
        dst_width_m = self.cfg.lane_width_m * self.cfg.num_lanes_in_roi
        bev_w = int(dst_width_m * self.cfg.scale)
        bev_h = int(self.cfg.ahead_m * self.cfg.scale)
        dst_pts = np.float32([
            [0, bev_h - 1],
            [bev_w - 1, bev_h - 1],
            [bev_w - 1, bev_h - 1 - self.cfg.ahead_m*self.cfg.scale],
            [0, bev_h - 1 - self.cfg.ahead_m*self.cfg.scale],
        ])
        self.dst_pts, self.bev_w, self.bev_h = dst_pts, bev_w, bev_h
        return dst_pts, bev_w, bev_h

    def _rebuild_homography(self):
        if self.src_pts is None:
            return
        dst_pts, _, _ = self._build_bev()
        self.H = cv2.getPerspectiveTransform(self.src_pts, dst_pts)
        self.Hinv = np.linalg.inv(self.H)

    def save_calibration(self, path: str):
        if self.src_pts is None:
            raise ValueError("没有可保存的标定点，请先 set_src_pts()")
        payload = {"src_pts": self.src_pts.tolist(), "cfg": asdict(self.cfg)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def load_calibration(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.src_pts = np.float32(data["src_pts"])
        self._rebuild_homography()

    def _pixel_to_bev_meters(self, px: float, py: float):
        pts = np.array([[[px, py]]], dtype=np.float32)
        bev = cv2.perspectiveTransform(pts, self.H)[0, 0]
        X_m = bev[0] / self.cfg.scale
        Y_m = (self.bev_h - 1 - bev[1]) / self.cfg.scale
        return X_m, Y_m

    @staticmethod
    def _classify_traffic_light_color(bgr_roi: np.ndarray) -> str:
        if bgr_roi is None or bgr_roi.size == 0:
            return 'unknown'
        h, w = bgr_roi.shape[:2]
        if h < 9 or w < 9:
            return 'unknown'
        seg = h // 3
        thirds = [bgr_roi[0:seg], bgr_roi[seg:2*seg], bgr_roi[2*seg:h]]

        def score_color(img, color):
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            if color == 'red':
                m1 = cv2.inRange(hsv, (0, 80, 80), (10, 255, 255))
                m2 = cv2.inRange(hsv, (170, 80, 80), (180, 255, 255))
                m = cv2.bitwise_or(m1, m2)
            elif color == 'yellow':
                m = cv2.inRange(hsv, (18, 80, 80), (38, 255, 255))
            elif color == 'green':
                m = cv2.inRange(hsv, (40, 60, 60), (90, 255, 255))
            else:
                return 0.0
            return float(np.count_nonzero(m)) / m.size

        scores = {
            'red':    score_color(thirds[0], 'red'),
            'yellow': score_color(thirds[1], 'yellow'),
            'green':  score_color(thirds[2], 'green'),
        }
        c, v = max(scores.items(), key=lambda kv: kv[1])
        return c if v > 0.05 else 'unknown'

    def update(self, frame_bgr: np.ndarray):
        if self.src_pts is None:
            self.set_src_pts(self._default_src_pts(frame_bgr), frame_bgr.shape[:2])

        res = self.model(frame_bgr, verbose=False)[0]
        dets_vehicle, dets_tlight, dets_stopsign = [], [], []

        for b in res.boxes:
            cls = int(b.cls.item()); conf = float(b.conf.item())
            if conf < self.cfg.conf_thres:
                continue
            x1, y1, x2, y2 = b.xyxy[0].cpu().numpy().tolist()
            if cls in self.VEHICLE_CLS:
                dets_vehicle.append((x1, y1, x2, y2, cls))
            elif cls in self.TLIGHT_CLS:
                dets_tlight.append((x1, y1, x2, y2, cls))
            elif cls in self.STOPSIGN_CLS:
                dets_stopsign.append((x1, y1, x2, y2, cls))

        overlay = frame_bgr.copy()

        # 车辆距离（BEV）
        dists = []
        dst_width_m = self.cfg.lane_width_m * self.cfg.num_lanes_in_roi
        for (x1, y1, x2, y2, cls) in dets_vehicle:
            px = 0.5*(x1+x2); py = y2
            X_m, Y_m = self._pixel_to_bev_meters(px, py)
            if 0 <= Y_m <= self.cfg.ahead_m and -self.cfg.lane_width_m <= X_m <= (dst_width_m + self.cfg.lane_width_m):
                dists.append((Y_m, (x1,y1,x2,y2), (X_m,Y_m)))
        min_dist = min([d[0] for d in dists], default=None)
        if min_dist is not None:
            self._ema_val = min_dist if self._ema_val is None else self._ema_alpha*min_dist + (1-self._ema_alpha)*self._ema_val

        for (_, (x1,y1,x2,y2), (xm,ym)) in dists:
            cv2.rectangle(overlay, (int(x1),int(y1)), (int(x2),int(y2)), (0,255,0), 2)
            cv2.putText(overlay, f"{ym:.1f} m", (int(x1), int(y1)-6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

        if self._ema_val is not None:
            cv2.putText(overlay, f"Nearest: {self._ema_val:.1f} m", (30,50), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,255,255), 2)
            if self._ema_val < self.cfg.alarm_dist_m:
                self._alarm_frames += 1
            else:
                self._alarm_frames = 0
            if self._alarm_frames >= self.cfg.alarm_hold_frames:
                cv2.putText(overlay, "ALERT!", (30,100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)

        # 红绿灯/停牌（不做 BEV）
        redlight_on = False
        tlight_list = []
        for (x1,y1,x2,y2,cls) in dets_tlight:
            roi = frame_bgr[int(y1):int(y2), int(x1):int(x2)]
            color = self._classify_traffic_light_color(roi)
            tlight_list.append((x1,y1,x2,y2,color))
            color_map = {'red':(0,0,255),'yellow':(0,255,255),'green':(0,255,0),'unknown':(200,200,200)}
            cv2.rectangle(overlay, (int(x1),int(y1)), (int(x2),int(y2)), color_map.get(color,(200,200,200)), 2)
            cv2.putText(overlay, f"TL:{color}", (int(x1), int(y1)-6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_map.get(color,(200,200,200)), 2)
            if color == 'red': redlight_on = True

        if redlight_on and self._ema_val is not None and self._ema_val < self.cfg.redlight_alert_dist_m:
            cv2.putText(overlay, "RED LIGHT AHEAD", (30,140), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,255), 3)

        stop_on = len(dets_stopsign) > 0
        for (x1,y1,x2,y2,cls) in dets_stopsign:
            cv2.rectangle(overlay, (int(x1),int(y1)), (int(x2),int(y2)), (0,165,255), 2)
            cv2.putText(overlay, "STOP", (int(x1), int(y1)-6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,128,255), 2)

        if stop_on and self._ema_val is not None and self._ema_val < self.cfg.stopsign_alert_dist_m:
            cv2.putText(overlay, "STOP SIGN", (30,180), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,128,255), 3)

        # 画 ROI
        for i in range(4):
            p1 = tuple(map(int, self.src_pts[i]))
            p2 = tuple(map(int, self.src_pts[(i+1)%4]))
            cv2.line(overlay, p1, p2, (255,200,0), 2)

        info = {
            "nearest_m": float(self._ema_val) if self._ema_val is not None else None,
            "red_light": bool(redlight_on),
            "stop_sign": bool(stop_on),
            "vehicle_count": len(dets_vehicle),
            "tlight_list": tlight_list,
        }
        return overlay, info
