# RAG 检索问答与引用

最后更新：2026-11-12。P5 提供私有知识库精确检索、单轮有据问答、SSE 和历史引用。

## 如何使用

1. 用自己的 Bearer 令牌创建知识库，上传公开或脱敏的 TXT、Markdown 或文本 PDF。等文档状态变为 `ready`。
2. 向 `POST /api/v1/knowledge-bases/{kb_id}/search` 提交 `{"question":"图书馆周五几点关闭？","top_k":3}`。查看排序、相似度和命中的分块正文。
3. 创建会话。向 `POST /api/v1/conversations/{conversation_id}/rag` 提交同一问题及 `knowledge_base_id`。结果含助手消息与 `citations`。加 `?stream=true` 时，以最终 `done` 内容为准。
4. 用引用的 `document_id` 查询文档分块，按 `chunk_id` 找到对应内容。用 `page_number` 查看原 PDF 页，或通过文档的 `/source` 下载原文件。历史引用通过 `GET /api/v1/conversations/{conversation_id}/messages/{message_id}/rag` 查询。

本地配置见 [.env.example](../../.env.example)。`AGENTHUB_RAG_TOP_K` 默认 5（1–20），`AGENTHUB_RAG_MIN_SIMILARITY` 默认 0.45（0–1）。`AGENTHUB_RAG_CONTEXT_CHARACTERS` 默认 8000（1–80000），计算证据加入用户 JSON 后新增的字符数。整条提示还受 `AGENTHUB_MODEL_CONTEXT_CHARACTERS` 约束，问题也受 Embedding 输入上限约束。请求可覆盖 Top-K 和相似度阈值。接口字段与错误见 [API 契约](../API_DESIGN.md#p5-检索与会话问答)。

## 第六课：沿一次真实问答读代码

| 代码 | 本轮职责 |
| --- | --- |
| [api/rag.py](../../src/agenthub/api/rag.py) | FastAPI 收到请求。完成鉴权后释放认证事务，调用 Embedding，协调 SQL、生成和响应。 |
| [rag/service.py](../../src/agenthub/rag/service.py) | `prepare` 验证会话及知识库，复用 P3 租约，原子创建消息对和 `rag_runs`。`finish` 复查证据并写入结果。 |
| [rag/retrieval.py](../../src/agenthub/rag/retrieval.py) | `eligible_chunks` 用 JOIN 约束所有权、文档状态、入库任务和版本。`search` 加上模型空间条件，执行余弦排序。`prompt_for` 按预算选择完整分块。`validate_answer` 解析实际引用。 |
| [embeddings/provider.py](../../src/agenthub/embeddings/provider.py) 与 [chat/provider.py](../../src/agenthub/chat/provider.py) | 现有百炼适配器把问题映射到文档所在向量空间，DeepSeek 适配器根据本轮证据生成答案。 |
| [rag/models.py](../../src/agenthub/rag/models.py) 与 [chat/service.py](../../src/agenthub/chat/service.py) | RAG 行保存证据位置和检索观测数据。普通聊天与 RAG 共用同一会话租约，组装普通提示时排除 RAG 历史对。 |

### 检索和生成各自负责什么

余弦相似度比较两个向量的方向。SQL 用 `1 - cosine_distance` 得到分数，先检查分数是否达到阈值，再按距离升序取最多 K 条。K 控制数量，阈值控制最低分，两者可以独立调节。相同分数时按分块 ID 排序。候选必须处于同一模型空间，即使向量维度相同，也要匹配模型和端点。精确检索逐个比较合格候选，适合建立可复验的排序基线。 [pgvector 官方文档](https://github.com/pgvector/pgvector#querying)

检索负责找证据，生成负责读证据并回答。`prompt_for` 把系统指令放在单独消息里，把问题和带 `S1` 等标签的分块作为 JSON 数据。字符预算按序列化后的长度计算，包含引号、换行转义与标签。过大的分块会跳过，后面的较小分块仍有机会入选。字符是工程上可复现的上限，模型的实际 token 用量来自供应商。空上下文时程序直接返回证据不足。

生成后只接受实际出现在答案里、且能与本轮证据对应的标签。模型也可通过固定标记表示证据不足。服务端能验证标签身份、所属分块及当前位置，语义是否支持答案仍需读原文或用标注样本评估。会话持久化可回看问答，每轮应提交完整问题，历史问答内容不会自动进入下一轮提示。

### 为什么这样处理并发

只在准备和提交两个阶段持有数据库锁。模型调用可能持续数秒，短事务减少连接与锁占用。准备时占用会话租约，两个并发请求不能写出交错的消息。提交时在知识库锁内复查模型收到的所有分块。P4 的删除也拿这把锁，先完成删除的一方会使这次问答失败。历史答案可保留，删除来源后引用的 `available` 变为 false。SSE 的草稿可提前显示，收到 `done` 才算正式完成。

替代方案包括 HNSW、全文搜索与向量混合、重排以及模型结构化输出。HNSW 可以降低大库搜索耗时，需要测量索引召回和权限过滤的影响。全文搜索适合型号等精确词。重排需要额外计算或模型调用。当前标签方式复用既有文本适配器，结构化输出可在供应商能力与契约确认后评估。

### 常见错误与面试方向

常见错误包括把余弦距离按降序排序、以 Top-K 替代最低阈值、跨模型比较同维向量、向模型发送后才做权限过滤、把检索到的所有分块都列为引用，以及把 SSE 草稿当成持久化答案。

面试可从本项目出发说明如何拆分检索与生成指标、为什么权限条件必须进入 SQL，以及为什么在远程调用期间不持有事务。用 `retrieved_count`、`context_count`、分块 ID 与模型用量举例，说明如何定位失败。验收样本、实际耗时和限制见 [P5 验收](../acceptance/P5_2026-11-12.md)。

## 学习检验

1. `top_k=5` 时，为什么搜索结果可能只有一条？分数相同时又按什么排序？
2. 若答案只引用 `[S2]`，为什么最终引用列表只有 S2，提交前却仍检查 S1？
3. `retrieved_count=4` 而 `context_count=0` 时，先看哪个函数和哪两项配置？

**代码练习**　给 `tests/test_rag_integration.py` 增加同分场景，上传两份会生成相同向量的短文，用 `top_k=1` 和 `top_k=2` 请求，比较返回的分块顺序。

**项目思考**　有一批用户提问得到错误答案。你会怎样用命中、上下文和引用的数据，区分分块问题、检索问题与生成问题？请设计一个可复现的排查顺序。
