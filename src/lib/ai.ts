// 请求 AI 服务的公共函数。接口由 ai/ 下的 FastAPI 服务提供，和站点同域（/api/ai/*）。

/** 每次模型调用的模型名、token、费用（元）和耗时，演示页面上直接展示 */
export interface Meta {
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  seconds: number;
}

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T;
  let detail = '';
  try {
    detail = ((await r.json()) as { detail?: unknown }).detail as string;
  } catch {
    // 非 JSON 响应（比如网关超时）
  }
  if (typeof detail !== 'string' || !detail) {
    detail = r.status === 429 ? '请求太频繁，请稍后再试' : `服务暂时不可用（${r.status}）`;
  }
  throw new Error(detail);
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  return handle<T>(
    await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

export async function getJSON<T>(url: string): Promise<T> {
  return handle<T>(await fetch(url));
}
