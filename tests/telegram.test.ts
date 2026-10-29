import { describe, expect, it } from 'vitest';
import { channel, toTelegramHtml } from '../deploy/monitoring/wecom-relay/relay.mjs';

describe('推送通道', () => {
  it('企业微信 markdown 转成 Telegram HTML', () => {
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

  it('配置了 Telegram 优先用 Telegram，否则用企业微信，都没有返回 null', () => {
    expect(channel({ TELEGRAM_BOT_TOKEN: 't', TELEGRAM_CHAT_ID: '1', WECOM_KEY: 'k' })?.name).toBe(
      'telegram',
    );
    expect(channel({ TELEGRAM_BOT_TOKEN: 't', WECOM_KEY: 'k' })?.name).toBe('wecom');
    expect(channel({})).toBeNull();
  });
});
