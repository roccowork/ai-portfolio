#!/usr/bin/env bash
# 安装单节点 k3s。版本固定，配置写在 /etc/rancher/k3s/config.yaml，换服务器重跑即可得到相同环境。
# 用法：bash install-k3s.sh <公网IP>
set -euo pipefail

K3S_VERSION="v1.36.5+k3s1"
PUBLIC_IP="${1:?用法: bash install-k3s.sh <公网IP>}"

mkdir -p /etc/rancher/k3s
cat > /etc/rancher/k3s/config.yaml <<EOF
# 节点名固定：主机名里有大写字母，不符合 K8s 命名规范；换服务器时节点名也保持不变
node-name: ops-hk-1
# API 证书包含公网 IP。安全组不开放 6443；需要从本机用 kubectl 时，经 SSH 隧道访问
tls-san:
  - ${PUBLIC_IP}
# kubeconfig 含集群管理员凭据，只允许 root 读
write-kubeconfig-mode: "0600"
# Secret 在 etcd/sqlite 里加密存储
secrets-encryption: true
# 每次部署都会留下旧镜像，系统盘只有 40G：磁盘用到 70% 就开始清理不再使用的镜像，清到 60%
# （默认是 85% / 80%，到那时已经接近磁盘告警线）
kubelet-arg:
  - image-gc-high-threshold=70
  - image-gc-low-threshold=60
EOF

# 指定版本后，安装脚本直接从 GitHub Releases 下载并校验 SHA256，不依赖 update.k3s.io
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="${K3S_VERSION}" sh -

echo "==> 等待节点就绪"
for i in $(seq 1 30); do
  if k3s kubectl get node ops-hk-1 2>/dev/null | grep -q " Ready"; then break; fi
  sleep 5
done
k3s kubectl get nodes -o wide
echo
echo "==> swap 状态（k3s 在有 swap 时应能正常运行）"
swapon --show
k3s kubectl get pods -A
