// 每日运维英语：每天早上从 words.json 里按顺序取 5 个运维常用词，连同音标（美式）和例句推送（优先 Telegram，其次企业微信）。
// 100 个词 20 天一轮，按日期计算当天是哪一组，不需要保存任何状态；和周报一样复用告警转发器的发送函数。
import { readFileSync } from 'node:fs';
import { channel, isMain } from './relay.mjs';

export const PER_DAY = 5;
const TZ = 'America/Los_Angeles';

/** 某个时区里的日期（YYYY-MM-DD） */
export function localDate(now, timeZone = TZ) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

/** 当天是第几组（从 0 开始），按当地日期计算，同一天多次运行结果相同 */
export function groupIndex(now, total, timeZone = TZ) {
  const groups = Math.ceil(total / PER_DAY);
  const days = Math.floor(Date.parse(`${localDate(now, timeZone)}T00:00:00Z`) / 86_400_000);
  return days % groups;
}

/** @param {{ word: string, ipa: string, zh: string, example: string, exampleZh: string }[]} words */
export function buildMessage(words, now) {
  const groups = Math.ceil(words.length / PER_DAY);
  const g = groupIndex(now, words.length);
  const today = words.slice(g * PER_DAY, g * PER_DAY + PER_DAY);
  const lines = [`## 📘 每日运维英语 ${localDate(now).slice(5)}（第 ${g + 1}/${groups} 组）`];
  for (const w of today) {
    lines.push(
      '',
      `**${w.word}**  ${w.ipa}  ${w.zh}`,
      `> ${w.example}`,
      `> <font color="comment">${w.exampleZh}</font>`,
    );
  }
  lines.push('', '<font color="comment">读出声来，再试着用每个词说一句自己工作里的话。</font>');
  return lines.join('\n');
}

if (isMain(process.argv[1], import.meta.url)) {
  const ch = channel();
  const words = JSON.parse(readFileSync(new URL('./words.json', import.meta.url), 'utf8'));
  const msg = buildMessage(words, new Date());
  if (process.env.DRY_RUN || !ch) {
    console.log(msg);
    if (!ch && !process.env.DRY_RUN) process.exit(1);
  } else {
    ch.send(msg)
      .then(() => console.log(`已发送（${ch.name}）`))
      .catch((err) => {
        console.error('发送失败：', err.message);
        process.exit(1);
      });
  }
}
