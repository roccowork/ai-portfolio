---
title: GitOps 发布与回滚
summary: 推送代码即自动测试、构建、上线；坏版本不接流量，回滚就是 git revert。
category: release
status: production
pains: [speed, risk]
value: '推送即上线，坏版本不接流量，回滚约 1 分钟（实测）'
ai: boost
stack: [GitHub Actions, GHCR, Argo CD, k3s, Kustomize]
order: 1
flow:
  - label: git push
    detail: 推到 main
  - label: GitHub Actions
    detail: 格式、lint、类型检查、单测、构建
  - label: 构建镜像
    detail: 标签 = commit SHA，推到 GHCR
  - label: 改 Git 里的镜像标签
    detail: CI 只写 Git，不碰集群
  - label: Argo CD
    detail: 每 60 秒对比 Git，自动同步
  - label: 滚动更新
    detail: 就绪探针通过才接流量
  - label: 冒烟检查
    detail: 线上 /version.txt 等于新 SHA
related: [renovate-automerge, alert-wecom-weekly]
tags: [CI/CD, 持续部署, 滚动更新, 回滚, 发布]
---

## 问题

手工发布要登录服务器、拉代码、重启服务，步骤多、容易漏，出问题时也说不清线上跑的是哪个版本。CI 直接拿集群权限发布（push 模式）还要对公网开放 6443 端口。

## 适用场景

- 适合：有 2 个以上环境、每周发布多次、需要知道「线上到底是哪个版本」的团队；K8s 上几乎是标配
- 不值得：一台服务器、一年发布几次的内部小工具，写个部署脚本就够了
- 前提：应用已容器化，有基本的健康检查接口

## 工具选型

| 方案              | 特点                    | 适用                         |
| ----------------- | ----------------------- | ---------------------------- |
| Argo CD           | 拉取式 GitOps，界面直观 | 国内外都常用 ✅ 本站选用     |
| Flux              | 更轻量，纯命令行        | 外企、云原生团队             |
| Jenkins + kubectl | 推送式，CI 直接改集群   | 国内存量多；要给 CI 集群权限 |
| GitLab CI + Helm  | 代码和流水线在一个平台  | 国内私有化部署常见           |

选 Argo CD：CI 不需要集群凭据（不用对公网开 6443），有人手动改集群会被自动纠正，回滚就是改 Git。

## 怎么做

- **CI 只负责「产出版本」**：检查通过后构建镜像，标签用 commit SHA，然后把新标签写回 Git 里的 `kustomization.yaml`。CI 不需要集群凭据。
- **Argo CD 负责「让线上等于 Git」**：拉取式 GitOps，`prune` + `selfHeal`，有人手动改了集群会被改回来。
- **坏版本进不来**：`maxUnavailable: 0` + 就绪探针，新 Pod 探针不通过就一直不接流量，旧版本照常服务。
- **回滚就是改 Git**：`git revert` 掉 CI 那条 deploy 提交，不重新构建，约 1 分钟回到上一版。

## 实施步骤

| 步骤 | 做什么                                                      | 怎么验证                                 |
| ---- | ----------------------------------------------------------- | ---------------------------------------- |
| 1    | 写 Dockerfile、健康检查接口 `/healthz`                      | 本地 `docker run` 后 curl 返回 200       |
| 2    | CI：检查 → 构建镜像（标签 = commit SHA）→ 推到 GHCR         | GHCR 里出现对应 SHA 的镜像               |
| 3    | Kustomize 写 base + prod overlay，CI 把新标签写回 overlay   | main 上出现 `deploy: ... [skip ci]` 提交 |
| 4    | 安装 Argo CD，Application 指向 overlay，开 prune + selfHeal | Argo CD 界面显示 Synced / Healthy        |
| 5    | 滚动更新设 `maxUnavailable: 0` + 就绪探针 + preStop         | 故意发一个坏版本，旧 Pod 照常服务        |
| 6    | CI 最后访问 `/version.txt` 做冒烟检查                       | 返回值等于新 SHA，流水线才算成功         |

## 关键代码

CI 把新镜像标签写回 Git，提交信息带 `[skip ci]` 防止循环触发；有人同时推送时拉取后重试：

