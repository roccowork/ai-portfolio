---
title: 告警推送企业微信 + 每周运维周报
summary: 出问题时企业微信群里立刻收到告警；每周一自动收到一份周报，整体情况一眼看清。
category: monitoring
status: production
pains: [risk]
value: '故障 106 秒内推到企业微信（实测），每周自动出周报'
ai: boost
stack: [Prometheus, Alertmanager, Node.js, 企业微信, CronJob]
order: 1
flow:
  - label: Prometheus
    detail: 告警规则 + 7 天数据
  - label: Alertmanager
    detail: 分组、去重、抑制
  - label: wecom-relay
    detail: 转成群机器人格式
  - label: 企业微信群
  - label: weekly-report
    detail: 每周一 09:00 汇总 7 天
related: [cert-renewal, backup-restic, gitops-release]
tags: [告警, 企业微信, 周报, 监控]
---

## 问题

Alertmanager 原生只支持「企业微信应用」：要企业 ID、应用密钥，还要可信 IP（可信 IP 要求备案域名），不支持群机器人。另外告警只在出事时响，没人知道「这一周整体怎么样」。

## 适用场景

- 适合：用企业微信、钉钉、飞书办公的团队，希望告警直接进工作群；国内公司最常见的告警通道
- 不值得：已经有 PagerDuty、Opsgenie 等值班平台的外企团队，直接用它们的集成
- 前提：有 Prometheus + Alertmanager

## 工具选型

| 方案                             | 特点                                     | 适用                             |
| -------------------------------- | ---------------------------------------- | -------------------------------- |
| 自写转发器（本站）               | 零依赖、可控，约 150 行                  | ✅ 本站选用                      |
| PrometheusAlert                  | 国产开源，支持企业微信、钉钉、飞书、短信 | 国内常用                         |
| Alertmanager 自带 wechat_configs | 只支持企业微信应用                       | 需要企业 ID、可信 IP（备案域名） |
| PagerDuty / Opsgenie             | 值班、升级、电话                         | 外企常用，收费                   |

选自写转发器：群机器人只需要一个 webhook key，没有可信 IP 限制；顺便统一消息格式、按字节截断、复用给周报。

## 怎么做

- **wecom-relay**：约 150 行、零依赖的 Node 脚本，接收 Alertmanager 的 webhook，转成群机器人的 markdown，按字节截断到 4096 以内。用官方 Node 镜像 + ConfigMap 运行，不另外构建镜像
- **同一个问题只发一条**：关掉和自定义规则重复的默认告警；名字不同的同类告警用抑制规则去重（比如磁盘 90% 告警触发时不再发 80%）
- **weekly-report**：CronJob 每周一 9 点查 Prometheus，汇总访问量、5xx、p95、告警（按持续时间排序）、备份最近成功时间、证书剩余天数、节点资源、容器重启，复用 relay 的发送函数
- 两者都有单元测试，用假的企业微信、假的 Prometheus 服务器验证

## 实施步骤

| 步骤 | 做什么                                                    | 怎么验证                           |
| ---- | --------------------------------------------------------- | ---------------------------------- |
| 1    | 企业微信群里添加机器人，拿到 webhook key，存入 K8s Secret | 用 curl 发一条测试消息能收到       |
| 2    | 写转发器：接收 Alertmanager webhook，转成 markdown 发出   | 单元测试用假企业微信服务器验证     |
| 3    | Alertmanager 配置 webhook 接收器，info 级别路由到空       | 手动触发一条测试告警，群里收到     |
| 4    | 整理告警规则：关掉重复的默认规则，加抑制规则              | 同一个故障只收到一条中文告警       |
| 5    | 写周报 CronJob：查询 Prometheus 7 天数据并发送            | `DRY_RUN=1` 本地运行看到周报内容   |
| 6    | 验收：把站点缩容到 0                                      | 106 秒收到告警，恢复后收到恢复通知 |

## 关键代码

企业微信出错时 HTTP 也返回 200，必须看 errcode，否则告警丢了也不知道：

```js
const res = await fetch(webhookUrl, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ msgtype: 'markdown', markdown: { content } }),
  signal: AbortSignal.timeout(10_000),
});
const body = await res.json().catch(() => ({}));
if (!res.ok || body.errcode !== 0) throw new Error(`errcode=${body.errcode}`);
```

周报里告警持续时间用子查询估算：

```promql
sum by (alertname, severity) (
  count_over_time(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}[7d:5m])
)
```

## 效果指标

| 指标             | 之前                               | 之后                      |
| ---------------- | ---------------------------------- | ------------------------- |
| 发现故障         | 靠用户反馈或人工巡检               | 第 106 秒收到告警（实测） |
| 同一故障的告警数 | 默认规则 + 自定义规则重复，3～4 条 | 1 条                      |
| 每周整体情况     | 没人统计                           | 周一 09:00 自动周报       |

- 验收时把站点缩容到 0：第 106 秒告警触发，企业微信收到告警，恢复后收到恢复通知
- 周报从 2026-10-26 起每周一发送

## 常见坑

- **转发器 CrashLoopBackOff 而且日志为空**：ConfigMap 挂载的文件是符号链接，脚本判断「是否直接运行」时没有解析真实路径，服务没启动就正常退出了。比较前用 `realpathSync` 解析两边路径解决。
- **info 级别告警刷屏**：默认路由会发所有级别，把 `severity="info"` 路由到空接收器。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：告警附上可能原因和排查命令；周报自动写一段「本周总结」
- **不该交给 AI**：是否告警、告警阈值要由规则决定；AI 分析只能作为参考附在后面
- **怎么落地**：转发器里加一步调用模型，失败时照常发告警（本站服务器在香港，Claude API 不可用，曾评估后暂缓）

## 面试怎么讲

30 秒讲法：

> Alertmanager 原生只支持企业微信应用，需要可信 IP 和备案域名，我写了一个约 150 行、零依赖的 Node 转发器，把告警转成群机器人的 markdown。同时整理了告警规则：关掉和自定义规则重复的默认告警、用抑制规则去重，同一个故障只发一条。验收时把站点缩容到 0，第 106 秒收到告警。另外每周一自动发周报，汇总访问量、告警、备份和证书情况。

常见追问：

**问：企业微信接口出错为什么还要看 errcode？**

答：企业微信出错时 HTTP 也返回 200，只看状态码会以为发送成功，告警就静悄悄丢了。转发器检查 errcode，失败返回 500，Alertmanager 会重试。

**问：告警太多怎么治理？**

答：三步：关掉重复的默认规则；用抑制规则（比如磁盘 90% 触发时不再发 80%）；info 级别不通知。原则是每条告警都要有人需要处理。

**问：转发器挂了怎么办？**

答：有 AlertmanagerFailedToSendAlerts 告警；另外外部的 Uptime Kuma 和 UptimeRobot 从集群外拨测，集群整个挂了也能通知。

**被问到「能不能用 AI」时：** 我评估过给告警附 AI 分析，设计成可选、失败不影响告警送达；因为服务器在香港 API 不可用，暂缓了，这也是选型时要考虑的约束。
