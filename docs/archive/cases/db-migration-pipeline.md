---
title: '数据库变更走流水线：表结构改动自动执行、可追溯'
summary: 'SQL 变更脚本放进 Git，流水线自动在测试库执行、审核后在生产执行，每次变更都有记录。'
category: data
status: design
pains: [risk, compliance]
value: '表结构变更可追溯、可回放，环境之间不再不一致'
ai: boost
stack: ['Flyway', 'GitHub Actions', 'MySQL']
order: 30
flow:
  - label: '提交 V12__add_index.sql'
  - label: 'PR 检查'
    detail: '在临时库试跑'
  - label: '审核合并'
  - label: '发布前执行'
    detail: 'flyway migrate'
  - label: '变更记录表'
related: [gitops-release, mysql-backup-drill]
tags: ['数据库', 'DDL', '变更管理', 'Flyway']
---

## 问题

DBA 或开发手动登录生产库执行 SQL：漏执行、执行两次、测试库和生产库结构不一致，出了问题也查不到是谁什么时候改的。

## 适用场景

- 适合：多个环境、多人改表结构的团队；需要审计「谁什么时候改了数据库」的公司
- 不值得：只有一个人维护、几乎不改表结构的小项目
- 前提：所有表结构变更都走脚本，禁止直接在库里改

## 工具选型

| 方案                                | 特点                             | 适用                    |
| ----------------------------------- | -------------------------------- | ----------------------- |
| Flyway                              | SQL 文件按版本号执行，简单       | Java 生态、国内外都常用 |
| Liquibase                           | 支持 XML/YAML，能自动生成回滚    | 外企常用                |
| Atlas                               | 声明式，对比目标结构自动生成变更 | 新兴                    |
| 框架自带（Django、Rails migration） | 和代码一起                       | 用对应框架时首选        |

选 Flyway：脚本就是普通 SQL，DBA 能直接审；版本表记录清楚，学习成本最低。

## 怎么做

- SQL 脚本按版本号命名（V1__init.sql、V2__add_user_phone.sql）放在代码仓库，和应用代码一起审核
- PR 时用 Docker 起一个临时 MySQL，从零执行全部脚本，语法错误在合并前就能发现
- 发布时先执行 `flyway migrate`，再更新应用；Flyway 用 `flyway_schema_history` 表记录每个脚本的执行时间和校验和，已执行的脚本被改动会直接报错
- 大表加索引等耗时操作单独标注，安排在维护窗口

## 实施步骤

| 步骤 | 做什么                                     | 怎么验证                           |
| ---- | ------------------------------------------ | ---------------------------------- |
| 1    | 现有库导出结构作为 V1 基线                 | 新建空库执行后结构一致             |
| 2    | 约定命名 V<版本>__<描述>.sql，放进代码仓库 | `flyway info` 列出所有版本         |
| 3    | PR 时在临时 MySQL 里从零执行               | 语法错误在 CI 里报出               |
| 4    | 发布流水线先 migrate 再更新应用            | `flyway_schema_history` 里有新记录 |
| 5    | 大表变更标注，安排维护窗口或用 gh-ost      | 变更评审时能看到标注               |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
- name: 在临时库试跑全部变更
  run: |
    docker run -d --name db -e MYSQL_ROOT_PASSWORD=test -p 3306:3306 mysql:8.0
    until docker exec db mysqladmin ping -ptest --silent; do sleep 2; done
    docker run --rm --network host -v $PWD/sql:/flyway/sql flyway/flyway \
      -url=jdbc:mysql://127.0.0.1:3306/app?createDatabaseIfNotExist=true -user=root -password=test migrate
```

## 效果指标

| 指标               | 做之前           | 预期效果（估算）           |
| ------------------ | ---------------- | -------------------------- |
| 环境之间结构不一致 | 经常出现         | 从同一套脚本生成，一致     |
| 变更可追溯         | 没有记录         | 每个脚本有执行时间和校验和 |
| 新环境建库         | 导出导入、手工补 | 一条命令                   |

数据库结构和代码一样可追溯、可回放；新环境一条命令就能建出相同结构。

## 常见坑

- **已执行的脚本被修改后启动失败**：Flyway 校验和不匹配。已执行的脚本不能改，新改动写新版本号。
- **大表加索引锁表导致业务卡住**：MySQL 部分 DDL 会锁表。用在线变更工具（gh-ost、pt-online-schema-change），或 ALGORITHM=INPLACE。
- **回滚困难**：Flyway 社区版不支持自动回滚。每个变更写好对应的回滚脚本，或用向前修复。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：审查 SQL 变更的风险（锁表、全表扫描）
- **不该交给 AI**：执行顺序和版本控制必须确定
- **怎么落地**：PR 里 AI 审查变更脚本

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会把所有表结构变更写成按版本号命名的 SQL 脚本，和代码放在同一个仓库里审核。PR 时 CI 起一个临时 MySQL 从零执行全部脚本，语法错误在合并前就能发现；发布时先 flyway migrate 再更新应用，Flyway 的版本表记录每个脚本的执行时间和校验和。大表变更用 gh-ost 这类在线工具，避免锁表。

常见追问：

**问：已经执行过的脚本发现写错了怎么办？**

答：不能改已执行的脚本，写一个新版本的脚本修正。

**问：先发应用还是先改数据库？**

答：先做向前兼容的数据库变更，再发应用；删字段这种破坏性变更放在应用不再使用之后。

**问：大表 DDL 怎么做？**

答：用 gh-ost 或 pt-online-schema-change：建影子表、同步增量、最后原子切换，全程不锁原表。

**被问到「能不能用 AI」时：** AI 能审查 DDL 有没有锁表风险，执行仍按版本顺序。
