# 运维手册（runbook）

站点：<https://ops.liuxd.dev> · 服务器：LocVPS 香港 `<服务器 IP>`（4 核 12G，Ubuntu 24.04，单节点 k3s；2026-10-28 从雨云迁来，见 [migrate-server.md](migrate-server.md)）

## 1. 日常部署（GitOps）

推送到 `main` 即自动部署，无需人工操作。CI（GitHub Actions，`.github/workflows/ci.yml`）**不访问集群**：

1. `check`：格式、ESLint、`astro check`、单元测试、构建。PR 和 main 都跑，任何一项失败后续不执行。
2. `release`（构建和提交标签合在一个 job，省一次按分钟取整的计费）：先看这次推送改了哪些文件，只改了 `deploy/`（`deploy/docker/` 除外）、`docs/` 或根目录 `*.md` 就跳过构建，Argo CD 直接同步清单；否则构建镜像，标签为 commit SHA，推送到 `ghcr.io/roccowork/ops-desk`（公开，镜像名沿用拆分前的仓库名），构建时把 SHA 写进站点的 `/version.txt`，再把 `deploy/k8s/overlays/prod/kustomization.yaml` 里的 `newTag` 改成新 SHA，以 `deploy: ops-desk <sha> [skip ci]` 提交回 `main`（`[skip ci]` 防止再次触发流水线）。CI 不等上线、不做冒烟检查（2026-10-25 仓库还是私有、Actions 按分钟计费时省掉的）：需要确认时看 `https://ops.liuxd.dev/version.txt` 是否变成新 SHA，可用性由 Uptime Kuma 和告警盯着。只改 `docs/**` 或根目录 `*.md` 不触发 CI；同一个 PR 推新提交会取消旧的运行。
3. 集群里的 Argo CD 每 60 秒检查一次 Git，发现标签变化后自动同步（`prune` + `selfHeal`）。

查看线上当前版本：

```bash
curl https://ops.liuxd.dev/version.txt       # 正在接收流量的版本
grep newTag deploy/k8s/overlays/prod/kustomization.yaml   # Git 里期望的版本
```

也可以在 <https://argocd.liuxd.dev> 查看同步和健康状态。

### 改哪些配置、怎么生效

站点以外的配置也一样，改完提交到 `main` 即可，不需要登录服务器。按生效方式分三类：

| 生效方式                                                                                      | 配置                                                                                                              | 改哪里                                       |
| --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **Argo CD 直接应用清单**（约 1 分钟）                                                         | 站点：镜像版本（CI 自动改）、副本数、资源限制、Ingress                                                            | `deploy/k8s/`                                |
|                                                                                               | 告警规则、Grafana 仪表盘、告警转发器、Uptime Kuma、Traefik 指标采集                                               | `deploy/monitoring/`                         |
|                                                                                               | 云盘和它的备份任务（备份脚本在 `deploy/backup/`，云盘和 Uptime Kuma 共用）                                        | `deploy/apps/cloudreve/`                     |
|                                                                                               | 证书签发者（ClusterIssuer）                                                                                       | `deploy/platform/cluster-issuers.yaml`       |
|                                                                                               | 增删 Argo CD 应用                                                                                                 | `deploy/argocd/applications.yaml`            |
| **Argo CD 同步 HelmChart，再由 k3s 的 helm-controller 执行 `helm upgrade`**（约 2 到 5 分钟） | kube-prometheus-stack：Prometheus、Alertmanager（路由、抑制规则）、Grafana 的配置，默认告警的开关，各组件资源限制 | `deploy/platform/kube-prometheus-stack.yaml` |
|                                                                                               | cert-manager：版本和配置                                                                                          | `deploy/platform/cert-manager.yaml`          |
|                                                                                               | Argo CD 自己：版本和配置                                                                                          | `deploy/platform/argocd.yaml`                |
| **不在 Git 里，要上服务器或控制台操作**                                                       | k3s 本身（版本固定在脚本里，升级时改版本号后在服务器上重新执行）                                                  | `deploy/server/install-k3s.sh`               |
|                                                                                               | 系统配置：自动更新、fail2ban、SSH 加固                                                                            | `deploy/server/init.sh`、`ssh-hardening.sh`  |
|                                                                                               | Secret：Grafana 密码、企业微信 key、R2 密钥和 restic 密码                                                         | 第 6 节、第 9 节                             |
|                                                                                               | Uptime Kuma 的监控项、Alertmanager 的静默、Grafana 界面上的手动修改（仪表盘要导出 JSON 放进仓库才会保留）         | 第 6 节                                      |
|                                                                                               | DNS（Cloudflare）、安全组（雨云控制台）、GitHub 部署密钥                                                          | 第 3 节、第 7 节                             |

确认是否生效：

```bash
k3s kubectl -n argocd get applications                 # 应用都是 Synced、Healthy，说明 Git 里的改动已经同步
k3s kubectl -n kube-system get jobs | grep helm-install # 第二类：能看到刚运行过的 helm-install-<组件> 任务
```

第二类改动出错时（比如 values 写错），`helm-install-*` 任务会失败并不断重试，旧版本继续运行；`k3s kubectl -n kube-system logs job/helm-install-<组件>` 查看原因，在 Git 里修正后重新提交。网站「集群架构」页的「改平台配置」场景演示了第二类的完整过程。

## 2. 回滚

线上状态以 Git 为准，**回滚就是改 Git**。不要用 `kubectl rollout undo`：Argo CD 开了 `selfHeal`，几秒内就会改回 Git 里的版本（里程碑 4 验收时实测：手动改副本数，同一秒被改回）。

**快速回滚**（约 1 分钟，不重新构建）：撤销 CI 自动提交的那条 deploy 记录，提交信息**必须带 `[skip ci]`**，否则 CI 会用最新代码重新构建并把标签改回新版本，回滚被覆盖。

```bash
git log --oneline -5                        # 找到 "deploy: ops-desk <sha> [skip ci]"
git revert --no-edit <deploy 提交>
git commit --amend -m "Rollback ops-desk to <上一个 sha> [skip ci]"
git push
curl https://ops.liuxd.dev/version.txt      # 确认回到上一个版本
```

**修复式回滚**（约 3 分钟）：撤销出问题的那次**代码**提交，正常推送（不带 `[skip ci]`），CI 用撤销后的代码构建新版本上线。适合问题出在代码、需要在历史里留下「这段代码被撤销」记录的情况。

**部署卡住时**：新版本探针不通过时，`maxUnavailable: 0` 保证旧 Pod 继续服务，CI 会在 10 分钟后报错退出。到 Argo CD 看原因，再按上面任一方式回滚。

## 3. 换一台新服务器从零恢复

