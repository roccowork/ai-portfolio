// /status.json 的类型和展示用的小函数。数据由 deploy/k8s/base/status/status.mjs 每 10 分钟生成。
// 任何一项都可能是 null（那一项查询失败），页面要能照常显示其余部分。

export interface Deploy {
  time: string | null;
  sha: string;
  /** current：当前版本；not-ready：最新版本没有就绪副本（旧版本仍在服务）；replaced：已被替换 */
  state: 'current' | 'not-ready' | 'replaced';
}

export interface Status {
  generatedAt: string;
  deploys: Deploy[] | null;
  availability7d: number | null;
  p95Ms: number | null;
  requests7d: number | null;
  backups: { name: string; lastSuccess: string }[] | null;
  certs: { name: string; daysLeft: number | null }[] | null;
  alerts7d: { name: string; severity: string; minutes: number }[] | null;
  errors: string[];
}

/** 数据超过这个时间没更新就标黄 */
export const STALE_MS = 60 * 60 * 1000;
/** 备份超过这个时间没成功就标黄（备份每天跑一次） */
export const BACKUP_STALE_MS = 26 * 60 * 60 * 1000;

/** 「3 分钟前」「5 小时前」「2 天前」 */
export function ago(iso: string | null, now: Date): string {
  if (!iso) return '未知';
  const ms = now.getTime() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '未知';
  const min = Math.max(0, Math.floor(ms / 60_000));
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

export function isStale(iso: string | null, now: Date, limit = STALE_MS): boolean {
  if (!iso) return true;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) || now.getTime() - t > limit;
}

/** 首页的一行摘要 */
export function summarize(s: Status, now: Date): string {
  const parts: string[] = [];
  const latest = s.deploys?.[0];
  if (latest) parts.push(`最近发布 ${ago(latest.time, now)}（${latest.sha}）`);
  if (s.availability7d !== null) parts.push(`7 天可用率 ${s.availability7d}%`);
  if (s.backups?.length) {
    const ok = s.backups.filter((b) => !isStale(b.lastSuccess, now, BACKUP_STALE_MS)).length;
    parts.push(`备份 ${ok}/${s.backups.length} 正常`);
  }
  if (s.certs?.length) {
    const min = Math.min(...s.certs.map((c) => c.daysLeft ?? Infinity));
    if (Number.isFinite(min)) parts.push(`证书最少剩 ${min} 天`);
  }
  if (s.alerts7d) parts.push(s.alerts7d.length ? `7 天告警 ${s.alerts7d.length} 种` : '7 天无告警');
  parts.push(`数据更新于 ${ago(s.generatedAt, now)}`);
  return parts.join(' · ');
}
