# 换服务器：从旧机器整体迁移

2026-10 从雨云香港（`<旧服务器 IP>`，宿主机 CPU steal 50%）迁到新香港主机（4 核 12G、120G、100M）时写的步骤。和 [runbook 第 3 节](runbook.md#3-换一台新服务器从零恢复)的区别：旧机器还能开机，所以 **Secret 和数据直接从旧机器拷**，不用重新输入密钥、也不用从 R2 恢复。

全程大约 2 小时，其中等待约 1 小时。下面 `<旧IP>`、`<新IP>` 换成实际 IP。

| 阶段 | 在哪台机器 | 做什么                                                           | 约用时  |
| ---- | ---------- | ---------------------------------------------------------------- | ------- |
| A    | 新         | 验收：CPU 不被抢、系统版本、防火墙                               | 10 分钟 |
| B    | 新 + 旧    | 让新机器能 SSH 到旧机器                                          | 5 分钟  |
| C    | 旧         | 停掉旧机器的应用和推送，导出 Secret                              | 10 分钟 |
| D    | 新         | 装 k3s、导入 Secret、装 Argo CD、改 DNS，让 Git 把全部组件装起来 | 40 分钟 |
| E    | 新         | 把 7 个数据卷从旧机器拷过来                                      | 20 分钟 |
| F    | 新         | 验收、收尾                                                       | 20 分钟 |

## A. 验收新机器

在 **新服务器** 上：

```bash
cat /etc/os-release | head -2    # 要 Ubuntu 24.04（22.04 也行），不是就在控制台重装
nproc; free -h; df -h /
vmstat 1 5                       # st 列要接近 0；超过 10 就别迁，先找商家
```

控制台的防火墙（安全组）只放行 TCP 22、80、443 和 ICMP，**不要开 6443**。商家没有防火墙设置就先跳过，迁完再处理。

## B. 让新机器能 SSH 到旧机器

旧机器只允许密钥登录，所以给新机器生成一把临时密钥，加到旧机器上，迁完删掉。

在 **新服务器** 上：

```bash
apt-get update && apt-get -y install rsync
ssh-keygen -t ed25519 -f /root/.ssh/migrate -N '' -C migrate
cat /root/.ssh/migrate.pub       # 复制这一行
```

在雨云控制台把 **旧服务器** 开机，登录后（`rsync` 一般已装，没有就 `apt-get -y install rsync`）：

```bash
echo '粘贴刚才那一行' >> /root/.ssh/authorized_keys
```

回到 **新服务器** 测试，第一次会问是否信任，输入 `yes`：

```bash
ssh -i /root/.ssh/migrate root@<旧IP> hostname
```

## C. 停掉旧机器，导出 Secret

旧机器开机后告警可能又会刷屏，第一步就先停推送。在 **旧服务器** 上：

```bash
alias k='k3s kubectl'
# 1. 先停 Argo CD，否则它会把下面停掉的服务又拉起来
k -n argocd scale sts -l app.kubernetes.io/name=argocd-application-controller --replicas=0
# 2. 停告警转发和所有定时任务（英语、备份等），避免和新机器重复推送
k -n monitoring scale deploy wecom-relay --replicas=0
k get cronjob -A --no-headers | while read ns n _; do k -n "$ns" patch cronjob "$n" -p '{"spec":{"suspend":true}}'; done
# 3. 停掉有数据的应用，保证拷贝时数据不再变化（个人助理也在这里面，停了就不会和新机器抢 Telegram 消息）
for ns in ops-desk cloudreve goatcounter n8n odoo; do k -n "$ns" scale deploy,sts --all --replicas=0; done
k -n monitoring scale deploy uptime-kuma --replicas=0
```

导出 Secret（只导出自己建的，跳过 Helm 和证书自动生成的）：

```bash
python3 - <<'EOF'
import json, subprocess
out = []
for ns in ["ops-desk", "monitoring", "cloudreve", "goatcounter", "n8n", "odoo"]:
    try:
        items = json.loads(subprocess.check_output(["k3s", "kubectl", "-n", ns, "get", "secret", "-o", "json"]))["items"]
    except subprocess.CalledProcessError:
        continue
    for s in items:
        m = s["metadata"]
        if s.get("type") != "Opaque" or m.get("ownerReferences") or (m.get("labels") or {}).get("app.kubernetes.io/managed-by") == "Helm":
            continue
        meta = {"name": m["name"], "namespace": ns, **({"labels": m["labels"]} if m.get("labels") else {})}
        out.append({"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta, "data": s.get("data", {})})
json.dump({"apiVersion": "v1", "kind": "List", "items": out}, open("/root/migrate-secrets.json", "w"))
print(len(out), "个 Secret：", ", ".join(i["metadata"]["namespace"] + "/" + i["metadata"]["name"] for i in out))
EOF
chmod 600 /root/migrate-secrets.json
```

应该能看到 `telegram-bot`、`ai-api`、`backup-r2` 等。

## D. 新机器装好整套环境

在 **新服务器** 上：

```bash
OLD=<旧IP>; NEW=<新IP>
R="rsync -a -e 'ssh -i /root/.ssh/migrate'"
# 1. 部署脚本和 Secret 从旧机器拷过来
eval $R root@$OLD:/root/ops-desk-deploy/ /root/ops-desk-deploy/   # 提示目录不存在就停下，告诉 Claude
eval $R root@$OLD:/root/migrate-secrets.json /root/
chmod 600 /root/migrate-secrets.json

# 2. 系统初始化 + 装 k3s（init.sh 最后提示要重启的话，先 reboot，重新登录后再执行 install-k3s.sh）
bash /root/ops-desk-deploy/server/init.sh
bash /root/ops-desk-deploy/server/install-k3s.sh $NEW

# 3. 导入 Secret
for ns in ops-desk monitoring cloudreve goatcounter n8n odoo; do k3s kubectl create namespace $ns; done
k3s kubectl apply -f /root/migrate-secrets.json

# 4. 装 Argo CD，最后会打印一把公钥
bash /root/ops-desk-deploy/server/install-argocd.sh
```

把打印的公钥加到 GitHub：ops-desk 仓库 → Settings → Deploy keys → Add deploy key，**不勾** Allow write access。旧服务器那把先留着，F 阶段再删。

改 DNS：Cloudflare → liuxd.dev → DNS，把 `*.liuxd.dev`（以及其他指向旧 IP 的 A 记录）改成新 IP，保持「仅 DNS」（灰色云）。`liuxd.dev`、`cv`、`roadmap` 在 Cloudflare Pages 上，不用改。

让 Argo CD 从 Git 装好全部组件：

```bash
k3s kubectl apply -f /root/ops-desk-deploy/argocd/applications.yaml
watch -n 20 k3s kubectl -n argocd get applications     # 10～15 分钟后全部 Synced / Healthy，Ctrl+C 退出
```

前几分钟部分应用失败是正常的（组件之间有依赖），会自动重试。这时新机器上跑的是**空数据**的应用，下一步换成旧数据。

## E. 拷数据

在 **新服务器** 上（`OLD` 变量要还在，新开了终端就重新设一次）：

```bash
OLD=<旧IP>; alias k='k3s kubectl'
# 1. 停 Argo CD 和有数据的应用
k -n argocd scale sts -l app.kubernetes.io/name=argocd-application-controller --replicas=0
for ns in ops-desk cloudreve goatcounter n8n odoo; do k -n "$ns" scale deploy,sts --all --replicas=0; done
k -n monitoring scale deploy uptime-kuma --replicas=0
sleep 30

# 2. 逐个数据卷拷贝（local-path 的目录名是 pvc-xxx_命名空间_卷名）
S=/var/lib/rancher/k3s/storage
for p in ops-desk:ai-data monitoring:uptime-kuma-data cloudreve:cloudreve-data goatcounter:goatcounter-data n8n:n8n-data odoo:odoo-data odoo:odoo-db; do
  ns=${p%%:*}; name=${p#*:}
  src=$(ssh -i /root/.ssh/migrate root@$OLD "ls -d $S/*_${ns}_${name}" 2>/dev/null)
  dst=$(ls -d $S/*_${ns}_${name} 2>/dev/null)
  if [ -z "$src" ] || [ -z "$dst" ]; then echo "跳过 $ns/$name（旧：$src，新：$dst）"; continue; fi
  echo "==> $ns/$name"
  rsync -a --delete --numeric-ids -e 'ssh -i /root/.ssh/migrate' root@$OLD:"$src/" "$dst/"
done

# 3. 恢复 Argo CD，它会按 Git 把应用重新拉起来
k -n argocd scale sts -l app.kubernetes.io/name=argocd-application-controller --replicas=1
```

有「跳过」的告诉 Claude。Prometheus 的历史监控数据不拷，从零开始记录。

## 踩坑记录（2026-10-28 实际迁移）

| 现象                                                        | 原因                                                                                                  | 解决                                                                                                                                            |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Argo CD 只出现 3 个应用，`monitoring-extras` 一直 OutOfSync | 旧机器上的 `/root/ops-desk-deploy` 是最早的版本，`applications.yaml` 里没有 `root`、`platform` 等应用 | 按 runbook 第 7 节手动创建 `root` 应用，它从 Git 读最新的应用列表                                                                               |
| 加了公钥，SSH 仍然要求输入密码                              | 新商家的系统镜像默认 `PubkeyAuthentication no`                                                        | `/etc/ssh/sshd_config.d/00-hardening.conf` 里写 `PubkeyAuthentication yes`；关闭密码登录前先确认密钥能登录，被锁在外面时用商家控制台的 VNC 恢复 |
| 新机器 SSH 端口不是 22                                      | 商家分配了 <SSH 端口>                                                                                 | Termius 里填 <SSH 端口>；防火墙放行的也是这个端口                                                                                               |

## F. 验收和收尾

在 **新服务器** 上：

```bash
k3s kubectl get pods -A | grep -v -E 'Running|Completed'   # 只剩表头就是全部正常
curl -s https://ops.liuxd.dev/version.txt                   # 和 Git 里的 newTag 一致
vmstat 1 5                                                  # st 接近 0
```

再检查这些：

- Telegram 点「☀️ 今日简报」：提醒都在，服务器状态正常
- `https://status.liuxd.dev`（Uptime Kuma）、`https://pan.liuxd.dev`（云盘）、Odoo、n8n 能登录，数据都在
- Argo CD 是新装的：按 [runbook 第 7 节](runbook.md#7-argo-cd)取初始密码登录后改密码
- 手动跑一次备份，确认能写进 R2：`k3s kubectl -n ops-desk create job backup-ai-api-manual --from=cronjob/backup-ai-api`

都没问题之后：

1. GitHub Deploy keys 删掉旧服务器那把
2. 新服务器删临时文件：`rm /root/migrate-secrets.json /root/.ssh/migrate /root/.ssh/migrate.pub`
3. 旧服务器保留 3 天，确认新机器没问题再在雨云退订（退订前不用再开机）
4. 告诉 Claude 新 IP，更新 runbook 顶部的服务器信息
5. 可选：按 runbook 第 3 节第 5、6 步关闭密码登录（先确认电脑和手机都能用密钥登录）
