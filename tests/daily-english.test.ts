import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  buildMessage,
  groupIndex,
  localDate,
  PER_DAY,
} from '../deploy/monitoring/wecom-relay/daily-english.mjs';
import { MAX_BYTES } from '../deploy/monitoring/wecom-relay/relay.mjs';

const words = JSON.parse(readFileSync('deploy/monitoring/wecom-relay/words.json', 'utf8')) as Array<
  Record<'word' | 'ipa' | 'zh' | 'example' | 'exampleZh', string>
>;

describe('words.json', () => {
  it('每条都有五个字段、音标用 / / 包起来，单词不重复，数量是 5 的倍数', () => {
    for (const w of words)
      for (const k of ['word', 'ipa', 'zh', 'example', 'exampleZh'] as const)
        expect(w[k], w.word).toBeTruthy();
    for (const w of words) expect(w.ipa, w.word).toMatch(/^\/[^/]+\/$/);
    expect(new Set(words.map((w) => w.word.toLowerCase())).size).toBe(words.length);
    expect(words.length % PER_DAY).toBe(0);
  });
});

describe('daily english', () => {
  it('按洛杉矶日期计算：UTC 已是第二天、洛杉矶还是当天时，组号不变', () => {
    const a = new Date('2026-10-24T15:00:00Z'); // 洛杉矶 10-24 08:00
    const b = new Date('2026-10-25T05:00:00Z'); // 洛杉矶 10-24 22:00
    expect(localDate(b)).toBe('2026-10-24');
    expect(groupIndex(a, 100)).toBe(groupIndex(b, 100));
  });
  it('连续的日子轮流取每一组，一轮覆盖全部单词', () => {
    const seen = new Set<number>();
    for (let d = 0; d < 20; d++) seen.add(groupIndex(new Date(Date.UTC(2026, 9, 1 + d, 20)), 100));
    expect(seen.size).toBe(20);
  });
  it('每条消息 5 个词，不超过企业微信上限', () => {
    for (let d = 0; d < 20; d++) {
      const msg = buildMessage(words, new Date(Date.UTC(2026, 9, 1 + d, 20)));
      expect(msg.match(/^\*\*/gm)).toHaveLength(5);
      expect(Buffer.byteLength(msg)).toBeLessThan(MAX_BYTES);
    }
  });
});
