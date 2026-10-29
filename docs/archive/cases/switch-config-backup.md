---
title: '交换机配置自动备份：每天存一份，变化一目了然'
summary: '每天自动登录所有交换机导出配置存进 Git，有变化就发出差异，设备坏了能直接恢复配置。'
category: network
status: design
pains: [risk, compliance]
value: '谁改了交换机配置一清二楚，换设备 10 分钟恢复'
ai: boost
stack: ['Python', 'Netmiko', 'Git', '企业微信']
order: 20
flow:
  - label: '设备清单'
  - label: 'Netmiko SSH 登录'
    detail: 'display current-configuration'
  - label: '写入 Git 仓库'
  - label: '有变化 → 发 diff'
  - label: '坏了按版本恢复'
related: [it-checkup, network-inspection]
tags: ['交换机', '网络设备', '配置备份', '华为', 'Netmiko']
---

## 问题

交换机配置只存在设备里：有人改了配置没人知道，设备坏了换新的要凭记忆重配，出故障也说不清是谁改了什么。

## 适用场景

- 适合：交换机、路由器、防火墙十台以上的公司
- 不值得：只有一两台傻瓜交换机的
- 前提：设备支持 SSH，有只读账号

## 工具选型

| 方案             | 特点                             | 适用         |
| ---------------- | -------------------------------- | ------------ |
| Oxidized         | 开源，支持上百种设备，自动存 Git | 国内外都常用 |
| Python + Netmiko | 灵活，可做其他自动化             | ✅ 方案采用  |
| RANCID           | 老牌，基于 CVS/SVN               | 传统         |
| 厂商网管平台     | 单一品牌                         | 大型网络     |

Oxidized 开箱即用；用 Netmiko 自写则可以顺便做巡检和批量配置。

## 怎么做

- 设备清单（IP、型号、厂商）放在 YAML 里，账号用只读权限的专用账号
- Python + Netmiko 并发 SSH 登录，华为执行 `display current-configuration`、思科执行 `show running-config`
- 每台一个文件提交到 Git；`git diff` 有变化就把差异发到企业微信，谁什么时候改了什么一清二楚
- 去掉配置里会自动变化的行（时间戳）避免误报；密码字段脱敏后再入库

## 实施步骤

| 步骤 | 做什么                         | 怎么验证              |
| ---- | ------------------------------ | --------------------- |
| 1    | 整理设备清单（IP、厂商、型号） | 清单完整              |
| 2    | 创建只读账号                   | 只能执行 display/show |
| 3    | 脚本并发登录导出配置           | 每台一个文件          |
| 4    | 提交到 Git，有变化发 diff      | 改一行配置后收到差异  |
| 5    | 密码字段脱敏                   | Git 里没有明文密码    |

## 关键代码

示例代码（简化），没有在生产环境运行：

```python
from netmiko import ConnectHandler

conn = ConnectHandler(device_type="huawei", host=ip, username=user, password=pwd)
cfg = conn.send_command("display current-configuration")
cfg = "\n".join(l for l in cfg.splitlines() if not l.startswith("!Last configuration"))
Path(f"configs/{name}.cfg").write_text(cfg)
```

## 效果指标

| 指标           | 做之前       | 预期效果（估算）                 |
| -------------- | ------------ | -------------------------------- |
| 配置变更可追溯 | 不知道谁改的 | Git 历史                         |
| 设备故障恢复   | 凭记忆重配   | 直接导入备份，约 10 分钟（估算） |
| 配置备份       | 没有         | 每天一份                         |

网络变更可追溯，换设备时 10 分钟恢复配置。和本站「机房网络」页面的设备清单可以共用一份数据。

## 常见坑

- **每天都报告「有变化」**：配置里有时间戳等自动变化的行。比较前去掉这些行。
- **分页导致输出不完整**：设备开了分屏显示。先执行关闭分屏（screen-length 0 temporary）。
- **配置里的密码进了 Git**：明文或可逆加密的密码。入库前脱敏，仓库设为私有。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：把配置 diff 翻译成人能读懂的变更说明和风险
- **不该交给 AI**：备份必须确定
- **怎么落地**：diff 通知附 AI 说明

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会用 Python 的 Netmiko 并发 SSH 登录所有交换机，华为执行 display current-configuration、思科执行 show running-config，每台一个文件提交到 Git，有变化就把 diff 发到企业微信。去掉会自动变化的时间戳行避免误报，密码字段脱敏后再入库。设备坏了换新的，10 分钟就能恢复配置。

常见追问：

**问：Netmiko 和 Paramiko 的区别？**

答：Paramiko 是底层 SSH 库；Netmiko 在它之上封装了各厂商设备的提示符、分页、配置模式处理。

**问：华为设备怎么关闭分屏？**

答：`screen-length 0 temporary`；思科是 `terminal length 0`。

**问：网络自动化还能做什么？**

答：批量配置下发、巡检、合规检查（比如所有端口都配了描述），工具有 Ansible 网络模块、Nornir。

**被问到「能不能用 AI」时：** 配置备份是确定的，AI 把 diff 翻译成「这次改了哪个 VLAN」。