服务器是月付小厂商机器，按"随时可能丢失"设计。以下步骤只依赖本仓库和 GitHub。

旧服务器还能开机时，按 [migrate-server.md](migrate-server.md) 直接从旧机器拷 Secret 和数据，更快。

**前提**：在雨云控制台新建 Ubuntu 24.04 实例，安全组只放行 TCP 22、80、443 和 ICMP（6443 不对外开放）；创建时注入电脑的 SSH 公钥。

| 步骤 | 操作                                                                                                                                                | 说明                                                       |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1    | Cloudflare 把 `*.liuxd.dev` 的 A 记录改成新 IP（仅 DNS）；`liuxd.dev`、`cv`、`roadmap` 在 Cloudflare Pages 上，不用改                               | 证书签发依赖 DNS 指向正确                                  |
| 2    | 本机：`ssh-keyscan -t ed25519 <新IP>`，与 VNC 控制台里 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 比对后写入 `known_hosts`                  | 防止首次连接被冒充                                         |
| 3    | 本机：`scp -r deploy root@<新IP>:/root/ops-desk-deploy`                                                                                             | 复制全部脚本和清单                                         |
| 4    | 服务器：`bash /root/ops-desk-deploy/server/init.sh`，有提示则 `reboot`                                                                              | 系统更新、fail2ban、自动安全更新                           |
| 5    | 把手机公钥追加到 `/root/.ssh/authorized_keys`，**电脑和手机各登录一次确认**                                                                         | 关闭密码登录前的必要检查                                   |
| 6    | 保持一个会话不断开，执行 `bash /root/ops-desk-deploy/server/ssh-hardening.sh`；新开会话验证电脑、手机都能登录、密码登录被拒绝                       | 关闭密码登录                                               |
| 7    | `bash /root/ops-desk-deploy/server/install-k3s.sh <新IP>`                                                                                           | 固定版本的 k3s，含镜像自动清理                             |
| 8    | 按第 6 节创建 `monitoring` 命名空间和两个 Secret；再按第 9 节在 `cloudreve`、`monitoring` 两个命名空间创建 Secret `backup-r2`（每天的备份任务要用） | 密码和 key 不在仓库里，要在 Argo CD 部署前准备好           |
| 9    | `bash /root/ops-desk-deploy/server/install-argocd.sh`，把最后打印的公钥添加到 GitHub 仓库的 Deploy keys（不勾写权限），并删掉旧服务器的那把         | 每台服务器生成自己的部署密钥                               |
| 10   | `k3s kubectl apply -f /root/ops-desk-deploy/argocd/applications.yaml`                                                                               | Argo CD 从 Git 装好其余全部组件，见下方说明，不需要重跑 CI |
| 11   | **立刻**打开 `https://status.liuxd.dev` 创建 Uptime Kuma 管理员并按第 6 节配置；按第 7 节修改 Argo CD 管理员密码                                    | 初始化页面谁先打开谁就能设管理员                           |
| 12   | 验证：`curl https://ops.liuxd.dev/version.txt` 与 Git 里的 `newTag` 一致；证书签发者为 Let's Encrypt；Grafana「ops-desk 总览」有数据                |                                                            |

第 10 步之后，Argo CD 按 `deploy/argocd/applications.yaml` 依次安装 cert-manager、kube-prometheus-stack、证书签发者（`platform` 应用），以及站点、监控附加组件和云盘。组件之间有依赖（比如告警规则要等 kube-prometheus-stack 装好 CRD），前几分钟部分应用同步失败是正常的，会自动重试；约 10 到 15 分钟后 `k3s kubectl -n argocd get applications` 全部显示 `Synced`、`Healthy`。

云盘和 Uptime Kuma 的数据要从 R2 恢复：应用起来后按第 9 节「恢复」操作。

重建演练（在全新系统上按本节操作并记录耗时）尚未进行，计划在第一版完成后补做。

## 4. 证书问题

```bash
kubectl get certificate,certificaterequest,order,challenge -n ops-desk
kubectl describe challenge -n ops-desk      # 验证失败的具体原因
kubectl logs -n cert-manager deploy/cert-manager --tail=50
```

| 现象                   | 常见原因                          | 处理                                                                                                                 |
| ---------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Challenge 一直 pending | DNS 没指向本机，或安全组没放行 80 | `dig +short ops.liuxd.dev`；从外网 `curl http://ops.liuxd.dev/.well-known/acme-challenge/test` 应返回 404 而不是超时 |
| 报 rateLimited         | 短时间内反复失败或重复签发        | 失败验证每小时最多 5 次，等一小时；调试时把 overlay 临时改用 `letsencrypt-staging`                                   |
| 浏览器提示证书不受信任 | 仍在用 staging 证书               | 确认 Ingress 注解是 `letsencrypt-prod`，`kubectl delete secret ops-desk-tls -n ops-desk`（需管理员）让其重签         |

`.dev` 域名被浏览器强制 HTTPS，证书签好之前网页打不开属于正常现象。

## 5. 常见故障

| 现象                                                                              | 原因                                                                                | 处理                                                                         |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Pod `ImagePullBackOff`，事件里 `401 Unauthorized`                                 | GHCR 包是私有的（首次推送时默认私有）                                               | GitHub → Packages → ops-desk → Package settings → Change visibility → Public |
| CI 提交镜像标签被拒绝                                                             | 推送期间有人推了新提交（会自动重试 3 次），或 `GITHUB_TOKEN` 没有 `contents: write` | 看 Actions 日志；确认 deploy 任务的 permissions                              |
| Argo CD 应用显示 `ComparisonError`，提示读取仓库失败                              | 部署密钥没加到 GitHub，或换服务器后用的还是旧密钥                                   | 按第 7 节查看公钥，加到仓库 Deploy keys                                      |
| 新装 k3s 后 `helm-install-traefik` 显示 Error                                     | 依赖的 CRD 任务还没完成，属于正常重试                                               | 等一两分钟，会自动变成 Completed                                             |
| `curl https://update.k3s.io` 证书报错                                             | k3s 版本查询服务自身证书问题（2026-10-11 遇到）                                     | 安装脚本已显式指定版本，直接从 GitHub 下载，不受影响                         |
| pnpm 报 `ERR_PNPM_MINIMUM_RELEASE_AGE_VIOLATION`                                  | pnpm 12 默认拒绝发布不满 24 小时的版本（防供应链攻击）                              | 换用上一个版本，不要关闭这个保护                                             |
| 容器日志 `can not modify /etc/nginx/conf.d/default.conf (read-only file system?)` | 只读根文件系统下镜像自带的 IPv6 配置脚本被跳过                                      | 预期行为，无需处理                                                           |
| 滚动更新刚结束时偶发 504 Gateway Timeout                                          | Pod 删除时"摘出转发名单"和"nginx 退出"同时发生，Traefik 还在往已退出的 Pod 转发     | 已加 `preStop` 等待 5 秒（2026-10-11 发现并修复）                            |

