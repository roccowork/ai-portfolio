// 运维周报：每周一从 Prometheus 查询过去 7 天的数据，汇总成一条 markdown 发到企业微信群（和告警同一个群机器人）。
// 告警只在出问题时响；周报回答「这一周整体怎么样」：访问量、错误率、告警、备份、证书、资源、重启。
// 和告警转发器放在同一个目录、同一个 ConfigMap 里，复用它的发送和格式化函数；同样只用 Node 自带的库。
import { formatDate, formatTime, isMain, notify, truncate } from './relay.mjs';

const SITE = 'ops-desk-ops-desk-http@kubernetes'; // Traefik 给站点 Service 起的名字，和告警规则一致
const RANGE = '7d'; // Prometheus 只保留 7 天，周报正好覆盖全部数据
const ALERT_STEP_MIN = 5; // 告警持续时间按 5 分钟一个点估算

const DISK = `(1 - node_filesystem_avail_bytes{mountpoint="/", fstype!~"tmpfs|overlay"} / node_filesystem_size_bytes{mountpoint="/", fstype!~"tmpfs|overlay"}) * 100`;
const MEM = '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100';

/** 周报用到的全部 PromQL，键名就是 buildReport 里 data 的键名 */
export const QUERIES = {
  requests: `sum(increase(traefik_service_requests_total{service="${SITE}"}[${RANGE}]))`,
  errors5xx: `sum(increase(traefik_service_requests_total{service="${SITE}", code=~"5.."}[${RANGE}]))`,
  p95: `histogram_quantile(0.95, sum by (le) (increase(traefik_service_request_duration_seconds_bucket{service="${SITE}"}[${RANGE}])))`,
  minReplicas: `min_over_time(kube_deployment_status_replicas_available{namespace="ops-desk", deployment="ops-desk"}[${RANGE}])`,
  alerts: `sum by (alertname, severity) (count_over_time(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}[${RANGE}:${ALERT_STEP_MIN}m]))`,
  backups: 'time() - kube_cronjob_status_last_successful_time{cronjob=~"backup-.*"}',
  certs: '(certmanager_certificate_expiration_timestamp_seconds - time()) / 86400',
  memAvg: `avg by (instance) (avg_over_time((${MEM})[${RANGE}:5m]))`,
  memMax: `max by (instance) (max_over_time((${MEM})[${RANGE}:5m]))`,
  disk: `max by (instance) (${DISK})`,
  restarts: `topk(5, sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total[${RANGE}])) > 0)`,
};

/** 执行一条即时查询，返回 [{ labels, value }]。Prometheus 返回错误时抛异常 */
export async function query(baseUrl, expr) {
  const url = new URL('/api/v1/query', baseUrl);
  url.searchParams.set('query', expr);
  const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.status !== 'success') {
    throw new Error(`Prometheus 返回 HTTP ${res.status}：${body.error ?? '无法解析响应'}`);
  }
  return body.data.result.map((r) => ({ labels: r.metric, value: Number(r.value[1]) }));
}

/** @typedef {{ labels: Record<string, string>, value: number }} Row */

/**
 * 逐条查询。单条失败只影响周报里对应的一节（显示「查询失败」）；全部失败说明 Prometheus 不可用，抛异常让 Job 失败。
 * @param {string} baseUrl
 * @param {Record<string, string>} [queries]
 * @returns {Promise<Record<string, Row[] | null>>}
 */
export async function collect(baseUrl, queries = QUERIES) {
  /** @type {Record<string, Row[] | null>} */
  const data = {};
  const failed = [];
  for (const [key, expr] of Object.entries(queries)) {
    try {
      data[key] = await query(baseUrl, expr);
    } catch (err) {
      data[key] = null;
      failed.push(`${key}：${err.message}`);
    }
  }
  if (failed.length === Object.keys(queries).length) {
    throw new Error(`所有查询都失败了，Prometheus 可能不可用。${failed[0]}`);
  }
  for (const f of failed) console.error('查询失败', f);
  return data;
}

const first = (rows) => (rows && rows.length ? rows[0].value : null);
const num = (v, digits = 0) =>
  v === null || !Number.isFinite(v)
    ? '-'
    : v.toLocaleString('zh-CN', { maximumFractionDigits: digits });
const host = (labels) => (labels.instance ?? '').replace(/:\d+$/, '') || '节点';
const FAILED = '> <font color="warning">查询失败</font>';

/** 按新加坡时间输出 MM-DD */
function day(date) {
  return formatDate(date.toISOString()).slice(5);
}

