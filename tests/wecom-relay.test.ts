import { describe, expect, it } from 'vitest';
import {
  formatMessage,
  formatTime,
  isMain,
  MAX_BYTES,
  startServer,
  truncate,
} from '../deploy/monitoring/wecom-relay/relay.mjs';

const firing = {
  status: 'firing',
  labels: {
    alertname: 'OpsDeskNoReplicas',
    severity: 'critical',
    namespace: 'ops-desk',
    deployment: 'ops-desk',
  },
  annotations: {
    summary: '站点没有可用副本',
    description: 'ops-desk 可用副本数为 0，网站无法访问',
  },
  startsAt: '2026-10-11T07:30:00Z',
  endsAt: '0001-01-01T00:00:00Z',
};

describe('formatMessage', () => {
  it('告警中：标题、级别、详情、对象、新加坡时间', () => {
    const msg = formatMessage({
      commonLabels: { alertname: 'OpsDeskNoReplicas', severity: 'critical' },
      alerts: [firing],
    });
    expect(msg).toContain('## 🔴 OpsDeskNoReplicas：1 条告警中');
    expect(msg).toContain('**站点没有可用副本**');
    expect(msg).toContain('严重 · 告警中');
    expect(msg).toContain('> 对象：ops-desk/ops-desk');
    expect(msg).toContain('> 开始：2026-10-11 15:30'); // UTC 07:30 = 新加坡 15:30
    expect(msg).not.toContain('恢复：');
  });

  it('已恢复：显示恢复时间', () => {
    const resolved = { ...firing, status: 'resolved', endsAt: '2026-10-11T07:36:00Z' };
    const msg = formatMessage({
      commonLabels: { alertname: 'OpsDeskNoReplicas' },
      alerts: [resolved],
    });
    expect(msg).toContain('## ✅ OpsDeskNoReplicas：已恢复');
    expect(msg).toContain('> 恢复：2026-10-11 15:36');
  });

  it('没有告警时不发消息', () => {
    expect(formatMessage({ alerts: [] })).toBeNull();
    expect(formatMessage(null)).toBeNull();
  });

  it('没有 namespace 时用 instance 作为对象', () => {
    const node = {
      ...firing,
      labels: { alertname: 'NodeDiskHigh', severity: 'warning', instance: '10.0.0.5:9100' },
    };
    expect(formatMessage({ alerts: [node] })).toContain('> 对象：10.0.0.5:9100');
  });

  it('kube-state-metrics 的告警显示出问题的 Job，而不是 kube-state-metrics 的 Pod', () => {
    const job = {
      ...firing,
      labels: {
        alertname: 'KubeJobFailed',
        severity: 'warning',
        namespace: 'ops-desk',
        job_name: 'ops-desk-status-29884050',
        pod: 'kube-prometheus-stack-kube-state-metrics-7b46654d64-xf9db',
      },
    };
    expect(formatMessage({ alerts: [job] })).toContain(
      '> 对象：ops-desk/ops-desk-status-29884050\n',
    );
  });

  it('大量告警时不超过企业微信的长度上限', () => {
    const many = Array.from({ length: 200 }, (_, i) => ({
      ...firing,
      annotations: { summary: `告警 ${i}`, description: '很长的说明'.repeat(10) },
    }));
    const msg = formatMessage({ alerts: many })!;
    expect(Buffer.byteLength(msg, 'utf8')).toBeLessThanOrEqual(MAX_BYTES);
    expect(msg).toContain('已截断');
  });
});

describe('formatTime', () => {
  it('Alertmanager 用 0001-01-01 表示没有结束时间', () => {
    expect(formatTime('0001-01-01T00:00:00Z')).toBe('-');
    expect(formatTime('not a date')).toBe('-');
  });
});

describe('HTTP 转发（用假的企业微信服务器）', () => {
  it('收到 Alertmanager 告警后转发 markdown；企业微信返回 errcode 非 0 时返回 500 让 Alertmanager 重试', async () => {
    const { createServer } = await import('node:http');
    const received: unknown[] = [];
    let errcode = 0;
    const fake = createServer((req, res) => {
      let body = '';
      req.on('data', (c) => (body += c));
      req.on('end', () => {
        received.push(JSON.parse(body));
        res.end(JSON.stringify({ errcode, errmsg: errcode ? 'invalid webhook url' : 'ok' }));
      });
    });
    await new Promise<void>((r) => fake.listen(0, r));
    const fakeUrl = `http://127.0.0.1:${(fake.address() as { port: number }).port}/send`;

    const relay = startServer({ port: 0, webhookUrl: fakeUrl });
    await new Promise((r) => relay.once('listening', r));
    const url = `http://127.0.0.1:${(relay.address() as { port: number }).port}`;
    const post = () =>
      fetch(`${url}/alert`, { method: 'POST', body: JSON.stringify({ alerts: [firing] }) });

    expect((await fetch(`${url}/healthz`)).status).toBe(200);
    expect((await post()).status).toBe(200);
    expect(received[0]).toMatchObject({
      msgtype: 'markdown',
      markdown: { content: expect.stringContaining('站点没有可用副本') },
    });

    errcode = 93000;
    expect((await post()).status).toBe(500);

    relay.close();
    fake.close();
  });
});

describe('isMain', () => {
  it('通过符号链接目录运行时也能识别为直接运行（K8s ConfigMap 的挂载方式）', async () => {
    const { mkdtempSync, mkdirSync, writeFileSync, symlinkSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const { pathToFileURL } = await import('node:url');
    const root = mkdtempSync(join(tmpdir(), 'relay-'));
    mkdirSync(join(root, 'real'));
    writeFileSync(join(root, 'real', 'relay.mjs'), '');
    symlinkSync(join(root, 'real'), join(root, 'link'), 'junction'); // 模拟 /app/..data
    const moduleUrl = pathToFileURL(join(root, 'real', 'relay.mjs')).href;
    expect(isMain(join(root, 'link', 'relay.mjs'), moduleUrl)).toBe(true);
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
