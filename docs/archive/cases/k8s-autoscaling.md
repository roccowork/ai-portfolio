---
title: 'K8s 自动扩缩容：流量高峰自动加 Pod，低谷自动减'
summary: '按 CPU 或每秒请求数自动调整副本数，促销高峰不用人工扩容，夜间自动缩回省资源。'
category: infra
status: design
pains: [cost, risk]
value: '高峰自动扩容扛住，低谷自动缩容省资源'
ai: boost
stack: ['Kubernetes HPA', 'Prometheus Adapter', 'KEDA']
order: 30
flow:
  - label: 'Prometheus 指标'
    detail: 'CPU / 每秒请求数'
  - label: 'HPA / KEDA'
    detail: '计算目标副本数'
  - label: '扩容'
    detail: '2 → 10'
  - label: '冷却后缩容'
  - label: '副本上限保护'
related: [canary-release, cost-report]
tags: ['HPA', '自动扩缩容', '弹性', 'KEDA', 'Kubernetes']
---

## 问题

活动开始时流量暴涨，靠人盯着手动扩容总是慢一步；为了扛高峰长期保持很多副本，平时又浪费资源。

## 适用场景

- 适合：流量有明显高峰低谷的在线服务
- 不值得：流量平稳、或者节点资源固定没有余量的小集群
- 前提：应用无状态，能水平扩展；设置了资源 requests

## 工具选型

| 方案                           | 特点                               | 适用                     |
| ------------------------------ | ---------------------------------- | ------------------------ |
| HPA                            | K8s 内置，按 CPU/内存/自定义指标   | 国内外都常用             |
| KEDA                           | 按队列、请求数等事件扩容，能缩到 0 | 国内外都常用             |
| VPA                            | 自动调整资源 requests              | 和 HPA 不能同时按 CPU 用 |
| Cluster Autoscaler / Karpenter | 节点级扩缩容                       | 云上                     |

HPA 处理 CPU 类负载；按队列或请求数扩容用 KEDA；云上再配合节点自动扩缩。

## 怎么做

- HPA 按 CPU 使用率（目标 60%）自动调整副本数，最少 2 个保证高可用，最多 10 个防止异常流量耗尽集群
- CPU 不能反映负载的应用，用 KEDA 或 Prometheus Adapter 按每秒请求数、队列长度扩缩容
- 缩容设置 5 分钟稳定窗口，避免流量抖动时反复扩缩
- 配合节点自动扩缩容（云上），Pod 放不下时自动加节点

## 实施步骤

| 步骤 | 做什么                         | 怎么验证                       |
| ---- | ------------------------------ | ------------------------------ |
| 1    | 设置合理的 resources.requests  | HPA 能计算利用率               |
| 2    | 部署 metrics-server            | `kubectl top pods` 有数据      |
| 3    | 创建 HPA，设上下限和目标利用率 | `kubectl get hpa` 显示当前指标 |
| 4    | 配置缩容稳定窗口               | 流量抖动时不反复扩缩           |
| 5    | 压测验证                       | 加压时副本增加，停止后缓慢减少 |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: web }
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: web }
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource: { name: cpu, target: { type: Utilization, averageUtilization: 60 } }
  behavior:
    scaleDown: { stabilizationWindowSeconds: 300 }
```

## 效果指标

| 指标     | 做之前           | 预期效果（估算）          |
| -------- | ---------------- | ------------------------- |
| 高峰扩容 | 人工操作，慢一步 | 自动，约 1 分钟内（估算） |
| 低谷资源 | 按峰值常驻       | 自动缩回                  |
| 值守     | 活动期间盯着     | 无需                      |

高峰自动扛住，低谷自动省资源。本站是单节点静态站点，访问量很小，用不上。

## 常见坑

- **HPA 不工作，显示 unknown**：没设 requests 或 metrics-server 没装。检查 requests 和 metrics-server。
- **扩容了但新 Pod 一直 Pending**：节点资源不够。配合节点自动扩缩，或预留资源。
- **冷启动慢，扩容赶不上流量**：应用启动要几十秒。提高最小副本数、优化启动、提前按计划扩容。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：基于历史流量预测高峰，提前扩容
- **不该交给 AI**：扩缩容上下限和安全边界必须由规则约束
- **怎么落地**：用预测结果设定定时扩容，HPA 仍做实时兜底

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会给无状态服务配置 HPA：按 CPU 利用率 60% 自动调整副本，最少 2 个保证高可用，最多 10 个防止异常流量耗尽集群，缩容设 5 分钟稳定窗口避免抖动。CPU 反映不了负载的服务用 KEDA 按每秒请求数或队列长度扩容。前提是每个容器都设了合理的 requests。

常见追问：

**问：HPA 怎么计算目标副本数？**

答：期望副本 = 当前副本 × 当前指标 / 目标指标，向上取整，再受上下限约束。

**问：HPA 和 VPA 能一起用吗？**

答：不能都基于 CPU/内存；VPA 调 requests，HPA 按利用率扩副本，同时用会互相干扰。

**问：为什么必须设置 requests？**

答：HPA 的利用率是相对 requests 计算的，没有 requests 就算不出来。

**被问到「能不能用 AI」时：** HPA 是规则，AI 可以做流量预测实现提前扩容，但上下限由规则兜住。
