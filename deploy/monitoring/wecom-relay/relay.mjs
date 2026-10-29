// Alertmanager → 手机 的告警转发器（运维 Agent 会诊断的告警不重复发，见 diagnosedByAgent），也是周报、每日英语、R2 月报共用的推送函数。
// 推送通道：配置了 Telegram 机器人（TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID）就发 Telegram，否则发企业微信群机器人（WECOM_KEY）。
// 消息统一按企业微信 markdown 写，发 Telegram 时转成它支持的 HTML。
// 只用 Node 自带的库，没有任何第三方依赖。
import { realpathSync } from 'node:fs';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

/** 企业微信 markdown 消息内容上限 4096 字节 */
export const MAX_BYTES = 4096;

const SEVERITY = {
  critical: { icon: '🔴', label: '严重' },
  warning: { icon: '🟠', label: '警告' },
  info: { icon: '🔵', label: '提示' },
};

function parts(d, timeZone) {
  return Object.fromEntries(
    new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
      .formatToParts(d)
      .map((x) => [x.type, x.value]),
  );
}

/** 新加坡日期 YYYY-MM-DD */
export function formatDate(iso) {
  const p = parts(new Date(iso), 'Asia/Singapore');
  return `${p.year}-${p.month}-${p.day}`;
}

/** 新加坡时间，如 2026-10-11 15:30 */
export function formatTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime()) || d.getFullYear() < 2000) return '-';
  const sg = parts(d, 'Asia/Singapore');
  return `${sg.year}-${sg.month}-${sg.day} ${sg.hour}:${sg.minute}`;
}

function bytes(s) {
  return Buffer.byteLength(s, 'utf8');
}

/** 按字节截断，保证不超过上限，并且不截断在多字节字符中间 */
export function truncate(s, max = MAX_BYTES) {
  if (bytes(s) <= max) return s;
  const suffix = '\n\n…（内容过长已截断，请到 Grafana 查看）';
  let out = s;
  while (bytes(out) + bytes(suffix) > max) out = out.slice(0, -1);
  return out + suffix;
}

/**
 * 运维 Agent（ai-api 的自愈）会给这条告警发诊断吗？会的话转发器就不再单独发原始告警，免得一条告警推两次。
 * 和 Alertmanager 里 autoheal 路由的条件一致（deploy/platform/kube-prometheus-stack.yaml），另外：
 * ai-api 自己出问题时它没法诊断，这类告警照常由转发器发。
 */
const NOT_DIAGNOSED = new Set([
  'Watchdog',
  'InfoInhibitor',
  'AIDailyBudgetReached',
  'AIModelErrors',
]);
export function diagnosedByAgent(alert) {
  const l = alert?.labels ?? {};
  if (!['warning', 'critical'].includes(l.severity) || NOT_DIAGNOSED.has(l.alertname)) return false;
  const aboutAiApi =
    l.namespace === 'ops-desk' &&
    [l.deployment, l.pod, l.job_name, l.container].some(
      (v) => v === 'ai-api' || String(v ?? '').startsWith('ai-api-'),
    );
  return !aboutAiApi;
}

/** SKIP_DIAGNOSED=1 时只留运维 Agent 不会诊断的告警；没有剩下的就返回 null（不发） */
export function filterPayload(payload, env = process.env) {
  if (env.SKIP_DIAGNOSED !== '1' || !Array.isArray(payload?.alerts)) return payload;
  const alerts = payload.alerts.filter((a) => !diagnosedByAgent(a));
  return alerts.length ? { ...payload, alerts } : null;
}

/**
 * 把 Alertmanager webhook（version 4）的内容转成企业微信 markdown。
 * 一次通知可能包含多条告警（同一分组），合并成一条消息。
 */
export function formatMessage(payload) {
  const alerts = Array.isArray(payload?.alerts) ? payload.alerts : [];
  if (!alerts.length) return null;
  const firing = alerts.filter((a) => a.status === 'firing');
  const resolved = alerts.filter((a) => a.status === 'resolved');
  const name = payload.commonLabels?.alertname ?? alerts[0].labels?.alertname ?? '告警';

  const head =
    firing.length > 0
      ? `## ${SEVERITY[payload.commonLabels?.severity]?.icon ?? '🔴'} ${name}：${firing.length} 条告警中`
      : `## ✅ ${name}：已恢复`;
  const parts = [head];

  for (const a of [...firing, ...resolved]) {
    const sev = SEVERITY[a.labels?.severity] ?? SEVERITY.warning;
    const isFiring = a.status === 'firing';
    const state = isFiring
      ? `<font color="warning">${sev.label} · 告警中</font>`
      : '<font color="info">已恢复</font>';
    const lines = [
      `**${a.annotations?.summary ?? a.labels?.alertname ?? '未命名告警'}**`,
      `> 状态：${state}`,
    ];
    if (a.annotations?.description) lines.push(`> 详情：${a.annotations.description}`);
    // kube-state-metrics 产生的告警（Job、Deployment 等）里 pod 是 kube-state-metrics 自己，要先取真正出问题的资源名
    const l = a.labels ?? {};
    const name = l.job_name ?? l.cronjob ?? l.deployment ?? l.statefulset ?? l.daemonset ?? l.pod;
    const target = l.namespace ? [l.namespace, name].filter(Boolean).join('/') : l.instance;
    if (target) lines.push(`> 对象：${target}`);
    lines.push(`> 开始：${formatTime(a.startsAt)}`);
    if (!isFiring) lines.push(`> 恢复：${formatTime(a.endsAt)}`);
    parts.push(lines.join('\n'));
  }
  return truncate(parts.join('\n\n'));
}

