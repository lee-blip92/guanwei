# 第三方来源与许可

## MediaCrawler

- 项目：https://github.com/NanmiCoder/MediaCrawler
- 本机源码：`md/`（用户预先提供，未修改）
- Copyright (c) 2024 relakkes@gmail.com；源码中还包含 2025 年版权声明。
- 许可：**NON-COMMERCIAL LEARNING LICENSE 1.1**。
- 许可全文：`md/LICENSE` 与 `docs/upstream/MediaCrawler-LICENSE.txt`。
- 用途：独立采集适配器加载其浏览器与小红书、抖音、快手、B站、微博、贴吧、知乎客户端；不复用其全局 WebUI 服务。
- 限制：仅限非商业学习研究；禁止大规模/干扰平台采集；商业用途需要版权人书面同意。应用自己的许可不会覆盖此限制。

## Sift

- 项目：https://github.com/Gitter09/sift
- Copyright (c) 2025 Harshit Singh。
- 许可：MIT，全文在 `docs/upstream/sift/LICENSE`。
- 用途：分析流程参考与源码审计存档；没有复制运行代码或安装其依赖。

## 前后端运行依赖

React / React DOM、Vite、TypeScript、lucide-react、FastAPI、Uvicorn、Pydantic、httpx、python-dotenv、openpyxl 等依赖分别保留各自发行包内的许可证。`frontend/package-lock.json` 固定本次前端依赖解析，`backend/requirements.txt` 列出兼容范围。第三方依赖安装包的许可与来源不因本应用而改变。
