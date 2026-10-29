---
title: '告警自动处置：固定问题脚本处理，复杂问题 AI Agent 先排查'
summary: '步骤固定的告警由脚本自动处理（清日志、重启卡死的服务）；原因不明的告警交给 AI Agent 用只读工具排查，给出结论和建议动作，高危操作由人审批后执行。'
category: monitoring
status: design
pains: [effort, speed]
value: '固定类告警自动处理，复杂告警 AI 先排查，半夜少被叫醒'
ai: core
stack: ['Alertmanager', 'Webhook', 'Ansible', 'AI Agent', '大模型 API']
order: 40
flow:
  - label: '告警'
  - label: 'Alertmanager'
    detail: '按类型路由'
  - label: '固定问题 → 白名单动作'
    detail: 'Ansible 清理 / 重启'
  - label: '未知问题 → AI Agent'
    detail: '只读工具排查'
  - label: '人审批建议动作'
  - label: '企业微信'
    detail: '结论和处理结果'
related: [alert-root-cause, alert-noise-reduction, alert-wecom-weekly]
tags: ['自愈', '自动处置', '告警', 'Ansible', 'AI Agent', 'AIOps']
---

## 问题

半夜被「磁盘 85%」吵醒，登录服务器做的永远是同样几步：清旧日志、清 Docker 镜像。这类工作适合交给脚本。

另一类告警原因每次都不一样：接口变慢、Pod 反复重启。值班人员起床后要花十几分钟查日志、看指标、翻最近的发布，才知道该做什么。这部分排查工作可以交给 AI Agent 先做。

## 适用场景

- 适合：告警多、值班压力大、已有监控和日志平台的团队
- 不值得：问题少、环境小的团队；没有整理过处置手册的（AI 和脚本都没有依据）
- 前提：已经整理出「告警 → 标准处理步骤」的手册（runbook）；有可用的大模型（云端或私有部署）

## 工具选型

| 方案                                     | 特点                     | 适用           |
| ---------------------------------------- | ------------------------ | -------------- |
| Alertmanager webhook + 自写服务          | 灵活、可控               | ✅ 方案采用    |
| AI Agent（大模型 + 工具调用 / MCP）      | 读日志、查指标、给出结论 | ✅ 方案采用    |
| StackStorm                               | 事件驱动自动化平台       | 开源，较重     |
| Rundeck / AWX                            | 作业平台 + 触发          | 已有作业平台时 |
| 商业 AIOps（PagerDuty、Datadog Bits AI） | 托管，和自家监控深度集成 | 外企常用       |

两层设计：固定问题用自写小服务 + Ansible，逻辑简单易审计；未知问题用 AI Agent 排查，它只拿到只读工具，改动类操作必须经人审批。

## 怎么做

**第一层：固定问题，白名单动作**

- 只自动处理「步骤固定、风险低」的告警，动作写成白名单：清理 7 天前的日志、`docker image prune`、重启指定的 systemd 服务
- Alertmanager 把这几类告警额外发给处置服务（webhook），处置服务调用对应的 Ansible playbook
- 同一台机器 1 小时内最多自动处置 1 次，避免反复重启掩盖真正的问题

**第二层：未知问题，AI Agent 排查**

- 其余告警交给 AI Agent。它有一组**只读工具**：查 Prometheus 指标、查 Loki 日志、`kubectl get / describe / logs`、查最近的发布记录，以及 runbook 知识库
- Agent 按 runbook 的思路自己决定查什么，最多调用 15 次工具，最后输出：可能原因、证据（具体的日志行和指标）、建议动作
- 建议动作如果在白名单里，群里出现「批准执行」按钮，值班人员点一下才执行；不在白名单的只给出命令供人参考
- 所有工具调用和结论都留痕，处理前后的状态发到企业微信，人随时可以接手

## 实施步骤

| 步骤 | 做什么                                                  | 怎么验证                   |
| ---- | ------------------------------------------------------- | -------------------------- |
| 1    | 从值班记录挑出高频、步骤固定的告警，写 Ansible playbook | 测试环境手动执行能解决问题 |
| 2    | 写处置服务：白名单动作、频率限制、全局并发上限          | 同一台 1 小时内只处置 1 次 |
| 3    | Alertmanager 路由（continue: true，照常通知人）         | 告警同时到群和处置服务     |
| 4    | 为 Agent 定义只读工具，接入 runbook 知识库              | Agent 账号没有任何写权限   |
| 5    | 用历史故障回放，评估 Agent 结论的准确率                 | 结论和当时的复盘一致的比例 |
| 6    | 上线「批准执行」按钮，先只开放 2～3 个动作              | 未批准的动作不会执行       |

