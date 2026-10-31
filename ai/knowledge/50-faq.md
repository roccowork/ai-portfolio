---
title: 常见问题
summary: 访客常问的问题：用了哪些技术、怎么测试、代码能不能看、AI 用的是哪个模型。
---

## 用了哪些技术

AI 应用：RAG、函数调用 / Agent、Text-to-SQL、Dify、提示词设计。后端：Python、FastAPI、SQLite、pytest。部署与运维：Docker、k3s / Kubernetes、Argo CD、GitHub Actions、Prometheus / Grafana。前端：Astro、Vue、Tailwind CSS。

## 怎么测试

AI 后端的单元测试用假的模型服务，不需要密钥、不花钱，每次推送代码 CI 都会跑；网站有格式、lint、类型检查、单元测试和构建检查。RAG 有两套评测集（作品集问答和运维知识库，共 61 道题），统计检索命中率、答案正确率和「资料里没有时会不会编」，结果在 ops.liuxd.dev/rag/ 页面；运维 Agent 有 21 道故障演练题，在真实集群的快照上注入故障，最近两次整体通过率 52.4%，主要问题是模型会提不相关的操作、偶尔编数字，详情见 GitHub 仓库的 README。

## 代码能不能看

能。代码是公开的：github.com/roccowork/ai-portfolio，包括网站、AI 后端、K8s 部署和监控配置。首页首屏也有 GitHub 按钮。

## 用的是哪个模型

模型通过阿里云百炼调用，固定用一个模型（qwen-plus 的固定日期版本），这样回答和评测分数可以前后比较。所有调用都经过自写的模型网关：按 IP 限次数、每日费用上限、按 token 记账，实时用量在 /gateway/ 页面能看到。

## 作者在学什么

作者正在按一份完整的知识清单系统补齐 AI 应用开发的知识体系，目标是项目里用到的每项技术都能讲清原理。
