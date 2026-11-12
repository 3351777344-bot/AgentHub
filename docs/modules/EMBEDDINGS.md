# Embedding 接入模块

日期：2026-11-12。百炼北京 qwen3.7-text-embedding-flash 使用 1024 维，已通过真实公开文本验证。只允许公开或脱敏验收数据，独立预算上限 10 元。P4 worker 用它生成分块向量，P5 用相同适配器生成查询向量。本文保留适配器说明及 P3 验收历史。在线链路见 [RAG 模块](RAG.md)。

## 配置

本机 .env 新增以下独立配置（不要使用 DeepSeek 的密钥）：

```dotenv
# 将 YOUR_WORKSPACE_ID 替换为百炼北京业务空间 ID；必须是控制台的真实地址。
AGENTHUB_EMBEDDING_BASE_URL=https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
AGENTHUB_EMBEDDING_API_KEY=
AGENTHUB_EMBEDDING_MODEL=qwen3.7-text-embedding-flash
AGENTHUB_EMBEDDING_DIMENSIONS=1024
AGENTHUB_EMBEDDING_TIMEOUT_SECONDS=30
AGENTHUB_EMBEDDING_BATCH_SIZE=10
AGENTHUB_EMBEDDING_MAX_CHARACTERS=8000
```

BASE_URL 和 API_KEY 由用户填写，当前已配置；空 URL/密钥让适配器返回 embedding_not_configured，不影响应用启动或基础探针。未替换的占位域名不合法，不能原样写入启用。模型名与其他非敏感选项已补入本机 .env；示例和 Compose 同步。配置只接受北京业务空间 HTTPS 兼容端点，禁止用户信息、查询参数、其他地域或重定向。API_KEY 使用 SecretStr，排除 repr，按请求放 Authorization 头，不进入文本 input。

batch_size 1–20，是单个同步 HTTP 请求中的文本条数，默认 10；不是供应商异步 Batch 产品。max_characters 是应用字符限额，不是准确 token 计数。每次 embed 只处理一个已限定批次；超限明确拒绝，不静默截断、拆分或自动重试。P4 worker 已负责有记录的分批处理与显式版本重试，详见 KNOWLEDGE.md/JOBS.md。

## 实际代码与执行流程

- src/agenthub/core/config.py：配置校验；embedding_* 与 model_* 分开。
- src/agenthub/embeddings/provider.py：EmbeddingProvider.embed 与 EmbeddingResult，唯一网络边界。
- src/agenthub/main.py：lifespan 为 app.state.embedding_provider 注入现有异步 HTTP 客户端，退出时统一关闭；同一连接池中两种服务的认证头按请求隔离，不设置全局密钥。
- scripts/verify_p3_embedding.py：真实服务验收入口，只发送固定公开语句，最多两次请求（3 条文档、2 条问题）。
- tests/test_embedding_provider.py：30 项协议、异常、输入限制和生命周期测试，明确使用 HTTP transport 替身，不发付费请求。

embed(texts) 先检查配置和输入，再以应用总时限 + HTTP 超时 POST /embeddings，请求包含 model、input、dimensions 和 encoding_format=float。它限制响应大小为 4 MB，并校验：模型名一致、数量一致、index 完整且不重复、每条维度等于配置、所有元素是有限数值且非零向量。按 index 恢复输入顺序，L2 归一化后返回向量、模型/维度、total_tokens 和 duration_ms。

Flash 还可提供 prompt_tokens；适配器统一读取 usage.total_tokens，缺失用量保存为 None。所有批次原子校验，任何一条不合法即整批拒绝，没有部分结果落库。L2 归一化采用先缩放再求范数，防止极端数值平方溢出。它不代表脱敏，也不代表不同模型可混用。

错误使用 AppError：503 embedding_not_configured；422 embedding_invalid_input；429 embedding_rate_limited；504 embedding_timeout；502 embedding_upstream_error / embedding_connection_error / embedding_invalid_response。不会返回供应商原始错误正文、密钥或输入文本。适配器仍是内部 Python 服务，没有直接公开向量化 API；P4 的知识库 API/四表迁移由 knowledge 模块提供。

## 设计取舍与限制

复用锁定的 httpx2，不引入新 SDK/本地模型依赖；小适配器足以服务 P4，避免提前做多模型路由。替代方案是 DashScope SDK/原生 API，可启用 text_type=query/document 与 instruct。当前官方说明这些参数仅原生接口支持，因此兼容适配器不声称启用了查询侧指令，先以统一默认文本编码建立基线。以后启用原生查询/文档处理需固定处理版本并重新评估，不能无记录改变向量空间。

网络调用不创建数据库事务；P4 worker 接收完整、校验过的批次，全部分块成功后原子写 pgvector。不能在上传请求中长时间锁数据库等模型。错误/重试由 P4 持久任务状态机管理；0 次自动重试避免不确定请求重复计费。没有账号级金额限额或免费额度检测 API，10 元是本轮操作预算；验收只发固定短文本两次，不能将脚本的预算字段理解为平台自动账单管控。

## 如何验收

先检查配置（不发网络请求）：

```powershell
uv run --locked python scripts/verify_p3_embedding.py --check
```

填写本机配置后，显式运行真实验收：

```powershell
uv run --locked python scripts/verify_p3_embedding.py
```

