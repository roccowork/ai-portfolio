---
title: 常见问题
summary: 访客常问的问题：用了哪些技术、怎么测试、代码能不能看、AI 用的是哪个模型。
---

## 用了哪些技术

AI 应用：RAG、函数调用 / Agent、Text-to-SQL、Dify、提示词设计。后端：Python、FastAPI、SQLite、pytest。部署与运维：Docker、k3s / Kubernetes、Argo CD、GitHub Actions、Prometheus / Grafana。前端：Astro、Vue、Tailwind CSS。

## 怎么测试

AI 后端的单元测试用假的模型服务，不需要密钥、不花钱，每次推送代码 CI 都会跑；网站有格式、lint、类型检查、单元测试和构建检查。作品集问答（RAG）有一套 36 道题的评测集，统计检索命中率、答案正确率和「资料里没有时会不会编」，最近一次结果公开在 ops.liuxd.dev/api/ai/eval/latest；运维 Agent 的场景评测还在做。

## 代码能不能看

能。代码是公开的：github.com/roccowork/ai-portfolio，包括网站、AI 后端、K8s 部署和监控配置。首页首屏也有 GitHub 按钮。

## 用的是哪个模型

模型通过阿里云百炼调用，由自写的模型网关按用途选择；每次调用的模型、token、费用和耗时在演示页面上都能看到。

## 作者有学习资料吗

作者整理了一个 AI 应用开发学习路线网站：agent-workforce-70y.pages.dev