## 6. 监控告警

### 入口

| 地址                           | 用途                                                                          | 登录                                              |
| ------------------------------ | ----------------------------------------------------------------------------- | ------------------------------------------------- |
| <https://grafana.liuxd.dev>    | 仪表盘「ops-desk 总览」：站点请求量、响应时间、状态码，节点 CPU / 内存 / 磁盘 | `admin`，密码在 Secret `monitoring/grafana-admin` |
| <https://status.liuxd.dev>     | Uptime Kuma：从外部探测首页、`/healthz`、证书到期                             | 首次访问时创建的管理员                            |
| UptimeRobot（uptimerobot.com） | 外部兜底：服务器整体宕机时，Uptime Kuma 也发不出告警，由它发邮件              | 个人账号                                          |
| Prometheus、Alertmanager       | 不对公网开放                                                                  | 见下方「查看告警和静默」                          |

告警链路：Prometheus 规则触发 → Alertmanager 分组去重 → `wecom-relay`（转发器）转成 markdown → 企业微信群机器人。info 级别只在 Grafana 里看，不发通知。

### 创建 Secret（安装或重建时）

`read -s` 输入时不显示、不进命令历史：

```bash
k3s kubectl create namespace monitoring
read -rs -p 'Grafana 管理员密码: ' P; echo
k3s kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-user=admin --from-literal=admin-password="$P"; unset P
read -rs -p '企业微信机器人 key: ' K; echo
k3s kubectl -n monitoring create secret generic wecom-bot --from-literal=key="$K"; unset K
```

key 是群机器人 webhook 地址里 `key=` 后面的部分。**更换 key**：

```bash
read -rs -p '企业微信机器人 key: ' K; echo
k3s kubectl -n monitoring create secret generic wecom-bot --from-literal=key="$K" --dry-run=client -o yaml | k3s kubectl apply -f -; unset K
k3s kubectl -n monitoring rollout restart deploy/wecom-relay
```

### Uptime Kuma 配置（不支持配置文件，重建后手动做一遍）

1. 数据库选 SQLite，创建管理员（强密码）
2. 设置 → 通知 → 类型「WeCom（企业微信群机器人）」，填 key，打开「默认开启」「应用到所有现有监控项」，点「测试」确认群里收到
3. 添加监控项（重试次数都设 2，避免网络抖动误报）：
   - HTTP(s)「首页」`https://ops.liuxd.dev/`，间隔 60 秒，打开「证书到期通知」
   - HTTP(s) - 关键字「健康检查」`https://ops.liuxd.dev/healthz`，关键字 `ok`

UptimeRobot：New → HTTP 监控 `https://ops.liuxd.dev/healthz`，间隔 5 分钟（免费版最短），通知发到注册邮箱。

### 告警规则

自定义规则在 `deploy/monitoring/rules/ops-desk.yaml`，另外还有 kube-prometheus-stack 自带的规则（k3s 没有的 etcd、scheduler 等已关闭；和自定义规则重复的 4 条也已关闭，见 `deploy/platform/kube-prometheus-stack.yaml` 的 `defaultRules.disabled`）。

| 告警                            | 级别        | 条件                                                               |
| ------------------------------- | ----------- | ------------------------------------------------------------------ |
| OpsDeskNoReplicas               | 严重        | 站点可用副本为 0，持续 1 分钟                                      |
| OpsDeskHigh5xxRate              | 警告        | 站点 5xx 比例超过 5%，持续 5 分钟（每秒请求少于 0.05 个时不判断）  |
| OpsDeskSlowResponses            | 警告        | 站点 p95 响应时间超过 1 秒，持续 10 分钟（同样有最低请求量的条件） |
| DeploymentReplicasLow           | 警告        | 任意 Deployment 可用副本少于期望值，持续 10 分钟                   |
| PodRestartingOften              | 警告        | 30 分钟内重启超过 3 次                                             |
| NodeMemoryHigh                  | 警告        | 内存使用率超过 85%，持续 5 分钟                                    |
| NodeDiskHigh / NodeDiskCritical | 警告 / 严重 | 根分区超过 80% / 90%                                               |
| CertificateExpiringSoon         | 警告        | 证书 21 天内到期（cert-manager 正常会提前 30 天续期）              |
| BackupJobFailed / BackupStale   | 警告        | 备份任务失败 / 超过 26 小时没有成功的备份                          |

抑制规则：同名告警有严重级别时不再发警告；NodeDiskCritical 触发时不发 NodeDiskHigh；OpsDeskNoReplicas 触发时不发站点的 DeploymentReplicasLow。

### 查看告警和静默

计划维护时先静默，避免收到告警：

```bash
k3s kubectl -n monitoring port-forward svc/kps-alertmanager 9093   # 在服务器上执行
# 本机另开终端：ssh -L 9093:127.0.0.1:9093 root@<服务器IP>，然后浏览器打开 http://localhost:9093 → New Silence
```

查询当前触发的告警（服务器上）：

```bash
k3s kubectl get --raw "/api/v1/namespaces/monitoring/services/kps-prometheus:9090/proxy/api/v1/alerts"
```

### 运维周报

CronJob `weekly-report`（`deploy/monitoring/weekly-report.yaml`）每周一 09:00（新加坡时间）查询 Prometheus，把过去 7 天的访问量和 5xx、p95、告警（按持续时间排序）、备份最近成功时间、证书剩余天数、节点内存和磁盘、容器重启汇总成一条消息，发到告警所在的企业微信群。脚本是 `deploy/monitoring/wecom-relay/weekly-report.mjs`，复用告警转发器的发送函数和同一个 Secret `wecom-bot`，不用另外配置。

只看内容不发送（在本机仓库目录执行，需要 Node）：

```bash
# 服务器上：k3s kubectl -n monitoring port-forward svc/kps-prometheus 9090
# 本机另开终端：ssh -L 9090:127.0.0.1:9090 root@<服务器IP>，然后：
PROMETHEUS_URL=http://127.0.0.1:9090 DRY_RUN=1 node deploy/monitoring/wecom-relay/weekly-report.mjs
```

立即真正发一次（服务器上）：

```bash
k3s kubectl -n monitoring create job --from=cronjob/weekly-report weekly-report-manual-$(date +%s)
k3s kubectl -n monitoring logs job/<上一步的 Job 名>
```

某一项查询失败时，那一节显示「查询失败」，其他照常发送；所有查询都失败（Prometheus 不可用）或企业微信返回错误时 Job 失败，重试 1 次。告警规则 `KubeJobFailed`（默认规则）会通知。

### 平台实况（/live）

