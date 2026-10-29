#!/usr/bin/env bash
# 安装或升级 Dify。Dify 1.17 有十多个组件（还有 Agent 沙箱），官方只维护 Docker Compose 部署方式，
# 所以它不放进 k3s：用官方 Compose 跑在 /opt/dify，只监听 k3s 的内网地址（cni0 网卡，10.42.0.1:8088），
# 公网进不来；对外由 Traefik 按 deploy/apps/dify/ 转发，HTTPS 证书由 cert-manager 签发。
# 用法（root）：bash install-dify.sh。可重复执行：密钥只在第一次生成；升级时改 DIFY_VERSION 再执行。
set -euo pipefail

DIFY_VERSION="1.17.1"
DIR=/opt/dify
DOMAIN=dify.liuxd.dev

BIND=$(ip -4 -o addr show cni0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
if [ -z "$BIND" ]; then
  echo "找不到 cni0 网卡：先确认 k3s 在运行" >&2
  exit 1
fi

echo "==> 1/4 安装 Docker（已装则跳过）"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

echo "==> 2/4 下载 Dify ${DIFY_VERSION}"
if [ ! -d "$DIR/.git" ]; then
  git clone --depth 1 --branch "$DIFY_VERSION" https://github.com/langgenius/dify.git "$DIR"
else
  git -C "$DIR" fetch --depth 1 origin tag "$DIFY_VERSION"
  git -C "$DIR" checkout -q "$DIFY_VERSION"
fi
cd "$DIR/docker"

echo "==> 3/4 生成配置 .env"
set_env() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >>.env; fi
}
rand() { openssl rand -hex 24; }
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  # 官方示例里的默认密码和密钥人人都知道，第一次安装时全部换成随机值；之后保留，否则已有数据解不开
  for k in SECRET_KEY DB_PASSWORD REDIS_PASSWORD PLUGIN_DAEMON_KEY PLUGIN_DIFY_INNER_API_KEY \
    DIFY_AGENT_API_TOKEN DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN; do
    set_env "$k" "$(rand)"
  done
  SANDBOX_KEY=$(rand)
  set_env SANDBOX_API_KEY "$SANDBOX_KEY"
  set_env CODE_EXECUTION_API_KEY "$SANDBOX_KEY"
  set_env DIFY_AGENT_SERVER_SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
# 官方示例把默认 Redis 密码直接写在了 Celery 的地址里，要跟着换，否则后台任务（处理知识库文档）连不上 Redis
set_env CELERY_BROKER_URL "redis://:$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2)@redis:6379/1"
# 网络相关的配置每次都写，保证端口只开在内网
set_env EXPOSE_NGINX_PORT "${BIND}:8088"
set_env EXPOSE_NGINX_SSL_PORT "127.0.0.1:8443"
# 插件调试端口：这个变量 Dify 也拿去当端口号校验，只能写纯数字；只监听本机靠下面的 override 文件
set_env EXPOSE_PLUGIN_DEBUGGING_PORT 5003
cat >docker-compose.override.yaml <<'EOF2'
services:
  plugin_daemon:
    ports: !override
      - "127.0.0.1:5003:5003"
EOF2
set_env CONSOLE_WEB_URL "https://${DOMAIN}"
set_env APP_WEB_URL "https://${DOMAIN}"
set_env FILES_URL "https://${DOMAIN}"
set_env NEXT_PUBLIC_SOCKET_URL "wss://${DOMAIN}"
# 托管给客户用时，要能把聊天窗口嵌到客户自己的网站上
set_env ALLOW_EMBED true
# 登录时可以只用邮箱验证码（发信用 Resend：MAIL_TYPE、MAIL_DEFAULT_SEND_FROM、RESEND_API_KEY 手动写进 .env，密钥不进仓库）
set_env ENABLE_EMAIL_CODE_LOGIN true
# 不开多人协作编辑（api_websocket 组件约占 400MB 内存，一个人用不到）
set_env COMPOSE_PROFILES '${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql}'

echo "==> 4/4 启动"
docker compose pull -q
docker compose up -d --remove-orphans
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
# Argo CD 默认不管理 EndpointSlice（deploy/apps/dify/ 里只有 Service 和 Ingress），在这里创建：告诉 Traefik Dify 在哪
k3s kubectl get namespace dify >/dev/null 2>&1 || k3s kubectl create namespace dify
cat <<EOF2 | k3s kubectl apply -f -
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: dify-host
  namespace: dify
  labels:
    kubernetes.io/service-name: dify
addressType: IPv4
ports:
  - name: http
    port: 8088
    protocol: TCP
endpoints:
  - addresses: ['${BIND}']
EOF2

# 每天导出数据库，k3s 里的 backup-dify（deploy/apps/dify/backup.yaml）再把它加密备份到 R2
mkdir -p /opt/dify/backup
cat >/etc/cron.d/dify-dump <<'EOF2'
# Dify 数据库每天导出一次（服务器本地时间 03:40）。备份任务会检查这个文件是不是 25 小时内更新过
40 3 * * * root bash -c 'set -o pipefail; cd /opt/dify/docker && docker compose exec -T db_postgres pg_dumpall -U postgres | gzip >/opt/dify/backup/dify.sql.gz.tmp && mv /opt/dify/backup/dify.sql.gz.tmp /opt/dify/backup/dify.sql.gz && chmod 644 /opt/dify/backup/dify.sql.gz'
EOF2
# 先导出一次，备份任务第一次运行就有文件可用
bash -c 'set -o pipefail; docker compose exec -T db_postgres pg_dumpall -U postgres | gzip >/opt/dify/backup/dify.sql.gz && chmod 644 /opt/dify/backup/dify.sql.gz'
# 备份用的 R2 密钥：从 ops-desk 命名空间复制，只把仓库路径换成 /dify
if ! k3s kubectl -n dify get secret backup-r2 >/dev/null 2>&1; then
  g() { k3s kubectl -n ops-desk get secret backup-r2 -o jsonpath="{.data.$1}" | base64 -d; }
  k3s kubectl -n dify create secret generic backup-r2 \
    --from-literal=RESTIC_REPOSITORY="$(g RESTIC_REPOSITORY | sed 's#/[^/]*$#/dify#')" \
    --from-literal=RESTIC_PASSWORD="$(g RESTIC_PASSWORD)" \
    --from-literal=AWS_ACCESS_KEY_ID="$(g AWS_ACCESS_KEY_ID)" \
    --from-literal=AWS_SECRET_ACCESS_KEY="$(g AWS_SECRET_ACCESS_KEY)"
fi

echo
echo "完成：$(curl -s -o /dev/null -w '%{http_code}' "http://${BIND}:8088/") 表示 Dify 已在内网 ${BIND}:8088 响应（200 或 307 都正常）"
echo "然后浏览器打开 https://${DOMAIN}/install 创建管理员账号（谁先打开谁就是管理员，尽快完成）"
