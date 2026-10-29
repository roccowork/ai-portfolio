#!/usr/bin/env bash
# SSH 加固：关闭密码登录，root 只允许密钥登录。
# 执行前必须确认至少两把密钥能登录，并保持一个已登录的会话不断开（见 docs/runbook.md）。
set -euo pipefail

# 文件名用 00- 开头：sshd 对同一个选项只认第一次读到的值，
# 云镜像自带的 50-cloud-init.conf 写着 PasswordAuthentication yes，必须排在它前面。
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
EOF

# 先检查语法，有错就不重载，避免把自己锁在外面
sshd -t
# reload 只让新连接使用新配置，已登录的会话不受影响
systemctl reload ssh

echo "当前生效的配置："
sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin|pubkeyauthentication) '