CronJob `ops-desk-status`（命名空间 ops-desk，`deploy/k8s/base/status/`）每 10 分钟查询 K8s API（站点的 ReplicaSet = 发布历史）和 Prometheus（7 天可用率、p95、请求数、备份、证书、告警），写进 ConfigMap `ops-desk-status`。站点 Pod 把它挂载到 `/usr/share/nginx/status`，nginx 以 `/status.json` 提供；ConfigMap 更新后 kubelet 约 1 分钟内同步到 Pod，不用重启。

- 权限：ServiceAccount `ops-desk-status` 只能 list ReplicaSet、读写这一个 ConfigMap
- 单项查询失败：那一项为 null，页面显示「查询失败」，失败原因在 JSON 的 `errors` 里
- K8s API 和 Prometheus 都不通：不覆盖上一份，Job 失败；页面显示「数据更新于 X 分钟前」，超过 1 小时标黄
- p95 受 Traefik 分桶影响（见下面常见问题），页面上的值理解为「小于约 100ms」

立即生成一次（服务器上）：

```bash
k3s kubectl -n ops-desk create job --from=cronjob/ops-desk-status status-manual-$(date +%s)
k3s kubectl -n ops-desk logs job/<上一步的 Job 名>
k3s kubectl -n ops-desk get configmap ops-desk-status -o jsonpath='{.data.status\.json}'
```

### 验收记录（2026-10-11）

把站点缩容到 0：第 106 秒 Alertmanager 中告警触发，企业微信收到 Prometheus 和 Uptime Kuma 共 3 条告警，UptimeRobot 发来 1 封邮件；恢复后都收到了恢复通知。全部组件运行时监控命名空间约占 1.1G 内存，节点剩余可用 4.9G。

### 监控相关的常见问题

| 现象                                           | 原因                                                                                                                                      | 处理                                                                                                               |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `wecom-relay` CrashLoopBackOff，日志为空       | ConfigMap 挂载的文件是符号链接（`/app/relay.mjs` → `..data/relay.mjs`），脚本「是否直接运行」的判断没解析真实路径，服务没启动就正常退出了 | 已修复：比较前用 `realpathSync` 解析两边路径                                                                       |
| 告警 `AlertmanagerFailedToSendAlerts`          | 转发器没起来，或企业微信 key 无效（errcode 93000）                                                                                        | `kubectl -n monitoring logs deploy/wecom-relay`；按上面步骤更换 key                                                |
| 收到 `CPUThrottlingHigh` 之类的 info 告警刷屏  | 默认路由会发所有级别                                                                                                                      | 已在 Alertmanager 配置里把 `severity="info"` 路由到 null                                                           |
| Traefik 指标里服务名在 `exported_service` 标签 | Traefik 自己的 `service` 标签和采集时加的 `service` 标签重名                                                                              | ServiceMonitor 设置 `honorLabels: true`                                                                            |
| 仪表盘 P95 响应时间约 95ms，但实际很快         | Traefik 默认直方图分桶很粗（0.1s、0.3s…），请求都落在 0～0.1s 桶里，只能在桶内估算                                                        | 理解为「P95 小于 100ms」；需要更精确时再调整 Traefik 的分桶                                                        |
| Prometheus 镜像里没有 `wget` / `curl`          | distroless 精简镜像                                                                                                                       | 通过 API Server 代理访问：`kubectl get --raw /api/v1/namespaces/monitoring/services/kps-prometheus:9090/proxy/...` |

## 7. Argo CD

- 界面：<https://argocd.liuxd.dev>，用户名 `admin`。
- 集群里的所有组件都由 Argo CD 从 Git 同步，服务器上不手动放任何清单。应用定义在 `deploy/argocd/applications.yaml`：

  | 应用                | 内容                                                                  | 路径                       |
  | ------------------- | --------------------------------------------------------------------- | -------------------------- |
  | `root`              | 应用定义本身（增删应用改这个文件即可）                                | `deploy/argocd`            |
  | `platform`          | Argo CD、cert-manager、kube-prometheus-stack 的 HelmChart，证书签发者 | `deploy/platform`          |
  | `ops-desk`          | 站点                                                                  | `deploy/k8s/overlays/prod` |
  | `monitoring-extras` | 告警转发器、Uptime Kuma、Traefik 指标、告警规则、仪表盘               | `deploy/monitoring`        |
  | `cloudreve`         | 云盘和它的备份任务                                                    | `deploy/apps/cloudreve`    |

- 都开了自动同步、`prune`、`selfHeal` 和失败重试。
- `platform` 里的 HelmChart 由 k3s 的 helm-controller 实际执行 `helm install / upgrade`：改 `deploy/platform/` 下的文件并提交到 main，约 1 分钟后 Argo CD 同步，helm-controller 随即升级（`k3s kubectl -n kube-system get jobs` 能看到 `helm-install-*` 任务）。
- HelmChart 带了 `Prune=false,Delete=false`：删除 HelmChart 等于卸载整个组件，所以即使从 Git 里删掉，Argo CD 也不会删集群里的。真要卸载时手动 `kubectl delete helmchart`。
- 不要再往服务器的 `/var/lib/rancher/k3s/server/manifests/` 放这几个文件：k3s 每次启动都会重新应用那个目录里的文件，会和 Git 里的版本互相覆盖。

**从「手动复制清单」切换到 Git 管理**（2026-10-13，只需做一次）：

```bash
# 1. 把 k3s 自动部署目录里的三个文件移走。按 k3s 文档，删除这个目录里的文件不会删除集群里已经部署的资源
mkdir -p /root/k3s-manifests-backup
cd /var/lib/rancher/k3s/server/manifests
mv argocd.yaml cert-manager.yaml kube-prometheus-stack.yaml /root/k3s-manifests-backup/
k3s kubectl -n kube-system get helmchart            # 确认 argocd、cert-manager、kube-prometheus-stack 都还在

# 2. 只创建 root 应用：它会从 Git 读取 deploy/argocd/applications.yaml，自动创建 platform 等其余应用
cat <<'EOF' | k3s kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/roccowork/ai-portfolio.git
    targetRevision: main
    path: deploy/argocd
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
EOF

# 3. 验证：全部 Synced / Healthy；kube-prometheus-stack 按新配置升级了一次
k3s kubectl -n argocd get applications
k3s kubectl -n kube-system get jobs | grep helm-install
```

不要删除 `kube-system` 里的 `AddOn` 和 `HelmChart` 资源：删除 HelmChart 会卸载对应的组件。

**首次登录**（安装或重建后）：

