---
title: 故障演练自动化
summary: 定期自动停一个服务，确认告警按时触发和解除，自动生成复盘记录——证明监控一直有效。
category: inspection
status: pending
pains: [risk]
value: '定期验证告警真的有效，自动生成复盘'
ai: boost
stack: [Python, Prometheus API, Alertmanager API, SSH]
order: 2
flow:
  - label: 前置检查
    detail: Prometheus 可访问、告警没在响
  - label: 注入故障
    detail: 停一个服务
  - label: 等告警
    detail: pending → firing → Alertmanager
  - label: 恢复
    detail: 任何情况都执行
  - label: 等告警解除
  - label: 生成复盘
    detail: 每一步的耗时
related: [inspection-report, alert-wecom-weekly]
tags: [故障演练, 混沌工程, 告警验证, 复盘]
---

## 问题

告警规则写好以后，没人能保证它一直有效：标签改了、exporter 换了端口、Alertmanager 配错了，告警会「静悄悄地失效」，直到真出事才发现。

## 适用场景

- 适合：已经有告警体系、想证明「告警真的有效」的团队；SRE 文化的公司都会做
- 不值得：还没有监控告警的环境，先把告警建起来
- 前提：只在能快速恢复的场景演练，先在测试环境做

## 工具选型

| 方案             | 特点                          | 适用         |
| ---------------- | ----------------------------- | ------------ |
| 自写脚本（本站） | 只用标准库，场景可控          | ✅ 本站选用  |
| Chaos Mesh       | K8s 上注入 Pod、网络、IO 故障 | 国内外都常用 |
| LitmusChaos      | K8s 混沌工程，场景库丰富      | 外企常用     |
| ChaosBlade       | 阿里开源，支持主机和 K8s      | 国内常用     |

选自写脚本：演练目标是验证告警链路，只需要停一个服务再恢复；脚本能精确记录每个阶段的耗时并生成复盘。

## 怎么做

- 场景只选「一条命令就能恢复」的故障：停 Nginx、停 Redis、停 node_exporter（模拟机器失联）
- 脚本轮询 Prometheus，记录 pending、firing、Alertmanager 收到、解除的时间点
- **恢复放在 `finally` 里**：超时、报错、Ctrl+C 都会执行恢复命令
- 按复盘模板生成 Markdown 记录：现象（各阶段耗时）、排查时采集的现场、根因、修复、预防
- 只用 Python 标准库，控制机上的系统 Python 就能跑；单元测试用假时钟和假 Prometheus

## 实施步骤

| 步骤 | 做什么                                       | 怎么验证                           |
| ---- | -------------------------------------------- | ---------------------------------- |
| 1    | 选场景：停 Nginx、停 Redis、停 node_exporter | 每个场景一条命令就能恢复           |
| 2    | 前置检查：Prometheus 可访问、目标告警没在响  | 检查失败直接退出，不注入故障       |
| 3    | 注入故障，轮询 Prometheus 和 Alertmanager    | 记录 pending、firing、通知的时间点 |
| 4    | 恢复放在 finally 里                          | Ctrl+C 中断也会执行恢复            |
| 5    | 等告警解除，生成复盘 Markdown                | 复盘里有每一步的耗时               |

## 关键代码

以下为节选，省略了部分参数和细节。

```python
code, out = runner(s.host, s.inject)
try:
    while clock() - t0 < s.timeout:
        state = alert_state(prom, s.alert, s.labels)
        if state == "firing" and r.firing_at is None:
            r.firing_at = clock() - t0
        if r.firing_at is not None and notified(am, s.alert, s.labels):
            r.notified_at = clock() - t0
            break
        sleep(interval)
finally:
    # 无论成功、超时还是 Ctrl+C，都要恢复
    runner(s.host, s.recover)
```

## 效果指标

| 指标           | 之前         | 之后                                                    |
| -------------- | ------------ | ------------------------------------------------------- |
| 告警是否有效   | 出事时才知道 | 每次演练都验证一遍                                      |
| 注入到告警触发 | —            | 待实测补充（实验 09 手工演练时 ServiceDown 设为 30 秒） |
| 复盘记录       | 手写         | 自动生成                                                |

- 脚本和单元测试已完成，CI 通过
- 还没在实验环境实际执行，跑完后补上「注入到告警触发」的实测耗时

## 常见坑

- **演练脚本出错导致服务没恢复**：异常时直接退出，跳过了恢复步骤。恢复命令放在 finally 里，任何情况都执行。
- **告警一直不触发**：规则里的 for 持续时间太长，或标签和演练对象对不上。演练前检查规则；实验 09 里把 ServiceDown 的 for 改成了 30 秒。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：根据演练记录生成复盘初稿；建议新的演练场景
- **不该交给 AI**：注入故障和恢复必须由脚本确定执行
- **怎么落地**：演练结束后 AI 写复盘初稿

## 面试怎么讲

30 秒讲法：

> 告警规则写好后可能因为标签、端口变化而静悄悄失效，所以我写了故障演练脚本：前置检查后停掉一个服务，轮询 Prometheus 和 Alertmanager，记录 pending、firing、通知各阶段的耗时，然后恢复并等告警解除，最后按复盘模板生成记录。恢复操作放在 finally 里，脚本出错或被中断也会执行。只用 Python 标准库，单元测试用假时钟和假 Prometheus。

常见追问：

**问：混沌工程和故障演练有什么区别？**

答：故障演练验证已知场景（告警、切换是否有效）；混沌工程更进一步，在生产环境随机注入故障，发现未知的薄弱点。先做演练再做混沌。

**问：演练会不会影响生产？**

答：先在测试环境；生产演练选低峰期、通知相关人、只选可快速恢复的场景，并设置超时自动恢复。

**问：告警从故障发生到通知要多久？由什么决定？**

答：采集间隔 + 规则评估间隔 + for 持续时间 + Alertmanager 的 group_wait。本站验收时站点缩容到 0，106 秒收到告警。

**被问到「能不能用 AI」时：** 故障注入必须确定可控，AI 帮写复盘和建议下一个演练场景。
