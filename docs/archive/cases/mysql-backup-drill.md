---
title: 'MySQL 自动备份 + 每周自动恢复演练'
summary: '每天全量备份并上传异地，每周自动把备份恢复到临时库、核对行数，证明备份真的能用。'
category: backup
status: design
pains: [risk]
value: '每周自动恢复演练，证明备份真的能用'
ai: none
stack: ['MySQL', 'Shell', 'cron', 'restic']
order: 20
flow:
  - label: '每天 02:00 mysqldump'
  - label: '压缩加密 → 异地'
  - label: '每周日恢复到临时库'
  - label: '核对表数量 / 行数'
  - label: '报告发到群'
related: [backup-restic, db-migration-pipeline]
tags: ['MySQL', '备份', '恢复演练', 'mysqldump']
---

## 问题

很多公司「有备份」，但从没恢复过。真出事时才发现备份是空的、缺表、或者恢复要几个小时。

## 适用场景

- 适合：任何使用 MySQL 的生产系统
- 不值得：数据可以随时重建的测试库
- 前提：有异地存储，有空闲资源起临时容器做恢复

## 工具选型

| 方案             | 特点               | 适用             |
| ---------------- | ------------------ | ---------------- |
| mysqldump        | 逻辑备份，简单通用 | 小于几十 GB      |
| XtraBackup       | 物理热备，速度快   | 大库，国内外常用 |
| mydumper         | 多线程逻辑备份     | 中等规模         |
| 云数据库自动备份 | 托管               | 云上             |

数据量小用 mysqldump 足够，恢复演练也简单；超过几十 GB 换 XtraBackup。

## 怎么做

- 每天 02:00 用 `mysqldump --single-transaction` 做不锁表的全量备份，gzip 后用 restic 加密上传异地
- 每周日自动起一个临时 MySQL 容器，下载最新备份恢复进去，对比关键表的行数和生产库的差距
- 恢复耗时、行数对比、是否成功写成报告发到企业微信；连续两次失败升级告警
- 在本地实验 03、04 里实现过备份脚本和 cron，恢复演练是在此基础上的自动化

## 实施步骤

| 步骤 | 做什么                                       | 怎么验证                     |
| ---- | -------------------------------------------- | ---------------------------- |
| 1    | 每天 mysqldump --single-transaction 全量备份 | 备份文件能解压并看到建表语句 |
| 2    | restic 加密上传异地                          | 异地有快照                   |
| 3    | 每周起临时容器恢复最新备份                   | 恢复成功                     |
| 4    | 核对表数量和行数                             | 和生产差异在合理范围         |
| 5    | 报告恢复耗时，失败告警                       | 群里收到演练报告             |

## 关键代码

示例代码（简化），没有在生产环境运行：

```bash
docker run -d --name restore-test -e MYSQL_ROOT_PASSWORD=drill mysql:8.0
until docker exec restore-test mysqladmin ping -pdrill --silent; do sleep 2; done
gunzip -c "$LATEST" | docker exec -i restore-test mysql -pdrill
docker exec restore-test mysql -pdrill -N -e \
  "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema='labdb'"
```

## 效果指标

| 指标            | 做之前       | 预期效果（估算） |
| --------------- | ------------ | ---------------- |
| 备份是否可用    | 不知道       | 每周验证         |
| 恢复耗时（RTO） | 未知         | 每周实测一次     |
| 备份失败发现    | 可能几个月后 | 当天告警         |

把「备份能恢复」从相信变成每周验证一次的事实，还能得到真实的恢复耗时（RTO）。

## 常见坑

- **备份锁表影响业务**：用了默认参数导致 MyISAM 或 DDL 锁。InnoDB 用 --single-transaction；备份在从库上做。
- **恢复时字符集乱码**：导出和导入的字符集不一致。导出时指定 --default-character-set=utf8mb4。
- **只有全量备份，恢复点太旧**：两次全量之间的数据丢失。同时备份 binlog，可以恢复到任意时间点。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：解读演练失败的日志
- **不该交给 AI**：备份、恢复、核对都要确定
- **怎么落地**：保持规则

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我在实验 03、04 做过 MySQL 主从和每天 02:00 的 mysqldump 备份。进一步的方案是每周自动起一个临时 MySQL 容器，把最新备份恢复进去，核对关键表的行数，把恢复耗时和结果发到企业微信，连续失败就告警。备份同时用 restic 加密上传异地，binlog 也备份，可以恢复到任意时间点。

常见追问：

**问：mysqldump 的 --single-transaction 有什么用？**

答：在 InnoDB 上开启一致性快照，备份期间不锁表，得到某一时刻一致的数据。

**问：如何恢复到某个时间点？**

答：先恢复最近的全量备份，再用 mysqlbinlog 重放 binlog 到误操作之前的位置。

**问：逻辑备份和物理备份怎么选？**

答：逻辑备份（mysqldump）可读、跨版本，但大库慢；物理备份（XtraBackup）快，适合大库。

**被问到「能不能用 AI」时：** 恢复演练要可重复、可证明，不适合 AI。