```bash
# 服务器上查看初始密码
k3s kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

登录后在 User Info → Update Password 改成强密码，然后删除初始密码：

```bash
k3s kubectl -n argocd delete secret argocd-initial-admin-secret
```

**仓库访问**：本仓库是公开的，Argo CD 直接用 HTTPS 拉取，不需要凭据。只有私人助理（`personal` 应用）在私有仓库 `roccowork/ops-desk`，用 SSH 部署密钥读取。私钥在服务器 `/root/.ssh/argocd-ops-desk` 和 Secret `argocd/repo-ops-desk`，公钥在 GitHub 仓库的 Deploy keys（只读）。查看公钥：`cat /root/.ssh/argocd-ops-desk.pub`。

## 8. 安全相关

- SSH 只允许密钥登录（电脑、手机两把），fail2ban 保护 22 端口。
- 防火墙由雨云安全组负责，服务器内**不启用 ufw**（会和 k3s 的网络规则冲突）。安全组只能在雨云控制台手动修改。
- 安全组只开放 22、80、443。K8s API（6443）不对公网开放：里程碑 0～3 时 CI 推送部署需要它，里程碑 4 改成 Argo CD 拉取后关闭。CI 不再持有任何集群凭据。
- Argo CD 读取仓库用的是只读、只限这一个仓库的部署密钥；CI 往仓库提交镜像标签用的是 GitHub 自动提供的 `GITHUB_TOKEN`，只在流水线运行期间有效。
- Argo CD 界面开放在公网：管理员用强密码，初始密码登录后删除，及时升级版本；界面上禁止进入容器执行命令（`exec.enabled: false`）。

## 9. 云盘与异地备份

> 2026-10-28 网站改版为 AI 落地服务站时下线（Argo CD 应用和清单已删除），本节只留作记录。

### 入口和组成

- 云盘：`https://pan.liuxd.dev`（Cloudreve，命名空间 `cloudreve`，Argo CD 应用 `cloudreve`），清单在 `deploy/apps/cloudreve/`
- 数据：SQLite 数据库、配置和上传的文件都在 PVC `cloudreve-data`（local-path，系统盘上）
- 备份：每天 03:30 备份云盘、04:00 备份 Uptime Kuma、04:30 备份访问统计，用 restic 加密后存到 Cloudflare R2。脚本在 `deploy/backup/`，几个应用共用
- 告警：备份任务失败（`BackupJobFailed`），或超过 26 小时没有成功（`BackupStale`）

### 准备 R2 和创建 Secret（安装或重建时）

1. Cloudflare 控制台 → R2 → 创建存储桶（如 `ops-backup`），记下账户 ID
2. R2 → 管理 API 令牌 → 创建令牌：权限「对象读和写」，只限这个存储桶。记下 Access Key ID 和 Secret Access Key（只显示一次）
3. 生成 restic 密码（如 `openssl rand -base64 32`），**存进密码管理器**。这个密码丢了，备份就无法解密，也无法找回
4. 在服务器上创建 Secret（每个命名空间各一个，仓库路径不同）。`read -s` 输入时不显示、不进命令历史：

```bash
k3s kubectl create namespace cloudreve
k3s kubectl create namespace goatcounter
read -r -p 'R2 账户 ID: ' ACCOUNT; read -r -p '存储桶名: ' BUCKET
read -rs -p 'Access Key ID: ' A; echo
read -rs -p 'Secret Access Key: ' S; echo
read -rs -p 'restic 密码: ' R; echo
for pair in cloudreve:cloudreve monitoring:uptime-kuma goatcounter:goatcounter; do
  k3s kubectl -n "${pair%%:*}" create secret generic backup-r2 \
    --from-literal=RESTIC_REPOSITORY="s3:https://$ACCOUNT.r2.cloudflarestorage.com/$BUCKET/${pair#*:}" \
    --from-literal=RESTIC_PASSWORD="$R" \
    --from-literal=AWS_ACCESS_KEY_ID="$A" \
    --from-literal=AWS_SECRET_ACCESS_KEY="$S"
done
unset A S R
```

### 首次上线

1. Argo CD 同步后，**立刻**打开 `https://pan.liuxd.dev` 注册账号：第一个注册的账号就是管理员
2. 管理面板 → 参数设置 → 用户会话：关闭「允许新用户注册」
3. 管理面板 → 用户组：给管理员所在用户组设置 10G 容量上限（local-path 不限制磁盘用量，靠这里兜住）
4. 手动跑一次备份（见下），确认成功；`BackupStale` 告警只有成功过一次之后才会生效

### 手动备份和查看快照

```bash
# 立即运行一次（不等定时）
k3s kubectl -n cloudreve create job --from=cronjob/backup-cloudreve backup-cloudreve-manual-$(date +%s)
k3s kubectl -n cloudreve get jobs
k3s kubectl -n cloudreve logs job/<上一步的 Job 名> --all-containers
```

快照列表会打印在每次备份日志的最后。Uptime Kuma 的把命名空间换成 `monitoring`、CronJob 换成 `backup-uptime-kuma`。

### 恢复

思路：停掉应用 → 用 restic 把最新快照恢复进 PVC → 用快照里的数据库替换 → 启动应用。恢复演练时把具体命令补在这里。

## 10. 访问统计

> 2026-10-28 网站改版为 AI 落地服务站时下线（Argo CD 应用和清单已删除），本节只留作记录。

### 入口和组成

- 后台：`https://stats.liuxd.dev`（GoatCounter，命名空间 `goatcounter`，Argo CD 应用 `goatcounter`），清单在 `deploy/apps/goatcounter/`
- 站点每个页面加载 `stats.liuxd.dev/count.js` 计数；首页底部的「累计访客」读 `stats.liuxd.dev/counter/TOTAL.json`，接口不通时不显示
- 数据：一个 SQLite 文件 `goatcounter.db`，在 PVC `goatcounter-data` 里；每天 04:30 备份到 R2（和云盘共用备份脚本）
- 访客 IP：Traefik 的 Service 设为 `externalTrafficPolicy: Local`（`deploy/platform/traefik-config.yaml`），否则所有访客 IP 都是 10.42.0.1，独立访客数会被算成 1

### 首次上线

Argo CD 同步、证书签发完成后，在服务器上执行（Termius）：

```bash
# 1. 创建站点和管理员账号，会提示输入密码（不进命令历史）
k3s kubectl -n goatcounter exec -it deploy/goatcounter -- goatcounter db create site \
  -db=sqlite+/home/goatcounter/goatcounter-data/goatcounter.db \
  -vhost=stats.liuxd.dev -user.email=<你的邮箱>

# 2. 备份用的 Secret：从云盘的 Secret 复制，只把仓库路径换成 /goatcounter
g() { k3s kubectl -n cloudreve get secret backup-r2 -o jsonpath="{.data.$1}" | base64 -d; }
k3s kubectl -n goatcounter create secret generic backup-r2 \
  --from-file=RESTIC_REPOSITORY=<(g RESTIC_REPOSITORY | sed 's#/cloudreve$#/goatcounter#') \
  --from-file=RESTIC_PASSWORD=<(g RESTIC_PASSWORD) \
  --from-file=AWS_ACCESS_KEY_ID=<(g AWS_ACCESS_KEY_ID) \
  --from-file=AWS_SECRET_ACCESS_KEY=<(g AWS_SECRET_ACCESS_KEY)
```

