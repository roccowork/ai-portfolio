---
title: '提交前自动检查：格式、规范、密钥泄露在本地就拦住'
summary: '提交代码时自动格式化、检查代码规范、扫描是否带了密码和密钥，CI 再兜底一遍。'
category: dev
status: design
pains: [accuracy, risk]
value: '格式问题不再占用评审时间，密钥不会被提交进 Git'
ai: none
stack: ['pre-commit', 'ESLint / Ruff', 'gitleaks', 'GitHub Actions']
order: 10
flow:
  - label: 'git commit'
  - label: 'pre-commit 钩子'
    detail: '格式化、lint'
  - label: 'gitleaks'
    detail: '扫描密钥'
  - label: '提交成功'
  - label: 'CI 再跑一遍'
    detail: '防止绕过'
related: [gitops-release, ai-code-review]
tags: ['代码规范', 'pre-commit', '密钥泄露', 'lint']
---

## 问题

代码风格各写各的，评审时一半意见是格式问题；更糟的是有人把数据库密码、云 AccessKey 提交进 Git，一旦仓库公开或泄露就是安全事故。

## 适用场景

- 适合：所有多人协作的代码仓库
- 不值得：一次性脚本
- 前提：团队同意统一的代码风格

## 工具选型

| 方案                  | 特点                   | 适用         |
| --------------------- | ---------------------- | ------------ |
| pre-commit            | 多语言钩子管理         | 国内外都常用 |
| husky + lint-staged   | 前端项目常用           | Node 项目    |
| gitleaks / trufflehog | 密钥扫描               | 开源         |
| GitHub 推送保护       | 服务端拦截已知密钥格式 | GitHub       |

pre-commit 跨语言、配置简单；密钥扫描本地和服务端各做一层。

## 怎么做

- 用 pre-commit 框架管理钩子：提交时自动运行格式化（Prettier、Black/Ruff）和 lint，能自动修的直接修
- gitleaks 扫描本次提交里的密钥特征（AccessKey、私钥、Token），发现就拒绝提交
- CI 里用同一份配置再跑一遍，防止有人用 `--no-verify` 跳过
- 本站仓库的 CI 已有 format:check 和 lint，这是把检查提前到开发者本地

## 实施步骤

| 步骤 | 做什么                             | 怎么验证                          |
| ---- | ---------------------------------- | --------------------------------- |
| 1    | 统一格式化和 lint 配置并提交到仓库 | 本地运行无报错                    |
| 2    | 添加 .pre-commit-config.yaml       | `pre-commit run --all-files` 通过 |
| 3    | 开发者安装钩子                     | 提交时自动运行                    |
| 4    | 加入 gitleaks                      | 故意写入假密钥时提交被拒绝        |
| 5    | CI 里运行同样的检查                | 跳过钩子的提交在 PR 里失败        |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks: [{ id: trailing-whitespace }, { id: check-yaml }, { id: check-added-large-files }]
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.24.0
    hooks: [{ id: gitleaks }]
```

## 效果指标

| 指标             | 做之前         | 预期效果（估算） |
| ---------------- | -------------- | ---------------- |
| 评审里的格式意见 | 占一半（估算） | 几乎为零         |
| 密钥泄露         | 偶尔发生       | 提交前拦截       |
| 代码风格         | 各写各的       | 全仓库一致       |

格式问题不再出现在代码评审里；密钥在离开开发者电脑之前就被拦住。

## 常见坑

- **开发者用 --no-verify 跳过**：钩子只是本地约束。CI 里必须再跑一遍。
- **密钥已经在历史提交里**：只扫描新提交。用 gitleaks 扫描全部历史，泄露的密钥立即作废更换。
- **钩子太慢影响提交体验**：每次检查全部文件。只检查本次修改的文件。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：基本不需要；AI 审查放在 PR 环节
- **不该交给 AI**：格式和规范检查必须确定、快速
- **怎么落地**：保持规则

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会用 pre-commit 统一管理提交前检查：自动格式化、lint、gitleaks 扫描密钥，能自动修的直接修好，发现密钥就拒绝提交；CI 里用同一份配置再跑一遍，防止有人跳过钩子。这样格式问题不再出现在代码评审里，密钥在离开开发者电脑前就被拦住。

常见追问：

**问：密钥已经提交到 Git 了怎么办？**

答：先作废并更换密钥（这是最重要的），再用 git filter-repo 清理历史；只删除最新提交不够，历史里还在。

**问：为什么本地检查了 CI 还要再查？**

答：本地钩子可以被跳过或者没安装，CI 是强制的最后一道关。

**问：lint 和格式化的区别？**

答：格式化只管样式（缩进、引号），lint 检查潜在错误和不好的写法（未使用的变量、可能的空指针）。

**被问到「能不能用 AI」时：** 提交前检查要快且确定，AI 放在 PR 审查环节。
