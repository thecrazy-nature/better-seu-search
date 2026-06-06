# 东南大学官网智能检索 MVP

这是一个本地可跑的“官网信息智能检索器”MVP。它按你设定的原则工作：

1. 同学输入自然语言问题。
2. AI Query Planner 解析意图、拆 `sub_questions`，生成 `retrieval_keywords`、少量同义查询、学院、年级、身份等结构化字段。
3. 本地检索用 FTS / LIKE / BGE embedding 并行粗召回候选，尽量提高召回，不在这一层过度替用户下结论。
4. 本地排序用 BM25/关键词/BGE 相似度、标题命中、来源权威、发布时间和用户画像做轻量综合排序。
5. 单次 AI Answer Composer 阅读排序后的精简证据包生成答案，并列出参考消息源。
6. 硬规则校验会确保 URL 和来源来自数据库，`publish_date` 只使用数据库字段。
7. AI Reranker 和 Evidence Judge 默认关闭；需要诊断或更强证据过滤时可显式打开。没有配置 AI Key 时，Planner/总结会明确提示 AI 不可用。

## 功能范围

- 教务处和学校官网公开页面爬取
- SQLite FTS 全文检索，支持文档级和证据块级检索
- 自然语言意图识别
- 检索计划包含 `sub_questions` 和 `retrieval_keywords`
- 少量高置信固定别名扩展，主要语义召回交给 AI Planner 和 BGE embedding
- 学院、年级、学生类型参与排序
- 找原文 / 问结论 / 查流程 / 查截止时间 / 找附件等意图
- 可选 AI Reranker / Evidence Judge，用于诊断或更严格证据过滤
- 答案硬校验，移除未收录 URL，并只保留真实命中来源
- AI 总结和参考来源展示
- 结构化依据片段 `evidence`
- 过期、历史来源、缺少适用对象等风险提示 `warnings`
- 检索计划支持 `exclude_terms`、`time_scope`、`authority_preference`
- 常见搜索结果缓存，索引更新后自动失效
- 同一事项轻量聚合，优先保留最新和权威来源
- `document_chunks` 记录标题、正文块、附件正文块、heading、page、attachment_name、chunk_kind、token_count、keywords、embedding
- `source_profiles` 来源分级表，教务处、研究生院、学院、学校官网可参与权威排序
- `crawl_tasks` 持久化后台爬虫任务状态
- 搜索接口支持 `session_id`，可承接“那研究生呢？”这类连续追问
- 标题、正文、附件名和可解析附件正文预处理关键词 `keywords`
- PDF / DOCX / XLSX 附件正文解析和回填，`.doc/.xls` 老 Office 附件会做保守文本抽取，附件正文可作为独立 `attachment_text` 证据块召回
- `/api/health` 返回文档、chunk、附件解析和附件正文 chunk 统计
- 离线评测脚本，可不启动 API 服务直接跑检索回归
- 数据库质量审计报告，检查发布日期、附件解析、chunk 分布、标签覆盖、URL 和 embedding 状态
- 两年时间窗口抓取，默认 `CRAWL_DAYS_BACK=730`
- 栏目覆盖报告 `backend/data/crawl_report.json`
- 本地网页界面

## 快速开始

```powershell
cd D:\Jela\项目\更好用的官网检索
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
Copy-Item backend\.env.example .env
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_server.ps1 -Foreground
```

打开：

```text
http://127.0.0.1:8000
```

Windows 下也可以直接双击根目录的 `start_server.cmd`。启动脚本会自动停止旧的 8000 端口进程、修复索引元数据并启动服务。前台窗口保持打开时服务持续运行，关闭窗口或按 `Ctrl+C` 会停止服务。

`python -m backend.app.seed_demo` 只用于空库演示，不建议在真实索引里运行；它会写入 `/demo/` 演示链接，影响真实检索结果。

## 接入 DeepSeek API

编辑根目录 `.env`：