/** 把查询结果拼成企业微信 markdown。data 的值为 null 表示这条查询失败 */
export function buildReport(data, now = new Date()) {
  const start = new Date(now.getTime() - 7 * 86400_000);
  const parts = [`## 📊 运维周报 ${day(start)} ~ ${day(now)}`];

  // 站点
  if (data.requests === null) {
    parts.push(`**站点**\n${FAILED}`);
  } else {
    const total = first(data.requests) ?? 0;
    const errors = first(data.errors5xx) ?? 0;
    const ratio = total > 0 ? (errors / total) * 100 : 0;
    const p95 = first(data.p95);
    const minReplicas = first(data.minReplicas);
    const lines = [
      `> 请求 ${num(total)} 次 · 5xx ${num(errors)} 次（${num(ratio, 2)}%）`,
      `> p95 响应 ${p95 === null || Number.isNaN(p95) ? '-' : `${num(p95 * 1000)} ms`}`,
    ];
    if (minReplicas !== null) {
      lines.push(
        minReplicas >= 1
          ? '> 可用副本始终 ≥ 1'
          : '> <font color="warning">本周出现过可用副本为 0（站点不可访问）</font>',
      );
    }
    parts.push(`**站点**\n${lines.join('\n')}`);
  }

  // 告警
  if (data.alerts === null) {
    parts.push(`**告警**\n${FAILED}`);
  } else if (!data.alerts.length) {
    parts.push('**告警**\n> 本周没有告警 ✅');
  } else {
    const rows = [...data.alerts]
      .sort((a, b) => b.value - a.value)
      .map((r) => {
        const minutes = r.value * ALERT_STEP_MIN;
        const span = minutes >= 60 ? `${num(minutes / 60, 1)} 小时` : `${num(minutes)} 分钟`;
        return `> ${r.labels.alertname}（${r.labels.severity ?? '-'}）约 ${span}`;
      });
    parts.push(`**告警**（${rows.length} 种）\n${rows.join('\n')}`);
  }

  // 备份：超过 26 小时没成功和告警规则 BackupStale 的阈值一致
  if (data.backups === null) {
    parts.push(`**备份**\n${FAILED}`);
  } else if (!data.backups.length) {
    parts.push('**备份**\n> <font color="warning">没有找到备份任务的记录</font>');
  } else {
    const rows = [...data.backups]
      .sort((a, b) => a.labels.cronjob.localeCompare(b.labels.cronjob))
      .map((r) => {
        const hours = r.value / 3600;
        const ok = hours <= 26;
        const text = `${r.labels.cronjob} 最近成功：${num(hours, 1)} 小时前`;
        return ok ? `> ✅ ${text}` : `> <font color="warning">⚠️ ${text}</font>`;
      });
    parts.push(`**备份**\n${rows.join('\n')}`);
  }

  // 证书：21 天内到期和告警规则 CertificateExpiringSoon 一致
  if (data.certs === null) {
    parts.push(`**证书**\n${FAILED}`);
  } else if (data.certs.length) {
    const rows = [...data.certs]
      .sort((a, b) => a.value - b.value)
      .map((r) => {
        const text = `${r.labels.name ?? r.labels.exported_name ?? '证书'} 剩余 ${num(r.value)} 天`;
        return r.value >= 21 ? `> ${text}` : `> <font color="warning">⚠️ ${text}</font>`;
      });
    parts.push(`**证书**\n${rows.join('\n')}`);
  }

  // 节点资源
  if (data.memAvg === null && data.disk === null) {
    parts.push(`**节点**\n${FAILED}`);
  } else {
    const hosts = new Set(
      [...(data.memAvg ?? []), ...(data.disk ?? [])].map((r) => host(r.labels)),
    );
    const pick = (rows, h) => rows?.find((r) => host(r.labels) === h)?.value ?? null;
    const rows = [...hosts].sort().map((h) => {
      const disk = pick(data.disk, h);
      const diskText =
        disk !== null && disk > 80 ? `<font color="warning">${num(disk)}%</font>` : `${num(disk)}%`;
      return `> ${h}：内存平均 ${num(pick(data.memAvg, h))}%、峰值 ${num(pick(data.memMax, h))}% · 磁盘 ${diskText}`;
    });
    parts.push(`**节点**\n${rows.join('\n')}`);
  }

  // 重启
  if (data.restarts === null) {
    parts.push(`**容器重启**\n${FAILED}`);
  } else if (!data.restarts.length) {
    parts.push('**容器重启**\n> 本周没有容器重启 ✅');
  } else {
    const rows = data.restarts.map(
      (r) => `> ${r.labels.namespace}/${r.labels.pod}：${num(Math.round(r.value))} 次`,
    );
    parts.push(`**容器重启**（前 5）\n${rows.join('\n')}`);
  }

  parts.push(
    `<font color="comment">生成于 ${formatTime(now.toISOString())} · 详情见 Grafana</font>`,
  );
  return truncate(parts.join('\n\n'));
}

async function main() {
  const prometheus = process.env.PROMETHEUS_URL ?? 'http://kps-prometheus.monitoring.svc:9090';
  const report = buildReport(await collect(prometheus));
  // DRY_RUN=1：只打印不发送，手动试跑用（见 docs/runbook.md）
  if (process.env.DRY_RUN === '1') {
    console.log(report);
    return;
  }
  console.log(`周报已发送（${await notify(report)}）`);
}

if (isMain(process.argv[1], import.meta.url)) {
  main().catch((err) => {
    console.error(new Date().toISOString(), '周报失败：', err.message);
    process.exit(1);
  });
}
