# 网页与打印共用报告（2026-10-31）

V3 打印 HTML 使用 `frontend/src/DecisionReport.tsx`，与网页共用同一 React 组件、`decision.css`、报告 JSON 快照及图表。原后端独立 HTML 模板已删除，Markdown 和 CSV 保持各自适合文本/明细的导出方式。V1/V2 历史报告保留原导出兼容性。

## 使用

1. 打开报告，选择“精简决策版”或“完整研究版”。
2. 点击“打印 / PDF”；新页面的报告版本、统计、观点和图表与选中阅读深度一致。
3. 点击“打印 / 另存为 PDF”，选择 A4，建议关闭浏览器页眉页脚。共享样式指定精确打印颜色，已经在关闭背景图形的情况下核查图表。

打印会展开折叠内容；弹窗原文入口转换为 E 编号及证据索引。精简版附代表性/正反证据及结论实际引用，全部关联评论明细见 CSV；完整版包含完整分类、观点与质量记录、全部有效评论原文。页面长度受实际原文和来源标题长度影响，不通过删减事实凑页数。

屏幕布局使用同一 CSS；A4 仅调整图表排列、列宽及分页。PDF 不保留网页的筛选/弹窗交互。

## 本地构建与运行

```powershell
& 'C:\Program Files\nodejs\npm.cmd' --prefix frontend run build
```

构建同时产出 `frontend/dist/report-renderer.cjs`、`report-base.css`、`report-decision.css`、`report-print.css`。后端调用本机 Node 短进程渲染，不启动第二个服务，不联网、不调用模型。评论数据经 stdin 传递，不进入命令行或日志。Node 默认从 PATH 查找，也可在 `.env` 配置 `REPORT_NODE_PATH`。

缺构建产物、Node 不可用或渲染失败时，HTML 导出返回 503 和明确提示，不悄悄回退不同模板。更新前端源文件后需重新构建。

## 验收

- 全量 Python：141 项通过（53.40 秒）；最后一轮 V3 专项22项通过；TypeScript、Vite 和 Node 共享渲染器构建通过。
- `node frontend/tests/shared-print-ui.mjs`：真实任务 brief/full 的执行摘要、一级维度、情绪、条形图、气泡数、痛点、建议、方法和版本逐项对照通过；折叠项全部展开，所有引用锚点存在。
- 既有 `decision-report-ui.mjs` 实际 API/React 验收通过，网页证据抽屉和切换功能保留。
- 原任务 v2，1000条去重评论、351条有效反馈、386条观点、903项统计/证据检查，CSV1000行/纳入351行，与网页及导出相符。未采集或调用模型，报告未重新生成。
- 实际 HTML → Chrome PDF 生成时关闭背景图形；环图、条形图、维度正负分布和优先级矩阵保留。精简PDF20页（含36条证据原文），完整PDF96页（含351条原文及完整附录），A4，无空白页。精简版全部页面及完整版分类/质量附录/原文段落和末页已渲染核查。
- 浏览器检查共享打印页面；预览留档：`examples/acceptance/shared-print-charts.png`、`shared-print-matrix.png`。
- PDF：`output/pdf/飞书-共享模板-精简版.pdf`、`飞书-共享模板-完整版.pdf`。用户原文件 `D:/飞书 决策洞察.pdf` 未修改。

复现 PDF（使用当前本机 Chrome 和已有 Playwright 依赖，不调用模型）：

```powershell
.\md\.venv\Scripts\python.exe -X utf8 scripts/render_print_acceptance.py 0de4b927-d03c-4704-a180-49b42f6ace01
```

尚有70条未分类观点与12条待复核生成表述，保持与网页一致。此次只统一渲染，未修改分析结论、筛选规则或原始评论。

## HTML 文件下载（2026-11-01）

报告工具栏新增“下载 HTML”，按当前精简/完整视图导出。HTML 接口增加 `download=true`，返回 `text/html` 和 UTF-8 中文附件文件名；打印入口继续以内联页面显示。文件内嵌样式、图表和原文证据，不依赖本地服务即可打开，原文外链访问仍需联网。

两种视图附件内容与对应预览逐字一致、附件响应头及中文文件名回归检查通过；前端构建和共享模板对照通过。浏览器实际下载成功，文件为用户下载目录的“飞书-反馈报告-精简版.html”。按钮截图：`examples/acceptance/html-download-toolbar.png`。未重新采集或分析。
