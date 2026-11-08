# 七平台扩展：使用与实际验收

日期：2026-10-30。扩展范围为 MediaCrawler 本地副本支持的全部七个平台：小红书、抖音、快手、B站、微博、贴吧、知乎。用户本轮的多平台需求覆盖了最初“仅小红书”的范围限制。

## 已交付行为

- 首页直接选择一个或多个平台，支持全选，默认小红书。各平台串行运行，独立子进程、任务目录和采集预算；不重复利用全局配置进程。
- 每个平台显示实际阶段、内容/评论数量与失败次数；单个平台失败继续后续平台。登录/验证暂停当前平台，恢复标记只发送给它。取消停止当前平台与尚未开始的平台。
- 按平台＋评论 ID 去重；内容和父评论也按平台隔离。不同平台同号内容、评论、回复不会覆盖或串上下文。知乎回答/文章内容 ID 带类型前缀。
- 评论按平台筛选；核查抽屉、模型输入、报告证据保留平台。报告与 Markdown/HTML 增加平台样本分布，CSV 保留平台列。
- 原生导出通过独立映射接入。支持 JSON/JSONL/Excel；多平台导入可指定“文件默认平台”或 `X-Platform`，无需给原生 Excel 人工增加平台列。明确记录平台优先，不猜测平台。
- 旧小红书数据库启动前自动备份，原子迁移内容复合键；历史任务、人工修正、分析和报告保留。旧报告兼容为小红书样本。

默认**每个平台**最多 5 条内容、每条内容 20 条一级评论、总评论 100 条（含回复）；七平台全选合计最多 35 条内容、700 条评论。数量不是采齐保证。只显示所选平台共同支持的排序，日期范围仍未提供；微博只取接口返回的嵌入回复。平台数量不能直接比较口碑。

## 自动测试与构建

```powershell
.\md\.venv\Scripts\python.exe -m pytest tests -q --disable-warnings --tb=short
npm.cmd --prefix frontend run build
```

实际结果：**72 passed**；TypeScript / Vite 构建通过。完整输出见 [测试输出](../examples/acceptance/multi-platform-tests.txt) 与 [构建输出](../examples/acceptance/multi-platform-build.txt)。

关键覆盖：七平台本地接口字段；六个新增平台独立子进程协议、根评论预算与含回复总预算、父文本补全、关闭回复、重复游标终止、已有预算；真实子进程登录失效暂停/恢复和取消；选择顺序、平台失败续跑与部分完成；跨平台 ID/父关系隔离；原生 JSONL 和无标题微博 Excel；文件默认平台；错误数据事务回滚；重启状态；迁移快照；人工排除后证据失效；导出一致性。

平台和模型协议替身只能证明工程行为，不代表真实网站采集成功或模型准确率。原来的小红书、证据/模型结构校验与 DeepSeek HTTP 协议测试仍保留。

## API 与页面链路

使用独立临时数据目录、回环端口 8766，避免向主工作区注入人工样本：

1. 七平台各导入 4 条人工构造记录，含 1 条重复 ID；原始 28、去重后 21、纳入 21、来源内容 7。
2. 每个平台筛选返回 3 条；“我也是”关联本平台父评论；同号评论跨平台均保留。
3. 报告保留 21 个存在的证据 ID 与平台；CSV 21 行且纳入 21 行，Markdown/HTML/JSON 导出通过。
4. 主库仍有原来的 4 个任务；真实小红书导入 DeepSeek 任务保留 381 条、纳入 152，原报告未重写。

记录见 [API 验收 JSON](../examples/acceptance/multi-platform-api-check.json)。其中 task_id 属于临时验收库，不是主工作区真实采集任务；临时库收尾清理。

React 组件通过 jsdom 连接实际本机 API，验证七平台多选、排序能力变化、每平台及合计预算、七平台设置、评论核查/排除/恢复/重分析、平台筛选、报告平台分布、证据抽屉和导出链接。输出见 [组件/API 检查](../examples/acceptance/multi-platform-ui.txt)。可用 `INSIGHT_TEST_BASE_URL` 指定隔离服务，`INSIGHT_TEST_FIXTURES=1` 仅在临时库启用人工样本。

内置浏览器另行核查了真实构建的七平台多选界面和报告分布，未依赖 jsdom 判断视觉效果：[多选界面](../examples/acceptance/multi-platform-home.png)、[报告封面](../examples/acceptance/multi-platform-protocol-report.png)、[七平台统计表](../examples/acceptance/multi-platform-protocol-distribution.png)。后两张来自明确标为“人工构造”的隔离验收样本，不是平台实采。系统打印/另存 PDF 未重新验收。

## 上游真实检查与平台登录边界

本机环境实际 import 成功七组 crawler/client/login 类，cookie 更新签名匹配。记录 [42 个文件 SHA-256](upstream/mediacrawler-multiplatform-sha256.json)，原先审计的上游文件哈希均保持不变；未编辑 `md` 源码或配置。

真实抖音执行了极小规模启动检查：1 条内容、最多 2 条评论、规则预览模式。已打开上游流程并收到真实 `waiting_login`，当前平台 dy，零内容、零评论、无程序错误。检查未替用户登录或完成验证，随后取消并清理进程。详见 [真实启动记录](../examples/acceptance/multi-platform-live-smoke.json)。

**尚未验证：抖音登录后的搜索/评论返回，以及快手、B站、微博、贴吧、知乎的真实登录采集。** 完整平台实采需要用户在采集浏览器人工登录。操作入口：主工作台 → 多平台采集 → 选择需要的平台 → 开始；暂停后手动完成登录，再点击恢复。上游或平台协议变化仍可能要求修复适配器。

本轮未重复运行 381 条 DeepSeek 付费调用；模型客户端与后端密钥机制保留，新增来源字段及证据元数据由测试覆盖。此前真实 AI 验收见 [DeepSeek 记录](DEEPSEEK_ACCEPTANCE.md)，不能由此推导新增平台的模型质量。

## 文件与许可

核心代码：`platforms.py`、`upstream_adapter.py`、`multi_bridge.py`、`crawler.py`、`ingest.py`、`db.py`；React 平台控件与来源展示位于 `frontend/src/App.tsx`。架构与数据表见 [架构文档](ARCHITECTURE.md)，平台能力与恢复限制见 [采集说明](COLLECTION.md)。

MediaCrawler 的 NON-COMMERCIAL LEARNING LICENSE 1.1 与原来源声明保留，商业用途仍需作者书面授权；采集适配层可替换。Sift 仍仅参考流程，没有引入或复制运行时代码。
