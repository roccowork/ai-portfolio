---
title: '密钥自动轮换：数据库密码、API Key 定期更换不停服'
summary: '密钥存在统一的密钥管理里，到期自动生成新密码、更新数据库和应用配置、滚动重启，全程无人值守。'
category: security
status: design
pains: [risk]
value: '密码定期自动更换且不停服'
ai: none
stack: ['External Secrets', 'Vault', 'Kubernetes']
order: 40
flow:
  - label: 'Vault 定时轮换'
    detail: '生成新密码'
  - label: '更新数据库账号'
  - label: 'External Secrets'
    detail: '同步到 K8s Secret'
  - label: 'Reloader'
    detail: '滚动重启应用'
  - label: '审计日志'
related: [cert-renewal]
tags: ['密钥', '轮换', 'Vault', '安全']
---

## 问题

数据库密码写在配置文件里、几年不换，离职员工也知道；手动换密码要停服、改一堆配置，没人愿意做。

## 适用场景

- 适合：K8s 上运行、有多个数据库和第三方 API 密钥的团队
- 不值得：密钥很少、几乎不变的小项目（手动管理即可）
- 前提：应用能从环境变量或文件读取密钥，支持重启或热加载

## 工具选型

| 方案                               | 特点                 | 适用            |
| ---------------------------------- | -------------------- | --------------- |
| HashiCorp Vault / OpenBao          | 动态密钥、自动轮换   | 国内外都常用    |
| External Secrets Operator          | 把外部密钥同步到 K8s | 国内外都常用    |
| 云密钥管理（KMS、Secrets Manager） | 托管，自动轮换       | 云上            |
| Sealed Secrets / SOPS              | 加密后存 Git         | GitOps 简单场景 |

Vault 管理和轮换、External Secrets 同步、Reloader 滚动重启，各司其职，应用不需要改代码。

## 怎么做

- 密钥统一放 Vault（或云厂商的密钥管理服务），应用不再在配置文件里写明文
- Vault 的数据库引擎按周期生成新密码并改数据库账号；External Secrets Operator 把新值同步到 K8s Secret
- Reloader 监听 Secret 变化，滚动重启用到它的 Deployment，旧 Pod 用旧密码处理完请求再退出
- 每次读取和轮换都有审计日志

## 实施步骤

| 步骤 | 做什么                                     | 怎么验证                      |
| ---- | ------------------------------------------ | ----------------------------- |
| 1    | 部署 Vault 和 External Secrets Operator    | ESO 能读取 Vault 里的测试密钥 |
| 2    | 迁移密钥到 Vault，删除配置文件里的明文     | Git 里没有明文密码            |
| 3    | 配置数据库动态密钥或定期轮换               | 新密码在数据库里生效          |
| 4    | ExternalSecret 定期同步，Reloader 滚动重启 | 密码变化后 Pod 自动更新       |
| 5    | 开启审计日志                               | 能查到每次读取和轮换          |

## 关键代码

示例代码（简化），没有在生产环境运行：

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: { name: app-db }
spec:
  refreshInterval: 1h
  secretStoreRef: { name: vault, kind: ClusterSecretStore }
  data:
    - secretKey: password
      remoteRef: { key: database/creds/app, property: password }
```

## 效果指标

| 指标         | 做之前       | 预期效果（估算） |
| ------------ | ------------ | ---------------- |
| 密码更换     | 几年不换     | 定期自动轮换     |
| 更换是否停服 | 要停服改配置 | 滚动重启，不停服 |
| 密钥泄露影响 | 长期有效     | 很快失效         |

密码定期更换且不用停服，泄露的旧密码很快失效。本站密钥由人手动创建 Secret，规模小时足够。

## 常见坑

- **轮换后旧 Pod 连接失败**：旧密码立即失效，旧 Pod 还没重启。新旧密码有重叠期（双账号轮换）。
- **Vault 本身不可用**：单点。高可用部署，ESO 有缓存，短时间不可用不影响运行中的 Pod。
- **密钥出现在日志或环境变量 dump 里**：应用打印了配置。代码审查，日志脱敏。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：基本不需要
- **不该交给 AI**：密钥处理必须确定，且不能把密钥交给模型
- **怎么落地**：保持规则

## 面试怎么讲

30 秒讲法（方案思路；没有实际落地，面试时如实说明是设计过的方案）：

> 我会把密钥统一放进 Vault，应用不再在配置文件里写明文。Vault 按周期生成新的数据库密码并更新账号，External Secrets Operator 把新值同步到 K8s Secret，Reloader 发现 Secret 变化后滚动重启用到它的应用，旧 Pod 处理完请求再退出，全程不停服。轮换时用双账号保证新旧密码有重叠期。本站规模小，密钥是手动创建 Secret 的。

常见追问：

**问：K8s Secret 是加密的吗？**

答：默认只是 base64 编码，存在 etcd 里；需要开启 etcd 静态加密，并用 RBAC 限制读取。

**问：什么是动态密钥？**

答：Vault 按需为每个应用生成临时数据库账号，到期自动删除，每个应用的凭据都不同。

**问：GitOps 里密钥怎么管理？**

答：不能把明文放 Git。用 Sealed Secrets/SOPS 加密后提交，或者 Git 里只放 ExternalSecret 引用，真实值在 Vault。

**被问到「能不能用 AI」时：** 密钥相关的流程绝不能经过 AI。
