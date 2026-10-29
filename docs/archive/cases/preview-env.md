---
title: 'PR 预览环境：每个 PR 自动起一套临时环境，合并后自动删'
summary: '提交 PR 后自动部署一个独立的预览地址，产品和测试直接点开验收；PR 关闭后环境自动销毁。'
category: dev
status: design
pains: [speed]
value: '每个改动几分钟就有独立的验收地址，不再抢测试环境'
ai: boost
stack: ['Argo CD ApplicationSet', 'Kubernetes', 'GitHub Actions']
order: 40
flow:
  - label: '打开 PR'
  - label: '构建镜像 pr-123'
  - label: 'ApplicationSet 发现 PR'
    detail: '建 namespace pr-123'
  - label: 'pr-123.preview.example.com'
  - label: 'PR 关闭自动删除'
related: [gitops-release, canary-release]
tags: ['预览环境', 'PR', 'ApplicationSet', '测试环境']
---

## 问题

多人共用一个测试环境：你的改动把别人的覆盖了，测试说「不是我改的问题」；想给产品看效果，要等合并到测试分支。

## 适用场景

- 适合：前端、全栈团队，多人并行开发、需要产品和测试频繁验收
- 不值得：后端依赖复杂（多个数据库、外部系统）、一套环境起不来的项目
- 前提：应用能用一套 K8s 清单完整部署，有通配符域名和证书

## 工具选型

| 方案                                | 特点               | 适用              |
| ----------------------------------- | ------------------ | ----------------- |
| Argo CD ApplicationSet（PR 生成器） | 和 GitOps 一体     | 国内外都常用      |
| Vercel / Netlify 预览               | 前端静态站点零配置 | 外企前端常用      |
| GitLab Review Apps                  | GitLab 自带        | 国内私有化 GitLab |
| 自写脚本 + kubectl                  | 灵活               | 小团队            |

选 ApplicationSet：PR 打开时自动建环境、关闭时自动删，全部声明式，不用写清理脚本。

## 怎么做

- CI 为每个 PR 构建镜像，标签用 `pr-<编号>-<提交号>`
- Argo CD ApplicationSet 的 Pull Request 生成器自动发现打开的 PR，为每个 PR 生成一个 Application，部署到独立命名空间
- Ingress 用 `pr-<编号>.preview` 子域名，通配符证书；环境地址自动评论到 PR 里
- PR 合并或关闭后 ApplicationSet 自动删除对应环境；资源设上限，防止预览环境占满集群

## 实施步骤

| 步骤 | 做什么                                    | 怎么验证                            |
| ---- | ----------------------------------------- | ----------------------------------- |
| 1    | 准备通配符域名 *.preview 和证书           | 任意子域名都能解析并返回有效证书    |
| 2    | CI 为 PR 构建镜像 pr-<号>-<sha>           | 镜像仓库里出现对应标签              |
| 3    | 配置 ApplicationSet 的 pullRequest 生成器 | 打开 PR 后 Argo CD 出现 web-pr-<号> |
| 4    | 命名空间设 ResourceQuota                  | 预览环境 CPU、内存超额时拒绝调度    |
| 5    | CI 把预览地址评论到 PR                    | PR 里出现可点击的链接               |
| 6    | 关闭 PR 验证自动清理                      | 命名空间被删除                      |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
generators:
  - pullRequest:
      github: { owner: corp, repo: web, labels: [preview] }
      requeueAfterSeconds: 120
template:
  metadata: { name: 'web-pr-{{number}}' }
  spec:
    source:
      { repoURL: https://github.com/corp/web.git, targetRevision: '{{head_sha}}', path: deploy }
    destination: { server: https://kubernetes.default.svc, namespace: 'pr-{{number}}' }
```

## 效果指标

| 指标         | 做之前                                 | 预期效果（估算）      |
| ------------ | -------------------------------------- | --------------------- |
| 验收等待     | 合并到测试分支、排队部署，半天（估算） | PR 打开几分钟后可访问 |
| 测试环境冲突 | 经常互相覆盖                           | 每个 PR 独立          |
| 资源浪费     | —                                      | PR 关闭即删除         |

每个改动都能单独验收，互不干扰，环境用完即删不浪费资源。

## 常见坑

- **预览环境越来越多占满集群**：长期不关的 PR、清理失败。只给带 preview 标签的 PR 建环境，设置配额，定期清理超过 7 天的。
- **预览环境共用测试数据库导致数据混乱**：多个环境写同一个库。每个环境用独立的轻量数据库或种子数据。
- **Let's Encrypt 频率限制**：每个 PR 申请一张证书。用一张通配符证书（DNS-01）。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：在预览环境上自动跑一遍 AI 驱动的探索测试，截图对比找视觉差异
- **不该交给 AI**：环境创建和销毁是确定流程
- **怎么落地**：预览环境就绪后触发 AI 视觉对比，结果评论到 PR

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会用 Argo CD ApplicationSet 的 Pull Request 生成器：给 PR 打上 preview 标签后，CI 构建镜像，ApplicationSet 自动建一个 Application 部署到独立命名空间，地址是 pr-编号.preview 子域名，用通配符证书；PR 关闭后环境自动删除。每个命名空间设资源配额，防止预览环境占满集群。

常见追问：

**问：预览环境的数据库怎么处理？**

答：每个环境起一个轻量数据库容器并灌入种子数据；不能共用测试库，否则互相污染。

**问：怎么控制成本？**

答：只对打了标签的 PR 建环境、资源配额、副本数 1、超过一定天数自动清理。

**问：和 Vercel 预览有什么区别？**

答：Vercel 只适合前端和 Serverless；ApplicationSet 能部署完整的后端服务。

**被问到「能不能用 AI」时：** 预览环境的创建是确定的，AI 可以在上面做视觉回归对比。
