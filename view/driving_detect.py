
import time

import torch
import numpy as np

from models.experimental import attempt_load
from utils.datasets import  letterbox
from utils.general import non_max_suppression, \
    scale_coords,   set_logging
from utils.torch_utils import select_device, time_synchronized

class driving_detect():
    def __init__(self):
        self.device = 'cpu'
        self.imgsz = 320
        self.weights = 'best.pt'
        # self.weights = 'yolov7.pt'
        self.trace = False
        self.augment = False
        self.conf_thres = 0.25
        self.iou_thres = 0.45
        self.classes = None
        self.agnostic_nms = False

        # Initialize
        set_logging()
        self.device = select_device(self.device)
        self.half = self.device.type != 'cpu'  # half precision only supported on CUDA

        # Load model
        self.model = attempt_load(self.weights, map_location=self.device)  # load FP32 model
        self.stride = int(self.model.stride.max())  # model stride  步长


    def detect(self, frame):
        '''
        检测每一帧，然后绘制到画面中
        :param frame:
        :return: labels, boxs
        '''
        #进行矩阵训练，把原来的图片画面进行缩放
        img = letterbox(frame, self.imgsz, stride=self.stride)[0]
        img = img[:, :, ::-1].transpose(2,0,1) #rgb
        img = np.ascontiguousarray(img)

        # Get names and colors
        names = self.model.module.names if hasattr(self.model, 'module') else self.model.names

        t0 = time.time()
        img = torch.from_numpy(img).to(self.device)
        img = img.half() if self.half else img.float()  # uint8 to fp16/32
        img /= 255.0  # 0 - 255 to 0.0 - 1.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)


        # Inference
        t1 = time_synchronized()
        with torch.no_grad():  # Calculating gradients would cause a GPU memory leak
            pred = self.model(img, augment=self.augment)[0]
        t2 = time_synchronized()

        # Apply NMS
        pred = non_max_suppression(pred, self.conf_thres, self.iou_thres, classes=self.classes,
                                   agnostic=self.agnostic_nms)

        t3 = time_synchronized()

        labels = []
        boxs = []

        # Process detections
        for i, det in enumerate(pred):  # detections per image
            s = ''

            if len(det):
                # Rescale boxes from img_size to im0 size
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], frame.shape).round()

                # Write results
                for *xyxy, conf, cls in reversed(det):
                    label = f'{names[int(cls)]}:{conf:.2f}'
                    labels.append(label)
                    box = [int(i.item()) for i in xyxy]
                    boxs.append(box)

            # Print time (inference + NMS)
            print(f'{s}Done. ({(1E3 * (t2 - t1)):.1f}ms) Inference, ({(1E3 * (t3 - t2)):.1f}ms) NMS')

        print(f'Done. ({time.time() - t0:.3f}s)')

        if len(labels) == 0:
            return None,None
        else:
            return labels, boxs