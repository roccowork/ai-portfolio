---
title: Terraform + Ansible 一条命令建好一组虚拟机
summary: make up 从模板克隆虚拟机、配好系统和 Nginx；make down 全部销毁，结果可重复。
category: infra
status: pending
pains: [speed, accuracy]
value: '一条命令建好一组虚拟机，结果可重复'
ai: boost
stack: [Terraform, bpg/proxmox, Ansible, cloud-init, Proxmox VE, Ceph]
order: 1
flow:
  - label: make up
  - label: Terraform
    detail: API 令牌，从模板克隆
  - label: cloud-init
    detail: IP、用户、公钥
  - label: 主机清单
    detail: 由 Terraform 输出生成
  - label: Ansible
    detail: 系统加固 + Nginx
  - label: 访问测试
    detail: 逐台 curl
related: [config-drift]
tags: [基础设施即代码, IaC, 虚拟化, PVE, 批量建机]
---

## 问题

虚拟机在网页上一台台点出来，换个人、换套环境就没法复现；装好系统后还要逐台登录配置。

## 适用场景

- 适合：经常要建一组相同配置的虚拟机（测试环境、培训环境、客户交付）的团队
- 不值得：几台长期不变的服务器，手工建一次就够了
- 前提：虚拟化平台有 API（PVE、vSphere、云厂商），有做好的系统模板

## 工具选型

| 方案                 | 特点                               | 适用                     |
| -------------------- | ---------------------------------- | ------------------------ |
| Terraform / OpenTofu | 声明式，有状态文件，计划和执行分开 | 国内外都常用 ✅ 本站选用 |
| Pulumi               | 用编程语言写基础设施               | 外企部分团队             |
| Ansible              | 配置管理，无需客户端               | 国内外都常用 ✅ 本站选用 |
| SaltStack            | 速度快，有客户端                   | 国内部分大厂存量         |

Terraform 管「机器有没有、长什么样」，Ansible 管「机器里装什么、怎么配」，两者分工清楚，是最常见的组合。

## 怎么做

在笔记本上用 VirtualBox 搭的 3 节点 Proxmox VE + Ceph 集群里实现：

- **最小权限令牌**：专用用户 `terraform@pve`，角色只包含克隆、配置、开关机、用存储和网桥的权限；令牌通过环境变量传给 provider，代码里没有密钥
- **cloud-init 模板**：Debian 13 云镜像导入成模板，磁盘放在 Ceph 上，克隆出来的虚拟机可以放到任意节点
- **Terraform** 用 `for_each` 按清单批量克隆，自动设 IP、注入公钥；加一行就多一台
- **主机清单不手写**：由 Terraform 输出生成，Ansible 直接用
- **Ansible** 两个 role：base（换源、时区、NTP、SSH 加固、防火墙）和 nginx；再跑一次 `changed=0`

## 实施步骤

| 步骤 | 做什么                                              | 怎么验证                                      |
| ---- | --------------------------------------------------- | --------------------------------------------- |
| 1    | PVE 上建最小权限用户和 API 令牌                     | 用令牌调 API 能列出节点，但不能删除别的虚拟机 |
| 2    | 导入 Debian 云镜像做成 cloud-init 模板，磁盘放 Ceph | 手工克隆一台能开机、能用密钥登录              |
| 3    | Terraform 用 for_each 批量克隆，设置 IP 和公钥      | `terraform plan` 显示要创建的虚拟机           |
| 4    | Terraform 输出生成 Ansible 主机清单                 | `ansible all -m ping` 全部成功                |
| 5    | Ansible 跑 base 和 nginx 两个 role                  | 再跑一次 `changed=0`                          |
| 6    | Makefile 串起来：`make up` / `make down`            | 从零到逐台 curl 通过，一条命令完成            |

## 关键代码

```hcl
resource "proxmox_virtual_environment_vm" "vm" {
  for_each  = var.vms
  name      = each.key
  node_name = each.value.node

  clone {
    vm_id        = var.template_id
    node_name    = var.template_node # 模板磁盘在共享存储上，才能跨节点克隆
    datastore_id = var.datastore
  }

  initialization {
    datastore_id = var.datastore
    ip_config {
      ipv4 {
        address = "${each.value.ip}/24"
        gateway = var.gateway
      }
    }
    user_account {
      username = "debian"
      keys     = [trimspace(file(pathexpand(var.ssh_public_key_file)))]
    }
  }
}
```

```makefile
up: token-check
	$(TF) apply -auto-approve -input=false
	$(TF) output -raw ansible_inventory > ansible/inventory.ini
	cd ansible && ansible-playbook site.yml
	$(MAKE) verify
```

## 效果指标

| 指标              | 之前                                            | 之后                               |
| ----------------- | ----------------------------------------------- | ---------------------------------- |
| 建一组 3 台虚拟机 | 网页上逐台点，再逐台登录配置，约 1 小时（估算） | `make up` 一条命令（实测耗时待补） |
| 环境是否可复现    | 不同人建的不一样                                | 代码一样，结果一样                 |
| 销毁              | 逐台删除，容易漏                                | `make down`                        |

- 代码和文档已写完，CI 里 `terraform validate`、`ansible-lint`（production 档）、`shellcheck` 都通过
- 还没在 PVE 集群上实际执行，跑通后补上 `make up` 的实测耗时

## 常见坑

- **跨节点克隆失败**：模板磁盘在本地存储上，其他节点访问不到。模板磁盘放共享存储（Ceph），克隆时指定模板所在节点。
- **Docker 版实验里 plan 永远有差异**：daemon.json 设置的日志配置、memory_swap 默认值没写进代码。把这些默认值显式写进 Terraform 代码（ops-lab 实验 12）。

## 前身

ops-lab 实验 12 用同样的流程管理 Docker 容器（kreuzwerker/docker provider）：2 个 → 3 个 → 修改触发重建 → 手动删容器测漂移 → destroy，已跑通。那次的坑：daemon.json 里的日志配置和 `memory_swap` 默认值必须写进代码，否则 `plan` 永远有差异。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：根据描述生成 Terraform 和 Ansible 代码初稿；解读 terraform plan 的影响
- **不该交给 AI**：apply 必须经过人审核 plan，不能让 AI 直接执行
- **怎么落地**：用 AI 写初稿，plan 输出交给 AI 做风险摘要，人审核后执行

## 面试怎么讲

30 秒讲法：

> 我在笔记本上用 VirtualBox 搭了 3 节点 Proxmox VE + Ceph 集群，用 Terraform 从 cloud-init 模板批量克隆虚拟机，Ansible 做系统加固和装 Nginx，主机清单由 Terraform 输出生成，make up 一条命令从零交付，make down 全部销毁。Terraform 用的是只有克隆和开关机权限的专用令牌。代码已通过 validate 和 ansible-lint，实际执行还在验证。

常见追问：

**问：Terraform 和 Ansible 怎么分工？**

答：Terraform 管资源的生命周期（创建、修改、销毁），有状态文件；Ansible 管机器内部的配置，靠幂等保证可重复。两者都能做对方的事，但分开最清楚。

**问：Terraform 的 state 文件怎么管理？**

答：多人协作时放远端后端（S3、OSS、Terraform Cloud）并开启锁；state 里可能有敏感信息，不能提交到 Git。

**问：什么是配置漂移？Terraform 怎么发现？**

答：有人绕过代码手工改了资源。`terraform plan` 会显示实际状态和代码的差异，实验里手动删容器后 plan 显示要重建。

**被问到「能不能用 AI」时：** AI 能写 Terraform 初稿和解读 plan，但 apply 前必须人看过计划。
