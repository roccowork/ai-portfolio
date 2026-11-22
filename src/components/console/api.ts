// 总控台请求后端：身份只认 Telegram（值班台机器人的小程序），请求体带上 Telegram 签名的 initData，后端校验（ai/app/tgauth.py）
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

/** 返回错误提示；空字符串表示可以用 */
export function initAuth(): string {
  const app = webApp();
  init = app?.initData || '';
  if (!init)
    return '总控台只能在 Telegram 里打开：OPS 值班台机器人输入框左边的「总控台」按钮（手机、电脑版 Telegram 都可以）';
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
    throw new Error(
      r.status === 404 && (!d.detail || d.detail === 'Not Found')
        ? '没有权限：只认主人本人的 Telegram 账号（小程序打开超过一天要重新打开）'
        : d.detail || `服务暂时不可用（${r.status}）`,
    );
  }
  return (await r.json()) as T;
}
