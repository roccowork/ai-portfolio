import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import {
  buildStatus,
  deploysFromReplicaSets,
  kubeClient,
  QUERIES,
  writeConfigMap,
} from '../deploy/k8s/base/status/status.mjs';

const servers: ReturnType<typeof createServer>[] = [];
afterEach(() => servers.splice(0).forEach((s) => s.close()));

async function serve(handler: (req: IncomingMessage, res: ServerResponse, body: string) => void) {
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => handler(req, res, body));
  });
  servers.push(server);
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

const json = (res: ServerResponse, status: number, data: unknown) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
};

const rs = (revision: number, sha: string, ready: number, time: string) => ({
  metadata: {
    annotations: { 'deployment.kubernetes.io/revision': String(revision) },
    creationTimestamp: time,
  },
  spec: {
    replicas: ready ? 2 : 0,
    template: { spec: { containers: [{ image: `ghcr.io/roccowork/ops-desk:${sha}` }] } },
  },
  status: { readyReplicas: ready },
});

describe('deploysFromReplicaSets', () => {
  it('按 revision 倒序，最新且有就绪副本的是当前版本', () => {
    const list = deploysFromReplicaSets([
      rs(1, 'aaaaaaaaaa', 0, '2026-10-20T00:00:00Z'),
      rs(3, 'cccccccccc', 2, '2026-10-22T00:00:00Z'),
      rs(2, 'bbbbbbbbbb', 0, '2026-10-21T00:00:00Z'),
    ]);
    expect(list).toEqual([
      { time: '2026-10-22T00:00:00Z', sha: 'ccccccc', state: 'current' },
      { time: '2026-10-21T00:00:00Z', sha: 'bbbbbbb', state: 'replaced' },
      { time: '2026-10-20T00:00:00Z', sha: 'aaaaaaa', state: 'replaced' },
    ]);
  });
  it('最新版本没有就绪副本时标 not-ready', () => {
    expect(deploysFromReplicaSets([rs(5, 'eeeeeee', 0, '2026-10-22T00:00:00Z')])[0].state).toBe(
      'not-ready',
    );
  });
});

describe('buildStatus', () => {
  const now = new Date('2026-10-24T08:00:00Z');
  const values: Record<
    string,
    Array<{ metric: Record<string, string>; value: [number, string] }>
  > = {
    [QUERIES.availability]: [{ metric: {}, value: [0, '99.9871'] }],
    [QUERIES.p95]: [{ metric: {}, value: [0, '0.0423'] }],
    [QUERIES.requests]: [{ metric: {}, value: [0, '1234.4'] }],
    [QUERIES.backups]: [{ metric: { cronjob: 'backup-cloudreve' }, value: [0, '1761264000'] }],
    [QUERIES.certs]: [
      { metric: { name: 'ops-desk-tls' }, value: [0, '61.2'] },
      { metric: { name: 'stats-tls' }, value: [0, '44.6'] },
    ],
    [QUERIES.alerts]: [
      { metric: { alertname: 'DeploymentReplicasLow', severity: 'warning' }, value: [0, '3'] },
    ],
  };

  it('汇总各项数据', async () => {
    const prom = await serve((req, res) => {
      const q = new URL(req.url ?? '', 'http://x').searchParams.get('query') ?? '';
      json(res, 200, { status: 'success', data: { result: values[q] ?? [] } });
    });
    const kube = async () => ({
      status: 200,
      data: { items: [rs(1, 'abcdef0123', 2, '2026-10-24T05:00:00Z')] },
    });
    const s = await buildStatus({ prom, kube, now });
    expect(s).toEqual({
      generatedAt: '2026-10-24T08:00:00.000Z',
      deploys: [{ time: '2026-10-24T05:00:00Z', sha: 'abcdef0', state: 'current' }],
      availability7d: 99.99,
      p95Ms: 42,
      requests7d: 1234,
      backups: [{ name: 'backup-cloudreve', lastSuccess: '2025-10-24T00:00:00.000Z' }],
      certs: [
        { name: 'stats-tls', daysLeft: 45 },
        { name: 'ops-desk-tls', daysLeft: 61 },
      ],
      alerts7d: [{ name: 'DeploymentReplicasLow', severity: 'warning', minutes: 15 }],
      errors: [],
    });
  });

  it('单项失败只记错误，其余照常', async () => {
    const prom = await serve((req, res) => {
      const q = new URL(req.url ?? '', 'http://x').searchParams.get('query') ?? '';
      if (q === QUERIES.p95) return json(res, 400, { status: 'error', error: 'bad query' });
      json(res, 200, { status: 'success', data: { result: values[q] ?? [] } });
    });
    const kube = async () => ({ status: 403, data: {} });
    const s = await buildStatus({ prom, kube, now });
    expect(s.p95Ms).toBeNull();
    expect(s.deploys).toBeNull();
    expect(s.availability7d).toBe(99.99);
    expect(s.errors).toHaveLength(2);
  });
});

describe('writeConfigMap', () => {
  it('不存在时改用 POST 创建，带上令牌', async () => {
    const calls: string[] = [];
    const api = await serve((req, res, body) => {
      calls.push(`${req.method} ${req.url} ${req.headers.authorization}`);
      if (req.method === 'PATCH') return json(res, 404, { message: 'not found' });
      expect(JSON.parse(body).data['status.json']).toBe('{"a":1}');
      json(res, 201, {});
    });
    await writeConfigMap(kubeClient(api, 'tok'), 'ops-desk', '{"a":1}');
    expect(calls).toEqual([
      'PATCH /api/v1/namespaces/ops-desk/configmaps/ops-desk-status Bearer tok',
      'POST /api/v1/namespaces/ops-desk/configmaps Bearer tok',
    ]);
  });
  it('写入失败时抛异常', async () => {
    const api = await serve((_req, res) => json(res, 403, { message: 'forbidden' }));
    await expect(writeConfigMap(kubeClient(api, 'tok'), 'ops-desk', '{}')).rejects.toThrow(
      'HTTP 403',
    );
  });
});
