// 个人知识库的 MCP 服务：让 AI（Claude、私人助理）读写 Obsidian 笔记。
// 笔记由 LiveSync Bridge 从 CouchDB 还原成普通文件（/vault），这里改了文件，几秒内同步回电脑和手机。
// 规则（LLM Wiki 的分工）：
//   原始/  用户随手记的原始资料，AI 只读
//   wiki/  AI 整理出来的主题页面，AI 可以写、删
//   私密/  AI 读不到也写不了；以点开头的隐藏目录同样不开放
// 协议：MCP Streamable HTTP 的无状态 JSON 模式（POST /mcp，一问一答，不开 SSE）。要 Bearer 令牌（Secret brain-mcp）。
// 只用 Node 自带的库。
import { createServer } from 'node:http';
import { mkdir, readdir, readFile, realpath, rm, stat, writeFile, rename } from 'node:fs/promises';
import { dirname, join, posix } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const PROTOCOL_VERSIONS = ['2025-06-18', '2025-03-26', '2024-11-05'];
const BLOCKED = '私密';
const WRITABLE = 'wiki';
const MAX_FILE = 1024 * 1024;
const MAX_BODY = 2 * 1024 * 1024;

const INSTRUCTIONS = `这是用户的个人知识库（Obsidian 笔记，按 LLM Wiki 的方式组织）。
- 原始/：用户随手记的原始资料（日记、体检、想法……），只读，不要改。
- wiki/：你负责整理的主题页面（如 wiki/健康状况.md、wiki/人生履历.md）。把原始资料里的信息整理进对应页面，写明日期和来源（如「来源：原始/2026-11-07 体检.md」），页面之间用 [[页面名]] 互相链接。
- 私密/：用户不让 AI 看的内容，访问不到。
回答用户的问题前先 search_notes / read_note 查相关页面，以笔记里的内容为准，查不到就直说。`;

export class NoteError extends Error {}

/** 把用户给的相对路径变成仓库里的安全路径；越界、私密、隐藏目录一律拒绝 */
export function cleanPath(p, { forWrite = false, allowRoot = false } = {}) {
  if (typeof p !== 'string' || p.includes('\0')) throw new NoteError('路径不对');
  const s = posix
    .normalize(p.normalize('NFC').replace(/\\/g, '/').replace(/^\/+/, ''))
    .replace(/\/+$/, '');
  if (s === '.' || s === '') {
    if (allowRoot) return '';
    throw new NoteError('要给出文件路径');
  }
  const parts = s.split('/');
  if (parts.some((x) => x === '..' || x.startsWith('.'))) throw new NoteError('路径不对');
  if (parts[0] === BLOCKED) throw new NoteError(`${BLOCKED}/ 不对 AI 开放`);
  if (forWrite) {
    if (parts[0] !== WRITABLE || parts.length < 2)
      throw new NoteError(`只能写 ${WRITABLE}/ 下的文件`);
    if (!s.endsWith('.md')) throw new NoteError('只能写 .md 文件');
  }
  return s;
}

/** 解析到磁盘上的真实位置，防止符号链接跳出仓库 */
async function resolveIn(root, rel, mustExist = true) {
  const full = join(root, rel);
  const realRoot = await realpath(root);
  let probe = full;
  if (!mustExist) {
    // 新文件：检查最近一个已存在的上级目录
    for (;;) {
      try {
        await stat(probe);
        break;
      } catch {
        probe = dirname(probe);
      }
    }
  }
  const real = await realpath(probe).catch(() => {
    throw new NoteError(`找不到：${rel}`);
  });
  if (real !== realRoot && !real.startsWith(realRoot + '/')) throw new NoteError('路径不对');
  // 真实位置也要过一遍规则：wiki 里指向 私密/ 的链接同样拒绝
  const realRel = real.slice(realRoot.length + 1);
  if (realRel) cleanPath(realRel);
  return full;
}