/** 发送 markdown 消息到企业微信群机器人；周报（weekly-report.mjs）也用它 */
export async function send(webhookUrl, content) {
  const res = await fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ msgtype: 'markdown', markdown: { content } }),
    signal: AbortSignal.timeout(10_000),
  });
  const body = await res.json().catch(() => ({}));
  // 企业微信即使出错也返回 200，要看 errcode
  if (!res.ok || body.errcode !== 0) {
    throw new Error(
      `企业微信返回 HTTP ${res.status}，errcode=${body.errcode}，${body.errmsg ?? ''}`,
    );
  }
}

/** 企业微信群机器人的 webhook 地址 */
export const wecomUrl = (key) =>
  `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${encodeURIComponent(key)}`;

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/**
 * 企业微信 markdown → Telegram HTML，只处理本仓库消息里用到的写法：
 * ## 标题、**加粗**、> 引用、[文字](链接)、<font color>（Telegram 不支持颜色，只保留文字）
 */
export function toTelegramHtml(md) {
  return md
    .split('\n')
    .map((raw) => {
      const quote = raw.startsWith('> ');
      let line = quote ? raw.slice(2) : raw;
      line = escapeHtml(line.replace(/<font[^>]*>(.*?)<\/font>/g, '$1'));
      const heading = line.match(/^#{1,6} (.*)$/);
      if (heading) line = `<b>${heading[1]}</b>`;
      line = line.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      line = line.replace(
        /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
        (_, text, url) => `<a href="${url.replace(/"/g, '&quot;')}">${text}</a>`,
      );
      return quote ? `┃ ${line}` : line;
    })
    .join('\n');
}

/** 发到 Telegram 机器人和你的私聊；extra 可以带按钮（reply_markup） */
export async function sendTelegram(token, chatId, content, extra = {}) {
  const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: chatId,
      text: toTelegramHtml(content),
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      ...extra,
    }),
    signal: AbortSignal.timeout(10_000),
  });
  const body = await res.json().catch(() => ({}));
  if (!body.ok) throw new Error(`Telegram 返回 HTTP ${res.status}：${body.description ?? ''}`);
}

/** 按环境变量选推送通道：Telegram 优先，没配就用企业微信；都没配返回 null */
export function channel(env = process.env) {
  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    return {
      name: 'telegram',
      send: (c) => sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, c),
    };
  }
  if (env.WECOM_KEY) return { name: 'wecom', send: (c) => send(wecomUrl(env.WECOM_KEY), c) };
  return null;
}

/** 用当前配置的通道发一条消息，返回通道名 */
export async function notify(content, env = process.env) {
  const ch = channel(env);
  if (!ch) throw new Error('没有配置推送通道：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID，或 WECOM_KEY');
  await ch.send(content);
  return ch.name;
}

export function startServer({ port = 8080, webhookUrl, deliver = (c) => send(webhookUrl, c) }) {
  const server = createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/healthz') {
      res.writeHead(200).end('ok\n');
      return;
    }
    if (req.method !== 'POST' || req.url !== '/alert') {
      res.writeHead(404).end();
      return;
    }
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
      if (raw.length > 1_000_000) req.destroy(); // 防止异常的超大请求
    });
    req.on('end', async () => {
      try {
        const payload = filterPayload(JSON.parse(raw));
        const content = payload && formatMessage(payload);
        if (content) await deliver(content);
        res.writeHead(200).end('ok\n');
      } catch (err) {
        // 返回 500，Alertmanager 会按自己的策略重试
        console.error(new Date().toISOString(), '转发失败：', err.message);
        res.writeHead(500).end('error\n');
      }
    });
  });
  server.listen(port, () => console.log(`告警转发器监听 :${port}`));
  // 收到 SIGTERM（Pod 删除）时停止接收新请求，处理完已有请求再退出
  process.on('SIGTERM', () => server.close(() => process.exit(0)));
  return server;
}

/**
 * 是否是被直接运行（而不是被单元测试导入）。
 * 两边都要解析成真实路径再比较：K8s 把 ConfigMap 挂载成符号链接（/app/relay.mjs → /app/..data/relay.mjs），
 * Node 加载时会解析成真实路径，直接比较会对不上，导致服务不启动、进程直接退出且没有任何日志。
 */
export function isMain(scriptPath = process.argv[1], moduleUrl = import.meta.url) {
  try {
    return realpathSync(scriptPath) === realpathSync(fileURLToPath(moduleUrl));
  } catch {
    return false;
  }
}

// 只有直接运行时才启动服务，单元测试导入时不启动
if (isMain()) {
  const ch = channel();
  if (!ch) {
    console.error('没有配置推送通道：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID，或 WECOM_KEY');
    process.exit(1);
  }
  console.log(`推送通道：${ch.name}`);
  startServer({ port: Number(process.env.PORT ?? 8080), deliver: ch.send });
}
