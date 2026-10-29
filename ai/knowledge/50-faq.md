---
title: 常见问题
summary: 访客常问的问题：用了哪些技术、怎么测试、代码能不能看、AI 用的是哪个模型。
---

## 用了哪些技术

AI 应用：RAG、函数调用 / Agent、Text-to-SQL、Dify、提示词设计。后端：Python、FastAPI、SQLite、pytest。部署与运维：Docker、k3s / Kubernetes、Argo CD、GitHub Actions、Prometheus / Grafana。前端：Astro、Vue、Tailwind CSS。

## 怎么测试

AI 后端的单元测试用假的模型服务，不需要密钥、不花钱，每次推送代码 CI 都会跑；网站有格式、lint、类型检查、单元测试和构建检查。RAG 和 Agent 的回答准确率评测（测试集）还在补充中。

## 代码能不能看

仓库目前是私有的，作者正在整理一个去掉密钥和个人内容的公开版本。想看具体实现，可以给作者留言。

## 用的是哪个模型

模型通过阿里云百炼调用，由自写的模型网关按用途选择；每次调用的模型、token、费用和耗时在演示页面上都能看到。

## 作者有学习资料吗

作者整理了一个 AI 应用开发学习路线网站：agent-workforce-70y.pages.dev
