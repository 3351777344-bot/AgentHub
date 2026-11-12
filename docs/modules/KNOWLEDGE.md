# 知识库与文档处理：P4 第五课

日期：2026-11-12。实现包括私有知识库、原文件、TXT/Markdown/文本 PDF 解析、字符分块、后台 Embedding 与 pgvector 入库。在线问答使用本模块产出的向量与位置元数据。参见 [RAG 模块](RAG.md)。

## 职责与真实入口

| 文件 | 职责 |
| --- | --- |
| src/agenthub/api/knowledge.py | 身份认证后的知识库/文档增删查、原文下载、任务/分块查询、失败重试 |
| knowledge/service.py | 所有权、模型空间兼容检查、上传事务、版本递增、删除墓碑 |
| knowledge/storage.py | 校验展示文件名，用 UUID 保存原文件，临时文件写入/fsync/原子替换 |
| knowledge/parsing.py | UTF-8/NFC/换行规范化、pypdf 文本提取、页内字符分块、子进程解析超时 |
| knowledge/models.py | 四张新表，vector(1024)、唯一约束、状态与租约 |
| knowledge/worker.py | 独立进程领取、解析、批量向量化、进度/用量、原子发布、故障回收、文件清理 |
| knowledge/queue.py | redis-py 有界唤醒列表；PostgreSQL 保存持久任务 |
| knowledge/middleware.py | 在 multipart 解析/磁盘暂存前限制实际请求体字节数 |

依赖 P2 的 CurrentPrincipal、SQLAlchemy Session 和用户状态；复用 P3 EmbeddingProvider 的超时、整批校验、排序与 L2 归一化。数据库迁移为 20261110_0003。

## 一份文件如何变成向量

1. `upload_document()` 先校验身份和资源所有权，读取有大小上限的文件。只接受纯文件名与 txt/md/pdf 后缀；MIME 不作为可信格式证据，实际内容仍由解析器检查。
2. `service.upload()` 锁知识库，检查固定模型、端点、1024 维与分块配置。SHA-256 相同的有效文档返回 409；同名不同内容允许上传。随机 UUID 对应磁盘文件名，不拼接用户路径。
3. 文件先写 `.part`，fsync 后替换成 `.raw`；文档与 queued 任务同事务提交。提交后才通知 Redis，通知失败仍返回已持久化的任务。
4. `claim()` 用 `FOR UPDATE SKIP LOCKED` 领取一项任务，提交 processing 和租约。事务不跨解析或模型网络调用。
5. worker 校验文件摘要，在有超时的子进程中解析。TXT/MD 仅支持 UTF-8，可带 BOM；统一 NFC 与 LF，保留 Markdown 标记和换行。PDF 提取文本层，保存页码；无文本、加密、损坏及过大输入分别失败。
6. `split_sections()` 在每一页/文本段内按字符窗口分块，默认大小 800、重叠 100，最多 500 块。位置为**规范化后的页内字符偏移**，`char_end` 不包含终点；不是原始 UTF-8 字节偏移或 PDF 坐标。TXT/MD 的 page_number 为 1。
7. 按 Embedding batch_size 分批请求。每批前后以短事务检查删除/版本/租约/模型空间/用户状态；成功后保存 completed_batches 和可获得的 token 用量。失败不自动再次调用模型。
8. 所有分块向量先在有上限的内存中收集。`publish()` 再锁资源并校验状态，在一个事务中插入所有分块、将文档置 ready、任务置 completed。读接口只有在 ready 时返回分块。

因此第二批失败时，第一批用量仍可查询，但数据库没有部分可用分块。缺失任一批用量则任务 total_tokens 为 NULL；失败/硬退出时记录只覆盖已确认批次，不代表供应商最终账单。

## 状态、幂等与删除

文档：queued → processing → ready / failed；删除进入 deleted。任务：queued → processing → completed / failed；删除将未完成任务置 cancelled。文件解析期间文档可能已标 processing，任务状态才是后台执行事实。

仅 failed 文档允许 `/retry`：原文不变，version 加一，创建新的任务，旧任务和失败原因保留。没有 ready 文档原地改模型的接口；模型、端点、维度或处理版本不兼容时返回 embedding_space_mismatch，应创建新知识库并重建。

幂等有三层：有效内容摘要唯一；每文档/版本任务唯一；每文档/位置分块唯一。领取只会消费 queued 任务；完成/取消/旧版本任务的 process 再执行会直接返回。这里保证数据库有效结果不重复，不能保证云端推理恰好计费一次。

