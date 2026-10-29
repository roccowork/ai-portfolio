---
title: '定时任务失败监控：备份、对账脚本没跑也能知道'
summary: '每个定时任务执行完都向监控「报到」，超过预定时间没报到或报告失败就告警，「静默失败」无处藏身。'
category: monitoring
status: design
pains: [risk]
value: '定时任务没跑、跑失败都会告警，不再静默失败'
ai: none
stack: ['Healthchecks', 'cron', 'curl']
order: 25
flow:
  - label: 'cron 执行脚本'
  - label: '成功：ping /uuid'
  - label: '失败：ping /uuid/fail'
  - label: '超时没报到'
  - label: '告警'
related: [backup-restic, mysql-backup-drill, inspection-report]
tags: ['定时任务', 'cron', '心跳', '监控', '静默失败']
---

## 问题

crontab 里的备份、对账、清理脚本，失败了没人知道；更糟的是机器重启后 cron 根本没跑，几个月后要恢复数据才发现备份早就停了。

## 适用场景

- 适合：服务器上有备份、对账、清理等定时任务的任何团队
- 不值得：几乎没有定时任务的环境
- 前提：定时任务能访问一个心跳地址（内网自建即可）

## 工具选型

| 方案                            | 特点                     | 适用        |
| ------------------------------- | ------------------------ | ----------- |
| Healthchecks（自建）            | 开源，配置简单           | ✅ 方案采用 |
| Cronitor / Dead Man's Snitch    | 托管                     | 外企常用    |
| kube-state-metrics + Prometheus | K8s CronJob 最近成功时间 | ✅ 本站在用 |
| Zabbix 自定义监控项             | 已有 Zabbix 时           | 国内常用    |

选 Healthchecks：能发现「根本没运行」——日志监控做不到这点；K8s 里的任务直接用 kube-state-metrics，本站已经这样做。

## 怎么做

- 每个定时任务在 Healthchecks（开源，可自建）里登记预期周期，比如「每天一次，宽限 1 小时」
- 脚本结束时用 curl 报到：成功调用 `/<uuid>`，失败调用 `/<uuid>/fail` 并附上最后几行日志
- 超过宽限期没有报到或报告失败就告警——这能发现「根本没运行」的情况，单靠日志做不到
- K8s 里的 CronJob 用 kube-state-metrics 的最近成功时间做同样的检查（本站备份就是这样监控的）

## 实施步骤

| 步骤 | 做什么                                | 怎么验证                             |
| ---- | ------------------------------------- | ------------------------------------ |
| 1    | 部署 Healthchecks，为每个任务建检查项 | 每个任务有一个 UUID                  |
| 2    | 设置周期和宽限时间                    | 面板显示每个任务的下次期望时间       |
| 3    | 脚本结束时 ping 成功或失败地址        | 手动运行后面板显示成功               |
| 4    | 接入企业微信通知                      | 停掉 cron 服务后收到告警             |
| 5    | K8s CronJob 用 BackupStale 类规则     | 超过 26 小时没成功告警（本站已上线） |

## 关键代码

示例代码（简化），没有在生产环境运行：

```bash
# crontab：0 2 * * * /usr/local/bin/backup.sh
if /usr/local/bin/mysql-backup.sh > /tmp/backup.log 2>&1; then
  curl -fsS -m 10 --retry 3 https://hc.example.com/ping/$UUID > /dev/null
else
  tail -c 10000 /tmp/backup.log | curl -fsS -m 10 --retry 3 --data-binary @- https://hc.example.com/ping/$UUID/fail
fi
```

## 效果指标

| 指标       | 做之前         | 预期效果（估算）     |
| ---------- | -------------- | -------------------- |
| 静默失败   | 几个月后才发现 | 超过宽限期就告警     |
| 根本没运行 | 无法发现       | 能发现               |
| 失败原因   | 要登录看日志   | 告警里带最后几行日志 |

所有定时任务都有人「盯着」。本站的 BackupStale 告警是 K8s 版本，已在生产运行。

## 常见坑

- **ping 本身失败导致误报**：网络抖动。curl 加重试和超时；宽限时间留足。
- **任务失败却报了成功**：脚本没检查退出码。用 set -e 或判断命令退出码再 ping。
- **任务改了周期忘了改监控**：两边不同步。监控项配置和 crontab 放在同一个仓库。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：失败时解读任务日志的最后几行
- **不该交给 AI**：是否按时运行是确定性判断，不需要 AI
- **怎么落地**：保持规则；失败告警里可选附 AI 日志解读

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 定时任务最怕静默失败，更怕根本没运行。我会给每个任务在 Healthchecks 里登记周期和宽限时间，脚本结束时成功就 ping 一下、失败就 ping 失败地址并附上最后几行日志；超过宽限期没报到就告警，这能发现机器重启后 cron 没跑这类问题。K8s 里的 CronJob 用 kube-state-metrics 的最近成功时间做同样的事，本站的备份告警就是这样上线的。

常见追问：

**问：为什么只看日志发现不了「没运行」？**

答：没运行就没有日志，不会触发任何错误告警；只有「期望报到却没报到」的心跳机制能发现。

**问：K8s CronJob 怎么监控？**

答：kube-state-metrics 提供 kube_cronjob_status_last_successful_time，用 time() 减去它超过阈值告警；Job 失败用 KubeJobFailed。

**问：cron 和 systemd timer 怎么选？**

答：systemd timer 有日志、能补跑错过的任务（Persistent=true）、能设置随机延迟，新系统推荐用它。

**被问到「能不能用 AI」时：** 定时任务监控是心跳机制，不需要 AI。
