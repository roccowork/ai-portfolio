---
title: 配置漂移：发现并自动纠正
summary: 有人手工改了服务器配置，Ansible 先报出差异，再一键改回代码描述的状态。
category: infra
status: lab
pains: [risk, compliance]
value: '手工改动几十秒内被发现并一键改回'
ai: boost
stack: [Ansible, Ansible Vault]
order: 2
flow:
  - label: 有人手工改配置
  - label: ansible-playbook --check --diff
    detail: 只检查，列出差异
  - label: ansible-playbook
    detail: 改回并重启服务
  - label: 再跑一次
    detail: changed=0
related: [terraform-ansible-vms, inspection-report]
tags: [配置管理, 漂移, 幂等, 审计, Ansible]
---

## 问题

服务器多了以后，总有人登录上去手工改配置。改动没有记录，下次出问题没人知道是谁改了什么，机器之间也越来越不一样。

## 适用场景

- 适合：服务器超过十台、多人有登录权限的团队；等保要求「配置基线」的公司
- 不值得：全部是不可变基础设施（容器、每次重建虚拟机）的环境，漂移靠重建解决
- 前提：配置已经用 Ansible 等工具描述出来

## 工具选型

| 方案                   | 特点                   | 适用                     |
| ---------------------- | ---------------------- | ------------------------ |
| Ansible --check --diff | 零额外组件             | 国内外都常用 ✅ 本站选用 |
| Puppet / Chef          | 客户端定期自动纠正     | 外企传统环境             |
| Argo CD selfHeal       | K8s 资源的漂移自动纠正 | ✅ 本站集群在用          |
| Terraform plan         | 云资源漂移             | 基础设施层               |

选 Ansible：已经用它交付配置，`--check --diff` 不需要任何新组件就是现成的审计工具。

## 怎么做

- 把系统初始化（运维账号、SSH 加固、时间同步、防火墙）和中间件部署写成 Ansible role：baseline、web、redis
- 所有 task 都是幂等的：同一个 playbook 跑多少次结果都一样，第二次 `changed=0`
- 密码用 Ansible Vault 加密后提交，密码文件只在控制机上
- **定期跑 `--check --diff`** 就是最简单的配置审计：没有 changed 说明机器和代码一致；有差异直接显示改了哪一行

## 实施步骤

| 步骤 | 做什么                                  | 怎么验证                          |
| ---- | --------------------------------------- | --------------------------------- |
| 1    | 把初始化写成 role：baseline、web、redis | 新机器跑完即可交付                |
| 2    | 密码用 Ansible Vault 加密后提交         | Git 里看不到明文密码              |
| 3    | 确认幂等                                | 连续跑两次，第二次 `changed=0`    |
| 4    | 模拟有人手工改配置                      | `--check --diff` 显示改动的那一行 |
| 5    | 执行 playbook 改回                      | 再跑一次 `changed=0`              |

## 关键代码

```bash
# 模拟有人手工改了 SSH 配置
ssh node3 "sudo sed -i 's/MaxAuthTries 3/MaxAuthTries 6/' /etc/ssh/sshd_config.d/10-hardening.conf"

ansible-playbook site.yml --check --diff   # 显示 -MaxAuthTries 6 / +MaxAuthTries 3
ansible-playbook site.yml                  # 改回 3，并触发重启 sshd
ansible-playbook site.yml                  # 再跑：changed=0
```

```yaml
- name: SSH 加固
  ansible.builtin.template:
    src: 10-hardening.conf.j2
    dest: /etc/ssh/sshd_config.d/10-hardening.conf
    mode: '0644'
    validate: /usr/sbin/sshd -t -f %s # 配置写错时不覆盖，避免把自己锁在外面
  notify: 重启 sshd
```

## 效果指标

| 指标         | 之前               | 之后                                    |
| ------------ | ------------------ | --------------------------------------- |
| 发现手工改动 | 出问题后才发现     | `--check --diff` 几十秒列出差异（实测） |
| 纠正         | 登录逐台改         | 一条命令全部改回                        |
| 新机器交付   | 手工按文档一步步做 | 3 个 role 从零交付（ops-lab 实验 06）   |

- 在实验环境（ops-lab 实验 06）用 3 个 role 从零交付一台新机器，重复执行 `changed=0`
- 手工改坏的 SSH 配置被 `--check --diff` 发现，执行后改回

## 常见坑

- **改坏 sshd 配置把自己锁在外面**：配置写错，重启 sshd 后无法登录。模板任务加 `validate: sshd -t -f %s`，检查不通过就不覆盖。
- **对 node1、node2 跑 baseline 挡住了 MySQL 复制**：baseline 会开启 ufw，默认拒绝 3306。role 只用于目标主机组；开防火墙前先放行已有服务的端口。

## 下一步

把 `--check` 放进定时任务，有差异时推送企业微信，就是一个自动的配置漂移告警。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：解释 diff 的含义和风险：「这行改动会让 SSH 允许密码登录」
- **不该交给 AI**：是否改回由人决定，配置以 Git 为准
- **怎么落地**：漂移告警附 AI 对差异的风险说明

## 面试怎么讲

30 秒讲法：

> 我把系统初始化和中间件写成了 3 个 Ansible role，所有任务都是幂等的，第二次运行 changed=0。配置漂移检测就是定期跑 --check --diff：没有 changed 说明机器和代码一致，有差异会直接显示改了哪一行。实验里手工改了 SSH 配置，被 diff 发现后一条命令改回。SSH 配置模板加了 validate，写错了不会覆盖，避免把自己锁在外面。

常见追问：

**问：什么是幂等？怎么保证？**

答：同一操作执行多次结果一样。用 Ansible 的声明式模块（template、service、user）而不是 shell 命令；必须用 shell 时加 creates 或 changed_when。

**问：handler 和普通 task 有什么区别？**

答：handler 只有被 notify 且对应任务有变化时才执行，并且在 play 末尾只执行一次，适合重启服务。

**问：如何把漂移检测做成自动告警？**

答：定时任务跑 `--check`，解析输出里的 changed 数量，大于 0 就推送到企业微信，附上 diff。

**被问到「能不能用 AI」时：** 漂移检测是确定的，AI 帮我把 diff 翻译成风险说明。
