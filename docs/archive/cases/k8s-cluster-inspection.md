---
title: 'K8s 集群每日巡检：证书、资源、异常 Pod 一份报告'
summary: '每天早上自动检查集群组件证书有效期、节点资源、重启频繁的 Pod、Pending 的 Pod、过大的镜像，生成报告。'
category: inspection
status: design
pains: [risk]
value: '证书过期这种一年一次的大坑提前一个月发现'
ai: boost
stack: ['kubectl', 'Python', 'CronJob']
order: 40
flow:
  - label: 'CronJob 每天 08:00'
  - label: 'kubeadm 证书剩余天数'
  - label: '节点 CPU / 内存 / 磁盘'
  - label: 'CrashLoop、Pending、频繁重启'
  - label: '报告发到群'
related: [inspection-report, cert-renewal]
tags: ['Kubernetes', '巡检', '证书', 'kubeadm']
---

## 问题

kubeadm 集群的组件证书默认一年过期，到期整个集群 API 不可用，是很多公司踩过的大坑。日常的异常 Pod 也常常没人注意。

## 适用场景

- 适合：自建 K8s 集群（kubeadm、二进制部署）的团队
- 不值得：托管 K8s（证书由云厂商管理）且已有完善监控的
- 前提：有能访问集群的只读账号

## 工具选型

| 方案                     | 特点             | 适用        |
| ------------------------ | ---------------- | ----------- |
| 自写脚本（kubectl + jq） | 零依赖           | ✅ 方案采用 |
| Popeye                   | 扫描集群配置问题 | 开源        |
| kube-bench               | CIS 安全基线     | 安全检查    |
| Prometheus 规则          | 持续监控         | 已有监控时  |

自写脚本：几条 kubectl 命令就能覆盖证书、异常 Pod、资源，结果汇总成一条消息；配置问题用 Popeye 补充。

## 怎么做

- `kubeadm certs check-expiration` 读取控制面证书剩余天数，少于 30 天告警（实验 13 用 kubeadm 搭过集群）
- 用 kubectl 的 JSON 输出统计：24 小时内重启超过 3 次的 Pod、Pending 超过 10 分钟的 Pod、没有设置资源限制的容器
- 节点资源用 `kubectl top` 或 Prometheus 数据，超过 80% 标黄
- 汇总成一条消息发到企业微信，没有异常就只发一行「全部正常」

## 实施步骤

| 步骤 | 做什么                                   | 怎么验证             |
| ---- | ---------------------------------------- | -------------------- |
| 1    | 只读 ServiceAccount 和 kubeconfig        | 只能 get/list        |
| 2    | 证书检查：kubeadm certs check-expiration | 剩余天数正确         |
| 3    | 异常 Pod：频繁重启、Pending、CrashLoop   | 人为制造异常时被列出 |
| 4    | 节点资源和没设 limits 的容器             | 报告里有清单         |
| 5    | 每天 08:00 推送，正常时只发一行          | 连续几天按时收到     |

## 关键代码

示例代码（简化），没有在生产环境运行：

```bash
kubeadm certs check-expiration -o json | jq -r '.certificates[] | select(.residualTime | tonumber < 2592000000000000) | .name'
kubectl get pods -A -o json | jq -r '.items[] | select(any(.status.containerStatuses[]?; .restartCount > 3)) | "\(.metadata.namespace)/\(.metadata.name)"'
```

## 效果指标

| 指标         | 做之前           | 预期效果（估算） |
| ------------ | ---------------- | ---------------- |
| 证书过期事故 | 一年一次的大坑   | 提前 30 天发现   |
| 异常 Pod     | 没人注意         | 每天列出         |
| 巡检耗时     | 手动执行一堆命令 | 0                |

证书到期这种「一年一次」的事故被提前一个月发现；本站 k3s 的证书由 k3s 自动轮换，所以只做方案。

## 常见坑

- **证书检查要在控制面节点执行**：kubeadm 命令读取本地证书。脚本在控制面节点运行，或者用 openssl 检查 API Server 证书。
- **kubelet 客户端证书没续期**：只续了控制面证书。开启 kubelet 证书自动轮换。
- **报告太长没人看**：把所有正常项都列出来。只列异常，正常时一行。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：对异常 Pod 的事件和日志给出可能原因
- **不该交给 AI**：证书剩余天数等检查是确定的
- **怎么落地**：巡检报告的异常项附 AI 原因分析

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> kubeadm 集群的证书默认一年过期，到期整个 API 就不可用了。我会写一个每天早上运行的巡检：kubeadm certs check-expiration 检查证书剩余天数，少于 30 天告警；kubectl 统计 24 小时内重启超过 3 次的 Pod、Pending 超过 10 分钟的 Pod、没设资源限制的容器；没有异常就只发一行「全部正常」。实验 13 用 kubeadm 搭过集群。

常见追问：

**问：kubeadm 证书过期了怎么办？**

答：`kubeadm certs renew all` 续期，然后重启控制面组件（静态 Pod），更新 admin.conf。升级集群版本时也会自动续期。

**问：Pod 一直 Pending 怎么排查？**

答：`kubectl describe pod` 看事件：资源不足、节点选择器或污点不匹配、PVC 没绑定、镜像拉取问题。

**问：CrashLoopBackOff 怎么排查？**

答：`kubectl logs --previous` 看上一次退出的日志，检查探针、配置、资源限制（OOMKilled）。

**被问到「能不能用 AI」时：** 巡检项是确定的，AI 帮我分析异常 Pod 可能的原因，省去逐个 describe。
