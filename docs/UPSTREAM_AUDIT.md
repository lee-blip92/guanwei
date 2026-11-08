# 上游核查、许可与复用边界

核查日期：2026-10-29。先检查上游，再实现本应用。未假设任一仓库已经提供完整的网站或稳定公共 API。

2026-10-30 扩展核查：已检查本机七个平台的 core/client/field/login、store 字段及贴吧/知乎模型，共记录 [42 个文件 SHA-256](upstream/mediacrawler-multiplatform-sha256.json)。全部 crawler/client/login 类可由本机环境真实加载，cookie 接口匹配；修正了贴吧真实 crawler 类名为 `TieBaCrawler`。先前审计的文件哈希保持不变。各平台具体分页、排序与回复差异见 [采集说明](COLLECTION.md)。

## MediaCrawler：用户提供的本地 `md` 副本

来源：https://github.com/NanmiCoder/MediaCrawler 。本地没有 `.git`，无法取得 commit；已为 README、LICENSE、核心采集、客户端、存储与接口文件记录 [SHA-256](upstream/mediacrawler-local-sha256.json)。此审计对应用户当前下载副本，不声称等于远端最新 HEAD。

读取了：README 的安装、CDP 与 WebUI 部分；`pyproject.toml`；`config/base_config.py`、`config/xhs_config.py`；`cmd_arg/arg.py`；`main.py`；`media_platform/xhs/{core,client,login,exception,field}.py`；`store/xhs/{__init__,_store_impl}.py`；`api/routers/{crawler,data,websocket}.py` 与进程管理代码。

实际能力与边界：

| 项目 | 检查结果 | 本应用处理 |
| --- | --- | --- |
| 采集 | 本机支持 xhs/dy/ks/bili/wb/tieba/zhihu | 开放七平台关键词与多选，独立子进程串行；不复用全局 UI |
| 登录 | Playwright / CDP，独立浏览器上下文，保存本地登录状态 | 用户手工登录；检查失败时暂停，显式恢复 |
| WebUI / API | 自带 React WebUI 与 FastAPI `/api/crawler/start,stop,status` 等，管理全局采集进程 | 不复用全局服务，避免任务/配置冲突；不宣称是稳定 SDK |
| 原小红书搜索范围 | 搜索方法页大小 20；`search()` 会把 max notes 提高至 20；排序有 general/popularity/time descending | 不调用其默认 search 循环；应用桥接器执行有界搜索，控制全局评论/笔记数 |
| 日期 | 当前接入方法没有日期范围参数 | 前端不提供无效时间筛选 |
| 子评论 | 默认实现的数量统计和根评论数量混合，嵌入回复与追加回复路径不同 | 适配层分开遍历，强制含回复总上限，补全父 ID/文本 |
| 数据 | 笔记与评论分开；评论无笔记标题/原文 URL/来源词；缺失父文本常见 | 独立规范化契约，按 note ID / parent ID 关联 |
| 安全验证 | 401/403/429、461/471、300011 等异常，不同路径可能捕获或吞掉错误 | 在客户端调用边界暂停；不调用验证码绕过逻辑，不把上游日志转发到页面 |
| 恢复 | 开源版不提供本应用的持久化断点游标恢复契约 | 原进程暂停可继续；进程中断后是重新搜索并按 ID 合并，明确说明局限 |

**许可：NON-COMMERCIAL LEARNING LICENSE 1.1。** 仅授权非商业学习研究，限制大规模采集；商业用途需要版权人书面同意。完整许可保留于原 `md/LICENSE`，另存 [审计副本](upstream/MediaCrawler-LICENSE.txt)。它不是允许任意商业使用的宽松开源许可证。本项目没有将其重新许可为 MIT。

实际复用：通过 Python import 加载七平台客户端、CDP 浏览器管理、请求签名和基础 crawler 启动流程。未复制或编辑上游源码；在本应用桥接器替换搜索循环、登录入口和事件输出。这种运行依赖仍须遵守许可。替换 `crawler.py`、`crawler_bridge.py`、`multi_bridge.py`、`upstream_adapter.py` 可换采集来源；导入和分析不依赖上游内部模型。当前接口检查针对本地副本，不保证上游未来版本兼容；多平台协议替身不代表平台实采通过。

## Sift：远端源码审计

来源：https://github.com/Gitter09/sift 。直接 `git clone` 网络不可达，改用 GitHub API + raw.githubusercontent.com 只读取得当前 README、LICENSE、源码和配置。API 返回 main tree SHA：`5e0af723277b05a44fbc3ea76fc765d52ebf4c37`。审计材料在 `docs/upstream/sift`，包含原始 LICENSE，源码仅作参考存档，不会被应用 import。

真实能力：Click CLI + Rich 终端 UI；多来源采集适配、去重、相关性/消歧、句向量、UMAP/HDBSCAN、LLM 主题分析与 Markdown/JSON 报告。README 把 Web dashboard 列为 roadmap，不提供本项目需要的完整 Web 应用；未发现可直接依赖的 FastAPI 后端契约。

本次特别核对的五项问题：

1. `sift/models.py` 的 `FeedbackItem.__post_init__()` 用 `source:(url or text)` 做 hash。不同评论共用笔记 URL 时确实可能被合并。应用改为平台＋评论 ID；缺 ID 不猜测去重。
2. `scrapers/simple_sources.py` 多处 `len(text)<20`，另有 `<40`。这些阈值用于其部分来源，不适合直接用于中文评论。本应用没有短文本长度排除规则。
3. 同文件部分论坛/导入路径要求 `product_name.lower() in text.lower()`。本应用通过笔记和父评论上下文判断，不要求每条评论出现产品名。
4. `config.yaml` 默认 `all-MiniLM-L12-v2`；`embedder.py` 直接嵌入 item.text。中文任务不照搬默认模型。第一版不引入向量聚类，采用分批 LLM 提取与标签归并；未来如引入需选中文或多语言模型并实测。
5. analyzer 的 cluster label/summary/severity 与 overall insights 没有逐条 claim→comment ID 契约。其报告有 representative quotes，但不等于每个结论可核查。本应用要求结构化证据 ID，引用原文匹配校验，程序计算主题计数与比例。

**许可：MIT，Copyright (c) 2025 Harshit Singh。** [原许可](upstream/sift/LICENSE) 已保留。没有商业用途限制，但复制代码或实质部分时必须保留版权与许可。

最终复用清单：

- 参考：来源适配分离、分阶段处理、先归并再写报告的思路。
- 未复用：抓取代码、FeedbackItem hash、长度过滤、英文 embedding、UMAP/HDBSCAN、终端 UI、severity/业务优先级评级。
- 本应用重新实现：中文相关性与短回复上下文、不可变原始记录、SQLite 任务、人工修正审计、分批 JSON schema、证据引用校验、React 工作台、导出和异常恢复。
- **没有复制 Sift 运行时代码，没有修改其仓库，也未把它作为安装依赖。** 审计存档保留原许可与来源，不参与应用执行。
