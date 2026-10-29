---
title: '链路追踪：一个请求慢，直接看出慢在哪个服务'
summary: '给服务接入 OpenTelemetry，每个请求经过的服务、数据库调用和耗时串成一条链路，慢请求一眼定位。'
category: monitoring
status: design
pains: [speed]
value: '慢请求从「挨个服务查」变成看一条链路'
ai: boost
stack: ['OpenTelemetry', 'Grafana Tempo', 'Grafana']
order: 60
flow:
  - label: '应用接入 OTel SDK'
    detail: '自动埋点'
  - label: 'OTel Collector'
    detail: '采样、转发'
  - label: 'Tempo 存储'
  - label: 'Grafana 查链路'
  - label: '从告警跳到慢链路'
related: [log-alerting, slo-error-budget]
tags: ['链路追踪', 'OpenTelemetry', 'Tempo', 'APM', '微服务']
---

## 问题

微服务架构下一个请求经过五六个服务，用户说「慢」，每个服务的负责人都说自己正常，排查要来回翻日志。

## 适用场景

- 适合：微服务、一个请求经过多个服务的系统
- 不值得：单体应用、只有一两个服务——日志和指标就够了
- 前提：能修改应用（接入 SDK 或自动探针）

## 工具选型

| 方案                      | 特点                      | 适用            |
| ------------------------- | ------------------------- | --------------- |
| OpenTelemetry + Tempo     | 开放标准，和 Grafana 一体 | 国内外都常用    |
| Jaeger                    | 老牌开源，CNCF 毕业       | 国内外都常用    |
| SkyWalking                | 国产开源，Java 探针无侵入 | 国内很常用      |
| Datadog APM / 阿里云 ARMS | 商业托管                  | 外企 / 国内云上 |

选 OpenTelemetry：厂商中立的标准，以后换后端（Tempo、Jaeger、商业 APM）不用改代码。

## 怎么做

- 应用接入 OpenTelemetry SDK，HTTP、数据库、Redis 调用自动埋点，不用改业务代码
- OTel Collector 统一接收：正常请求按 10% 采样，出错和慢请求全部保留
- Grafana Tempo 存储链路，和 Prometheus 指标、Loki 日志用 trace ID 关联：从慢请求告警点进去直接看到链路，再跳到对应日志
- 和日志告警、指标告警组成完整的可观测性

## 实施步骤

| 步骤 | 做什么                                  | 怎么验证                   |
| ---- | --------------------------------------- | -------------------------- |
| 1    | 部署 OTel Collector 和 Tempo            | Collector 能接收测试数据   |
| 2    | 应用接入 OTel SDK（先用自动埋点）       | Grafana 里能看到第一条链路 |
| 3    | 配置尾部采样：错误、慢请求全留          | 慢请求链路 100% 被保留     |
| 4    | 日志里打印 trace ID，Loki 和 Tempo 关联 | 从日志能跳到对应链路       |
| 5    | 告警里附链路查询链接                    | 从慢请求告警点进去看到链路 |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
# OTel Collector：错误和慢请求全部保留，其余按比例采样
processors:
  tail_sampling:
    policies:
      - { name: errors, type: status_code, status_code: { status_codes: [ERROR] } }
      - { name: slow, type: latency, latency: { threshold_ms: 1000 } }
      - { name: sample, type: probabilistic, probabilistic: { sampling_percentage: 10 } }
```

## 效果指标

| 指标         | 做之前                            | 预期效果（估算）        |
| ------------ | --------------------------------- | ----------------------- |
| 慢请求定位   | 逐个服务查日志，约 1 小时（估算） | 几分钟看一条链路        |
| 服务依赖关系 | 靠文档（经常过时）                | 自动生成服务拓扑        |
| 存储成本     | —                                 | 采样后约 10% 的正常链路 |

慢请求定位从「挨个服务查」变成「看一条链路」。本站是静态站点，没有后端调用链，只做方案。

## 常见坑

- **链路断开，只看到一半**：某个服务没有透传 trace 上下文（如消息队列、异步线程）。检查中间件的上下文传播，手动注入。
- **全量采集存储爆炸**：高流量下每个请求都存。尾部采样：错误和慢请求全留，其余按比例。
- **探针带来性能开销**：埋点过多。只自动埋点框架和数据库调用，业务代码按需加。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：读一条慢链路，用自然语言指出瓶颈在哪个服务、哪次调用
- **不该交给 AI**：采样和存储策略是工程决策，不需要 AI
- **怎么落地**：Grafana 里选中慢链路，交给 AI 生成分析摘要

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会给服务接入 OpenTelemetry SDK，HTTP、数据库、Redis 调用自动埋点；OTel Collector 做尾部采样，错误和超过 1 秒的请求全部保留，其余按 10% 采样，存到 Tempo。日志里打印 trace ID，在 Grafana 里从慢请求告警跳到链路、再跳到日志，三种数据串起来。

常见追问：

**问：Trace、Span 是什么？**

答：Trace 是一个请求的完整链路，由多个 Span 组成，每个 Span 是一段操作（一次 HTTP 调用、一次 SQL），记录开始时间和耗时。

**问：头部采样和尾部采样的区别？**

答：头部采样在请求开始时就决定是否保留，简单但会丢掉慢请求；尾部采样等请求结束再决定，能保证错误和慢请求全部保留，但 Collector 需要更多内存。

**问：可观测性三大支柱是什么？**

答：指标、日志、链路。指标发现问题，链路定位在哪个服务，日志看具体原因。

**被问到「能不能用 AI」时：** 链路数据很适合交给 AI 读：它能直接指出慢在哪一段、可能是什么原因。