脚本守卫模型名和 1024 维，配置不足退出 2；调用/简单检索检查失败退出 1；成功退出 0。报告写入被 Git 忽略的 data/acceptance/p3-embedding-*.json，只包含配置标识、用量、时延、相似度排序索引及价格快照估算，不保存原始向量或凭据。该三个文档的小例子仅验证链路与语义常识，不是 Recall 基准或生产质量证明。无文件读取或任意用户文本输入入口。

用户说明约有 100 万免费 token；官方同步 API 文档也列出对应免费额度及有效期。是否仍有效、是否适用当前北京业务空间，以账号控制台和账单为准。报告按标准价 0.125 元/百万 token 估算，估算不是实扣金额，免费额度未耗尽时可能不扣费。未确认账号可用额度前不把验收记为免费。

## 已验证与未验证

P3 时 30 项 Embedding 替身测试通过，默认全套 88 passed / 31 skipped。维度错误、缺少/重复/越界索引、NaN/Inf/布尔值/零向量、模型不匹配、非法 JSON、过大响应、401/429/500/重定向、网络与总时限、缺失配置、生命周期均有覆盖。Flash 真实百炼调用已通过，完整版之前的额度失败见历史段落。P5 的真实查询与三种文件对照见 [P5 验收](../acceptance/P5_2026-11-12.md)。

## 学习检验（待回答/练习）

1. 为什么必须按返回的 index 恢复顺序，不能直接按 data 数组顺序配回文档？
2. 1024 维已经正确，为什么还要检查模型身份、NaN 和零向量？
3. 为什么只有 total_tokens 也能记录用量，缺失时却不能补成 0？

代码练习：给当前 EmbeddingProvider 增加一项测试，验证 8000 字符恰好允许、8001 字符在发 HTTP 前拒绝；只使用 transport 替身。

项目思考：P4 的第 3 个批次失败，前两个批次已写库，如何重试且不生成重复有效分块？先画状态流，不急着加队列框架。

## 官方依据

[兼容向量接口与 dimensions](https://help.aliyun.com/zh/model-studio/embedding)、[请求/响应与用量](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)、[模型北京价格](https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding-flash)。

## 常见错误与面试关联

把响应 data 顺序当作输入顺序会将正确向量关联到错误文档；只检查维度会让同维度的其他模型污染语义空间；把缺失 token 补 0 会低估费用；归一化前不排除零向量会产生除零。面试可结合 embed/_parse 追问：批量 HTTP 的部分失败语义是什么、超时后能否确认上游未计费、为什么知识库事务应在网络调用之后、分批重试如何保持幂等。当前代码将不确定性停在适配器边界，未来 worker 必须显式处理这些问题。

## 历史：完整版额度验收（切换 Flash 前）

用户最初只将 ws- 开头的业务空间 ID 填入 BASE_URL，已经按官方北京格式补齐 HTTPS 域名和 /compatible-mode/v1 路径，保留密钥。--check 通过，不再存在缺失配置。GET /models=200，列表包含目标模型；正式批次和一次公开短文本诊断均未成功生成向量，诊断返回 403 / insufficient_quota，说明当前免费额度不可用，需要核对该模型额度有效期、仅使用免费额度策略和余额。没有修改账号计费开关或读取完整账单；不声称准确扣费金额或免费额度总量。

已新增安全分类：上游 403 且 error.code=insufficient_quota 转为 AppError 503 embedding_quota_exhausted，其余 403 仍为 502 embedding_upstream_error。仅解析最多 8 KiB 错误正文里的 code，不向调用方暴露原始 message；0 次自动重试。新增 3 项额度/普通403/非对象错误测试，Embedding 共 33 passed；默认全套 91 passed / 31 skipped。当前错误源于供应商账号额度，向量维度/质量等成功路径仍未真实验证。

补充理解题：为什么模型列表查询成功不能证明向量推理权限和额度可用？本次没有新增重要业务模块，原有三道题和边界练习继续待验证。

## 当前 Flash 验收与模型切换教学

用户截图显示有免费额度的是 qwen3.7-text-embedding-flash，而不是原完整版；用户授权切换后只修改 model 配置，地址/密钥、1024 维和 L2 处理保持一致。适配器不写死模型名，无需修改网络代码；脚本的防误调用模型守卫、价格快照和测试数据同步更新。

真实脚本两次请求成功，共 72 + 43 = 115 输入 token；5 条向量均 1024 维，两个问题分别匹配到预期文档索引 0、1。标准价 0.125 元/百万 token 的估算为 0.000014375 元；截图是调用前的免费额度，实际抵扣/账单没有查询。API 容器已重新创建并读取 Flash 配置，ready 正常。

没有既有知识库向量，所以本次无需重建；未来即使从完整版换到同样 1024 维的 Flash，也必须建立新索引版本并重算文档向量，不可混用。这是模型身份校验在 _parse 中仍保留的原因。面试可追问“维度相同为什么仍要重建索引”；练习可给模型不匹配分支增加完整版/Flash 互换的拒绝用例，只用测试替身。原有三道理解题和幂等思考题继续待回答。

## P4 接线更新

knowledge/worker.py 已通过 EmbeddingProvider.embed 分批处理 TXT/MD/文本 PDF，记录已知批次用量并整份原子发布至 vector(1024)。知识库固定模型名、北京端点、维度及处理版本，配置不一致拒绝入库/重试。真实三文件经三个独立 worker 子进程共81 token验收通过。适配器仍不做数据库操作；用户级原文权限、删除与幂等由 knowledge 服务/worker负责，见 [第五课](KNOWLEDGE.md)。
