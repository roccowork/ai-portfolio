import { describe, expect, it } from 'vitest';
import { ago, isStale, summarize, type Status } from '../src/lib/status';

const now = new Date('2026-10-24T08:00:00Z');
const full: Status = {
  generatedAt: '2026-10-24T07:55:00Z',
  deploys: [
    { time: '2026-10-24T05:00:00Z', sha: 'abc1234', state: 'current' },
    { time: '2026-10-23T05:00:00Z', sha: 'def5678', state: 'replaced' },
  ],
  availability7d: 99.98,
  p95Ms: 42,
  requests7d: 1234,
  backups: [
    { name: 'backup-cloudreve', lastSuccess: '2026-10-23T19:00:00Z' },
    { name: 'backup-goatcounter', lastSuccess: '2026-10-20T19:00:00Z' },
  ],
  certs: [
    { name: 'ops-desk-tls', daysLeft: 61 },
    { name: 'stats-tls', daysLeft: 45 },
  ],
  alerts7d: [],
  errors: [],
};

describe('ago', () => {
  it('按分钟、小时、天显示', () => {
    expect(ago('2026-10-24T07:59:40Z', now)).toBe('刚刚');
    expect(ago('2026-10-24T07:55:00Z', now)).toBe('5 分钟前');
    expect(ago('2026-10-24T05:00:00Z', now)).toBe('3 小时前');
    expect(ago('2026-10-20T08:00:00Z', now)).toBe('4 天前');
    expect(ago(null, now)).toBe('未知');
    expect(ago('坏数据', now)).toBe('未知');
  });
});

describe('isStale', () => {
  it('超过 1 小时算过期，缺失或无法解析也算', () => {
    expect(isStale('2026-10-24T07:30:00Z', now)).toBe(false);
    expect(isStale('2026-10-24T06:30:00Z', now)).toBe(true);
    expect(isStale(null, now)).toBe(true);
  });
});

describe('summarize', () => {
  it('汇总各项', () => {
    expect(summarize(full, now)).toBe(
      '最近发布 3 小时前（abc1234） · 7 天可用率 99.98% · 备份 1/2 正常 · 证书最少剩 45 天 · 7 天无告警 · 数据更新于 5 分钟前',
    );
  });
  it('查询失败的项直接跳过', () => {
    const s: Status = {
      ...full,
      deploys: null,
      availability7d: null,
      backups: null,
      certs: null,
      alerts7d: null,
    };
    expect(summarize(s, now)).toBe('数据更新于 5 分钟前');
  });
});