```text
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=你的 DeepSeek key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

配置后：

- 查询规划默认走 AI JSON 解析，由 AI 理解意图、拆子问题、生成检索关键词
- 检索候选默认走本地 FTS / LIKE / BGE embedding 粗召回和轻量排序，不再默认调用 AI Reranker
- 答案默认由单次 AI Answer Composer 基于排序后的检索来源生成
- AI Reranker 和 Evidence Judge 默认关闭，可在需要诊断排序或更严格证据筛选时开启
- 仍会保留“不得脱离来源回答”的系统规则
- 最后的硬规则校验仍会移除未收录 URL，并把来源日期固定为数据库里的 `publish_date`

三段 AI 模块的默认开关如下。调延迟或做确定性回归时，可以把某一段改成 `auto` 或 `off`：

```text
AI_PLANNER_MODE=always
AI_RERANKER_MODE=off
AI_EVIDENCE_JUDGE_MODE=off
AI_ANSWER_COMPOSER_MODE=simple
```

当前主流程是：

```text
用户问题
  -> AI Query Planner
  -> 本地 FTS / LIKE / BGE embedding 并行粗召回与轻量排序
  -> 单次 AI Answer Composer
  -> 硬规则校验
```

也可以使用任何 OpenAI-compatible 服务：

```text
AI_API_KEY=你的 key
AI_BASE_URL=https://your-compatible-endpoint
AI_MODEL=你的模型名
```

## 更新真实索引

网页右上角点击“更新索引”，或运行：

```powershell
python -m backend.app.crawl
```

网页触发更新时会创建后台任务，前端轮询进度；命令行运行仍是同步执行。默认会抓取公开页面，并控制请求频率。首次抓取可以先保持小规模，后续再扩大栏目和学院官网。

抓取策略说明：

- 从教务处和学校官网首页、主要栏目入口出发做同域 BFS。
- 自动发现 `list.htm`、分页、详情页和附件链接。
- 可解析 PDF / DOCX / XLSX 附件正文，并把附件摘录写入检索证据块。
- 只把默认两年内文章写入索引，旧文章会进入覆盖报告的 skipped_old_count。
- 抓取后生成覆盖报告，记录 visited_count、document_count、list_pages、article_pages、hit_page_limit。
- 不能数学上保证“绝对无遗漏”，但可以通过覆盖报告发现是否达到页数上限、是否有栏目未进入队列，再补 seed。

可以用环境变量追加更多官网来源，不需要改代码：

```text
EXTRA_SEED_SITES_JSON=[
  {
    "source": "计算机科学与工程学院",
    "base": "https://cse.seu.edu.cn/",
    "seeds": ["https://cse.seu.edu.cn/"]
  }
]
```

## 评测

启动服务后评测 API：

```powershell
python -m tests.run_query_eval --base-url http://127.0.0.1:8000
```

不启动服务，直接评测本地检索链路：

```powershell
python -m tests.run_query_eval --mode local --disable-ai --sleep 0
```

评测输出会写入：

```text
tests/outputs/latest_summary.md
tests/outputs/query_eval_*.json
tests/outputs/query_eval_report.md
```

生成更易读的 Markdown 测试报告：

```powershell
python -m tests.render_query_eval_report
```

报告会集中展示失败项、缺失关键词、误命中禁用词、Top 来源、来源 URL 和答案摘要，方便判断是检索、标签还是答案抽取出了问题。

生成数据库质量审计报告：

```powershell
python -m tests.audit_database_quality
```

报告输出到：

```text
tests/outputs/database_quality_report.md
tests/outputs/database_quality_report.json
```

它会检查文档数、chunk 数、附件解析率、缺发布时间、旧文档比例、标签覆盖、URL 年份一致性、embedding 状态和高风险样例。

## 附件正文回填

对已有索引中的附件补解析正文，不必重爬页面：

```powershell
python -m backend.app.backfill_attachments --limit 50
```

省略 `--limit` 会扫描全部带附件文档。解析成功后，附件正文会写回 `attachments_json`、文档正文和 `document_chunks`，并生成 `chunk_kind=attachment_text` 的独立证据块；PDF 会保留页码，XLSX 会保留工作表名。

老式 `.doc/.xls` 附件由于格式本身不稳定，会使用更保守的正文抽取和噪声清洗。若调整过清洗规则，可以只刷新这类旧 Office 附件：

```powershell
python -m backend.app.backfill_attachments --refresh-legacy
```

本轮已在当前本地数据库完成全量附件回填和旧 Office 清洗：

```text
documents=470
chunks=2495
docs_with_attachments=157
attachments=356
attachments_with_text=324
attachments_with_pages=132
attachments_with_sheets=25
attachment_text_chunks=1192
parsed_by_ext={.pdf:132, .docx:88, .doc:73, .xlsx:25, .xls:6}
remaining_without_text={.pdf:11, .docx:3, .doc:5, .rar:2, .zip:1, .jpg:10}
```

也可以直接查看健康接口确认当前索引状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## Embedding 回填

当前推荐使用本地 BGE embedding。它和 FTS / LIKE 是并行召回通道，用来弥补官网术语、学生口语和附件正文之间的字面差异。

```powershell
python -X utf8 -m backend.app.backfill_embeddings --metadata --refresh
```

回填结果写入 `document_chunks.embedding_json`。

## API

```text
POST /api/search
POST /api/crawl
GET  /api/health
GET  /api/documents/{id}
```

响应中最重要的字段：

```text
query_plan：AI/规则生成的检索计划
query_plan.sub_questions：用户问题拆出的证据子任务
query_plan.retrieval_keywords：本地召回使用的核心检索词
hits：最终参考消息源
answer.answer：面向用户的总结
answer.evidence：可核验的依据片段
answer.warnings：准确性和时效性提示
```

`GET /api/health` 会返回索引统计，重点看：

```text
attachments_with_text：已解析出正文的附件数
attachment_text_chunks：可被检索召回的附件正文证据块数
```

意图和输出预设在：

```text
backend/app/ai/presets.py
```

DeepSeek 会被要求从固定意图中选择，并按固定输出结构生成答案，避免每次临场发挥导致接口不稳定。

请求示例：

```json
{
  "query": "计算机学院大二能不能转专业？",
  "profile": {
    "college": "计算机科学与工程学院",
    "grade": "2024级",
    "student_type": "本科生"
  },
  "limit": 10
}
```

## 后续优化路线

- 对剩余未解析附件做专项处理：区分 404/过大/图片/压缩包/低置信旧 Office，并考虑接入 LibreOffice、antiword、xlrd 等更强解析器。
- 继续评估 BGE 向量召回质量，并按测试结果引入本地 reranker。
- 做后台来源 seed 管理和覆盖率看板，优先补研究生院、各学院官网和高权威栏目。
- 做更强的重复通知/历年通知聚合，默认回答最新有效版本。
- 增加更多高难度评测样例，覆盖附件正文、排除词、连续追问和跨来源冲突。
- 继续把同义词表保持在少量确定性别名，避免用机械字典替代语义召回。

## Embedding Provider

当前推荐使用本地 BGE embedding，兼顾中文语义召回效果和本地运行成本。`hash` 仍保留为无需额外依赖的离线兜底模式。

可选配置：

```text
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
EMBEDDING_BATCH_SIZE=16
EMBEDDING_MAX_CHARS=2400
HF_ENDPOINT=https://hf-mirror.com

EMBEDDING_PROVIDER=hash

EMBEDDING_PROVIDER=api
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_BASE_URL=https://your-openai-compatible-endpoint
EMBEDDING_MODEL=text-embedding-3-small
```

本地模型需要安装 `sentence-transformers`；API 模式使用 OpenAI-compatible embeddings 接口。首次切换 provider 或 model 后，运行：

```powershell
python -X utf8 -m backend.app.backfill_embeddings --metadata --refresh
```

## 一键启动说明

如果只是想在一台新 Windows 设备上把项目跑起来，可以在仓库根目录直接执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_server.ps1 -Foreground
```

也可以直接双击根目录的 `start_server.cmd`。

首次启动时，脚本会自动完成这些步骤：

- 查找本机可用的 Python
- 在项目根目录创建 `.venv`
- 按 `backend\requirements.txt` 安装或补齐依赖
- 修复索引元数据并启动服务

启动成功后打开：

```text
http://127.0.0.1:8000
```

注意：

- 机器上需要先安装 Python
- 首次启动需要联网下载依赖，时间会比后续启动更久
