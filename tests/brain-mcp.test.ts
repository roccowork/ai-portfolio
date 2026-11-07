import { mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanPath, server } from '../deploy/apps/notes/mcp.mjs';

let root = '';
let base = '';
let srv: ReturnType<typeof server>;

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'vault-'));
  for (const d of ['原始', 'wiki', '私密', '.obsidian']) await mkdir(join(root, d));
  await writeFile(join(root, '原始/体检.md'), '今天体检，血压 135/85\n');
  await writeFile(join(root, 'wiki/健康状况.md'), '# 健康状况\n- 血压\n');
  await writeFile(join(root, '私密/日记.md'), '秘密：血压\n');
  await writeFile(join(root, '.obsidian/app.json'), '{}');
  srv = server(root, 'tok');
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(srv.address() as AddressInfo).port}`;
});
afterEach(() => srv.close());

const rpc = async (method: string, params: unknown = {}, token = 'tok') => {
  const res = await fetch(`${base}/mcp`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  });
  return { status: res.status, body: res.status === 200 ? await res.json() : null };
};
const tool = async (name: string, args: Record<string, unknown>) => {
  const { body } = await rpc('tools/call', { name, arguments: args });
  return { text: body.result.content[0].text as string, error: body.result.isError === true };
};

describe('个人知识库 MCP', () => {
  it('没有令牌或令牌不对一律 401；通知返回 202', async () => {
    expect((await rpc('tools/list', {}, 'wrong')).status).toBe(401);
    expect((await fetch(`${base}/mcp`, { method: 'POST', body: '{}' })).status).toBe(401);
    const res = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: { Authorization: 'Bearer tok' },
      body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }),
    });
    expect(res.status).toBe(202);
    expect((await fetch(`${base}/healthz`)).status).toBe(200);
  });

  it('握手协商协议版本，列出六个工具', async () => {
    const init = await rpc('initialize', { protocolVersion: '2025-03-26' });
    expect(init.body.result.protocolVersion).toBe('2025-03-26');
    expect(init.body.result.instructions).toContain('wiki/');
    const list = await rpc('tools/list');
    expect(list.body.result.tools.map((t: { name: string }) => t.name)).toEqual([
      'list_notes',
      'read_note',
      'search_notes',
      'read_image',
      'write_note',
      'delete_note',
    ]);
  });

  it('列表和搜索看不到 私密/ 和隐藏目录', async () => {
    const list = await tool('list_notes', {});
    expect(list.text).toContain('原始/体检.md');
    expect(list.text).toContain('wiki/健康状况.md');
    expect(list.text).not.toContain('私密');
    expect(list.text).not.toContain('.obsidian');
    const hits = await tool('search_notes', { query: '血压' });
    expect(hits.text).toContain('原始/体检.md:1:');
    expect(hits.text).not.toContain('私密');
  });

  it('读不到 私密/、越界路径和符号链接', async () => {
    expect((await tool('read_note', { path: '原始/体检.md' })).text).toContain('135/85');
    for (const path of [
      '私密/日记.md',
      '/私密/日记.md',
      'wiki/../私密/日记.md',
      '../etc/passwd',
      '.obsidian/app.json',
    ]) {
      const r = await tool('read_note', { path });
      expect(r.error, path).toBe(true);
    }
    await symlink(join(root, '私密'), join(root, 'wiki/捷径'));
    expect((await tool('read_note', { path: 'wiki/捷径/日记.md' })).error).toBe(true);
    expect((await tool('write_note', { path: 'wiki/捷径/新.md', content: 'x' })).error).toBe(true);
  });

  it('只能写、删 wiki/ 下的 .md', async () => {
    const w = await tool('write_note', { path: 'wiki/人生/履历.md', content: '# 履历\n' });
    expect(w.error).toBe(false);
    expect(await readFile(join(root, 'wiki/人生/履历.md'), 'utf8')).toBe('# 履历\n');
    for (const path of ['原始/体检.md', '私密/新.md', 'wiki/a.txt', 'wiki', '健康.md']) {
      expect((await tool('write_note', { path, content: 'x' })).error, path).toBe(true);
    }
    expect((await tool('delete_note', { path: '原始/体检.md' })).error).toBe(true);
    expect((await tool('delete_note', { path: 'wiki/人生/履历.md' })).error).toBe(false);
    expect((await tool('list_notes', { folder: 'wiki' })).text).not.toContain('履历');
  });

  it('路径整理：反斜杠、多余斜杠', () => {
    expect(cleanPath('wiki\\健康状况.md')).toBe('wiki/健康状况.md');
    expect(cleanPath('/wiki//a.md/')).toBe('wiki/a.md');
    expect(cleanPath('', { allowRoot: true })).toBe('');
    expect(() => cleanPath('私密')).toThrow();
  });

  it('看图片：返回 base64 和类型；私密/ 的图和非图片拒绝', async () => {
    const png = Buffer.from('89504e470d0a1a0a', 'hex');
    await writeFile(join(root, 'IMG_1.png'), png);
    await writeFile(join(root, '私密/证件.jpg'), png);
    const { body } = await rpc('tools/call', {
      name: 'read_image',
      arguments: { path: 'IMG_1.png' },
    });
    expect(body.result.content).toEqual([
      { type: 'image', data: png.toString('base64'), mimeType: 'image/png' },
    ]);
    expect((await tool('read_image', { path: '私密/证件.jpg' })).error).toBe(true);
    expect((await tool('read_image', { path: '原始/体检.md' })).error).toBe(true);
    expect((await tool('read_image', { path: 'nope.png' })).error).toBe(true);
  });
});
