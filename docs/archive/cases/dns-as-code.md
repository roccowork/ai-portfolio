---
title: 'DNS 即代码：域名解析记录放 Git，改完自动生效'
summary: '所有域名解析记录写在 Git 的配置文件里，提 PR 修改，合并后自动同步到 DNS 服务商，改错了一键回滚。'
category: infra
status: design
pains: [risk, compliance]
value: '解析记录改错几分钟回滚，每次变更有审核'
ai: none
stack: ['OctoDNS / DNSControl', 'GitHub Actions', 'Cloudflare / 阿里云 DNS']
order: 90
flow:
  - label: '修改 zones/*.yaml'
  - label: 'PR 里预览变更'
    detail: '新增、修改、删除'
  - label: '审核合并'
  - label: '自动同步到 DNS 服务商'
  - label: '改错 git revert'
related: [config-drift, cert-renewal]
tags: ['DNS', '域名', '基础设施即代码', 'OctoDNS']
---

## 问题

域名解析在网页控制台里手动改：不知道谁改的、改了什么；误删一条记录可能导致网站或邮件中断，也没有备份。

## 适用场景

- 适合：域名多、解析记录多、多人修改的公司
- 不值得：一两个域名、几条记录、很少变动
- 前提：DNS 服务商有 API

## 工具选型

| 方案                   | 特点                            | 适用              |
| ---------------------- | ------------------------------- | ----------------- |
| OctoDNS                | YAML 定义，支持多家服务商       | 国内外都有        |
| DNSControl             | 用 JS 定义，Stack Overflow 出品 | 外企常用          |
| Terraform DNS provider | 和其他基础设施一起管            | 已用 Terraform 时 |
| 控制台手工改           | 最简单                          | 记录少时          |

选 OctoDNS：YAML 好读，能同时同步到多家服务商，自带 plan 预览。

## 怎么做

- 用 OctoDNS（或 DNSControl）把每个域名的记录写成 YAML，放进 Git
- PR 时 CI 运行预览，评论出本次会新增、修改、删除哪些记录；删除记录需要额外确认
- 合并后自动同步到 Cloudflare、阿里云 DNS 等服务商，多家服务商可以同时维护
- 每天比对一次线上记录和 Git，发现有人在控制台手动改了就告警（漂移检测）

## 实施步骤

| 步骤 | 做什么                        | 怎么验证                    |
| ---- | ----------------------------- | --------------------------- |
| 1    | 从服务商导出现有记录生成 YAML | `octodns-sync` 显示没有差异 |
| 2    | PR 时运行 plan 并评论到 PR    | PR 里能看到新增、修改、删除 |
| 3    | 合并后 CI 执行同步            | 线上解析变化                |
| 4    | 每天比对线上和 Git            | 控制台手改后收到漂移告警    |
| 5    | 限制控制台写权限              | 日常修改只走 Git            |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
# zones/example.com.yaml
'':
  - type: A
    value: 203.0.113.10
  - type: MX
    values: [{ exchange: mx1.example.com., preference: 10 }]
www:
  type: CNAME
  value: example.com.
```

## 效果指标

| 指标         | 做之前 | 预期效果（估算） |
| ------------ | ------ | ---------------- |
| 谁改了解析   | 查不到 | Git 历史         |
| 改错恢复     | 靠记忆 | git revert       |
| 解析记录备份 | 没有   | Git 就是备份     |

每次解析变更都有审核和记录，改错了几分钟回滚。本站的解析目前在 Cloudflare 手动维护，记录很少，只做方案。

## 常见坑

- **同步时删掉了没纳入管理的记录**：工具默认把 Git 当唯一来源。先完整导入现有记录；删除操作需要确认。
- **TTL 太长导致修改生效慢**：缓存。重要变更前先把 TTL 调低。
- **API 令牌权限过大**：能修改账号下所有域名。令牌只授权需要管理的域名。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：审查解析变更时提示风险（如删掉了 MX 记录）
- **不该交给 AI**：变更执行必须确定
- **怎么落地**：保持规则；PR 里可选 AI 风险提示

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会用 OctoDNS 把每个域名的解析写成 YAML 放进 Git，PR 时 CI 运行 plan 把变更评论到 PR，合并后自动同步到 Cloudflare 或阿里云 DNS。每天比对一次线上和 Git，有人在控制台手改就告警。上线前要先完整导入现有记录，否则同步会删掉没纳入管理的记录。

常见追问：

**问：DNS 修改为什么不是立即生效？**

答：各级缓存按 TTL 缓存记录，要等 TTL 过期；重要变更前先把 TTL 调低。

**问：A、CNAME、MX、TXT 记录的区别？**

答：A 指向 IP，CNAME 指向另一个域名，MX 指定邮件服务器，TXT 存放文本（域名验证、SPF）。

**问：根域名能不能用 CNAME？**

答：标准不允许，Cloudflare 等用 CNAME 拉平（flattening）实现类似效果。

**被问到「能不能用 AI」时：** DNS 变更用 Git 和 plan 管理就够了。
