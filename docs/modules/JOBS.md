# P4 后台入库任务

日期：2026-11-10。当前仅支持文档入库，不是通用工作流调度器。

## 队列选型与边界

采用已有 redis-py 8.1.0 的 LPUSH/LTRIM/BLPOP 作为最多 100 条的唤醒列表，键为 `agenthub:ingestion:wakeup:v1`。Redis 不承载待执行文档 ID 或可信业务状态。PostgreSQL ingestion_jobs 是持久队列，worker 每次醒来先回收过期任务、清理文件，再按 created_at/id 用 SKIP LOCKED 领取 queued 行。

这修正了路线图中“P4 再引入队列框架”的初步建议：当前单一、低吞吐任务选用现有 redis-py；数据库承担领取/租约/失败状态，避免同时维护框架队列重试状态与业务状态。不是声称实现了 Redis 消息可靠投递或 exactly-once。未来有多任务类型、优先级、定时任务需求时评估 RQ/Celery/Dramatiq，必须保留持久业务状态与补偿。

事务提交后通知失败不影响任务；Redis 丢失、消息被消费后 worker 退出、数据库已提交但通知前 API 退出均靠 PostgreSQL 周期扫描恢复。Redis 不可用时等待 poll_seconds 再重试；数据库故障则本轮记录脱敏事件，等待下一轮。这里没有双写原子性假设。

## 调用与维护

```powershell
uv run --locked python -m agenthub.knowledge.worker
uv run --locked python -m agenthub.knowledge.worker --once
docker compose logs --tail 50 worker
```

宿主机 worker 必须能连接 Settings 中的数据库/Redis，且与 API 指向同一受控文档目录。Compose 已通过 document_data 共享卷配置。不要让宿主机默认 `.env` 指向与容器不同的数据库后声称已启动同一 worker。

`--once` 做一次回收/清理/领取，最多处理一份文档；无任务也正常退出。文档业务处理失败会记录 failed，进程可能仍退出 0，自动验收必须查询 document/job 状态；基础设施周期故障则抛出并非零退出。

租约长为 parse_timeout + embedding_timeout + 60 秒，每批前后延长。硬退出不自动续跑，过期后标 failed/worker_interrupted。重试是 API 新建任务，可能产生额外云端调用。每批成功保存用量，最后单事务发布全部向量；没有按批持久暂存向量。

清理：已逻辑删除且 file_removed_at 为空的原文重试 unlink；无文档引用且超过一小时的 UUID raw/part 文件作为孤儿删除。worker 离线时文件可继续占空间，API 权限及墓碑仍阻止访问。暂未实现墓碑保留周期清理、全库源文件缺失巡检；处理/下载时缺失会明确失败。

日志白名单新增 job_id、error_code、published 与安全的 request_id，不输出文件名、原文、向量、凭据或供应商错误正文。`ingestion_started/finished` 可将上传 request_id 与任务关联，失败可通过 job_id 查询脱敏 error_code。

限制：逐进程一次处理一个文档；可多 worker 竞争领取，但没有吞吐基线、优先级、排队时限或监控告警；解析子进程加超时与容器内存限制仍不等同完整不可信 PDF 安全沙箱。API 的 ready 只检查 PostgreSQL/vector 和 Redis，不检测 worker 存活。
