#!/usr/bin/env bash
# 服务器基础初始化：系统更新、fail2ban、自动安全更新。
# 可重复执行（幂等）。用法：在服务器上以 root 运行  bash init.sh
# SSH 加固和 k3s 安装是单独的脚本，见 docs/runbook.md。
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "==> 1/4 更新系统软件包"
apt-get update
# 保留现有配置文件，避免升级过程中弹出交互提示
apt-get -y -o Dpkg::Options::=--force-confold upgrade

echo "==> 2/4 安装 fail2ban 和自动更新组件"
apt-get -y install fail2ban unattended-upgrades

echo "==> 3/4 配置 fail2ban 保护 SSH"
# Ubuntu 默认让 sshd 规则读 systemd journal，但这台机器的 journal 为空，
# SSH 日志实际写在 /var/log/auth.log（rsyslog），所以显式指定日志来源。
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled  = true
backend  = auto
logpath  = /var/log/auth.log
# 10 分钟内失败 5 次，封禁 1 小时
findtime = 10m
maxretry = 5
bantime  = 1h
EOF
systemctl enable fail2ban
systemctl restart fail2ban

echo "==> 4/4 开启自动安全更新"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

# 防火墙由云厂商安全组负责，服务器内不启用 ufw（会和 k3s 的网络规则冲突）
if ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "警告：ufw 处于开启状态，请确认后手动执行 ufw disable" >&2
fi

echo
echo "完成。检查结果："
fail2ban-client status sshd
if [ -f /var/run/reboot-required ]; then
  echo "提示：有内核等更新需要重启才能生效（/var/run/reboot-required）"
fi
