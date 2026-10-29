---
title: '服务器申请自助化：填表审批后自动创建、自动交付'
summary: '开发在表单里选配置和用途，审批通过后 Terraform 自动创建云服务器、Ansible 初始化，账号和地址自动发给申请人。'
category: infra
status: design
pains: [speed, cost]
value: '申请服务器从一两天到半小时，到期自动回收'
ai: boost
stack: ['Terraform', 'Ansible', '审批流', 'GitLab CI / GitHub Actions']
order: 80
flow:
  - label: '填申请表'
    detail: '配置、用途、使用期限'
  - label: '主管审批'
  - label: '生成 Terraform 变量 PR'
  - label: 'CI 执行 apply + Ansible 初始化'
  - label: '交付 + 到期提醒回收'
related: [terraform-ansible-vms, env-auto-shutdown, cmdb-discovery]
tags: ['自助服务', 'Terraform', '服务器申请', '基础设施即代码']
---

## 问题

申请一台测试服务器要走邮件、等运维排期，一两天才能拿到；用完了没人回收，云费用越来越高。

## 适用场景

- 适合：开发和测试经常申请服务器、运维成为瓶颈的团队
- 不值得：服务器很少变化的环境
- 前提：已有 Terraform 和 Ansible 基础，审批流程明确

## 工具选型

| 方案                    | 特点                 | 适用        |
| ----------------------- | -------------------- | ----------- |
| 表单 + Terraform + CI   | 自建，灵活           | ✅ 方案采用 |
| Backstage 模板          | 开发者门户，外企常用 | 外企        |
| 云厂商服务目录          | 托管                 | 云上        |
| 蓝鲸、ServiceNow 等平台 | 流程完整             | 大型企业    |

复用现有 Terraform 和 Ansible，把申请变成对 IaC 仓库的一个 PR，运维只审核计划。

## 怎么做

- 申请表固定几种规格（小/中/大）和镜像，避免随意配置
- 审批通过后自动往 IaC 仓库提交一个 PR，在变量文件里加一台机器；CI 运行 `terraform plan`，运维看一眼计划后合并
- 合并后 CI 执行 `terraform apply`，再用 Ansible 跑基线初始化（账号、SSH 加固、监控 agent）
- 每台机器带「到期日」标签，到期前 3 天提醒申请人续期，不续期自动关机、7 天后删除

## 实施步骤

| 步骤 | 做什么                        | 怎么验证                   |
| ---- | ----------------------------- | -------------------------- |
| 1    | 定义规格模板和镜像            | 申请表只能选固定选项       |
| 2    | 审批通过后自动提交变量 PR     | IaC 仓库出现新 PR          |
| 3    | CI 运行 plan，运维审核合并    | PR 里能看到计划            |
| 4    | 合并后 apply + Ansible 初始化 | 新机器可登录，基线已配置   |
| 5    | 到期提醒和自动回收            | 到期未续的机器先关机后删除 |

## 关键代码

示例代码（简化），没有在生产环境运行：

```hcl
variable "servers" {
  type = map(object({ size = string, owner = string, expire = string }))
}
resource "alicloud_instance" "app" {
  for_each      = var.servers
  instance_name = each.key
  instance_type = local.sizes[each.value.size]
  tags          = { owner = each.value.owner, expire = each.value.expire, managed = "terraform" }
}
```

## 效果指标

| 指标       | 做之前         | 预期效果（估算）        |
| ---------- | -------------- | ----------------------- |
| 申请到交付 | 一两天（估算） | 约半小时                |
| 机器负责人 | 经常不清楚     | 每台都有 owner 和到期日 |
| 闲置机器   | 长期不回收     | 到期自动回收            |

申请到交付从一两天变成半小时，所有服务器都有负责人和到期日。

## 常见坑

- **并发申请导致 state 冲突**：多个 apply 同时运行。远端 state 加锁，CI 串行执行。
- **配额或库存不足导致创建失败**：云资源限制。plan 阶段检查配额，失败通知申请人。
- **到期删除了还在用的机器**：申请人没看到提醒。多次提醒，先关机 7 天再删除。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：把自然语言需求转成规格和配置；解读 plan
- **不该交给 AI**：审批和 apply 必须走流程
- **怎么落地**：申请页加 AI 助手推荐规格

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会把服务器申请变成对 IaC 仓库的一个 PR：申请表只能选几种规格，审批通过后自动往变量文件加一台机器并开 PR，CI 运行 terraform plan，运维看一眼就合并；合并后 apply 并用 Ansible 跑基线。每台机器带负责人和到期日标签，到期前提醒续期，不续期先关机再删除。

常见追问：

**问：为什么不让申请直接触发 apply？**

答：先 plan 让人审核，能发现意外的变更（比如误删资源）；Terraform 的变更必须可预期。

**问：Terraform 多人协作怎么避免冲突？**

答：远端 state + 锁，变更走 PR 和 CI 串行执行。

**问：怎么防止资源无限增长？**

答：每个资源有负责人和到期日，配额限制，定期清理。

**被问到「能不能用 AI」时：** 申请时 AI 可以根据描述推荐规格，审批和执行仍走流程。