## 关键代码

示例代码（简化），没有在生产环境运行。Alertmanager 路由：

```yaml
route:
  routes:
    - matchers: [alertname="DiskSpaceHigh"]
      receiver: remediation
      continue: true # 照常通知人
    - matchers: [severity=~"warning|critical"]
      receiver: ai-agent
      continue: true
receivers:
  - name: remediation
    webhook_configs: [{ url: http://remediator:8080/disk-cleanup }]
  - name: ai-agent
    webhook_configs: [{ url: http://ops-agent:8080/investigate }]
```

Agent 只拿到只读工具，改动类动作只能「申请」：

```python
TOOLS = [query_prometheus, query_loki, kubectl_readonly, recent_deploys, search_runbook]
APPROVABLE = {"restart_deployment", "disk_cleanup", "rollback_last_deploy"}

result = agent.run(alert, tools=TOOLS, max_steps=15)
wecom.send(card(result.summary, result.evidence))
if result.action in APPROVABLE:
    wecom.send(approve_button(result.action, alert))  # 人点了才执行对应的 playbook
```

## 效果指标

| 指标             | 做之前                       | 预期效果（估算）                    |
| ---------------- | ---------------------------- | ----------------------------------- |
| 半夜被叫醒次数   | 每次磁盘告警都要人处理       | 固定类问题自动处理                  |
| 固定问题处理耗时 | 人起床登录约 15 分钟（估算） | 约 1 分钟                           |
| 未知问题初步定位 | 人工查日志、指标十几分钟     | 告警后几分钟群里就有排查结论        |
| 问题是否被掩盖   | —                            | 频率限制 + 全程通知，反复出现会升级 |

值班人员只处理真正需要判断的问题，而且拿到的是带证据的排查结论，不是一条光秃秃的告警。

## 常见坑

- **自动重启掩盖了内存泄漏**：每次都自动重启，没人修根因。频率限制，超过次数转人工并建工单。
- **处置脚本本身出错扩大故障**：脚本没在测试环境验证。只上线验证过的动作，执行前做前置检查。
- **Agent 一本正经地给出错误结论**：大模型会「编」原因。要求每个结论都附具体证据（日志行、指标值），没有证据的结论不展示。
- **Agent 权限过大**：给了能执行任意命令的工具。只给只读工具，执行走审批过的 playbook。
- **告警风暴时大量并发处置和调用模型**：很多机器同时告警。全局并发上限，先做告警降噪再交给 Agent。

## AI 视角

**🤖 AI 是核心**

- **AI 能做什么**：像值班工程师一样排查——自己决定查哪些日志和指标，把线索串起来，给出原因、证据和建议动作
- **不该交给 AI**：直接在生产执行它生成的命令；高危操作（删数据、主从切换）的决定
- **怎么落地**：固定问题仍用白名单脚本；AI Agent 只拿只读工具，改动类动作由人点「批准」后执行审核过的 playbook

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会把告警处置分两层。固定问题比如磁盘超过 85%，由处置服务调用 Ansible 清理，只执行白名单动作，同一台机器 1 小时最多一次。原因不明的告警交给 AI Agent，它只有只读工具：查指标、查日志、kubectl describe、看最近发布，最后给出原因、证据和建议动作；建议在白名单里的，值班人员在群里点批准才执行。这样半夜被叫醒时，看到的已经是带证据的结论。

常见追问：

**问：哪些告警适合自动处置？**

答：原因明确、步骤固定、操作可逆、风险低的：清日志、清缓存、重启无状态服务。数据库主从切换这类不适合全自动。

**问：AI Agent 和普通脚本有什么区别？**

答：脚本只能执行写好的固定步骤；Agent 能根据看到的情况决定下一步查什么，适合原因不固定的问题。所以固定问题用脚本，未知问题用 Agent。

**问：怎么防止 AI 把事情搞得更糟？**

答：只给只读工具；改动只能从白名单里申请、由人批准；限制调用次数；所有调用留痕；结论必须附证据；可以一键关闭。

**问：怎么评估 Agent 好不好用？**

答：用历史故障回放，看它的结论和当时复盘的根因是否一致，以及节省了多少定位时间。

**被问到「能不能用 AI」时：** 能，排查是 AI 最适合的部分；但它只读不写，执行仍然是人批准的 playbook。
