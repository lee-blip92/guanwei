# 网页与打印共用报告组件

目标：V3 HTML/PDF 与网页共用 DecisionReport.tsx 及 decision.css，按 brief/full 使用同一报告快照和图表。仅添加 A4 分页与静态证据索引，不重新分析数据。

1. 为 DecisionReport 增加初始阅读深度、打印模式；同一组件中的折叠内容在导出时展开，引用转为证据链接。
2. 构建时产出自包含的 React 服务端渲染器；FastAPI 用本机 Node 短进程渲染 HTML，数据经 stdin，不启动额外服务。使用相同 CSS，增加 print CSS。无构建产物时明确提示构建，不回退旧模板。
3. 保留 Markdown/CSV 路径，HTML 入口与版本号继续由任务快照提供。添加组件/HTML 对照测试及错误边界。
4. 构建与回归验证；使用真实任务比较标题、统计、图表及证据，浏览器检查打印 HTML，渲染实际 PDF 页面检查 A4 分页。保留验收结果，清理临时文件。
