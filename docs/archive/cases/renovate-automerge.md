---
title: 依赖自动升级
summary: Renovate 每周检查所有仓库的依赖，提 PR；CI 通过就自动合并，不用人盯。
category: release
status: production
pains: [risk, effort]
value: '8 个仓库的依赖每周自动升级，漏洞不再长期挂着'
ai: boost
stack: [Renovate, GitHub Actions]
order: 2
flow:
  - label: Renovate
    detail: 每周一早上扫描
  - label: 升级 PR
    detail: npm、镜像、Actions、Helm chart、Terraform
  - label: CI
    detail: 构建、测试、校验
  - label: 自动合并
    detail: CI 全绿才合
  - label: GitOps 发布
    detail: 合并后正常上线
related: [gitops-release]
tags: [依赖更新, 安全补丁, 供应链]
---

## 问题

依赖不升级，漏洞会一直留着；手动升级 8 个仓库又没人有时间做。镜像版本写死在 Dockerfile、K8s 清单、k3s 的 HelmChart 资源里，常规工具找不全。

## 适用场景

- 适合：仓库多、依赖多，又没人有时间定期升级的团队；尤其是要应对漏洞扫描、等保整改的公司
- 不值得：没有 CI 测试的仓库——自动合并等于没测就上线，要先补测试
- 前提：每个仓库都有能拦住坏升级的 CI

## 工具选型

| 方案            | 特点                                   | 适用                               |
| --------------- | -------------------------------------- | ---------------------------------- |
| Renovate        | 支持格式最多，可自定义正则识别任意文件 | 国内外都能用 ✅ 本站选用           |
| Dependabot      | GitHub 自带，零配置                    | 外企 GitHub 用户常用；格式支持较少 |
| 手动 + 定期排期 | 完全可控                               | 国内很多团队现状，容易拖延         |

选 Renovate：能识别 k3s HelmChart 这类非标准文件里的版本号（Dependabot 做不到），8 个仓库一套配置。

## 怎么做

- 每个仓库一份 `renovate.json`：每周一早上 9 点前（北京时间）提 PR，同类小版本合并成一个 PR，减少打扰
- **CI 通过后自动合并**，包括大版本。CI 覆盖不到的才留给人：本站的集群组件（cert-manager、监控、Argo CD 的 Helm chart）升级要人看，因为 CI 只能检查写法，验证不了升级后集群是否正常
- k3s 的 HelmChart 资源不是标准格式，用自定义正则让 Renovate 认出 chart 版本
- 站点自己的镜像标签由 CI 写入，排除在外
- 实验文档仓库只升级 CI 用的 Actions，文档里的版本号保持实际跑通时的样子

## 实施步骤

| 步骤 | 做什么                                            | 怎么验证                                    |
| ---- | ------------------------------------------------- | ------------------------------------------- |
| 1    | 先给每个仓库补齐 PR 检查（构建、测试、lint）      | 随便提一个 PR，CI 自动运行                  |
| 2    | 安装 Renovate GitHub App，选中要管理的仓库        | 仓库里收到 Renovate 的 onboarding PR        |
| 3    | 写 `renovate.json`：周一早上提 PR、同类小版本合并 | `renovate --dry-run=extract` 能列出全部依赖 |
| 4    | 为非标准文件写正则管理器（HelmChart）             | dry-run 结果里出现 chart 版本               |
| 5    | 开启自动合并，集群组件除外                        | CI 全绿的升级 PR 自动合并                   |

## 关键代码

```json
{
  "schedule": ["before 9am on monday"],
  "timezone": "Asia/Shanghai",
  "customManagers": [
    {
      "customType": "regex",
      "description": "k3s HelmChart 资源里的 chart 版本",
      "managerFilePatterns": ["/^deploy/platform/.+\\.yaml$/"],
      "matchStrings": [
        "repo:\\s*(?<registryUrl>\\S+)\\s+chart:\\s*(?<depName>\\S+)\\s+version:\\s*(?<currentValue>\\S+)"
      ],
      "datasourceTemplate": "helm"
    }
  ],
  "packageRules": [
    { "matchPackageNames": ["ghcr.io/roccowork/**"], "enabled": false },
    { "matchUpdateTypes": ["major", "minor", "patch", "pin", "digest"], "automerge": true },
    { "matchDatasources": ["helm"], "automerge": false }
  ]
}
```

## 效果指标

| 指标           | 之前                               | 之后                                |
| -------------- | ---------------------------------- | ----------------------------------- |
| 依赖升级频率   | 想起来才升级，常常半年以上（估算） | 每周自动检查                        |
| 每次升级的人工 | 逐个改版本号、提 PR、看测试        | 只看集群组件的 PR                   |
| 覆盖的仓库     | —                                  | 8 个仓库统一配置（2026-10-24 启用） |

- 8 个仓库统一配置，2026-10-24 启用；上线前用 `renovate --dry-run=extract` 确认能识别出 Dockerfile、K8s 清单、HelmChart、npm、Actions、Terraform 里的依赖
- 自动合并的效果数据等运行一段时间后补充

## 常见坑

- **没有 CI 的仓库不能开自动合并**：自动合并只在 CI 全绿时发生，所以先给每个仓库补齐了 PR 检查，再打开自动合并。
- **Renovate 认不出 k3s HelmChart 里的 chart 版本**：HelmChart 不是标准 Helm 文件格式。写 `customManagers` 正则，指定 datasource 为 helm。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：读升级的变更日志（changelog），总结有没有破坏性变更，帮人判断大版本升级的风险
- **不该交给 AI**：是否合并仍由 CI 结果决定，不能因为 AI 说「没问题」就跳过测试
- **怎么落地**：在升级 PR 上用 AI 总结变更日志并评论，人看摘要决定集群组件升级

## 面试怎么讲

30 秒讲法：

> 我给 8 个仓库统一配了 Renovate，每周一自动提依赖升级 PR，CI 全绿就自动合并，包括大版本。CI 验证不了的集群 Helm chart 升级留给人看。k3s 的 HelmChart 资源不是标准格式，我写了自定义正则让它识别。前提是每个仓库都先补齐了 PR 检查，否则自动合并就是不测就上线。

常见追问：

**问：大版本也自动合并，不怕出问题吗？**

答：只有 CI 全绿才合并，CI 覆盖了构建和测试；合并后还要走 GitOps 发布，坏版本卡在滚动更新、不接流量，回滚 1 分钟。真正验证不了的（集群组件）才手动。

**问：和 Dependabot 比有什么区别？**

答：Renovate 支持的文件格式多，能用正则识别任意文件里的版本号，能分组合并 PR、设时间窗口；Dependabot 胜在零配置。

**问：升级 PR 太多怎么办？**

答：同类小版本合并成一个 PR，固定每周一提交，限制同时打开的 PR 数量。

**被问到「能不能用 AI」时：** AI 适合读又长又乱的 changelog，告诉我大版本升级要注意什么，但合并标准仍是 CI 全绿。