3. 浏览器登录 `https://stats.liuxd.dev` → Settings：勾选「Allow adding visitor counts on your website」，否则首页的访客数接口返回 404
4. 手动跑一次备份（命名空间 `goatcounter`、CronJob `backup-goatcounter`，命令同第 9 节），确认成功

### 检查访客 IP 是否正确

后台 Locations 里应该能看到国家；全部显示为未知，说明拿到的还是集群内部地址，检查 Traefik Service 的 `externalTrafficPolicy` 是不是 `Local`：

```bash
k3s kubectl -n kube-system get svc traefik -o jsonpath='{.spec.externalTrafficPolicy}'
```

## 11. n8n 工作流平台

### 入口和组成

- **已从集群下线**（2026-10-24，节省服务器资源）：Argo CD 应用已删除，连同 PVC 一起清理；清单保留在 `deploy/apps/n8n/` 作为案例。启用：在 `deploy/argocd/applications.yaml` 加回 n8n 应用（可参考 git 历史）、`n8n.yaml` 的 `replicas` 改成 1、删掉 `backup.yaml` 的 `suspend: true`，再按下面「首次上线」做
- 界面：`https://n8n.liuxd.dev`（命名空间 `n8n`，Argo CD 应用 `n8n`），清单在 `deploy/apps/n8n/`
- 两层登录：外面是 Traefik 的 HTTP 基本认证（Secret `n8n-basic-auth`），里面是 n8n 自己的账号。`/webhook/`、`/form/` 不加基本认证，表单和外部系统可以直接访问
- 数据：PVC `n8n-data` 里的 `database.sqlite`（工作流、凭据、执行记录）和 `config`（加密凭据的密钥），每天 04:50 备份到 R2；执行记录只保留 14 天
- 示例工作流：`deploy/apps/n8n/workflows/`，在 n8n 里「Import from File」导入

### 首次上线

在服务器上执行（Termius）：

```bash
# 1. 基本认证账号（Secret 不存在时管理界面无法访问）。会提示输入两次密码
sudo apt-get install -y apache2-utils
htpasswd -c /tmp/n8n-users admin
k3s kubectl -n n8n create secret generic n8n-basic-auth --from-file=users=/tmp/n8n-users
rm /tmp/n8n-users

# 2. 备份用的 Secret：从云盘的 Secret 复制，只把仓库路径换成 /n8n
g() { k3s kubectl -n cloudreve get secret backup-r2 -o jsonpath="{.data.$1}" | base64 -d; }
k3s kubectl -n n8n create secret generic backup-r2 \
  --from-file=RESTIC_REPOSITORY=<(g RESTIC_REPOSITORY | sed 's#/cloudreve$#/n8n#') \
  --from-file=RESTIC_PASSWORD=<(g RESTIC_PASSWORD) \
  --from-file=AWS_ACCESS_KEY_ID=<(g AWS_ACCESS_KEY_ID) \
  --from-file=AWS_SECRET_ACCESS_KEY=<(g AWS_SECRET_ACCESS_KEY)
```

3. 浏览器打开 `https://n8n.liuxd.dev`，先输入上面的基本认证账号，再注册 n8n 管理员账号
4. 导入 `deploy/apps/n8n/workflows/it-repair-form.json`，把「发到企业微信」节点 URL 里的 `替换成企业微信key` 改成群机器人的 key，点 Publish（激活）
5. 手动跑一次备份（命名空间 `n8n`、CronJob `backup-n8n`，命令同第 9 节），确认成功

### 常见问题

| 现象                            | 原因                                              | 处理                                                     |
| ------------------------------- | ------------------------------------------------- | -------------------------------------------------------- |
| 打开界面返回 401 / 一直弹登录框 | 基本认证账号不对，或 Secret `n8n-basic-auth` 没建 | 按上面第 1 步重建 Secret                                 |
| Pod OOMKilled                   | 工作流一次处理的数据太多                          | 内存上限 1Gi；拆小批量，或调高 `resources.limits.memory` |
| 恢复后凭据报解密失败            | 只恢复了数据库，没恢复 `config`                   | 两个文件要从同一个快照一起恢复                           |

## 12. AI 服务（作品集的 AI 后端）

**2026-10-29 改版为作品集**：首页是代表作（运维 Agent、ChatBI、GitOps 平台），恢复了 `/live/`，ChatBI 演示在 `/chatbi/`；报价和服务套餐下线。

- 右下角的 AI 助手调用 `/api/ai/ask`，只按 `ai/knowledge/*.md` 的作品资料回答（改作品介绍时，`src/lib/site.ts` 和这里要一起改）；带最近几轮对话，每个访客每天 30 次
- 访客留言调用 `/api/ai/lead`：存进 `ai.db` 的 `leads` 表，并推送到我的 Telegram；每个访客每天 5 次
- ChatBI 调用 `/api/ai/chatbi`，每个访客每天 20 次
- 查看全部线索：`k3s kubectl -n ops-desk exec deploy/ai-api -- python -c "import sqlite3;[print(r) for r in sqlite3.connect('/data/ai.db').execute('select id,ts,name,contact,company,need from leads order by id desc')]"`
- 原来的单据提取、工单分拣、会议纪要等接口还在后端，页面已下线，需要时再恢复
- **运维 Agent 自愈**（`ai/app/autoheal.py`，页面 `/ops-agent/` 下方展示记录）：Alertmanager 把 warning / critical 告警同时发给 `http://ai-api.ops-desk.svc/internal/alertmanager`（集群内部地址，外网访问不到）。Agent 查完原因后从清单里提议一个操作：补跑 `backup-*` 任务、删除有控制器的 Pod 自动执行（同一对象 6 小时内只自动一次）；滚动重启 Deployment、补跑其他任务推到 Telegram，点「✅ 执行」才动手。写权限是 ClusterRole `ai-api-heal`，只绑在 ops-desk、monitoring、dify 三个命名空间；每天最多分析 10 条（`HEAL_DAILY`）。查看记录：`k3s kubectl -n ops-desk exec deploy/ai-api -- python -c "import sqlite3;[print(r) for r in sqlite3.connect('/data/ai.db').execute('select id,ts,alert,status,action,result from incidents order by id desc limit 10')]"`

