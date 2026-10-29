---
title: 'Issue 自动修复成 PR：打上标签，AI Agent 改代码、跑测试、提 PR'
summary: '给 Issue 打上 ai-fix 标签，AI Agent 在 CI 里读 Issue 和代码、修改、跑测试，测试通过后提交 PR，由人审查后合并。'
category: dev
status: design
pains: [speed, effort]
value: '小 bug 和小需求从排期几天到当天出 PR'
ai: core
stack: ['GitHub Actions', 'Claude Code / Copilot coding agent', '单元测试']
order: 25
flow:
  - label: 'Issue 打上 ai-fix 标签'
  - label: 'CI 启动 Agent'
    detail: '隔离环境，最小权限'
  - label: '读代码、修改、跑测试'
  - label: '测试通过 → 提 PR'
  - label: '人审查后合并'
related: [ai-coding-assistant, ai-code-review, ai-test-case-gen]
tags: ['AI Agent', 'Issue', '自动修复', 'Claude Code', 'GitHub Actions']
---

## 问题

Issue 列表里堆着大量小问题：文案错误、简单的 bug、加一个参数、升级一个废弃的接口。每个只要半小时，但总排不上；开发人员也不愿意花时间在这些事上。

## 适用场景

- 适合：有大量小 Issue 积压、测试覆盖较好的项目
- 不值得：没有测试的项目（AI 改完无法验证）；需求不清楚、需要讨论的 Issue
- 前提：Issue 写清楚了期望行为；CI 能跑测试；有可用的 AI 编程 Agent

## 工具选型

| 方案                        | 特点                               | 适用         |
| --------------------------- | ---------------------------------- | ------------ |
| Claude Code GitHub Actions  | Issue / PR 里 @claude 或打标签触发 | ✅ 方案采用  |
| GitHub Copilot coding agent | 把 Issue 指派给 Copilot            | 外企常用     |
| OpenHands 等开源 Agent      | 可自建、接私有模型                 | 代码不能外传 |
| 人工处理                    | 现状                               | 积压         |

选在 CI 里跑：每次都是干净的隔离环境，权限可控，过程有日志，结果以 PR 形式交给人审查。

## 怎么做

- 维护者给合适的 Issue 打上 `ai-fix` 标签（不是所有 Issue 都适合）
- GitHub Actions 被触发，在隔离的 runner 里启动 Agent，给它的输入是 Issue 内容和仓库的规范文件（`CLAUDE.md`）
- Agent 自己读相关代码、修改、补测试、运行测试，直到测试通过；最多尝试一定步数
- 测试通过就推送到新分支并提 PR，PR 描述里写清改了什么、为什么；失败就在 Issue 里留言说明卡在哪
- 权限最小：只能写自己的分支、不能合并、不能访问生产密钥；PR 照常走 CI 和人工审查

## 实施步骤

| 步骤 | 做什么                              | 怎么验证             |
| ---- | ----------------------------------- | -------------------- |
| 1    | 补齐仓库规范文件和测试命令          | Agent 能跑通测试     |
| 2    | 配置 workflow：标签触发、权限最小   | 只能推分支、不能合并 |
| 3    | 挑 10 个历史小 Issue 试跑           | 统计一次通过率       |
| 4    | 制定「哪些 Issue 适合打标签」的指引 | 团队认可             |
| 5    | 正式启用，每月统计合并率和节省时间  | 有数据               |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
on:
  issues: { types: [labeled] }
jobs:
  fix:
    if: github.event.label.name == 'ai-fix'
    runs-on: ubuntu-latest
    permissions: { contents: write, pull-requests: write, issues: write }
    steps:
      - uses: actions/checkout@v7
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            修复 Issue #${{ github.event.issue.number }}。
            先读 CLAUDE.md；修改后运行 make test，全部通过再提 PR；
            无法完成时在 Issue 里说明原因。
```

## 效果指标

| 指标              | 做之前         | 预期效果（估算） |
| ----------------- | -------------- | ---------------- |
| 小 Issue 处理周期 | 排期几天到几周 | 当天出 PR        |
| 开发人员投入      | 每个半小时以上 | 只需审查         |
| AI PR 合并率      | —              | 以试点数据为准   |

开发人员把时间留给需要设计和讨论的工作，小问题由 Agent 起草、人把关。

## 常见坑

- **Issue 描述不清**：AI 按自己的理解改了。只给描述清楚、有验收标准的 Issue 打标签。
- **为了让测试通过改测试**：AI 修改或删除了测试。规范文件里禁止，审查时重点看测试的改动。
- **权限过大**：Agent 能读生产密钥、能合并。最小权限，只能推分支，合并必须人工。
- **提示注入**：Issue 里写了恶意指令。只允许维护者打标签触发，不处理外部人员直接触发。
- **成本失控**：大 Issue 反复尝试。限制步数和超时，按月看调用费用。

## AI 视角

**🤖 AI 是核心**

- **AI 能做什么**：理解 Issue、在代码库里定位相关代码、修改、补测试、根据测试结果自己修正
- **不该交给 AI**：合并的决定、需求和设计的讨论、涉及安全和数据的改动
- **怎么落地**：人挑选 Issue，AI 起草 PR，CI 和人审查把关

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会配一个 GitHub Actions 工作流：维护者给描述清楚的小 Issue 打上 ai-fix 标签后，在隔离的 runner 里启动 Claude Code，它读规范文件和相关代码，修改、补测试、运行测试，通过后提 PR，失败就在 Issue 里说明原因。Agent 只能推自己的分支，合并必须人工审查。先用 10 个历史 Issue 试跑，统计通过率再推广。

常见追问：

**问：哪些 Issue 适合交给 AI？**

答：范围小、描述清楚、有测试能验证的：简单 bug、文案、加参数、废弃接口升级。需要讨论的设计问题不适合。

**问：怎么防止 AI 改坏东西？**

答：测试必须通过；PR 照常走 CI 和人工审查；Agent 权限最小；审查时特别注意测试有没有被改。

**问：外部贡献者能触发吗？**

答：不能。只允许维护者打标签触发，防止 Issue 内容里的提示注入。

**被问到「能不能用 AI」时：** 这个案例本身就是 AI Agent；关键是人挑 Issue、测试验证、人审查合并。
