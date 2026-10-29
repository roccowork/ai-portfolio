---
title: K8s + GitOps 平台：本站的部署和监控
summary: 本站按生产标准部署在单节点 k3s 上：推送到 main 后 CI 自动测试、构建镜像，Argo CD 从 Git 同步上线；监控、证书、Argo CD 自己也都由 Git 管理。状态：真实环境运行中，集群数据在 ops.liuxd.dev/live/ 公开。
---

## 发布流程

推送到 main 后，GitHub Actions 跑格式检查、lint、类型检查、单元测试和构建，构建镜像（标签是 commit SHA）推到 GHCR，再把新标签写回 Git。集群里的 Argo CD 每 60 秒对比 Git，发现变化自动同步。CI 不持有任何集群凭据，K8s API 端口不对公网开放。回滚就是 git revert，实测 89 秒生效。

## 可用性

- 站点 2 个副本，滚动更新时先起新 Pod、就绪探针通过后才接流量，再停旧 Pod
- 新版本不健康时 rollout 停住，旧版本继续服务
- 实测：滚动重启期间 592 个请求全部返回 200；手动改副本数，Argo CD 同一秒纠正

## 监控和备份

- kube-prometheus-stack（Prometheus、Alertmanager）和 Grafana 做监控，Uptime Kuma 做可用性探测
- 告警推送到企业微信群；需要人确认的操作走 Telegram
- 应用数据用 restic 每天加密备份到 Cloudflare R2
- 证书由 cert-manager 自动签发和续期（Let's Encrypt）

## 安全

- 容器以非 root 运行、只读根文件系统、去掉全部 capabilities
- Secret 静态加密；Argo CD 用只读、只限本仓库的部署密钥拉取
- 依赖用 lockfile 锁定，基础镜像和组件全部固定版本，Renovate 自动提升级 PR

## 平台实况

集群里的 CronJob 每 10 分钟查询 K8s API 和 Prometheus，把最近发布、7 天可用率和响应时间、备份、证书、告警汇总成 /status.json，ops.liuxd.dev/live/ 页面直接展示。
