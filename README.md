# Smart_driving

```markdown
# Smart_driving 🚗🧠

## 项目简介 (Project Description)

Smart_driving 是一个基于 Python + PyQt5 + OpenCV + deep-learning (YOLO) 构建的“智能驾驶辅助系统 / 视觉检测程序”。  
它支持实时摄像头／视频输入，通过深度学习模型识别目标（如车辆、行人、物体），并在 GUI 界面展示检测结果。  

Smart_driving 的目标是：提供一个**轻量、易部署**的驾驶辅助／视觉检测原型，便于研究、教学或二次开发。

---

## 目录结构 (Directory Structure)

```

Smart_driving/
├─ main.py               # 程序入口
├─ models/               # 深度学习模型 + 辅助脚本
├─ utils/                # 工具脚本，如数据处理、格式转换等
├─ view/                 # UI 界面文件 (Qt UI / .py /布局文件)
├─ image/                # 静态资源 (图标、照片、样例图片等)
│    ├─ icons/
│    ├─ imgpath/
│    └─ captures/
├─ music/                # 报警音 / 提示音 / 音频素材
├─ videos/               # 测试视频或素材视频
├─ requirements.txt      # Python 依赖列表
└─ README.md             # 本说明文档

````

> ⚠️ 模型权重 (.pt)、大视频文件、不建议放入 Git 仓库（可另行下载或存储）。

---

## 环境依赖 (Dependencies)

- Python 3.10  
- PyQt5  
- OpenCV (cv2)  
- torch + (ultralytics / yolov8)  
- 其他依赖（详见 `requirements.txt`）

安装依赖：
```bash
pip install -r requirements.txt
````

---

## 本地运行 (Run Locally)

在源码根目录下执行：

```bash
python main.py
```

程序将打开 GUI 界面，你可以选择摄像头或视频文件进行目标检测/识别。

---

## 打包成可执行文件 (Build Executable)

推荐使用 PyInstaller 打包：

```bash
cd path/to/Smart_driving

python -m PyInstaller --noconfirm --clean --onedir main.py
```

然后手动将 `image/`, `models/`, `music/`, `view/`, `videos/` 等资源目录复制到 `dist/main/` 下，与 `main.exe` 同级。
最后执行：

```bash
cd dist/main
main.exe
```

这样即可得到一个“拷贝即用”的可执行版。

---

## 注意事项 & 提示 (Notes & Tips)

* **路径处理**：代码中资源读取建议使用 `resource_path()` 函数，根据是否被 PyInstaller 打包自动定位资源。
* **模型权重**：请确保 `.pt` 模型文件在运行目录中存在，且路径正确。
* **Qt 插件**：若打包后 GUI 显示异常或多媒体功能丢失，请确保包含 Qt 的 `plugins/` 目录（platforms / imageformats / mediaservice 等）。
* **数据库 / 配置文件**：若项目包含数据库或配置，请避免将含敏感信息（如密码）的文件直接推到 GitHub。
* **Git 忽略**：建议在 `.gitignore` 中排除 `dist/`、`build/`、模型权重、大文件等，以保持仓库清洁。

---

## LICENSE / 作者 (License & Author)

© 2025 你 的 名字 / 昵称（hane）

该项目基于 MIT License（或你选择的许可证）。

欢迎 Fork / Issue / Pull Requests！

```

---

如果你同意，我可以 **直接帮你生成一个 README.md 文本**，并把 **中文版 + 英文版**都给你 —— 这样对你未来申请实习 /分享项目也更友好。
::contentReference[oaicite:2]{index=2}
```
