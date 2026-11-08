# 评论观点与产品洞察 V2 Implementation Plan

**Goal:** 将逐条主题标签升级为可统计、可追溯的产品维度观点、痛点、需求与建议。

**Architecture:** 继续 React / FastAPI / SQLite 与现有后台任务；采集保持独立。版本化分析 JSON 存在 comments.analysis，先提取观点，再带上下文归并具体话题，程序统计，最后按实际证据生成报告。旧报告保留兼容，重新分析可以升级；无 Redis、向量数据库或微服务。

**Tech Stack:** Python、Pydantic、DeepSeek/OpenAI-compatible JSON API、TypeScript/React。

## 决策与边界

- 采用用户参考中的 ABSA。一条评论可同时表达多个关注点、情绪、问题和需求；允许未知和无具体反馈。
- 身份去重沿用平台＋评论 ID。同文但不同 ID 只提示，不删除；语义相似归组且保留人数口径为评论数。
- 比较评论按观点判断产品归属，只用目标产品观点。短附和保留当前原话与父评论引用，明确“依赖上下文”。
- 每个观点包含 aspect/topic/sentiment/kind/pain_point/impact/expected_outcome/keywords/quote；缺失信息为 null。impact 仅允许用户明确表达，不推算商业损失。
- 分批模型输出严格 JSON，经 Pydantic、ID 和当前/父评论原文校验。使用输入＋提示词/模型指纹缓存已校验结果。
- 主题归并输入观点摘要和原文例子，不仅比较标签名；同一具体问题跨表达归并。关键词及方面/话题计数按唯一评论 ID 计算，支持一评多观点。
- 建议根据各话题证据生成并校验；无支持时不展示。统计数量由程序生成。频次是核查线索，不冒充业务优先级。
- 原始数据、模型密钥、多平台隔离、人工修正与排除优先级不变。模型内容不替代人工质量评估。

## 执行任务

1. **结构与兼容**：扩展 backend/insight/schemas.py（复用现有 Strict/Claim 定义）；TaskConfig 支持 analysis_version；保留 V1 回归，新增 V2 测试。定义数据与提示词，暴露 JSON Schema。
2. **分批理解**：新增 aspect_analysis.py，细粒度提取、引用校验、有界重试、成功结果缓存；确定性规则预览明确能力限制。测试混合情绪、多产品、附和、未知字段、排除与缓存失效。
3. **聚合与报告**：语义归并、方面情绪表、关键词提及数、具体痛点/需求、同文提示；有依据的建议与验证问题。测试一评多观点不重复计算同一话题、证据完整、导出一致。
4. **前后端接入**：现有分析入口分发版本；任务详情可查看观点；报告新增关注点/关键词/痛点，CSV 带结构化明细，Markdown/打印 HTML 保留统计和证据。
5. **验证**：完整 pytest、前端构建、隔离组件/API 检查；使用少量已有真实评论调用已配置模型，人工核查原文支持；保留真实结果与不足，不编造质量指标。
6. **交付**：更新 AI_DESIGN、README 与 V2 验收记录，说明 JSON/缓存/统计与人工修正规则。应用保持本机可运行，旧任务不自动重采或重分析。

验证命令：`md/.venv/Scripts/python.exe -m pytest tests -q --disable-warnings --tb=short`、`npm.cmd --prefix frontend run build`。本目录无根 Git 仓库，不创建提交或新 worktree。

## 验收收尾：计费错误下的离线恢复

DeepSeek 真实请求已返回 HTTP 402，不再新增模型调用。新增 `report_snapshots` 保存失效前报告；`POST /api/tasks/{id}/report/recount` 后台复算最新有效观点、统计与证据，仅复用引用评论及观点全部未变化的旧文案。语义分组仅在完整成员未变化时复用，其余按原始观点标签分组。报告明确标为部分完成、离线复用。新增测试禁止离线路径调用模型，验证人工改判/排除与父评论依赖、导出数量和快照；真实任务从本轮已有 JSON 备份恢复快照后离线验收。
