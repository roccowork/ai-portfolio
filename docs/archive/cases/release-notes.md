---
title: '发布说明自动生成：按提交记录整理成可读的更新日志'
summary: '提交信息按约定格式写，发布时自动归类成「新功能 / 修复 / 其他」，生成发布说明并通知到群里。'
category: dev
status: design
pains: [effort]
value: '发布说明自动生成，业务方及时知道上线了什么'
ai: boost
stack: ['Conventional Commits', 'release-please', 'GitHub Actions']
order: 60
flow:
  - label: '约定式提交'
    detail: 'feat: / fix:'
  - label: '合并到 main'
  - label: '自动汇总变更'
    detail: '按类型分组'
  - label: '生成版本号和 CHANGELOG'
  - label: '发布通知'
related: [gitops-release, renovate-automerge]
tags: ['发布说明', 'CHANGELOG', '版本号', '提交规范']
---

## 问题

每次发布都要人工整理「这次改了什么」，经常漏写；业务方不知道新功能已经上线。

## 适用场景

- 适合：对外发布的产品、开源项目、和业务方协作多的团队
- 不值得：提交信息难以规范、只有一个人维护的项目
- 前提：团队愿意遵守提交信息格式

## 工具选型

| 方案                    | 特点                         | 适用              |
| ----------------------- | ---------------------------- | ----------------- |
| release-please          | Google 出品，自动开发布 PR   | 国内外都常用      |
| semantic-release        | 全自动发布，插件多           | 前端、npm 包常用  |
| git-cliff               | 只生成 CHANGELOG，可高度定制 | Rust 写的，速度快 |
| GitHub 自动生成发布说明 | 按 PR 标签分组               | 零配置            |

选 release-please：不会直接发布，而是先开一个「发布 PR」，合并才发布，节奏可控。

## 怎么做

- 团队约定提交信息格式：`feat: 增加导出功能`、`fix: 修复分页错误`，用 commitlint 在 PR 里检查
- release-please 根据提交自动计算版本号（有 feat 升次版本、只有 fix 升修订号）并维护 CHANGELOG
- 它会自动开一个「发布 PR」，合并即打标签、生成 GitHub Release
- 发布后把本次的新功能和修复发到企业微信群，业务方直接看到

## 实施步骤

| 步骤 | 做什么                         | 怎么验证                           |
| ---- | ------------------------------ | ---------------------------------- |
| 1    | 团队约定 Conventional Commits  | 新提交都是 feat:/fix: 格式         |
| 2    | 加 commitlint 检查 PR 标题     | 格式不对的 PR 检查失败             |
| 3    | 配置 release-please Action     | 合并后出现「发布 PR」              |
| 4    | 合并发布 PR 生成标签和 Release | GitHub Releases 里有分组的更新说明 |
| 5    | 发布后推送更新摘要到群         | 业务群收到新功能列表               |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
on: { push: { branches: [main] } }
permissions: { contents: write, pull-requests: write }
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        with: { release-type: node }
```

## 效果指标

| 指标         | 做之前                   | 预期效果（估算）         |
| ------------ | ------------------------ | ------------------------ |
| 整理发布说明 | 每次 30 分钟以上（估算） | 自动生成                 |
| 版本号       | 随意                     | 按语义化版本规则自动计算 |
| 业务方知晓   | 经常不知道上线了什么     | 发布即通知               |

发布说明不再靠人整理，版本号有规则，业务方及时知道上线了什么。

## 常见坑

- **提交信息不规范导致说明很乱**：有人不按格式写。合并 PR 时用 squash 并检查 PR 标题，而不是每个提交。
- **一个 PR 改了很多东西**：一条 feat 概括不全。PR 拆小；或在 PR 描述里写多条变更。
- **破坏性变更没有提示**：没写 BREAKING CHANGE。约定破坏性变更用 `feat!:` 并在说明里突出显示。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：把技术提交改写成业务方看得懂的更新说明
- **不该交给 AI**：版本号计算按规则
- **怎么落地**：生成 CHANGELOG 后用 AI 改写一份面向业务的版本

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会让团队用约定式提交（feat:、fix:），PR 里用 commitlint 检查标题；合并后 release-please 自动计算语义化版本号、维护 CHANGELOG，并开一个发布 PR，合并就打标签、生成 Release，再把新功能和修复推送到业务群。

常见追问：

**问：语义化版本号怎么定？**

答：主版本.次版本.修订号：不兼容的改动升主版本，新功能升次版本，修复升修订号。

**问：为什么用 squash 合并？**

答：每个 PR 只留一个提交，PR 标题就是提交信息，便于检查格式和生成说明。

**问：release-please 和 semantic-release 的区别？**

答：前者先开发布 PR、人工合并才发布；后者合并即发布，更自动但更难控制节奏。

**被问到「能不能用 AI」时：** 版本号用规则算，AI 把技术提交改写成业务方能看懂的更新说明。
