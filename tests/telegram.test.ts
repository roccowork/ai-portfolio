import { describe, expect, it } from 'vitest';
import {
  channel,
  formatTime,
  isMain,
  pushEnabled,
  toTelegramHtml,
  truncate,
} from '../deploy/monitoring/scripts/telegram.mjs';

describe('推送通道', () => {
  it('简单 markdown 转成 Telegram HTML', () => {
    const html = toTelegramHtml(
      [
        '## 🟠 告警 <x>',
        '**站点** & 证书',
        '> 状态：<font color="warning">告警中</font>',
        '[查看实况](https://ops.liuxd.dev/live/?a=1&b)',
      ].join('\n'),
    );
    expect(html).toBe(
      [
        '<b>🟠 告警 &lt;x&gt;</b>',
        '<b>站点</b> &amp; 证书',
        '┃ 状态：告警中',
        '<a href="https://ops.liuxd.dev/live/?a=1&amp;b">查看实况</a>',
      ].join('\n'),
    );
  });

  it('只发 Telegram（不再有企业微信），没配返回 null', () => {
    expect(channel({ TELEGRAM_BOT_TOKEN: 't', TELEGRAM_CHAT_ID: '1' })?.name).toBe('telegram');
    expect(channel({ TELEGRAM_BOT_TOKEN: 't', WECOM_KEY: 'k' })).toBeNull();
    expect(channel({})).toBeNull();
  });
});

describe('formatTime', () => {
  it('Alertmanager 用 0001-01-01 表示没有结束时间', () => {
    expect(formatTime('0001-01-01T00:00:00Z')).toBe('-');
    expect(formatTime('not a date')).toBe('-');
  });
});

describe('isMain', () => {
  it('通过符号链接目录运行时也能识别为直接运行（K8s ConfigMap 的挂载方式）', async () => {
    const { mkdtempSync, mkdirSync, writeFileSync, symlinkSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const { pathToFileURL } = await import('node:url');
    const root = mkdtempSync(join(tmpdir(), 'notify-'));
    mkdirSync(join(root, 'real'));
    writeFileSync(join(root, 'real', 'telegram.mjs'), '');
    symlinkSync(join(root, 'real'), join(root, 'link'), 'junction'); // 模拟 /app/..data
    const moduleUrl = pathToFileURL(join(root, 'real', 'telegram.mjs')).href;
    expect(isMain(join(root, 'link', 'telegram.mjs'), moduleUrl)).toBe(true);
    expect(isMain(join(root, 'real', 'other.mjs'), moduleUrl)).toBe(false);
    expect(isMain(undefined, moduleUrl)).toBe(false);
  });
});

describe('truncate', () => {
  it('不截断在中文字符中间', () => {
    const s = '中'.repeat(2000); // 6000 字节
    const out = truncate(s, 100);
    expect(Buffer.byteLength(out, 'utf8')).toBeLessThanOrEqual(100);
    expect(out).not.toContain('�');
  });
});

describe('总控台「推送」页的开关', () => {
  it('关掉了就不发；查不到照常发', async () => {
    const real = globalThis.fetch;
    try {
      globalThis.fetch = (async () =>
        new Response(JSON.stringify({ enabled: false }))) as typeof fetch;
      expect(await pushEnabled('weekly-report', {})).toBe(false);
      globalThis.fetch = (async () =>
        new Response(JSON.stringify({ enabled: true }))) as typeof fetch;
      expect(await pushEnabled('weekly-report', {})).toBe(true);
      globalThis.fetch = (async () => {
        throw new Error('down');
      }) as typeof fetch;
      expect(await pushEnabled('weekly-report', {})).toBe(true);
    } finally {
      globalThis.fetch = real;
    }
  });
});