所有涉及多资源的写操作按 KB → document → job 锁定。删除事务设置墓碑、移除分块、取消未完成任务。模型返回后重新检查墓碑和版本，迟到任务不能发布。正在进行的 HTTP 请求可能继续消耗 token，删除不保证撤回已发送原文或已产生费用。

worker 硬退出后，租约到期由下一轮 `recover()` 标记 worker_interrupted，用户显式重试。不会自动重发不确定的付费请求。worker 停止期间不能回收，需要恢复 worker。新版本任务不被旧 worker 的迟到失败/成功覆盖。

## 文件一致性与边界

数据库与磁盘没有跨系统事务。若 COMMIT 返回网络错误，不能确定是否已经提交，因此不立刻删除文件；worker 每轮清理超过一小时且无文档引用的 UUID raw/part 文件。删除文件失败不会撤销逻辑删除，file_removed_at 为空表示仍需重试。墓碑和历史任务暂不物理清除。只清理配置的受控目录，不遍历任意上传路径。

默认文件 5 MB、文本 20 万字符、200 页；body 另外允许 64 KiB multipart 开销。解析子进程默认 30 秒；pypdf 解压/解码设上限；Compose worker 限 512 MiB、1 CPU。宿主机手动运行没有容器内存限制。没有恶意文档完整沙箱、OCR、杀毒、DOCX/HTML/网址抓取；复杂版面、表格/阅读顺序和混合扫描页仍有提取限制。云端只使用公开或脱敏资料，向量本地存储不改变原文出域事实。

目前知识库仅支持所有者访问。在线检索采用精确余弦排序，已用固定公开样本验证。HNSW、自动重嵌入、自动金额配额、租约续跑和任务调度面板仍属后续工作。

## 为什么这样设计，以及其他方式

`claim/progress/publish` 把数据库事务与网络等待分开，让上传和删除不等待模型锁。整份发布容易理解和验证，代价是有内存/文档大小上限，失败重试会重算已完成批次。更大文档可改为按 job/version 暂存向量并原子切换有效版本，需要额外清理和引用规则。

字符窗口不需要 tokenizer 或 LangChain 依赖，偏移稳定，便于学习；代价是可能切断句子、代码和 Markdown 结构，也不是准确 token 长度。替代方案是递归分隔、标题/段落切分、按 token 限额，均需升级处理版本并比较真实检索效果。

本地卷适合单机 Compose；多主机应迁移到对象存储并保留同样的权限、校验和和清理语义。后台调度与其他队列方案的取舍见 JOBS.md 和 ADR-016。

常见错误：把同维向量当成同一语义空间；上传请求内等待全部推理；先提交 ready 再写分块；仅靠 Redis 任务状态；删除只删文件不拦迟到任务；把规范化字符位置当 PDF 字节位置；超时后无条件重试并声称不会重复计费。

面试可以结合本项目追问：文件/DB/Redis 三次写入怎样处理部分成功；SKIP LOCKED 为什么适合领取任务；分块重叠怎样影响成本与召回；为什么数据库幂等不等于模型 exactly-once；如何防止删除后数据复活。

## 学习检验：待回答与实践

1. 结合 `upload()` 与 `WakeupQueue.notify()`：为什么 Redis 失败后仍能返回 202，worker 靠什么找回任务？
2. `split_sections()` 的 size=100、overlap=10 时，第二个分块从哪个字符开始？为什么 char_end 使用不含终点的约定？
3. 第二批 Embedding 失败后，哪些事实已经入库、哪些结果尚不可用？重试为什么递增 version？

代码练习：为一个新知识库设 chunk_size=120、chunk_overlap=20，添加测试验证长度 221 的文本分块位置和重叠；不要修改已经有向量的知识库配置。先自行推算，再运行测试。

项目思考：第三批模型请求正在进行时用户删除文档，同时另一个 worker 开始回收租约，三个操作如何遵守锁顺序并阻止迟到发布？画出时间线后再解释。尚未收到回答或练习，不能标记已掌握。

## 官方依据

[pypdf 文本提取限制](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)、[pgvector SQLAlchemy 接入](https://github.com/pgvector/pgvector-python)、[Redis BLPOP 语义](https://redis.io/docs/latest/commands/blpop/)。实际依赖版本以 uv.lock 为准；测试与真实验收见 P4 验收记录。
