---
title: '日志轮转和自动清理：磁盘不再被日志写满'
summary: '统一配置日志按天切割、压缩、保留 14 天，另有定时任务清理临时文件和旧安装包，磁盘使用率超标前先自动处理。'
category: infra
status: design
pains: [risk]
value: '从源头消除最常见的「磁盘满」故障'
ai: none
stack: ['logrotate', 'systemd-journald', 'Ansible']
order: 15
flow:
  - label: '应用日志'
  - label: 'logrotate 每天切割、压缩'
  - label: '保留 14 天'
  - label: 'journald 限制大小'
  - label: 'Ansible 统一下发配置'
related: [auto-remediation, inspection-report, registry-cleanup]
tags: ['日志', 'logrotate', '磁盘', '清理']
---

## 问题

「磁盘满了」是最常见的故障之一，而元凶十有八九是没有切割的日志：一个 nohup.out 几十 G。

## 适用场景

- 适合：所有 Linux 服务器——这是最基础的运维规范
- 不值得：完全容器化、日志全部走标准输出的环境（由容器运行时轮转）
- 前提：应用日志写到固定目录

## 工具选型

| 方案                       | 特点             | 适用                     |
| -------------------------- | ---------------- | ------------------------ |
| logrotate                  | 系统自带，最常用 | 国内外通用 ✅            |
| journald 限额              | systemd 日志     | systemd 系统             |
| Docker 日志驱动限额        | 容器日志         | Docker（实验 07 已配置） |
| 应用自身轮转（logback 等） | 按大小、时间切割 | Java 等                  |

logrotate 系统自带、配置简单；配合 journald 和 Docker 的限额，覆盖所有日志来源。

## 怎么做

- 所有应用日志统一写到 `/var/log/<应用>/`，用 logrotate 每天切割、压缩，保留 14 天
- 应用不支持重新打开日志文件的用 `copytruncate`，支持的用 `postrotate` 发信号，避免丢日志
- journald 设置 `SystemMaxUse=1G`，Docker 设置日志驱动大小限制（实验 07 里配过 10m × 3）
- 配置用 Ansible 统一下发，新服务器自动生效；巡检脚本检查是否有超过 1G 的单个日志文件

## 实施步骤

| 步骤 | 做什么                            | 怎么验证                             |
| ---- | --------------------------------- | ------------------------------------ |
| 1    | 统一应用日志目录 /var/log/<应用>/ | 日志都在约定位置                     |
| 2    | 编写 logrotate 配置并用 -d 调试   | `logrotate -d` 输出符合预期          |
| 3    | 配置 journald SystemMaxUse        | `journalctl --disk-usage` 不超过限额 |
| 4    | Docker 配置日志大小限制           | 容器日志不再无限增长                 |
| 5    | Ansible 统一下发，巡检检查大文件  | 没有超过 1G 的单个日志               |

## 关键代码

示例代码（简化），没有在生产环境运行：

```text
/var/log/myapp/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

## 效果指标

| 指标       | 做之前         | 预期效果（估算） |
| ---------- | -------------- | ---------------- |
| 磁盘满故障 | 常见           | 从源头消除       |
| 日志保留   | 无限制或手工删 | 统一 14 天       |
| 配置一致性 | 各台不同       | Ansible 统一     |

从源头上消除最常见的「磁盘满」故障。

## 常见坑

- **轮转后应用继续写已删除的旧文件**：应用一直持有文件句柄。用 postrotate 发信号让应用重新打开，或用 copytruncate。
- **删除了大日志但磁盘没释放**：文件仍被进程打开。`lsof +L1` 找到进程，重启或清空文件而不是删除。
- **copytruncate 丢了少量日志**：复制和截断之间的写入。能重开文件的应用不用 copytruncate。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：基本不需要
- **不该交给 AI**：清理规则必须确定
- **怎么落地**：保持规则

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 磁盘满是最常见的故障之一，元凶多半是没切割的日志。我会规定应用日志写在 /var/log/应用名/，用 logrotate 每天切割、压缩、保留 14 天；journald 设 SystemMaxUse，Docker 设日志大小限制。配置用 Ansible 统一下发，巡检脚本检查有没有超过 1G 的单个日志文件。

常见追问：

**问：删了大文件磁盘空间没释放怎么办？**

答：文件还被进程打开。用 `lsof +L1` 找到进程，重启它，或者用 `> 文件` 清空而不是删除。

**问：copytruncate 和 create 的区别？**

答：copytruncate 复制后截断原文件，应用无感知但可能丢少量日志；create 重命名后建新文件，需要应用重新打开文件。

**问：logrotate 是怎么定时运行的？**

答：由 cron.daily 或 systemd 的 logrotate.timer 每天触发。

**被问到「能不能用 AI」时：** 日志轮转是标准配置，不需要 AI。
