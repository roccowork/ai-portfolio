// 导出集群里人工创建的 Secret（密码、密钥、令牌），写成一个可以直接 kubectl apply 的 List，交给 restic 加密备份到 R2。
// 服务器彻底坏了时：装好 k3s → restic 恢复这个文件 → kubectl apply → 再装 Argo CD，所有应用就能拿到原来的密钥。
// 跳过能自动重新生成的：证书（kubernetes.io/tls）、Helm 发布记录、有 ownerReferences 的（控制器生成的）、Helm 装的。
// 只用 Node 自带的库。
import { readFileSync, writeFileSync, realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SA_DIR = '/var/run/secrets/kubernetes.io/serviceaccount';
export const KEEP_TYPES = new Set([
  'Opaque',
  'kubernetes.io/basic-auth',
  'kubernetes.io/ssh-auth',
  'kubernetes.io/dockerconfigjson',
]);
export const SKIP_NAMESPACES = new Set(['kube-system', 'kube-public', 'kube-node-lease']);

/** 从 /api/v1/secrets 的结果里挑出要备份的，只留恢复需要的字段 */
export function pickSecrets(items) {
  const out = [];
  for (const s of items) {
    const m = s.metadata ?? {};
    if (!KEEP_TYPES.has(s.type ?? 'Opaque') || SKIP_NAMESPACES.has(m.namespace)) continue;
    if (m.ownerReferences?.length || m.labels?.['app.kubernetes.io/managed-by'] === 'Helm')
      continue;
    out.push({
      apiVersion: 'v1',
      kind: 'Secret',
      type: s.type ?? 'Opaque',
      // 标签要留：Argo CD 靠 argocd.argoproj.io/secret-type 认出私有仓库的部署密钥
      metadata: { name: m.name, namespace: m.namespace, ...(m.labels ? { labels: m.labels } : {}) },
      data: s.data ?? {},
    });
  }
  out.sort((a, b) =>
    `${a.metadata.namespace}/${a.metadata.name}`.localeCompare(
      `${b.metadata.namespace}/${b.metadata.name}`,
    ),
  );
  return { apiVersion: 'v1', kind: 'List', items: out };
}

export async function fetchSecrets(
  api = 'https://kubernetes.default.svc',
  token = readFileSync(`${SA_DIR}/token`, 'utf8'),
) {
  const res = await fetch(`${api}/api/v1/secrets`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) throw new Error(`K8s API 返回 HTTP ${res.status}`);
  return (await res.json()).items ?? [];
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = process.argv[2] ?? '/snapshot/secrets.json';
  fetchSecrets()
    .then((items) => {
      const list = pickSecrets(items);
      if (!list.items.length) throw new Error('一个 Secret 都没导出来，不写文件，让任务失败');
      writeFileSync(out, JSON.stringify(list), { mode: 0o600 });
      // 只打印名字，不打印内容
      console.log(`导出 ${list.items.length} 个 Secret：`);
      for (const i of list.items) console.log(`  ${i.metadata.namespace}/${i.metadata.name}`);
    })
    .catch((err) => {
      console.error(new Date().toISOString(), '导出失败：', err.message);
      process.exit(1);
    });
}
