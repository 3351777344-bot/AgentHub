# AgentHub 文档索引

P10 本地技术交付见 [运维手册](OPERATIONS.md)、[P10 验收记录](acceptance/P10_2026-11-14.md) 和 [第十一课](modules/OPERATIONS.md)。

最后更新：2026-11-14。

AgentHub 的目标是构建覆盖知识库、RAG、Agent、工具与 MCP、LangGraph 工作流、评测和运维的 AI Agent 平台，同时支持通过真实开发学习这些技术。

P9 增加了同源浏览器控制台。项目状态和下一阶段见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 一、建议阅读顺序

1. [项目开发规则](../AGENTS.md)：开发、教学、安全和文档交付要求。
2. [项目状态](PROJECT_STATUS.md)：已经完成什么、尚未完成什么、下一步做什么。
3. [开发路线图](DEVELOPMENT_PLAN.md)：阶段依赖、任务清单、验收条件和学习任务。
4. [架构设计](ARCHITECTURE.md)：当前基础运行架构与后续目标设计。
5. [技术决策](TECH_DECISIONS.md)：已确定的开发方向、取舍和待决事项。
6. [开发记录](DEVELOPMENT_LOG.md)：实际发生的重要工作与验证结果。
7. [学习记录](LEARNING_LOG.md)：学习材料、练习及掌握情况。

## 二、现有文档

| 文档 | 主要回答的问题 | 更新时机 |
| --- | --- | --- |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 现在进行到哪里？ | 重要开发任务完成或出现阻塞时 |
| [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) | 接下来按什么顺序实现，怎样验收？ | 阶段范围、依赖或验收标准变化时 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块怎样协作，数据经过哪里？ | 架构、模块边界或核心数据流变化时 |
| [TECH_DECISIONS.md](TECH_DECISIONS.md) | 为什么选择这个方案？ | 重要技术决策形成或变更时 |
| [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) | 实际改了什么，验证了什么？ | 重要开发任务交付时 |
| [LEARNING_LOG.md](LEARNING_LOG.md) | 学了什么，还有什么需要练习？ | 教学、回答或实际练习之后 |
| [项目启动](../README.md) | 怎样安装、启动与检查？ | 启动或开发流程变化时 |
| [API_DESIGN.md](API_DESIGN.md) | 当前接口的请求、响应和错误是什么？ | API 变化时 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 本地容器怎样配置、验证与排查？ | 部署配置或验证结果变化时 |
| [TESTING.md](TESTING.md) | 测试怎样执行，哪些需要真实依赖？ | 测试与质量检查变化时 |
| [基础模块与第一课](modules/FOUNDATION.md) | 已有代码如何协作，怎样练习？ | P1 基础模块变化或教学后 |
| [P1 全技术学习指南](learning/P1_TECHNICAL_GUIDE.md) | Python、API、并发、数据库、Docker、测试如何串起来？ | P1 系统教学与复习时 |
| [P1 真实验收记录](acceptance/P1_2026-11-09.md) | 容器、依赖故障与数据保留实际验证了什么？ | 验收完成时保存证据 |
| [DATABASE.md](DATABASE.md) | 当前真实表、约束、事务与迁移是什么？ | ORM 或迁移变化时 |
| [SECURITY.md](SECURITY.md) | 认证、撤销、角色/资源权限及限制是什么？ | 身份/权限/部署边界变化时 |
| [身份与持久化模块第三课](modules/IDENTITY.md) | P2 真实代码怎样执行，如何学习与练习？ | P2 模块或教学变化时 |
| [P2 真实验收记录](acceptance/P2_2026-11-10.md) | 迁移、身份、权限和事务实际验证了什么？ | 验收完成时保存证据 |

## 三、按开发进度补充的文档

以下文件**尚未创建**，这里仅记录创建时机，不提供指向不存在文件的链接。

| 计划文件 | 创建时机 | 内容要求 |
| --- | --- | --- |
| docs/modules/ 后续业务模块文档 | 对应阶段实际实现时 | 职责、真实代码入口、执行流程、依赖、状态和限制 |

API 文档已包含身份与用户接口；DATABASE、SECURITY 和身份模块文档已随 P2 创建。vector 扩展基础设施初始化仍记录在部署文档。

## 四、事实与计划的记录规则

- `PROJECT_STATUS.md` 是当前进度的统一入口；路线图负责记录未来工作。
- 已完成：有实际交付，并记录了适用的验证结果。
- 开发中：已经开始实施，但还没有满足全部验收条件。
- 未开始：尚未实施。设计完成不等于实现完成。
- 阻塞：有明确的外部依赖阻止该任务，并说明解除条件；不影响其他独立任务继续推进。
- 学习状态独立记录。阅读讲解后只能标记为“待练习/待验证”，不能直接标记为“已掌握”。

每次开发先读本索引和项目状态，再读本次任务相关文档。开发后同步受影响的文档；不适用的文件无需为了留痕而修改。

P3 实现、配置、限制和第四课：[模型与会话模块](modules/CHAT.md)；验证边界：[P3 开发验证](acceptance/P3_2026-11-10.md)。

Embedding 服务、模型、维度、费用与隐私的历史选型材料：[选型分析](modules/EMBEDDING_SELECTION.md)，当前已使用百炼 Flash 1024维。

已实现的内部适配器、环境配置及验收方式：[Embedding 接入模块](modules/EMBEDDINGS.md)。

P4 实现和第五课：[知识库与文档处理](modules/KNOWLEDGE.md)；队列/租约/补偿：[后台入库任务](modules/JOBS.md)；验证证据：[P4 验收](acceptance/P4_2026-11-10.md)。

P5 实现、使用与第六课见 [RAG 模块](modules/RAG.md)。三种文件的标注样本、实际耗时与验证边界见 [P5 验收](acceptance/P5_2026-11-12.md)。

P6 使用与第七课见 [工具与 MCP 模块](modules/TOOLS.md)。本地容器、HTTPS 服务、权限及故障恢复见 [P6 验收](acceptance/P6_2026-11-12.md)。

P7 使用、运行流程与第八课见 [Agent 与工作流](modules/AGENTS.md)。真实模型检索、工具执行、审批与恢复证据见 [P7 验收](acceptance/P7_2026-11-13.md)。

P8 批量评测、规则评分与第九课见 [自动化评测](modules/EVALUATIONS.md)。基线/候选、确定性回归和真实模型指标见 [P8 验收](acceptance/P8_2026-11-13.md)。

P9 操作流程、代码与第十课见 [控制台](modules/CONSOLE.md)。浏览器与真实 Compose 证据见 [P9 验收](acceptance/P9_2026-11-14.md)。
