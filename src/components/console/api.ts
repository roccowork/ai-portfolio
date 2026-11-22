// 总控台请求后端：身份只认主人的 Telegram 账号，后端校验（ai/app/tgauth.py）。两种入口：
// - 值班台机器人的小程序：请求体带 Telegram 签名的 initData
// - 浏览器（2026-11-22 加）：Telegram 官方登录按钮，登录结果存在本机，以 "login:" 开头发给后端，7 天有效
interface WebApp {
  initData: string;
  initDataUnsafe: { start_param?: string };
  ready(): void;
  expand(): void;
  isVersionAtLeast(v: string): boolean;
  isFullscreen?: boolean;
  requestFullscreen?(): void;
  exitFullscreen?(): void;
  setHeaderColor?(c: string): void;
  setBackgroundColor?(c: string): void;
}
declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

let init = '';

export function webApp(): WebApp | undefined {
  return window.Telegram?.WebApp;
}

export const LOGIN_BOT = 'liuxd_ops_bot'; // 值班台机器人；BotFather 里要 /setdomain 绑定 ops.liuxd.dev，登录按钮才能用
const LOGIN_KEY = 'console-login';

function savedLogin(): string {
  try {
    return localStorage.getItem(LOGIN_KEY) || '';
  } catch {
    return '';
  }
}

/** 登录按钮回调：把 Telegram 给的字段（带 hash）原样存下来 */
export function saveLogin(user: Record<string, string | number>) {
  const q = new URLSearchParams(Object.entries(user).map(([k, v]) => [k, String(v)]));
  try {
    localStorage.setItem(LOGIN_KEY, 'login:' + q.toString());
  } catch {
    /* 存不了就只在这次打开里用 */
  }
  init = 'login:' + q.toString();
}

export function logout() {
  try {
    localStorage.removeItem(LOGIN_KEY);
  } catch {
    /* 忽略 */
  }
  init = '';
}

/** 是浏览器打开的（不在 Telegram 小程序里） */
export function inBrowser(): boolean {
  return !webApp()?.initData;
}

/** 返回 'login' 表示要先用 Telegram 登录；空字符串表示可以用 */
export function initAuth(): string {
  const app = webApp();
  init = app?.initData || '';
  if (!init) {
    init = savedLogin();
    return init ? '' : 'login';
  }
  app!.ready();
  app!.expand();
  app!.setHeaderColor?.('#070b14');
  app!.setBackgroundColor?.('#070b14');
  return '';
}

/** 推送消息里的直达链接：?t=标签页，或者小程序链接的 startapp 参数 */
export function startTab(): string {
  return (
    new URLSearchParams(location.search).get('t') || webApp()?.initDataUnsafe.start_param || ''
  );
}

export function canFullscreen(): boolean {
  const app = webApp();
  return !!app && app.isVersionAtLeast('8.0') && !!app.requestFullscreen;
}

export function toggleFullscreen() {
  const app = webApp();
  if (!app) return;
  if (app.isFullscreen) app.exitFullscreen?.();
  else app.requestFullscreen?.();
}

export async function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...body, init }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    if (r.status === 404 && init.startsWith('login:') && (!d.detail || d.detail === 'Not Found')) {
      logout(); // 登录过期（7 天）或不是主人：清掉，重新登录
      throw new Error('login');
    }
    throw new Error(
      r.status === 404 && (!d.detail || d.detail === 'Not Found')
        ? '没有权限：只认主人本人的 Telegram 账号（小程序打开超过一天要重新打开）'
        : d.detail || `服务暂时不可用（${r.status}）`,
    );
  }
  return (await r.json()) as T;
}
