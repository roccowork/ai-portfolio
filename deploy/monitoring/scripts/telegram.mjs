// 定时推送（周报、每日运维英语、R2 用量）共用的发送函数：只发 Telegram（2026-11-22 起不再用企业微信）。
// 告警不走这里：Alertmanager 直接交给 ai-api，它诊断得了的推诊断，诊断不了的转成消息推出去（ai/app/alerts.py）。
// 消息按简单的 markdown 写（## 标题、**加粗**、> 引用、[文字](链接)），发之前转成 Telegram 支持的 HTML。
// 只用 Node 自带的库，没有任何第三方依赖。
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/** Telegram 一条消息最多 4096 字；按字节截断更保守 */
export const MAX_BYTES = 4096;

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
  const suffix = '\n\n…（内容过长已截断，请到总控台查看）';
  let out = s;
  while (bytes(out) + bytes(suffix) > max) out = out.slice(0, -1);
  return out + suffix;
}

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/**
 * 简单 markdown → Telegram HTML，只处理本仓库消息里用到的写法：
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

/** Telegram 推送通道（TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID）；没配返回 null */
export function channel(env = process.env) {
  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    return {
      name: 'telegram',
      send: (c) => sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, c),
    };
  }
  return null;
}

/**
 * 总控台「推送」页的开关（只有一套，存在私人助理那边）：这个定时推送有没有被关掉。
 * 查不到（私人助理不在、超时）就照常发，免得开关坏了把推送全停了
 */
export async function pushEnabled(key, env = process.env) {
  const base = env.PUSH_SWITCH_URL || 'http://assistant.ops-desk.svc/internal/pushes';
  try {
    const res = await fetch(`${base}/${encodeURIComponent(key)}`, {
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return true;
    return (await res.json()).enabled !== false;
  } catch {
    return true;
  }
}

/** 用当前配置的通道发一条消息，返回通道名 */
export async function notify(content, env = process.env) {
  const ch = channel(env);
  if (!ch) throw new Error('没有配置推送通道：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID');
  await ch.send(content);
  return ch.name;
}

/**
 * 是否是被直接运行（而不是被单元测试导入）。
 * 两边都要解析成真实路径再比较：K8s 把 ConfigMap 挂载成符号链接（/app/telegram.mjs → /app/..data/telegram.mjs），
 * Node 加载时会解析成真实路径，直接比较会对不上，导致服务不启动、进程直接退出且没有任何日志。
 */
export function isMain(scriptPath = process.argv[1], moduleUrl = import.meta.url) {
  try {
    return realpathSync(scriptPath) === realpathSync(fileURLToPath(moduleUrl));
  } catch {
    return false;
  }
}
