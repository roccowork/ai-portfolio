// R2 用量报告：rclone 先把桶里所有文件列成 JSON（/out/files.json），这里按顶层目录（每个应用一个 restic 仓库）汇总，
// 发到企业微信。monthly 模式每月 1 号必发；check 模式每周跑，超过免费额度的 80% 才发。
// R2 免费额度：存储 10 GB / 月；读写次数（A 类 100 万、B 类 1000 万 / 月）备份用量远低于上限，这里不统计。
import { readFileSync } from 'node:fs';
import { channel, isMain } from './relay.mjs';

export const FREE_BYTES = 10 * 1024 ** 3;
export const WARN_RATIO = 0.8;

const gb = (b) => `${(b / 1024 ** 3).toFixed(2)} GB`;
const human = (b) => (b >= 1024 ** 3 ? gb(b) : `${(b / 1024 ** 2).toFixed(1)} MB`);

/** @param {{ Path: string, Size: number }[]} files */
export function summarize(files) {
  const byApp = {};
  let total = 0;
  for (const f of files) {
    const app = f.Path.includes('/') ? f.Path.split('/')[0] : '(根目录)';
    byApp[app] = (byApp[app] || 0) + f.Size;
    total += f.Size;
  }
  const apps = Object.entries(byApp).sort((a, b) => b[1] - a[1]);
  return { total, apps, ratio: total / FREE_BYTES };
}

export function buildMessage({ total, apps, ratio }, mode) {
  const pct = (ratio * 100).toFixed(1);
  const color = ratio >= WARN_RATIO ? 'warning' : 'info';
  const title = mode === 'monthly' ? '## ☁️ R2 存储月报' : '## ⚠️ R2 存储快到免费额度了';
  const lines = [
    title,
    `已用 <font color="${color}">${gb(total)}</font> / 10 GB 免费额度（${pct}%）`,
    '',
  ];
  for (const [app, size] of apps) lines.push(`- ${app}：${human(size)}`);
  lines.push('');
  if (ratio >= 1) {
    lines.push('<font color="warning">已超出免费额度，超出部分按 $0.015/GB·月 计费。</font>');
  } else if (ratio >= WARN_RATIO) {
    lines.push(
      '<font color="warning">超过 80%：检查最大的那个应用，或缩短备份保留时间（deploy/backup/backup.sh 的 forget 参数）。</font>',
    );
  } else {
    lines.push('<font color="comment">离免费额度还远，不用处理。</font>');
  }
  return lines.join('\n');
}

/** monthly 总是发；check 只在超过 80% 时发 */
export function shouldSend(ratio, mode) {
  return mode === 'monthly' || ratio >= WARN_RATIO;
}

if (isMain(process.argv[1], import.meta.url)) {
  const mode = process.argv[2] || 'monthly';
  const files = JSON.parse(readFileSync(process.env.FILES_JSON || '/out/files.json', 'utf8'));
  const sum = summarize(files);
  const msg = buildMessage(sum, mode);
  console.log(msg);
  const ch = channel();
  if (!shouldSend(sum.ratio, mode)) {
    console.log('未超过 80%，不发送');
  } else if (ch && !process.env.DRY_RUN) {
    ch.send(msg)
      .then(() => console.log(`已发送（${ch.name}）`))
      .catch((err) => {
        console.error('发送失败：', err.message);
        process.exit(1);
      });
  }
}
