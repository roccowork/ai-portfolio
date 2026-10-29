---
title: 证书自动续期 + 到期告警
summary: HTTPS 证书自动申请、自动续期；续期失败提前 3 周告警，不会等到网站打不开才发现。
category: monitoring
status: production
pains: [risk, effort]
value: '证书全自动续期，续期失败提前 3 周告警'
ai: none
stack: [cert-manager, Let's Encrypt, Traefik, Prometheus]
order: 2
flow:
  - label: Ingress
    detail: 注解指定签发者
  - label: cert-manager
    detail: HTTP-01 验证
  - label: Let's Encrypt
    detail: 签发 90 天证书
  - label: Secret
    detail: Traefik 自动加载
  - label: Prometheus
    detail: 剩余 21 天告警
related: [alert-wecom-weekly]
tags: [证书, HTTPS, TLS, 续期]
---

## 问题

证书过期是最常见、也最容易避免的事故。手工续期靠记性；自动续期失败了没人知道，等用户报告「网站不安全」才发现。

## 适用场景

- 适合：有多个对外 HTTPS 域名的任何团队；K8s 上用 cert-manager 几乎零成本
- 不值得：只有一两个域名、托管在 CDN 上的，用 CDN 自带的证书即可
- 前提：域名解析指向集群，80 端口对外开放（HTTP-01 验证）；内网服务用 DNS-01

## 工具选型

| 方案              | 特点                           | 适用                              |
| ----------------- | ------------------------------ | --------------------------------- |
| cert-manager      | K8s 原生，Ingress 加注解即可   | 国内外都常用 ✅ 本站选用          |
| acme.sh / certbot | 普通服务器上的脚本             | 国内服务器常用                    |
| 云厂商证书服务    | 控制台申请，自动部署到负载均衡 | 付费证书、企业 OV/EV              |
| Traefik 自带 ACME | 配置简单                       | 证书存在 Traefik 里，多副本时麻烦 |

选 cert-manager：证书是标准的 K8s Secret，任何 Ingress 都能用；有 Prometheus 指标，能做到期告警。

## 怎么做

- cert-manager 用 HTTP-01 验证向 Let's Encrypt 申请证书，Ingress 上加一行注解就自动签发
- 两个签发者：先用 staging 调通流程（不受正式环境的频率限制），确认后切到 prod
- 每个新应用（云盘、统计、Grafana）只要写 Ingress，证书全自动
- **续期失败兜底**：cert-manager 在到期前 30 天续期，告警阈值设在剩余 21 天，说明续期已经失败了一个多星期，需要人工处理

## 实施步骤

| 步骤 | 做什么                                | 怎么验证                                                 |
| ---- | ------------------------------------- | -------------------------------------------------------- |
| 1    | 安装 cert-manager（Argo CD 管理）     | `kubectl get pods -n cert-manager` 全部 Running          |
| 2    | 建 staging 和 prod 两个 ClusterIssuer | `kubectl get clusterissuer` 显示 Ready                   |
| 3    | 先用 staging 签一张测试证书           | Certificate 变成 Ready（staging 证书浏览器不信任，正常） |
| 4    | 切到 prod，Ingress 加注解             | 浏览器访问显示有效证书                                   |
| 5    | 加到期告警，阈值剩余 21 天            | 周报里出现每张证书的剩余天数                             |

## 关键代码

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod-account
    solvers:
      - http01:
          ingress:
            ingressClassName: traefik
```

```yaml
- alert: CertificateExpiringSoon
  expr: (certmanager_certificate_expiration_timestamp_seconds - time()) / 86400 < 21
```

## 效果指标

| 指标             | 之前                         | 之后                   |
| ---------------- | ---------------------------- | ---------------------- |
| 证书续期         | 每 90 天手工操作一次（估算） | 全自动，至今零手工操作 |
| 续期失败发现时间 | 网站打不开时                 | 剩余 21 天时告警       |
| 新应用接入 HTTPS | 手工申请、配置               | Ingress 加一行注解     |

- 本站、云盘、统计、Grafana 的证书都由它自动签发和续期，没有手工操作过
- 证书剩余天数每周一出现在运维周报里

## 常见坑

- **告警阈值原来是 14 天**：cert-manager 提前 30 天续期，14 天才告警时已经失败了半个月，改成 21 天。
- **全新集群上签发者同步失败**：cert-manager 的 CRD 还没装好，Argo CD 预检报错。加 `SkipDryRunOnMissingResource=true`，等 CRD 出现后自动重试。

## AI 视角

**⚙️ 规则就够**

- **AI 能做什么**：基本不需要；最多在续期失败时解释 cert-manager 的报错
- **不该交给 AI**：证书签发和续期是确定性流程，交给 AI 只会增加不确定性
- **怎么落地**：保持规则化；排障时可以把 Certificate 和 Order 的事件交给 AI 解读

## 面试怎么讲

30 秒讲法：

> 本站所有 HTTPS 证书用 cert-manager 向 Let's Encrypt 自动申请和续期，新应用写 Ingress 加一行注解就有证书。先用 staging 签发者调通流程，避免触发正式环境的频率限制。兜底是到期告警：cert-manager 提前 30 天续期，我把告警设在剩余 21 天，触发就说明自动续期已经失败了一个多星期，需要人处理。

常见追问：

**问：HTTP-01 和 DNS-01 有什么区别？**

答：HTTP-01 通过访问 80 端口的特定路径验证，简单，但要求能从公网访问；DNS-01 通过添加 TXT 记录验证，内网服务和通配符证书只能用它，需要 DNS 服务商的 API 权限。

**问：为什么告警阈值是 21 天而不是 7 天？**

答：cert-manager 在剩余 30 天时开始续期。21 天告警时已经失败了 9 天，还留有充足时间处理；7 天太紧，遇到周末可能来不及。

**问：Let's Encrypt 有频率限制，怎么避免？**

答：调试阶段用 staging 环境；同一域名每周正式证书有数量上限，不要反复删 Secret 重签。

**被问到「能不能用 AI」时：** 证书续期是典型的「规则就够」，加 AI 没有收益，反而增加风险。
