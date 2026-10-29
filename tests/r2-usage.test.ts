import { describe, expect, it } from 'vitest';
import {
  buildMessage,
  FREE_BYTES,
  shouldSend,
  summarize,
} from '../deploy/monitoring/wecom-relay/r2-usage.mjs';

const GB = 1024 ** 3;

describe('R2 用量报告', () => {
  it('按顶层目录（应用）汇总并按大小排序', () => {
    const s = summarize([
      { Path: 'cloudreve/data/aa', Size: 2 * GB },
      { Path: 'cloudreve/index/bb', Size: 1 * GB },
      { Path: 'ai-api/data/cc', Size: 5 * 1024 ** 2 },
      { Path: 'config', Size: 100 },
    ]);
    expect(s.apps[0]).toEqual(['cloudreve', 3 * GB]);
    expect(s.apps.map((a) => a[0])).toContain('ai-api');
    expect(s.total).toBe(3 * GB + 5 * 1024 ** 2 + 100);
    expect(buildMessage(s, 'monthly')).toContain('离免费额度还远');
  });

  it('月报总是发，每周检查只在超过 80% 时发', () => {
    expect(shouldSend(0.1, 'monthly')).toBe(true);
    expect(shouldSend(0.5, 'check')).toBe(false);
    expect(shouldSend(0.85, 'check')).toBe(true);
    const msg = buildMessage(summarize([{ Path: 'x/y', Size: 1.1 * FREE_BYTES }]), 'check');
    expect(msg).toContain('已超出免费额度');
  });
});