```bash
for i in 1 2 3; do
  git pull --rebase --quiet origin main
  sed -i "s/^    newTag: .*/    newTag: ${GITHUB_SHA}/" deploy/k8s/overlays/prod/kustomization.yaml
  if git diff --quiet; then echo "镜像标签已经是 ${GITHUB_SHA}"; exit 0; fi
  git commit -qam "deploy: ops-desk ${GITHUB_SHA::7} [skip ci]"
  if git push --quiet origin HEAD:main; then exit 0; fi
  git reset --quiet --hard origin/main
done
```

滚动更新时宁可多一个 Pod，也不先停旧的：

```yaml
strategy:
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
# ...
readinessProbe:
  httpGet: { path: /healthz, port: http }
  periodSeconds: 5
  failureThreshold: 2
```

回滚（约 1 分钟）：

```bash
git revert --no-edit <deploy 提交>
git commit --amend -m "Rollback ops-desk to <上一个 sha> [skip ci]"
git push
```

## 效果指标

| 指标             | 之前                                       | 之后                                       |
| ---------------- | ------------------------------------------ | ------------------------------------------ |
| 一次发布耗时     | 约 15 分钟（手工登录、拉代码、重启，估算） | 推送后约 3～4 分钟自动上线                 |
| 回滚耗时         | 重新部署旧版本，约 10 分钟（估算）         | 快速回滚约 1 分钟，修复式约 3 分钟（实测） |
| 线上版本是否可查 | 靠人记                                     | `/version.txt` 和 Git 一致                 |
| 手动改集群       | 改了没人知道                               | selfHeal 同一秒改回（实测）                |

- 本站每次推送都走这条流水线，CI 最后访问线上 `/version.txt`，等于新 SHA 才算发布成功
- 快速回滚约 1 分钟；修复式回滚（撤销代码提交、重新构建）约 3 分钟
- 手动改集群（比如改副本数）会被 Argo CD 在同一秒改回，线上始终和 Git 一致
- 实验环境里用 Jenkins + 自建镜像仓库做过同样的流程，演示过坏版本卡在滚动更新、旧版本照常服务（ops-lab 实验 15）

## 常见坑

- **回滚被 CI 覆盖**：revert 的提交没带 `[skip ci]`，CI 用最新代码重新构建，又把标签改回了新版本。回滚提交必须带 `[skip ci]`。
- **滚动更新结束时偶发 504**：Pod 删除时「从转发名单摘除」和「nginx 退出」同时发生，Traefik 还在往已退出的 Pod 转发。加了 `preStop` 等 5 秒解决。
- **`kubectl rollout undo` 没用**：selfHeal 会马上改回 Git 里的版本，回滚只能改 Git。

## AI 视角

**✨ AI 能增强**

- **AI 能做什么**：发布失败时自动读 CI 日志和 Argo CD 事件，给出可能原因；根据提交内容生成发布说明
- **不该交给 AI**：是否发布、是否回滚的决定和集群变更本身，必须由 Git 和流水线确定性地执行
- **怎么落地**：在 CI 失败步骤后调用大模型分析日志，结果评论到 PR；不给 AI 任何集群写权限

## 面试怎么讲

30 秒讲法：

> 本站用 GitHub Actions + Argo CD 做拉取式 GitOps：CI 只负责构建镜像并把 commit SHA 写回 Git，Argo CD 让集群和 Git 保持一致。滚动更新设了 maxUnavailable 0 和就绪探针，坏版本永远接不到流量；回滚就是 git revert，实测约 1 分钟。踩过的坑是回滚提交没带 [skip ci] 被 CI 覆盖，以及滚动更新结束时偶发 504，用 preStop 等 5 秒解决。

常见追问：

**问：为什么用拉取式，不让 CI 直接 kubectl apply？**

答：CI 不需要集群凭据，6443 端口不用对公网开放；集群状态以 Git 为准，手动改动会被纠正，审计也只看 Git 历史。

**问：新版本有 bug 但探针能通过怎么办？**

答：探针只能拦住「起不来」的版本。业务 bug 要靠冒烟检查、告警和快速回滚；更进一步是金丝雀发布，先放 10% 流量看指标。

**问：为什么 kubectl rollout undo 不能用来回滚？**

答：selfHeal 会马上把集群改回 Git 里的版本。GitOps 下回滚只能改 Git。

**被问到「能不能用 AI」时：** 发布流程本身保持确定性，AI 只用来加快排查：CI 失败时自动读日志给出原因。