代码在 `ai/`（FastAPI），镜像 `ghcr.io/roccowork/ops-desk-ai` 和站点一起由 CI 构建、用同一个 commit SHA 发布。清单在 `deploy/k8s/base/ai.yaml`：Deployment `ai-api`（1 副本，Recreate）、PVC `ai-data`（费用账本和向量缓存，SQLite）、只转发 `/api/ai` 的 Ingress（带限流和 3MB 请求体上限）。模型调用走阿里云百炼的 OpenAI 兼容接口。

### 第一次启用

1. **镜像设为公开**：第一次 CI 构建后，GitHub → 个人主页 → Packages → `ops-desk-ai` → Package settings → Change visibility → Public（和 `ops-desk` 镜像一样，集群拉取时不需要凭据）。在此之前 `ai-api` 会停在 ImagePullBackOff，站点本身不受影响
2. **创建密钥**（密钥不进 Git）。国际站和国内站地址不同，按开通的站点选一个：

```bash
kubectl -n ops-desk create secret generic ai-api \
  --from-literal=DASHSCOPE_API_KEY='sk-...' \
  --from-literal=DASHSCOPE_BASE_URL='https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
#   国内站用 https://dashscope.aliyuncs.com/compatible-mode/v1
kubectl -n ops-desk rollout restart deployment ai-api
```

3. **确认**：`curl https://ops.liuxd.dev/api/ai/health` 返回 `"enabled": true`；几分钟后 `index_ready` 变为 true（启动时给全部案例算一次向量，约 30 万 token，不到 0.2 元，之后只算有改动的段落）

### 费用和配额

| 项目                                  | 默认值                                                                                                  | 修改方法                                                                                     |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 全站每日费用上限                      | 1.6 元（约每月 50 元）                                                                                  | Deployment 环境变量 `AI_DAILY_BUDGET`                                                        |
| 每个访客每天次数                      | 问答 20、识别 5、分派 20、纪要 3、查数据 20                                                             | `AI_QUOTA_ASK`、`AI_QUOTA_EXTRACT`、`AI_QUOTA_TRIAGE`、`AI_QUOTA_MINUTES`、`AI_QUOTA_CHATBI` |
| 模型                                  | 问答 qwen-plus（失败降级 qwen-flash），识别 qwen3-vl-plus，向量 text-embedding-v3，语音 qwen3-asr-flash | `AI_CHAT_MODELS`、`AI_VISION_MODELS`、`AI_EMBED_MODEL`、`AI_ASR_MODEL`                       |
| 单价（估算，元 / 千 token；语音按秒） | 见 `ai/app/config.py`                                                                                   | `AI_PRICES`                                                                                  |

费用是按 token 估算的，以百炼控制台的账单为准；控制台里另设一个月度费用预警作为第二道保险。达到每日上限时触发 `AIDailyBudgetReached` 告警，模型连续出错时触发 `AIModelErrors`。

### 常见问题

- **页面提示「AI 功能还没有启用」**：Secret 不存在或没有重启。`kubectl -n ops-desk get secret ai-api`
- **提示「知识库还在准备中」**：索引失败会每 10 分钟重试，`kubectl -n ops-desk logs deploy/ai-api` 看原因（多半是密钥或地址不对）
- **某个功能提示「暂时不可用」**：`kubectl -n ops-desk logs deploy/ai-api --tail=50` 会记录失败的模型和百炼返回的原因。常见是该模型在百炼「免费额度」里没有额度、又开了「用完即停」或余额为 0（返回 403），换一个有额度的模型（改对应的环境变量）或充值
- **用到的模型**：qwen-plus、qwen-flash、qwen3-vl-plus、text-embedding-v3、qwen3-asr-flash。在百炼「免费额度」页逐个确认有额度
- **查数据的演示库**：启动时生成在 `/data/chatbi/`（hq.db 全部区域、east.db 只有华东），改了生成逻辑要把 `chatbi.py` 的 `DATA_VERSION` 加一，重启后自动重建
- **换密钥**：删除重建 Secret 后 `rollout restart`
- **清空账本和缓存**：删除 PVC `ai-data` 前先缩容 `ai-api` 到 0；它设置了 `Prune=false`，Argo CD 不会自动删

### 备份

费用账本、自愈记录都在 `ai.db` 里，每天 05:10（新加坡时间）由 CronJob `backup-ai-api` 加密备份到 R2（`ops-desk` 命名空间，仓库路径 `/ai-api`）。私人助理的 `backup-assistant` 用同一个 Secret 和仓库，按 host 区分。第一次需要创建 Secret（从云盘的复制，只换仓库路径），再手动跑一次：

```bash
g() { k3s kubectl -n cloudreve get secret backup-r2 -o jsonpath="{.data.$1}" | base64 -d; }
k3s kubectl -n ops-desk create secret generic backup-r2 \
  --from-file=RESTIC_REPOSITORY=<(g RESTIC_REPOSITORY | sed 's#/cloudreve$#/ai-api#') \
  --from-file=RESTIC_PASSWORD=<(g RESTIC_PASSWORD) \
  --from-file=AWS_ACCESS_KEY_ID=<(g AWS_ACCESS_KEY_ID) \
  --from-file=AWS_SECRET_ACCESS_KEY=<(g AWS_SECRET_ACCESS_KEY)
k3s kubectl -n ops-desk create job backup-ai-api-manual --from=cronjob/backup-ai-api
```

## 13. Odoo（AI 实战用的业务系统）

> 2026-10-28 网站改版为 AI 落地服务站时下线（Argo CD 应用和清单已删除），本节只留作记录。

### 入口和组成

- 界面：`https://odoo.liuxd.dev`（命名空间 `odoo`，Argo CD 应用 `odoo`），清单在 `deploy/apps/odoo/`
- 两层登录：外面是 Traefik 基本认证（Secret `odoo-basic-auth`），里面是 Odoo 管理员 `admin`（密码在 Secret `odoo-secrets` 的 `ODOO_ADMIN_PASSWORD`）
- 组成：`odoo`（19.0 社区版，单进程，内存上限 1Gi）+ `odoo-db`（PostgreSQL 17，内存上限 384Mi），各有一个 PVC
- 装好的模块：CRM、销售、采购、库存、开票、联系人、员工、请假、报销、项目，带官方演示数据；演示账号 demo/demo 已停用；数据库管理页已关闭
- 数据都是演示数据，暂不做异地备份；坏了可以重新初始化（见下面常见问题）

### 首次上线

在服务器上执行（Termius）：

