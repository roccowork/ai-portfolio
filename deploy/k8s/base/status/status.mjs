// 平台实况：每 10 分钟汇总一次本站的发布、备份、证书、可用率、告警，写进 ConfigMap ops-desk-status，
// 站点 Pod 挂载它，nginx 以 /status.json 提供给 /live 页面（SPEC 里程碑 8）。
// 只放汇总数字，不放任何密钥、内部地址和日志。只用 Node 自带的库。
import { readFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SITE = 'ops-desk-ops-desk-http@kubernetes'; // Traefik 给站点 Service 起的名字，和告警规则一致
const SA_DIR = '/var/run/secrets/kubernetes.io/serviceaccount';
export const CONFIGMAP = 'ops-desk-status';

export const QUERIES = {
  availability: `avg_over_time((kube_deployment_status_replicas_available{namespace="ops-desk", deployment="ops-desk"} > bool 0)[7d:5m]) * 100`,
  p95: `histogram_quantile(0.95, sum by (le) (increase(traefik_service_request_duration_seconds_bucket{service="${SITE}"}[7d])))`,
  requests: `sum(increase(traefik_service_requests_total{service="${SITE}"}[7d]))`,
  backups: 'kube_cronjob_status_last_successful_time{cronjob=~"backup-.*"}',
  certs: '(certmanager_certificate_expiration_timestamp_seconds - time()) / 86400',
  alerts: `sum by (alertname, severity) (count_over_time(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}[7d:5m]))`,
};

/** Prometheus 即时查询，返回 [{ labels, value }] */
export async function promQuery(baseUrl, expr) {
  const url = new URL('/api/v1/query', baseUrl);
  url.searchParams.set('query', expr);
  const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.status !== 'success') {
    throw new Error(`Prometheus 返回 HTTP ${res.status}：${body.error ?? '无法解析响应'}`);
  }
  return body.data.result.map((r) => ({ labels: r.metric, value: Number(r.value[1]) }));
}

