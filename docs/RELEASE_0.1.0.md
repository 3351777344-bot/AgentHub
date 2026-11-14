# AgentHub 0.1.0 本地发行

首版包含同源管理与聊天界面、私有知识库、RAG、受控工具与 MCP、可恢复 LangGraph Agent、版本化评测、登录限流和运维能力。数据库迁移头为 `20261114_0008`。本地发布采用 Git 标签 `v0.1.0`。 [验收与已知边界](acceptance/P10_2026-11-14.md)

## 发行文件

本机输出目录为 `dist/agenthub-0.1.0/`。包含 wheel、源代码包、Linux amd64 镜像归档 `agenthub-0.1.0-linux-amd64-images.tar`、四个 SPDX SBOM、`SHA256SUMS` 与镜像摘要。Git 提交包含 Compose、运行配置示例和迁移。提供方凭据、TLS 私钥、备份和验收账号都独立保护。

| 服务 | 本地镜像 |
| --- | --- |
| API、迁移、workers、本地 MCP | `agenthub/agenthub:0.1.0` |
| PostgreSQL 和 pgvector | `agenthub/postgres:17.12-vector0.8.7` |
| Redis | `redis:7.4.12-bookworm` |
| TLS 入口 | `agenthub/edge:0.1.0` |

将标签对应源码和镜像归档一同部署。检查 SHA-256，执行 `docker image load -i dist/agenthub-0.1.0/agenthub-0.1.0-linux-amd64-images.tar`，按启动步骤传独立配置。使用该归档时设置 `AGENTHUB_REDIS_IMAGE=redis:7.4.12-bookworm`，按 `up -d --no-build --pull never --wait` 启动。 [部署、恢复及回滚](OPERATIONS.md)

## 适用范围

技术验收覆盖 Windows Docker Desktop 上的 Linux amd64、Edge、回环与带验收证书的 TLS 配置。托管 Actions、远程发布、真实公开域名证书轮换、大规模容量与异地灾备留到目标部署。使用真实模型时只发送公开或脱敏资料，已知供应商、外部工具副作用和数据保留边界见 [安全说明](SECURITY.md)。
