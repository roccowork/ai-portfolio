---
title: 服务器巡检报告自动化
summary: 从手工登录每台机器看磁盘、内存、服务，到定时生成报告、有问题自动推送。
category: inspection
status: lab
pains: [effort, risk]
value: '每天巡检从逐台登录变成自动报告'
ai: boost
stack: [Bash, cron, Python, Prometheus API, Alertmanager]
order: 1
flow:
  - label: cron
    detail: 定时触发
  - label: 巡检脚本
    detail: Shell 版 ssh 逐台检查 / Python 版查 Prometheus
  - label: 巡检报告
    detail: 超阈值标 WARN
  - label: Alertmanager
    detail: 有告警才推送
related: [alert-wecom-weekly, fault-drill]
tags: [巡检, Shell, Python, cron, 报告]
---

## 问题

每天登录每台服务器看磁盘、内存、负载、服务状态，机器一多就做不完，也容易漏看。

## 适用场景

- 适合：服务器几台到几十台、还没有监控系统的团队；也适合给客户做定期巡检服务
- 不值得：已有完善 Prometheus 告警的环境，巡检应该被告警规则 + 周报取代
- 前提：控制机能免密 SSH 到各台服务器（Shell 版），或已有 Prometheus（Python 版）

## 工具选型

| 方案                    | 特点                      | 适用                  |
| ----------------------- | ------------------------- | --------------------- |
| Shell + cron            | 零依赖，任何 Linux 都能跑 | 国内传统运维最常见 ✅ |
| Python + Prometheus API | 不用登录机器，数据更全    | ✅ 本站实验           |
| Ansible ad-hoc          | 批量执行检查命令          | 已有 Ansible 时       |
| 监控平台自带报表        | Zabbix、夜莺的巡检报表    | 国内常用              |

两个版本对应两种环境：没有监控时用 Shell 逐台检查，有 Prometheus 后用 Python 直接查数据，不再登录机器。

## 怎么做

做了两个版本，对应两种常见环境：

- **Shell 版**（没有监控系统时）：控制机 ssh 到每台机器，检查磁盘、内存、负载、关键服务、MySQL 主从；超阈值标 `[WARN]`，有告警时退出码为 1；cron 每 5 分钟跑一次，报告保留 7 天
- **Python 版**（已经有 Prometheus 时）：直接调 Prometheus API 出报告，不用登录机器；`--notify` 有告警时推给 Alertmanager，标签里带上检查项，避免被当成同一条告警去重

## 实施步骤

| 步骤 | 做什么                                            | 怎么验证                                 |
| ---- | ------------------------------------------------- | ---------------------------------------- |
| 1    | 配置控制机到各节点的密钥登录                      | `ssh node1 true` 不需要密码              |
| 2    | 写 Shell 巡检：磁盘、内存、负载、服务、MySQL 主从 | 手动运行看到报告，停掉 Redis 出现 [WARN] |
| 3    | cron 每 5 分钟运行，报告保留 7 天                 | `~/inspect/` 下按时间生成报告            |
| 4    | Python 版调 Prometheus API 出 Markdown 报告       | 和 Shell 版结果一致                      |
| 5    | `--notify` 有告警时推给 Alertmanager              | 调低阈值后 Alertmanager 收到告警         |

## 关键代码

```bash
check_host() {
  local h=$1
  # 1. 能不能连上：连不上后面都不用查了
  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$h" true 2>/dev/null; then
    warn "$h SSH 连接失败"
    return 1
  fi
  # 2. 磁盘
  local disk
  disk=$(ssh "$h" df --output=pcent / | tail -1 | tr -dc '0-9')
  if (( disk >= DISK_WARN )); then
    warn "$h 磁盘 / 使用率 ${disk}%（阈值 ${DISK_WARN}%）"
  fi
  # 内存、负载、服务、MySQL 主从同理
}
```

```text
# crontab：Shell 巡检每 5 分钟；Python 报告每天 9 点，有问题才推送
*/5 * * * * /home/vagrant/bin/inspect.sh > /dev/null 2>> /home/vagrant/inspect/cron-error.log
0 9  * * * cd /home/vagrant/opstool && .venv/bin/python report.py -o reports/report-$(date +\%F).md --notify >/dev/null 2>&1
```

## 效果指标

| 指标         | 之前                                | 之后                      |
| ------------ | ----------------------------------- | ------------------------- |
| 每天巡检耗时 | 逐台登录查看，每台约 5 分钟（估算） | 自动生成，0 人工          |
| 发现问题     | 巡检时才发现                        | 5 分钟内出现在报告里      |
| 覆盖         | 容易漏看                            | 每项都检查，超阈值标 WARN |

- 在实验环境（ops-lab 实验 04、11）跑通：停掉 Redis 后出现 `[WARN]`、退出码 1；调低阈值后告警推到了 Alertmanager
- 生产环境里这部分已经被 Prometheus 告警规则 + 每周周报取代，见相关案例

## 常见坑

- **手动能跑、cron 里不跑**：cron 的 PATH 只有 `/usr/bin:/bin`，命令要写绝对路径。
- **合盖后巡检中断**：宿主机睡眠，虚拟机被冻结，cron 不会补跑错过的任务。需要补跑时用 systemd timer 的 `Persistent=true`。
- **PromQL 结果里 `host` 标签丢了**：用 `on(instance)` 做一对一匹配时，结果只保留 on 里列出的标签。改成 `count without (cpu, mode)`。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：把巡检数据写成一段人能读懂的总结，指出趋势（磁盘一周涨了 10%）
- **不该交给 AI**：阈值判断保持规则
- **怎么落地**：巡检报告末尾加 AI 总结

## 面试怎么讲

30 秒讲法：

> 我做了两个版本的巡检：Shell 版在控制机上 SSH 到每台机器检查磁盘、内存、负载、服务和 MySQL 主从，超阈值标 WARN、退出码为 1，cron 每 5 分钟跑；Python 版直接调 Prometheus API 出报告，有问题推给 Alertmanager。踩过 cron 里 PATH 不全导致命令找不到的坑。生产环境里这些已经被 Prometheus 告警规则和每周周报取代。

常见追问：

**问：cron 里脚本手动能跑、定时不跑，怎么排查？**

答：先看 cron 日志确认是否执行；最常见是 PATH 只有 /usr/bin:/bin，命令写绝对路径；其次是环境变量、工作目录、% 需要转义。

**问：巡检脚本怎么避免一台机器卡住拖慢整体？**

答：SSH 加 ConnectTimeout 和 BatchMode，连不上直接记为异常；机器多时用并发（xargs -P 或 Python 线程池）。

**问：有了 Prometheus 还需要巡检吗？**

答：日常问题由告警覆盖；巡检转为周报和趋势分析（容量规划、证书、备份），以及告警覆盖不到的配置检查。

**被问到「能不能用 AI」时：** 巡检阈值用规则，AI 帮我把几十项数据总结成几句话。
