# 小规模人工标注与核查方案

## V3决策洞察核查

V3增加独立意图、对象、情绪、立场、体验依据、层级分类和研究优先级。**135项程序测试和253项报告规则检查不是语义准确率**。尚无独立人工金标，不报告准确率或时间收益。

```powershell
.\md\.venv\Scripts\python.exe -X utf8 scripts/prepare_decision_evaluation.py 27587ab4-ab78-48af-8a42-7e8c41c04feb
```

生成 `runtime/evaluation/decision-v3.comments.jsonl`、`.claims.jsonl`、`.priorities.jsonl`，不会调用模型或修改任务。当前分别40条评论、30项文案、21个议题，gold字段全空，`labelled_rows=0, metrics=null`。固定种子按相关性分层并补入重点/分化边界，不是随机总体样本。

推荐两人独立标注，先隐藏predicted字段，再仲裁争议；单人时注明没有双人一致性评估。先核查对象/产品归属和需求/疑问/体验，再处理分类粒度及推断。

| 表 | 人工填写及标准 |
| --- | --- |
| comments | `gold_relevance`、逐观点 `gold_points`（意图可多选、对象、情感、立场、依据、后果原话）、`gold_taxonomy_assignments`（一级/二级及合并理由）；核查短回复、对比归属、少数意见是否丢失 |
| claims | `gold_support=yes/partial/no/unknown`、`gold_overclaim`；原话是否忠实，结论范围是否受证据支持，假设是否冒充事实，章节是否重复 |
| priorities | `gold_priority_appropriate=yes/no/unknown`、`gold_severity_supported`；复核原话后果、串/账户来源和研究等级规则，不评价开发排期 |

逐项填写reviewer/reason并固定报告版本。重点查教程请求误计满意、其他产品误归属、相反体验、同串附和、单账户多串、严重低频和无依据后果。

只有真实标注完成后，才按轴报告混淆矩阵/多标签precision、recall、F1，分类的过度合并/碎片化/错归数、结论的yes/partial/no/unknown计数、优先级依据错误数。分母是实际标注项，空分母为null，unknown不算支持，模型自检不充当人工金标。分层指标只用于定位问题；总体估计需随机样本或合理加权。旧 `evaluation.py score` 仅覆盖其相关性和平面主题字段，不能宣称V3全部轴的效果。

修正后核查报告失效、排除项退出证据、统计/图表/CSV同源和引用有效，保留前后版本，不为提高指标删除难例。真实助手核查案例见 [V3验收](DECISION_V3_ACCEPTANCE.md)。下文保留既有评估方法。

## 当前能报告什么

自动测试用于验证程序行为，不代表相关性或主题分析准确率。真实数据已在 2026-10-30 完成 DeepSeek 调用、引用与导出验收，但尚未进行人工金标准标注，**没有准确率、召回率、节省时间或总体 AI 效果指标可报告**。本轮生成的 `runtime/evaluation/deepseek-review.csv` 有 36 条待核查记录，gold 字段留空。

## 第一轮核查

先从一项真实导入/采集任务取不超过 40 条：target、uncertain、other_product、irrelevant 每类最多 10 条，固定随机种子。再手工补查至少若干短回复、多产品对比、疑似广告、缺父评论等边界情况。分层样本刻意放大疑难样本，不能直接宣称是总体效果。

准备表格：

```powershell
.\md\.venv\Scripts\python.exe scripts\evaluation.py prepare <任务ID>
```

输出 `runtime/evaluation/review.csv`，含当前评论、笔记、父评论、来源、预测相关性和主题；`gold_relevance/gold_themes/reviewer/reason` 留空，必须由人填写。准备脚本不生成伪金标准。

推荐两位核查者独立标注，先隐藏 predicted 字段，避免锚定。只有一人时，隔天复核疑难项，并明确说明不是双人一致性评估。

相关性标准：

- target：可以确认在讨论目标软件的实际体验或相关使用反馈。
- other_product：具体反馈归属其他产品。
- irrelevant：无法提供产品体验信息的明显无关内容。
- uncertain：上下文不足、对比归属不清、含糊短评或标注者无法决定；不得为提高纳入量而硬判 target。

中文短评不直接判无关：“我也是”“更新后更严重”需检查父评论与笔记。广告只标为疑似，记录判断理由；不等同于已证实广告。无来源链接、父文本或版本信息时记录缺失。

