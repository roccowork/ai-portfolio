// 总控台请求后端：统一在这里带上身份（目前是链接里的私人密钥 #k=…）
let key = '';

export function initAuth(): string {
  key = decodeURIComponent((location.hash.match(/k=([^&]+)/) || [])[1] || '');
  return key ? '' : '链接里缺少私人密钥（#k=…）';
}

export async function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...body, k: key }),
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(
      r.status === 404 && !d.detail ? '没有权限' : d.detail || `服务暂时不可用（${r.status}）`,
    );
  }
  return (await r.json()) as T;
}
