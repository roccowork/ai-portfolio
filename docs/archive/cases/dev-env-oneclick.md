---
title: '开发环境一键启动：新人第一天就能跑起项目'
summary: '用 Dev Container 或 Docker Compose 定义完整的开发环境，新人克隆代码后一条命令启动数据库、缓存和应用。'
category: dev
status: design
pains: [speed, effort]
value: '新人搭环境从一两天缩短到半小时'
ai: boost
stack: ['Dev Containers', 'Docker Compose', 'Makefile']
order: 40
flow:
  - label: 'git clone'
  - label: 'make dev / 打开容器'
  - label: '启动数据库、缓存'
  - label: '灌入种子数据'
  - label: '应用热更新运行'
related: [preview-env, precommit-lint]
tags: ['开发环境', 'Dev Container', 'Docker Compose', '新人入职']
---

## 问题

新人入职搭开发环境要一两天：装特定版本的语言、数据库，配置文件找老员工要，「我电脑上能跑」的问题天天发生。

## 适用场景

- 适合：依赖数据库、缓存等多个服务的项目，人员流动较多的团队
- 不值得：纯前端或单文件脚本项目
- 前提：开发者电脑能运行 Docker

## 工具选型

| 方案                       | 特点                                 | 适用         |
| -------------------------- | ------------------------------------ | ------------ |
| Dev Containers             | VS Code / JetBrains 支持，环境即代码 | 国内外都常用 |
| Docker Compose + Makefile  | 通用，简单                           | ✅ 方案采用  |
| GitHub Codespaces / Gitpod | 云端开发环境                         | 外企常用     |
| Nix / devbox               | 可复现的工具链                       | 小众         |

Compose 定义依赖服务、Makefile 统一命令，最通用；团队用 VS Code 的再加 Dev Container。

## 怎么做

- 仓库里提交 `.devcontainer/` 或 `compose.yaml`，写明语言版本、数据库、缓存和依赖服务
- `make dev` 一条命令启动全部依赖，自动执行数据库迁移和种子数据
- 应用代码挂载到容器，支持热更新；本地 IDE 直接连接容器开发
- 实验 08 用 Compose 编排过 Nginx + 应用 + MySQL + Redis，这是同样的思路用在开发环境

## 实施步骤

| 步骤 | 做什么                           | 怎么验证                         |
| ---- | -------------------------------- | -------------------------------- |
| 1    | 列出项目依赖的服务和版本         | 有一份清单                       |
| 2    | 编写 compose.dev.yaml 和健康检查 | `docker compose up` 全部 healthy |
| 3    | Makefile 封装常用命令            | `make dev` 一条命令启动          |
| 4    | 自动迁移和种子数据               | 启动后能直接登录测试账号         |
| 5    | 新人按 README 试一遍             | 半小时内跑起来                   |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
# compose.dev.yaml（节选）
services:
  app:
    build: { context: ., target: dev }
    volumes: [./:/app]
    environment: { DB_HOST: db, REDIS_HOST: redis }
    depends_on: { db: { condition: service_healthy } }
  db:
    image: mysql:8.0
    healthcheck: { test: [CMD, mysqladmin, ping, -h, localhost], interval: 5s }
```

## 效果指标

| 指标               | 做之前         | 预期效果（估算） |
| ------------------ | -------------- | ---------------- |
| 新人搭环境         | 一两天（估算） | 半小时           |
| 「我电脑能跑」问题 | 经常           | 明显减少         |
| 环境文档           | 过时           | 代码即文档       |

新人当天就能跑起项目，团队里不再有环境不一致的问题。

## 常见坑

- **Mac M 系列芯片镜像不兼容**：镜像只有 amd64。选多架构镜像。
- **容器里文件读写慢**：Mac 上的挂载性能。只挂载源码，依赖目录用卷。
- **种子数据包含真实客户信息**：从生产导出。使用脱敏或生成的数据。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：新人遇到环境报错时，AI 结合项目文档给出解决办法
- **不该交给 AI**：环境定义本身必须确定
- **怎么落地**：仓库里放 AI 可读的开发文档，配合 IDE 助手使用

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会在仓库里提交 Compose 文件定义开发环境：语言版本、MySQL、Redis 都写清楚，带健康检查；Makefile 提供 make dev，一条命令启动依赖、执行迁移、灌入种子数据，代码挂载进容器支持热更新。新人克隆仓库半小时就能跑起项目。实验 08 用 Compose 编排过类似的多服务应用。

常见追问：

**问：depends_on 能保证数据库已经可用吗？**

答：默认只保证容器启动；加 condition: service_healthy 配合健康检查，才能等数据库真正可用。

**问：开发环境和生产环境要一样吗？**

答：依赖的版本要一致；资源规模、高可用不需要一样。

**问：Dev Container 有什么好处？**

答：IDE 直接在容器里开发，工具链、插件、版本全部统一，新人打开即用。

**被问到「能不能用 AI」时：** 开发环境要确定可复现，AI 帮新人解决启动时遇到的报错。