```bash
# 1. 命名空间（Argo CD 也会建，先建好才能放 Secret）
k3s kubectl create namespace odoo

# 2. 数据库密码随机生成；管理员密码自己定，把 '你的密码' 换掉
k3s kubectl -n odoo create secret generic odoo-secrets \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=ODOO_ADMIN_PASSWORD='你的密码'

# 3. 外层基本认证（会提示输入两次密码）
sudo apt-get install -y apache2-utils
htpasswd -c /tmp/odoo-users admin
k3s kubectl -n odoo create secret generic odoo-basic-auth --from-file=users=/tmp/odoo-users
rm /tmp/odoo-users

# 4. 看初始化进度：第一次要装模块和演示数据，大约 5～10 分钟
k3s kubectl -n odoo logs deploy/odoo -c init -f
```

5. 日志出现「管理员设置完成」后，浏览器打开 `https://odoo.liuxd.dev`：先输入第 3 步的账号，再用 `admin` + 第 2 步的密码登录

### 连接 AI 服务（办公 Agent）

AI 服务用 Odoo 里的 `ai-agent` 账号（只有销售权限）。同一个随机密码要放进两个 Secret：

```bash
P=$(openssl rand -hex 16)
k3s kubectl -n odoo patch secret odoo-secrets -p "{\"stringData\":{\"ODOO_AI_PASSWORD\":\"$P\"}}"
k3s kubectl -n ops-desk patch secret ai-api -p "{\"stringData\":{\"ODOO_PASSWORD\":\"$P\"}}"
k3s kubectl -n odoo rollout restart deployment odoo        # 启动时按 Secret 建好 ai-agent 账号
k3s kubectl -n ops-desk rollout restart deployment ai-api
```

AI 建的商机和报价单带「AI 创建」标签，在 Odoo 里按标签筛选就能找到。

### 常见问题

| 现象                                  | 原因                                        | 处理                                                                                               |
| ------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Pod 一直 `CreateContainerConfigError` | Secret `odoo-secrets` 没建                  | 按第 2 步建好，Pod 会自动启动                                                                      |
| 打开返回 401 / 一直弹登录框           | 基本认证账号不对，或 `odoo-basic-auth` 没建 | 按第 3 步重建                                                                                      |
| 改管理员密码                          | —                                           | 改 Secret 里的 `ODOO_ADMIN_PASSWORD`，再 `rollout restart deployment odoo`（启动时按 Secret 重设） |
| init 被 OOMKilled                     | 装演示数据时内存不够                        | 调高 init 容器的 `limits.memory` 后重启，安装会从中断处继续                                        |
| 想恢复成全新的演示数据                | —                                           | 缩容 `odoo` 到 0，`psql` 里 `DROP DATABASE odoo`，再扩容到 1，会重新初始化（选做，平时不用）       |

## 14. 私人助理（已拆出）

私人助理（Telegram 机器人）2026-10-29 从 AI 服务里拆了出去，代码和文档在私有仓库，由同一个 Argo CD 的 `personal` 应用部署。

## 15. R2 用量报告

- 每月 1 号 09:30（新加坡时间）CronJob `r2-usage-monthly` 发月报到企业微信：总用量、占 10 GB 免费额度的比例、每个应用（restic 仓库）各占多少
- 每周一 09:30（新加坡时间）`r2-usage-check` 检查一次，超过 80% 才提醒
- 用 `monitoring` 命名空间里 Uptime Kuma 备份的 Secret `backup-r2` 列出整个桶，不需要额外的 Cloudflare API 令牌；读写次数没统计（备份用量远低于免费额度）
- 手动试跑：`k3s kubectl -n monitoring create job r2-test --from=cronjob/r2-usage-monthly`

## 16. Telegram 推送（替代企业微信群机器人）

用两个机器人。一个机器人同一时间只能有一个程序收消息（getUpdates），分开以后互不影响：

| Secret                                        | 谁在用                                                                                           | 收消息吗                                         |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------ |
| `telegram-ops`（`ops-desk` 命名空间，值班台） | ai-api：运维 Agent 自愈消息和「执行 / 忽略」按钮、访客留言、客服 Agent 演示预约                  | 只收按钮点击（`ai/app/telegram.py` 的 `OpsBot`） |
| `telegram-bot`（`ops-desk`、`monitoring`）    | 私人助理（私有仓库）；告警、运维周报、每日英语、R2 用量也用它的 token 发消息（只发不收，不冲突） | 私人助理收                                       |

告警、运维周报、每日英语、R2 用量共用 `deploy/monitoring/wecom-relay/relay.mjs` 里的推送函数：Secret `telegram-bot` 存在就发 Telegram，否则发企业微信（Secret `wecom-bot`）。消息格式自动转换。

网站的机器人（值班台）还有常驻菜单，按钮都只读、不调用模型（`ai/app/dutybot.py`）：服务器状态、当前告警、自愈记录、最近留言、今日用量；直接打字提问交给运维 Agent（和网站值班台同一套工具，主人自己用可以查日志，日志先脱敏），每次调用模型。菜单在机器人里发 /start 出现。

### 创建网站的机器人（telegram-ops）

没建这个 Secret 时 ai-api 照常运行，只是自愈审批、访客留言收不到推送（留言仍然存在 `ai.db` 里）。

1. Telegram 里找 @BotFather，`/newbot` 建一个新机器人（名字比如 ops-desk 运维），拿到 Token；再打开这个新机器人点 Start，随便发一句话
2. 在服务器上执行（`read -s` 输入时不显示、不进命令历史）：

```bash
read -rs -p 'Bot Token: ' TG; echo
# 取自己的 chat id：就是刚才给机器人发消息的那个会话
CHAT=$(curl -s "https://api.telegram.org/bot$TG/getUpdates" | grep -o '"chat":{"id":[0-9-]*' | head -1 | grep -o '[0-9-]*$')
echo "chat id: $CHAT"
k3s kubectl -n ops-desk create secret generic telegram-ops --from-literal=token="$TG" --from-literal=chat_id="$CHAT"
unset TG
k3s kubectl -n ops-desk rollout restart deployment ai-api
```

- `chat id` 为空：说明还没给新机器人发过消息，先发一句再执行

### 重建私人助理的机器人（telegram-bot）

只在换服务器、Secret 丢了的时候需要。步骤同上，Secret 名换成 `telegram-bot`，并且 `monitoring` 命名空间也要建一份：

```bash
for ns in monitoring ops-desk; do
  k3s kubectl -n "$ns" create secret generic telegram-bot --from-literal=token="$TG" --from-literal=chat_id="$CHAT"
done
k3s kubectl -n monitoring rollout restart deployment wecom-relay
k3s kubectl -n ops-desk rollout restart deployment assistant
k3s kubectl -n monitoring create job tg-test --from=cronjob/daily-english   # 手机上应该收到今天的运维英语
```

- 确认 Telegram 正常后可以删掉企业微信：`k3s kubectl -n monitoring delete secret wecom-bot`（不删也行，Telegram 优先）