async function walk(root, rel, out) {
  const entries = await readdir(join(root, rel), { withFileTypes: true }).catch(() => []);
  for (const e of entries) {
    const name = e.name.normalize('NFC');
    if (name.startsWith('.') || (rel === '' && name === BLOCKED)) continue;
    const r = rel ? `${rel}/${name}` : name;
    if (e.isDirectory()) await walk(root, r, out);
    else if (e.isFile()) out.push(r);
  }
  return out;
}

export function tools(root) {
  return {
    list_notes: {
      description: '列出笔记文件（不含 私密/）。folder 可选，比如 "原始" 或 "wiki"。',
      inputSchema: { type: 'object', properties: { folder: { type: 'string' } } },
      async run({ folder = '' }) {
        const rel = cleanPath(folder, { allowRoot: true });
        if (rel) await resolveIn(root, rel);
        const files = await walk(root, rel, []);
        const lines = [];
        for (const f of files.sort()) {
          const st = await stat(join(root, f)).catch(() => null);
          if (st)
            lines.push(
              `${f}\t${st.size} 字节\t${st.mtime.toISOString().slice(0, 16).replace('T', ' ')}`,
            );
        }
        return lines.length ? lines.join('\n') : '（没有文件）';
      },
    },
    read_note: {
      description: '读一篇笔记的全文。path 是相对路径，比如 "wiki/健康状况.md"。',
      inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
      async run({ path }) {
        const rel = cleanPath(path);
        const full = await resolveIn(root, rel);
        const st = await stat(full);
        if (!st.isFile()) throw new NoteError(`不是文件：${rel}`);
        if (st.size > MAX_FILE) throw new NoteError(`文件太大（${st.size} 字节）`);
        return await readFile(full, 'utf8');
      },
    },
    search_notes: {
      description:
        '在笔记里搜关键词（不分大小写），返回「路径:行号: 内容」，最多 100 条。folder 可选。',
      inputSchema: {
        type: 'object',
        properties: { query: { type: 'string' }, folder: { type: 'string' } },
        required: ['query'],
      },
      async run({ query, folder = '' }) {
        const q = String(query ?? '')
          .normalize('NFC')
          .toLowerCase()
          .trim();
        if (!q) throw new NoteError('要给出关键词');
        const rel = cleanPath(folder, { allowRoot: true });
        if (rel) await resolveIn(root, rel);
        const out = [];
        for (const f of (await walk(root, rel, [])).sort()) {
          if (!/\.(md|txt|canvas)$/i.test(f)) continue;
          const text = await readFile(join(root, f), 'utf8').catch(() => '');
          const nameHit = f.toLowerCase().includes(q);
          const lines = text.split('\n');
          let hit = false;
          lines.forEach((line, i) => {
            if (out.length < 100 && line.normalize('NFC').toLowerCase().includes(q)) {
              out.push(`${f}:${i + 1}: ${line.slice(0, 300)}`);
              hit = true;
            }
          });
          if (nameHit && !hit && out.length < 100) out.push(`${f}:（文件名匹配）`);
          if (out.length >= 100) break;
        }
        return out.length ? out.join('\n') : `没找到「${query}」`;
      },
    },
    write_note: {
      description:
        '写一篇 wiki 页面（整篇覆盖，不存在就新建）。只能写 wiki/ 下的 .md 文件。改已有页面前先 read_note，保留原有内容。',
      inputSchema: {
        type: 'object',
        properties: { path: { type: 'string' }, content: { type: 'string' } },
        required: ['path', 'content'],
      },
      async run({ path, content }) {
        const rel = cleanPath(path, { forWrite: true });
        if (typeof content !== 'string') throw new NoteError('content 要是文本');
        if (Buffer.byteLength(content) > MAX_FILE) throw new NoteError('内容太长');
        const full = await resolveIn(root, rel, false);
        await mkdir(dirname(full), { recursive: true });
        // 先写临时文件再改名，同步桥不会读到写了一半的文件
        const tmp = join(dirname(full), `.tmp-${process.pid}-${Date.now()}`);
        await writeFile(tmp, content, 'utf8');
        await rename(tmp, full);
        return `已保存 ${rel}（${Buffer.byteLength(content)} 字节）`;
      },
    },
    delete_note: {
      description: '删除一篇 wiki 页面。只能删 wiki/ 下的 .md 文件。',
      inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
      async run({ path }) {
        const rel = cleanPath(path, { forWrite: true });
        const full = await resolveIn(root, rel);
        await rm(full);
        return `已删除 ${rel}`;
      },
    },
  };
}

