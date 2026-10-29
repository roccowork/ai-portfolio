---
title: '新电脑自动装机：开箱联网，软件和设置自动装好'
summary: '新电脑开机登录公司账号后，自动加入管理、装好常用软件、打印机和 VPN，IT 不用再一台台装。'
category: itsupport
status: design
pains: [speed]
value: '新电脑准备从半天降到不到 1 小时'
ai: none
stack: ['Windows Autopilot / Intune', 'winget', 'PowerShell']
order: 60
flow:
  - label: '新电脑开箱联网'
  - label: '登录公司账号'
    detail: 'Autopilot 自动注册'
  - label: '按部门下发软件'
    detail: 'winget / Intune'
  - label: '打印机、VPN、Wi-Fi 配置'
  - label: '完成后通知 IT'
related: [asset-inventory, account-lifecycle]
tags: ['装机', '新员工', 'Autopilot', 'winget', '软件分发']
---

## 问题

每来一个新员工，IT 要花半天装系统、装软件、配打印机。电脑一多，配置还各不相同。

## 适用场景

- 适合：每月都有新员工入职、电脑数量多的公司
- 不值得：一年只来几个人的小团队
- 前提：有标准的软件清单；Autopilot 需要 Intune 授权

## 工具选型

| 方案                       | 特点                 | 适用        |
| -------------------------- | -------------------- | ----------- |
| Windows Autopilot + Intune | 开箱自动配置，零接触 | 外企常用    |
| MDT / WDS                  | 免费，网络装机       | 国内常用    |
| winget + PowerShell        | 免费，软件自动安装   | ✅ 方案采用 |
| 联想、戴尔出厂预装服务     | 厂商提供             | 大批量采购  |

有 Intune 用 Autopilot；没有的小公司用无人值守安装 + winget 脚本，也能把装机时间缩短到 1 小时内。

## 怎么做

- 有 Intune 的：电脑硬件哈希提前登记到 Autopilot，员工开箱登录公司账号后自动完成注册和配置
- 没有 Intune 的小公司：U 盘无人值守安装 Windows + 首次登录运行的 PowerShell 脚本，用 winget 按清单装软件
- 软件清单按部门区分（财务多装网银控件、设计多装设计软件），放在共享目录里统一维护
- 装完自动上报到资产表，并在企业微信通知 IT

## 实施步骤

| 步骤 | 做什么                                     | 怎么验证             |
| ---- | ------------------------------------------ | -------------------- |
| 1    | 整理按部门的软件清单                       | 清单经部门确认       |
| 2    | 制作无人值守安装 U 盘（或 Autopilot 注册） | 新电脑自动装好系统   |
| 3    | 首次登录运行 winget 安装脚本               | 常用软件全部装好     |
| 4    | 配置打印机、VPN、Wi-Fi                     | 新员工开机即可用     |
| 5    | 完成后上报资产并通知 IT                    | 资产表和群里都有记录 |

## 关键代码

示例代码（简化），没有在生产环境运行：

```powershell
$apps = @("7zip.7zip", "Google.Chrome", "Tencent.WeCom", "Microsoft.Teams")
if ($dept -eq "Finance") { $apps += "Kingsoft.WPSOffice" }
foreach ($id in $apps) {
  winget install --id $id --silent --accept-package-agreements --accept-source-agreements
}
```

## 效果指标

| 指标           | 做之前       | 预期效果（估算） |
| -------------- | ------------ | ---------------- |
| 一台新电脑准备 | 半天（估算） | 不到 1 小时      |
| 配置一致性     | 每台不同     | 按部门标准化     |
| IT 投入        | 全程手工     | 只需开箱和联网   |

新电脑准备时间从半天降到不到 1 小时，所有电脑配置一致。

## 常见坑

- **winget 在新系统上不可用**：应用安装程序还没更新。先更新 App Installer，或者离线安装 winget。
- **部分软件 winget 里没有**：国产软件、内部软件。放共享目录用静默安装参数。
- **驱动缺失**：无人值守镜像没带驱动。按型号注入驱动，或用厂商驱动工具。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：基本不需要
- **不该交给 AI**：装机流程必须确定
- **怎么落地**：保持规则

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 有 Intune 的话用 Autopilot：硬件哈希提前登记，员工开箱联网、登录公司账号就自动注册和下发配置。没有 Intune 的小公司，用无人值守安装 U 盘装系统，首次登录运行 PowerShell 脚本，用 winget 按部门清单静默装软件，再配好打印机和 VPN，完成后自动上报到资产表。

常见追问：

**问：winget 是什么？**

答：Windows 自带的包管理器，可以命令行搜索和静默安装软件。

**问：Autopilot 和传统镜像部署的区别？**

答：传统方式要给电脑重装企业镜像；Autopilot 用出厂系统，通过云端下发配置，不用碰电脑。

**问：标准化装机有什么好处？**

答：配置一致、故障排查简单、安全基线统一、新员工第一天就能工作。

**被问到「能不能用 AI」时：** 装机必须标准化，不需要 AI。
