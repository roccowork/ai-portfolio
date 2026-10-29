#!/usr/bin/env bash
# 安装 Argo CD，并为它生成读取私有仓库 roccowork/ops-desk（私人助理）用的部署密钥（只读、只能访问那一个仓库）。
# 网站所在的 ai-portfolio 是公开仓库，Argo CD 不需要凭据。
# 用法：bash deploy/server/install-argocd.sh
# 执行完按提示把公钥添加到 GitHub 的 Deploy keys，再执行 docs/runbook.md 里创建 Application 的命令。
# 之后 cert-manager、kube-prometheus-stack 等其余组件都由 Argo CD 从 Git 安装（deploy/platform/），
# 包括 Argo CD 自己的 HelmChart，这里只负责第一次创建。
set -euo pipefail
cd "$(dirname "$0")/.."   # 进入 deploy/
K="k3s kubectl"
KEY=/root/.ssh/argocd-ops-desk   # 私钥只留在服务器上（和集群 Secret 里），不进仓库

echo "==> 创建 Argo CD 的 HelmChart，由 k3s 的 helm-controller 安装"
# 用 kubectl 创建，而不是放进 /var/lib/rancher/k3s/server/manifests/：
# 那个目录里的文件 k3s 每次启动都会重新应用，会和 Argo CD 从 Git 同步的版本互相覆盖
$K apply -f platform/argocd.yaml

echo "==> 等待 Argo CD 就绪（首次需要拉取镜像，约 1～3 分钟）"
for i in $(seq 1 90); do
  $K get deploy -n argocd argocd-server >/dev/null 2>&1 && break
  sleep 5
done
$K wait -n argocd --for=condition=Available deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis --timeout=600s
$K rollout status -n argocd statefulset/argocd-application-controller --timeout=600s

echo "==> 生成部署密钥（已存在则复用）"
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N '' -C 'argocd@ops-hk-1' -f "$KEY" >/dev/null
fi

echo "==> 创建仓库凭据 Secret"
$K -n argocd create secret generic repo-ops-desk \
  --from-literal=type=git \
  --from-literal=url=git@github.com:roccowork/ops-desk.git \
  --from-file=sshPrivateKey="$KEY" \
  --dry-run=client -o yaml | $K apply -f -
$K -n argocd label secret repo-ops-desk argocd.argoproj.io/secret-type=repository --overwrite

echo
$K get pods -n argocd
echo
echo "请把下面这行公钥添加到 GitHub：私有仓库 roccowork/ops-desk 的 Settings → Deploy keys → Add deploy key（不要勾选 Allow write access）"
echo
cat "$KEY.pub"