/** 处理一条 JSON-RPC 消息；通知返回 null */
export async function handle(msg, root) {
  const { id, method, params = {} } = msg ?? {};
  const isNotification = id === undefined || id === null;
  const ok = (result) => (isNotification ? null : { jsonrpc: '2.0', id, result });
  const fail = (code, message) =>
    isNotification ? null : { jsonrpc: '2.0', id, error: { code, message } };
  if (msg?.jsonrpc !== '2.0' || typeof method !== 'string') return fail(-32600, 'Invalid Request');
  if (method === 'initialize') {
    const asked = params.protocolVersion;
    return ok({
      protocolVersion: PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSIONS[0],
      capabilities: { tools: {} },
      serverInfo: { name: 'brain', version: '1.0.0' },
      instructions: INSTRUCTIONS,
    });
  }
  if (method.startsWith('notifications/')) return null;
  if (method === 'ping') return ok({});
  const all = tools(root);
  if (method === 'tools/list') {
    return ok({
      tools: Object.entries(all).map(([name, t]) => ({
        name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    });
  }
  if (method === 'tools/call') {
    const t = all[params.name];
    if (!t) return fail(-32602, `没有这个工具：${params.name}`);
    try {
      const text = await t.run(params.arguments ?? {});
      return ok({ content: [{ type: 'text', text }] });
    } catch (e) {
      const text =
        e instanceof NoteError ? e.message : e?.code === 'ENOENT' ? '找不到这个文件' : '操作失败';
      if (!(e instanceof NoteError))
        console.error(new Date().toISOString(), params.name, e?.message);
      return ok({ content: [{ type: 'text', text }], isError: true });
    }
  }
  return fail(-32601, `Method not found: ${method}`);
}

function sameToken(given, token) {
  const a = Buffer.from(given);
  const b = Buffer.from(token);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function server(root, token) {
  if (!token) throw new Error('没有配置令牌，拒绝启动');
  return createServer(async (req, res) => {
    const send = (status, body, headers = {}) => {
      res.writeHead(status, { 'Content-Type': 'application/json', ...headers });
      res.end(body === undefined ? undefined : JSON.stringify(body));
    };
    const url = new URL(req.url ?? '/', 'http://x');
    if (url.pathname === '/healthz') return send(200, { ok: true });
    if (url.pathname !== '/mcp') return send(404, { error: 'not found' });
    const auth = req.headers.authorization ?? '';
    if (!auth.startsWith('Bearer ') || !sameToken(auth.slice(7), token)) {
      return send(401, { error: 'unauthorized' }, { 'WWW-Authenticate': 'Bearer' });
    }
    if (req.method !== 'POST') return send(405, { error: 'POST only' }, { Allow: 'POST' });
    let size = 0;
    const chunks = [];
    for await (const c of req) {
      size += c.length;
      if (size > MAX_BODY) return send(413, { error: 'too large' });
      chunks.push(c);
    }
    let msg;
    try {
      msg = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      return send(400, {
        jsonrpc: '2.0',
        id: null,
        error: { code: -32700, message: 'Parse error' },
      });
    }
    if (Array.isArray(msg)) {
      const out = (await Promise.all(msg.map((m) => handle(m, root)))).filter(Boolean);
      return out.length ? send(200, out) : send(202);
    }
    const out = await handle(msg, root);
    return out ? send(200, out) : send(202);
  });
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = process.env.VAULT_DIR ?? '/vault';
  const port = Number(process.env.PORT ?? 8080);
  server(root, process.env.MCP_TOKEN ?? '').listen(port, () =>
    console.log(`brain MCP 在 :${port}，笔记目录 ${root}`),
  );
}