主题标准：为实际表达的反馈贴一个或多个具体中文主题。不要根据笔记中的所有内容给每条评论复制主题；短回复只继承能够确定的指代。`gold_themes` 使用 `|` 分隔；无体验主题可填 `__none__`，待确认未评主题保持为空。

## 证据支持核查

另建 claim-level 核查表：

| 字段 | 填写内容 |
| --- | --- |
| task_id / report_version | 固定此次报告快照 |
| claim_text / category | 发现、正面表达、建议或待验证 |
| evidence_ids / quote | 原文与上下文 |
| supported | yes / partial / no / unknown |
| issue | 归属错误、过度概括、无依据因果、上下文缺失、把建议当事实等 |
| reviewer / adjudication | 核查人与争议处理 |

检查全部核心发现与所有建议，至少覆盖每个主题。ID 存在只能证明可追溯，**不能证明证据支持结论**。重点检查：排除评论是否仍影响判断；一个比较句是否把其他产品的问题归给目标产品；“我也是”是否被扩大解读；模型是否虚构画像、业务损失和优先级。

## 只对实际标注计算指标

```powershell
.\md\.venv\Scripts\python.exe scripts\evaluation.py score runtime\evaluation\review.csv
```

没有标注时返回 `labelled_rows: 0, metrics: null`。有实际标注后才计算本样本中 target 的 precision/recall 和主题标签 Jaccard；分母为零返回空值，不用 0 或 100% 替代未知。主题无体验用 `__none__` 作为显式标签。

解释：precision=正确判 target / 所有预测 target；recall=正确判 target / 人工 target。报告必须附 TP/FP/FN、样本量、采样方法、产品与规则/模型版本。分层样本指标只用于定位问题，不能推导总体准确率；要估计总体效果，另取简单随机样本或按总体分层比例加权。

claim 支持率只有人工表格填写后才计算，并分别报告 yes、partial、no、unknown 数量，不将 unknown 算成支持。双人一致性、真实模型质量与耗时比较均待实际评估后再报告。

## 问题回流

在工作台修正相关性、主题、疑似广告或排除状态，写明核查备注，再重新分析。保留修正记录与报告版本，比较引用变化。优先修复归属错误和证据不支持，再处理主题粒度；不要根据未经核查的规则计数提出业务优先级。

## V2 多维度观点核查

在原有相关性/主题/证据标注之外，逐条标注观点的目标产品归属、方面、态度、问题/提问/诉求类型；校验后果和期待是否为连续原文，附和是否有可用父评论，公告转述是否被误当成满意体验。混合情绪必须分维度标注，不能用整条负面标签覆盖所有方面。保留不同 ID 的同文，分别统计观点覆盖评论数与附和数。以独立人工复核后的金标评价模型，不把第二次模型自检视作人工准确率。

## 固定真实评论语义回归（2026-11-01）

`examples/evaluation/semantic-real-comments.jsonl` 固定10条真实评论及其原始来源、父上下文、任务ID；`gold_points/reviewer` 初始为空，不能用当前模型预测自动填成金标。原始评论文字不得为通过测试而改写。

人工复核时，至少覆盖意图、情绪、一级维度、具体对象/任务/障碍、是否同问题、引用支持程度、后果是否明确。`gold_points` 可填写 `unit_id/intents/sentiment/domain_name/issue_label`；同时填写 `reviewer`、复核时间和备注。`issue_label` 是人工认可的具体问题身份，相同问题用相同值，不直接复制模型议题ID。

```powershell
.\md\.venv\Scripts\python.exe -X utf8 scripts/evaluate_semantic_quality.py 任务ID --labels examples/evaluation/semantic-real-comments.jsonl
```

脚本只读报告，无模型调用、不重采集。无人工标签输出 `human_labelled_points=0, human_metrics=null`；存在有效人工标签才按实际标注观点计算意图micro Precision/Recall/F1、情绪/维度准确率、议题pairwise Precision/Recall。标签只覆盖已提取观点时，指标不衡量遗漏观点的召回率；固定错误案例集也不能代表总体质量。统计和ID检查则可对完整报告重复执行。

人工抽查建议：先核查全部执行摘要与Top6问题；每个多成员议题抽3条（少于3全查），正反pair逐对检查相同对象/任务；另覆盖所有高风险单条、未映射观点、模型拒绝/改写后的表述及10条随机普通评论。记录错误合并、过细拆分、对象错配、后果扩大、证据无关，修正和独立复核后才计算真实效果指标。主观语义是否成立仍需人工，不以模型审核通过替代。
