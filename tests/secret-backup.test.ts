import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { fetchSecrets, pickSecrets } from '../deploy/apps/secret-backup/export.mjs';

const servers: ReturnType<typeof createServer>[] = [];
afterEach(() => servers.splice(0).forEach((s) => s.close()));

const secret = (namespace: string, name: string, extra: Record<string, unknown> = {}) => ({
  type: 'Opaque',
  metadata: { namespace, name, uid: 'x', resourceVersion: '1', ...(extra.metadata as object) },
  data: { k: 'dg==' },
  ...Object.fromEntries(Object.entries(extra).filter(([k]) => k !== 'metadata')),
});

describe('Secret 备份：挑出人工创建的', () => {
  it('留下手建的，跳过证书、Helm、控制器生成的和系统命名空间', () => {
    const list = pickSecrets([
      secret('ops-desk', 'telegram-bot'),
      secret('argocd', 'repo-ops-desk', {
        type: 'Opaque',
        metadata: { labels: { 'argocd.argoproj.io/secret-type': 'repository' } },
      }),
      secret('notes', 'notes-tls', { type: 'kubernetes.io/tls' }),
      secret('monitoring', 'sh.helm.release.v1.x', { type: 'helm.sh/release.v1' }),
      secret('monitoring', 'grafana', {
        metadata: { labels: { 'app.kubernetes.io/managed-by': 'Helm' } },
      }),
      secret('notes', 'generated', { metadata: { ownerReferences: [{ kind: 'Certificate' }] } }),
      secret('kube-system', 'k3s-token'),
    ]);
    expect(list.kind).toBe('List');
    expect(list.items.map((i) => `${i.metadata.namespace}/${i.metadata.name}`)).toEqual([
      'argocd/repo-ops-desk',
      'ops-desk/telegram-bot',
    ]);
    // 只留恢复需要的字段，标签保留（Argo CD 靠它认出仓库密钥）
    expect(list.items[0].metadata).toEqual({
      name: 'repo-ops-desk',
      namespace: 'argocd',
      labels: { 'argocd.argoproj.io/secret-type': 'repository' },
    });
    expect(list.items[1]).toEqual({
      apiVersion: 'v1',
      kind: 'Secret',
      type: 'Opaque',
      metadata: { name: 'telegram-bot', namespace: 'ops-desk' },
      data: { k: 'dg==' },
    });
  });

  it('从 K8s API 列出全部 Secret，带上令牌；出错时报错', async () => {
    let auth = '';
    const server = createServer((req, res) => {
      auth = req.headers.authorization ?? '';
      if (req.url !== '/api/v1/secrets') return res.writeHead(404).end();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ items: [secret('a', 'b')] }));
    });
    servers.push(server);
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
    const api = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    expect(await fetchSecrets(api, 't')).toHaveLength(1);
    expect(auth).toBe('Bearer t');
    await expect(fetchSecrets(`${api}/nope`, 't')).rejects.toThrow('404');
  });
});
