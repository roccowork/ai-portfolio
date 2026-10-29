import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import { buildReport, collect, query } from '../deploy/monitoring/wecom-relay/weekly-report.mjs';
import { MAX_BYTES } from '../deploy/monitoring/wecom-relay/relay.mjs';

const now = new Date('2026-10-26T01:00:00Z'); // 新加坡 10-26 09:00（周一）= 洛杉矶 10-25 18:00
const row = (labels: Record<string, string>, value: number) => ({ labels, value });

const healthy = {
  requests: [row({}, 12345)],
  errors5xx: [row({}, 3)],
  p95: [row({}, 0.042)],
  minReplicas: [row({}, 1)],
  alerts: [],
  backups: [
    row({ cronjob: 'backup-cloudreve' }, 5 * 3600),
    row({ cronjob: 'backup-uptime-kuma' }, 6 * 3600),
  ],
  certs: [row({ name: 'ops-desk-tls' }, 60)],
  memAvg: [row({ instance: '10.0.0.5:9100' }, 61.2)],
  memMax: [row({ instance: '10.0.0.5:9100' }, 78.9)],
  disk: [row({ instance: '10.0.0.5:9100' }, 42.4)],
  restarts: [],
};

describe('buildReport', () => {
  it('一切正常：标题是新加坡时间的日期范围，各节都有内容', () => {
    const msg = buildReport(healthy, now);
    expect(msg).toContain('## 📊 运维周报 10-19 ~ 10-26');
    expect(msg).toContain('请求 12,345 次 · 5xx 3 次（0.02%）');
    expect(msg).toContain('p95 响应 42 ms');
    expect(msg).toContain('可用副本始终 ≥ 1');
    expect(msg).toContain('本周没有告警 ✅');
    expect(msg).toContain('✅ backup-cloudreve 最近成功：5 小时前');
    expect(msg).toContain('ops-desk-tls 剩余 60 天');
    expect(msg).toContain('10.0.0.5：内存平均 61%、峰值 79% · 磁盘 42%');
    expect(msg).toContain('本周没有容器重启 ✅');
    expect(msg).not.toContain('warning');
  });

  it('有问题时标黄：告警按持续时间排序、备份超过 26 小时、证书 21 天内到期、副本曾为 0', () => {
    const msg = buildReport(
      {
        ...healthy,
        minReplicas: [row({}, 0)],
        alerts: [
          row({ alertname: 'NodeDiskHigh', severity: 'warning' }, 3), // 3 个点 × 5 分钟
          row({ alertname: 'OpsDeskNoReplicas', severity: 'critical' }, 24), // 2 小时
        ],
        backups: [row({ cronjob: 'backup-cloudreve' }, 30 * 3600)],
        certs: [row({ name: 'grafana-tls' }, 10)],
        restarts: [row({ namespace: 'monitoring', pod: 'wecom-relay-abc' }, 4)],
      },
      now,
    );
    expect(msg).toContain('可用副本为 0');
    expect(msg).toMatch(
      /OpsDeskNoReplicas（critical）约 2 小时[\s\S]*NodeDiskHigh（warning）约 15 分钟/,
    );
    expect(msg).toContain('⚠️ backup-cloudreve 最近成功：30 小时前');
    expect(msg).toContain('⚠️ grafana-tls 剩余 10 天');
    expect(msg).toContain('monitoring/wecom-relay-abc：4 次');
  });

  it('单条查询失败只影响对应的一节', () => {
    const msg = buildReport({ ...healthy, backups: null, alerts: null }, now);
    expect(msg).toMatch(/\*\*告警\*\*\n> <font color="warning">查询失败/);
    expect(msg).toMatch(/\*\*备份\*\*\n> <font color="warning">查询失败/);
    expect(msg).toContain('请求 12,345 次');
  });

  it('没有访问量时比例是 0，p95 是 NaN 时显示 -', () => {
    const msg = buildReport({ ...healthy, requests: [], errors5xx: [], p95: [row({}, NaN)] }, now);
    expect(msg).toContain('请求 0 次 · 5xx 0 次（0%）');
    expect(msg).toContain('p95 响应 -');
  });

  it('内容很多时不超过企业微信的长度上限', () => {
    const alerts = Array.from({ length: 300 }, (_, i) =>
      row({ alertname: `VeryLongAlertName${i}`, severity: 'warning' }, i),
    );
    const msg = buildReport({ ...healthy, alerts }, now);
    expect(Buffer.byteLength(msg, 'utf8')).toBeLessThanOrEqual(MAX_BYTES);
  });
});

describe('查询 Prometheus（用假的 Prometheus 服务器）', () => {
  it('解析即时查询结果；单条报错不影响其他；全部失败时抛异常', async () => {
    let fail = false;
    const fake = createServer((req, res) => {
      const q = new URL(req.url!, 'http://x').searchParams.get('query');
      if (fail || q === 'bad') {
        res.writeHead(400).end(JSON.stringify({ status: 'error', error: 'parse error' }));
        return;
      }
      res.end(
        JSON.stringify({
          status: 'success',
          data: { resultType: 'vector', result: [{ metric: { job: 'x' }, value: [0, '42.5'] }] },
        }),
      );
    });
    await new Promise<void>((r) => fake.listen(0, r));
    const base = `http://127.0.0.1:${(fake.address() as { port: number }).port}`;

    expect(await query(base, 'up')).toEqual([{ labels: { job: 'x' }, value: 42.5 }]);
    await expect(query(base, 'bad')).rejects.toThrow('parse error');

    const data = await collect(base, { good: 'up', broken: 'bad' });
    expect(data.good).toHaveLength(1);
    expect(data.broken).toBeNull();

    fail = true;
    await expect(collect(base, { a: 'up', b: 'up' })).rejects.toThrow('所有查询都失败');
    fake.close();
  });
});