/** 访问 K8s API（用 Pod 自带的 ServiceAccount 令牌；CA 证书通过 NODE_EXTRA_CA_CERTS 信任） */
export function kubeClient(
  api = 'https://kubernetes.default.svc',
  token = readFileSync(`${SA_DIR}/token`, 'utf8'),
) {
  return async (method, path, body) => {
    const res = await fetch(api + path, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': method === 'PATCH' ? 'application/merge-patch+json' : 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    });
    const data = await res.json().catch(() => ({}));
    return { status: res.status, data };
  };
}

/** 发布历史：站点 Deployment 的 ReplicaSet，每个对应一次发布（镜像标签就是 commit SHA） */
export function deploysFromReplicaSets(items) {
  const list = items
    .map((rs) => ({
      revision: Number(rs.metadata?.annotations?.['deployment.kubernetes.io/revision'] ?? 0),
      time: rs.metadata?.creationTimestamp ?? null,
      sha: (rs.spec?.template?.spec?.containers?.[0]?.image ?? '').split(':').pop() ?? '',
      desired: rs.spec?.replicas ?? 0,
      ready: rs.status?.readyReplicas ?? 0,
    }))
    .sort((a, b) => b.revision - a.revision);
  return list.slice(0, 10).map((d, i) => ({
    time: d.time,
    sha: d.sha.slice(0, 7),
    // 最新的一个：有就绪副本就是当前版本，否则说明新版本卡住了、旧版本还在服务
    state: i > 0 ? 'replaced' : d.ready > 0 ? 'current' : 'not-ready',
  }));
}

const first = (rows) => (rows && rows.length ? rows[0].value : null);
const round = (v, d = 0) => (v === null || !Number.isFinite(v) ? null : Number(v.toFixed(d)));

/** 把各项原始数据汇总成页面用的 JSON。每一项单独容错：某项失败只记在 errors 里 */
export async function buildStatus({ prom, kube, now = new Date() }) {
  const errors = [];
  const safe = async (name, fn, fallback) => {
    try {
      return await fn();
    } catch (err) {
      errors.push(`${name}：${err.message}`);
      return fallback;
    }
  };
  const q = (key) => safe(key, () => promQuery(prom, QUERIES[key]), null);

  const [rs, availability, p95, requests, backups, certs, alerts] = await Promise.all([
    safe(
      '发布历史',
      async () => {
        const r = await kube(
          'GET',
          '/apis/apps/v1/namespaces/ops-desk/replicasets?labelSelector=app.kubernetes.io%2Fname%3Dops-desk',
        );
        if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
        return r.data.items ?? [];
      },
      null,
    ),
    q('availability'),
    q('p95'),
    q('requests'),
    q('backups'),
    q('certs'),
    q('alerts'),
  ]);
  const p95Value = first(p95);
  return {
    generatedAt: now.toISOString(),
    deploys: rs ? deploysFromReplicaSets(rs) : null,
    availability7d: round(first(availability), 2),
    p95Ms: p95Value === null || Number.isNaN(p95Value) ? null : round(p95Value * 1000),
    requests7d: round(first(requests)),
    backups: backups
      ? backups
          .map((b) => ({
            name: b.labels.cronjob,
            lastSuccess: new Date(b.value * 1000).toISOString(),
          }))
          .sort((a, b) => a.name.localeCompare(b.name))
      : null,
    certs: certs
      ? certs
          .map((c) => ({
            name: c.labels.name ?? c.labels.exported_name ?? '证书',
            daysLeft: round(c.value),
          }))
          .sort((a, b) => a.daysLeft - b.daysLeft)
      : null,
    alerts7d: alerts
      ? alerts
          .map((a) => ({
            name: a.labels.alertname,
            severity: a.labels.severity ?? '',
            minutes: a.value * 5,
          }))
          .sort((a, b) => b.minutes - a.minutes)
      : null,
    errors,
  };
}

/** 写进 ConfigMap：先 PATCH，不存在时创建 */
export async function writeConfigMap(kube, namespace, json) {
  const path = `/api/v1/namespaces/${namespace}/configmaps/${CONFIGMAP}`;
  const data = { 'status.json': json };
  let r = await kube('PATCH', path, { data });
  if (r.status === 404) {
    r = await kube('POST', `/api/v1/namespaces/${namespace}/configmaps`, {
      apiVersion: 'v1',
      kind: 'ConfigMap',
      metadata: { name: CONFIGMAP, labels: { 'app.kubernetes.io/name': 'ops-desk-status' } },
      data,
    });
  }
  if (r.status >= 300)
    throw new Error(`写 ConfigMap 失败：HTTP ${r.status} ${r.data?.message ?? ''}`);
}

export function isMain(scriptPath = process.argv[1], moduleUrl = import.meta.url) {
  try {
    return realpathSync(scriptPath) === realpathSync(fileURLToPath(moduleUrl));
  } catch {
    return false;
  }
}

if (isMain()) {
  const prom = process.env.PROMETHEUS_URL ?? 'http://kps-prometheus.monitoring.svc:9090';
  const kube = kubeClient();
  buildStatus({ prom, kube })
    .then(async (status) => {
      // Prometheus 和 K8s API 都不通时不覆盖上一份，页面会显示「数据更新于 X 分钟前」
      if (status.deploys === null && status.availability7d === null && status.backups === null) {
        throw new Error(`所有数据源都不可用：${status.errors.join('；')}`);
      }
      await writeConfigMap(kube, 'ops-desk', JSON.stringify(status));
      console.log(
        `已更新 ${CONFIGMAP}${status.errors.length ? `，部分失败：${status.errors.join('；')}` : ''}`,
      );
    })
    .catch((err) => {
      console.error(new Date().toISOString(), '生成失败：', err.message);
      process.exit(1);
    });
}
