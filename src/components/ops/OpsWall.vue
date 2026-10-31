<script setup lang="ts">
// 运维值班大屏：本站服务器的实时状态（/api/ai/ops/overview 每 15 秒、/api/ai/ops/trend 每 60 秒）。
// 数据直接查 Prometheus，不调用模型；页面切到后台时暂停刷新，省请求
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { getJSON } from '../../lib/ai';

interface Alert {
  name: string;
  severity: string;
  state: string;
  namespace: string | null;
  since: string;
  summary: string;
}
interface EvalScore {
  ts: string | null;
  cases: number | null;
  hit_at_1: number | null;
  judge_correct: number | null;
  pass_rate: number | null;
}
interface Overview {
  enabled: boolean;
  ts: string;
  node: { cpu_used_pct: number; mem_used_pct: number; disk_used_pct: number; load1: number } | null;
  cpu_cores: number | null;
  mem_total_gb: number | null;
  disk_total_gb: number | null;
  uptime_days: number | null;
  req_per_min: number | null;
  err_5xx_pct: number | null;
  pods: Record<string, { running: number; total: number }> | null;
  alerts: Alert[] | null;
  availability7d: number | null;
  p95_ms: number | null;
  deploy: { sha: string; time: string } | null;
  cert_days_left: number | null;
  evals: Record<'rag' | 'ops-rag' | 'agent', EvalScore | null>;
  heal: { total: number; fixed: number };
  backups: { name: string; lastSuccess: string }[] | null;
  alerts7d: { name: string; severity: string; minutes: number }[] | null;
  probes?: Probe[] | null;
}
/** 外部拨测（Blackbox Exporter）：自己的网站和外部依赖 */
interface Probe {
  name: string;
  group: 'own' | 'dependency';
  ok: boolean;
  ms: number | null;
  cert_days: number | null;
}
const PROBE_GROUPS = [
  { key: 'own', name: '自己的网站' },
  { key: 'dependency', name: '外部依赖（百炼、GitHub、Cloudflare、Telegram）' },
] as const;
type Series = [number, number][];
interface Trend {
  enabled: boolean;
  cpu: Series;
  mem: Series;
  req: Series;
}

const ov = ref<Overview | null>(null);
const trend = ref<Trend | null>(null);
const error = ref('');
const now = ref(Date.now());
let timers: number[] = [];

async function loadOverview() {
  try {
    ov.value = await getJSON<Overview>('/api/ai/ops/overview');
    error.value = '';
  } catch (e) {
    error.value = (e as Error).message;
  }
}
async function loadTrend() {
  try {
    trend.value = await getJSON<Trend>('/api/ai/ops/trend');
  } catch {
    // 趋势图查不到就空着，不影响上面的状态
  }
}
const visible = () => document.visibilityState === 'visible';

onMounted(() => {
  loadOverview();
  loadTrend();
  timers = [
    window.setInterval(() => visible() && loadOverview(), 15000),
    window.setInterval(() => visible() && loadTrend(), 60000),
    window.setInterval(() => (now.value = Date.now()), 1000),
  ];
});
onUnmounted(() => timers.forEach(clearInterval));

// ── 展示用的小函数 ──
const fmt = (v: number | null | undefined, d = 0) => (v == null ? '—' : v.toFixed(d));
const tone = (v: number | null | undefined, warn = 70, bad = 85) =>
  v == null
    ? 'text-slate-500'
    : v >= bad
      ? 'text-rose-400'
      : v >= warn
        ? 'text-amber-300'
        : 'text-emerald-400';
const stroke = (v: number | null | undefined) =>
  v == null ? '#475569' : v >= 85 ? '#fb7185' : v >= 70 ? '#fcd34d' : '#34d399';
function ago(iso: string | null | undefined): string {
  if (!iso) return '—';
  const s = Math.max(0, (now.value - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)} 秒前`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}
const updatedAgo = computed(() => (ov.value?.ts ? ago(ov.value.ts) : '加载中'));

const gauges = computed(() => {
  const n = ov.value?.node;
  return [
    {
      label: 'CPU',
      v: n?.cpu_used_pct,
      sub: ov.value?.cpu_cores ? `${ov.value.cpu_cores} 核` : '',
    },
    {
      label: '内存',
      v: n?.mem_used_pct,
      sub: ov.value?.mem_total_gb ? `${fmt(ov.value.mem_total_gb)} G` : '',
    },
    {
      label: '磁盘',
      v: n?.disk_used_pct,
      sub: ov.value?.disk_total_gb ? `${fmt(ov.value.disk_total_gb)} G` : '',
    },
  ];
});
const C = 2 * Math.PI * 42; // 圆环周长

const pods = computed(() =>
  Object.entries(ov.value?.pods ?? {}).sort((a, b) => b[1].total - a[1].total),
);
const podsTotal = computed(() => pods.value.reduce((s, [, p]) => s + p.total, 0));
const podsRunning = computed(() => pods.value.reduce((s, [, p]) => s + p.running, 0));
const firing = computed(() => (ov.value?.alerts ?? []).filter((a) => a.state === 'firing'));

// 折线：横轴 60 分钟，纵轴 0～100（%），访问量按自己的最大值缩放
const W = 300;
const H = 90;
function path(s: Series | undefined, max = 100): string {
  if (!s?.length) return '';
  const t0 = s[0][0];
  const span = s[s.length - 1][0] - t0 || 1;
  return s
    .map(
      ([t, v], i) =>
        `${i ? 'L' : 'M'}${(((t - t0) / span) * W).toFixed(1)},${(H - (Math.min(v, max) / max) * H).toFixed(1)}`,
    )
    .join(' ');
}
const reqMax = computed(() => Math.max(1, ...(trend.value?.req ?? []).map(([, v]) => v)) * 1.15);
const last = (s: Series | undefined) => (s?.length ? s[s.length - 1][1] : null);

// 备份：看最久没成功的那个（超过 26 小时会告警）
const oldestBackup = computed(() =>
  (ov.value?.backups ?? []).reduce<string | null>(
    (o, b) => (!o || b.lastSuccess < o ? b.lastSuccess : o),
    null,
  ),
);
const alerts7d = computed(() => ov.value?.alerts7d ?? []);
</script>

<template>
  <div class="text-slate-200">
    <!-- 顶栏 -->
    <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-slate-400">
      <div class="flex items-center gap-2">
        <span class="relative flex size-2.5">
          <span
            class="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-60"
          ></span>
          <span class="relative inline-flex size-2.5 rounded-full bg-emerald-400"></span>
        </span>
        <span class="font-semibold tracking-widest text-emerald-300">LIVE</span>
        <span>单节点 k3s · 香港</span>
        <span v-if="ov?.uptime_days != null">· 已连续运行 {{ fmt(ov.uptime_days) }} 天</span>
      </div>
      <span class="font-mono">{{
        error ? `数据暂时取不到：${error}` : `更新于 ${updatedAgo} · 每 15 秒刷新`
      }}</span>
    </div>

    <p v-if="ov && !ov.enabled" class="mt-6 rounded-xl bg-white/5 p-5 text-sm text-slate-400">
      这里只能在集群里看到实时数据（本地开发环境没有集群权限）。
    </p>

    <template v-else>
      <!-- 第一排：三个圆环 + 四个指标 -->
      <div class="mt-5 grid gap-3 lg:grid-cols-[1.1fr_1fr]">
        <div class="grid grid-cols-3 gap-3">
          <div
            v-for="g in gauges"
            :key="g.label"
            class="rounded-2xl bg-white/[0.04] p-3 ring-1 ring-white/10 sm:p-4"
          >
            <div class="relative mx-auto w-full max-w-[120px]">
              <svg viewBox="0 0 100 100" class="w-full -rotate-90">
                <circle
                  cx="50"
                  cy="50"
                  r="42"
                  fill="none"
                  stroke="rgba(255,255,255,.08)"
                  stroke-width="9"
                />
                <circle
                  cx="50"
                  cy="50"
                  r="42"
                  fill="none"
                  stroke-linecap="round"
                  stroke-width="9"
                  :stroke="stroke(g.v)"
                  :stroke-dasharray="`${((g.v ?? 0) / 100) * C} ${C}`"
                  class="transition-[stroke-dasharray] duration-700"
                />
              </svg>
              <div class="absolute inset-0 grid place-items-center">
                <span
                  class="font-mono text-xl font-bold tabular-nums sm:text-2xl"
                  :class="tone(g.v)"
                >
                  {{ fmt(g.v) }}<span class="text-xs">%</span>
                </span>
              </div>
            </div>
            <div class="mt-2 text-center text-xs text-slate-400">
              {{ g.label }} <span class="text-slate-500">{{ g.sub }}</span>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
            <div class="text-xs text-slate-400">网站访问</div>
            <div class="mt-1 font-mono text-2xl font-bold text-cyan-300 tabular-nums">
              {{ fmt(ov?.req_per_min, 1) }}
            </div>
            <div class="text-xs text-slate-500">次 / 分钟</div>
          </div>
          <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
            <div class="text-xs text-slate-400">5xx 错误率</div>
            <div
              class="mt-1 font-mono text-2xl font-bold tabular-nums"
              :class="tone(ov?.err_5xx_pct, 1, 5)"
            >
              {{ fmt(ov?.err_5xx_pct, 2) }}<span class="text-sm">%</span>
            </div>
            <div class="text-xs text-slate-500">最近 5 分钟</div>
          </div>
          <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
            <div class="text-xs text-slate-400">7 天可用率</div>
            <div class="mt-1 font-mono text-2xl font-bold text-emerald-400 tabular-nums">
              {{ fmt(ov?.availability7d, 2) }}<span class="text-sm">%</span>
            </div>
            <div class="text-xs text-slate-500">P95 {{ fmt(ov?.p95_ms) }} ms</div>
          </div>
          <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
            <div class="text-xs text-slate-400">Pod 运行</div>
            <div
              class="mt-1 font-mono text-2xl font-bold tabular-nums"
              :class="podsRunning < podsTotal ? 'text-amber-300' : 'text-emerald-400'"
            >
              {{ podsRunning }}<span class="text-sm text-slate-500"> / {{ podsTotal }}</span>
            </div>
            <div class="text-xs text-slate-500">{{ pods.length }} 个命名空间</div>
          </div>
        </div>
      </div>

      <!-- 第二排：趋势 + 告警 -->
      <div class="mt-3 grid gap-3 lg:grid-cols-[1.6fr_1fr]">
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span class="text-slate-400">最近 1 小时</span>
            <span class="flex gap-3">
              <span class="text-cyan-300">● CPU {{ fmt(last(trend?.cpu), 1) }}%</span>
              <span class="text-violet-300">● 内存 {{ fmt(last(trend?.mem), 1) }}%</span>
              <span class="text-emerald-300">● 访问 {{ fmt(last(trend?.req), 1) }}/分</span>
            </span>
          </div>
          <svg
            :viewBox="`0 0 ${W} ${H}`"
            preserveAspectRatio="none"
            class="mt-3 h-36 w-full overflow-visible lg:h-52"
          >
            <line
              v-for="y in [0.25, 0.5, 0.75]"
              :key="y"
              x1="0"
              :x2="W"
              :y1="H * y"
              :y2="H * y"
              stroke="rgba(255,255,255,.06)"
            />
            <path
              v-if="trend?.req.length"
              :d="`${path(trend.req, reqMax)} L${W},${H} L0,${H} Z`"
              fill="rgba(52,211,153,.12)"
            />
            <path
              :d="path(trend?.req, reqMax)"
              fill="none"
              stroke="#6ee7b7"
              stroke-width="1.2"
              vector-effect="non-scaling-stroke"
            />
            <path
              :d="path(trend?.mem)"
              fill="none"
              stroke="#c4b5fd"
              stroke-width="2"
              vector-effect="non-scaling-stroke"
            />
            <path
              :d="path(trend?.cpu)"
              fill="none"
              stroke="#67e8f9"
              stroke-width="2"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <p v-if="trend && !trend.cpu.length" class="text-center text-xs text-slate-500">
            趋势数据暂时取不到
          </p>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-400">当前告警</span>
            <span :class="firing.length ? 'text-rose-400' : 'text-emerald-400'">{{
              firing.length ? `${firing.length} 条触发中` : '全部正常'
            }}</span>
          </div>
          <ul v-if="ov?.alerts?.length" class="mt-3 space-y-2">
            <li
              v-for="a in ov.alerts.slice(0, 5)"
              :key="a.name + a.since"
              class="rounded-lg bg-white/5 px-3 py-2 text-sm"
            >
              <span :class="a.severity === 'critical' ? 'text-rose-400' : 'text-amber-300'"
                >● {{ a.name }}</span
              >
              <span class="ml-2 text-xs text-slate-500">{{
                a.state === 'firing' ? '触发中' : '等待中'
              }}</span>
              <p class="mt-0.5 text-xs text-slate-400">{{ a.summary }}</p>
            </li>
          </ul>
          <div v-else class="mt-6 flex flex-col items-center gap-2 py-4 text-sm text-slate-400">
            <span
              class="grid size-12 place-items-center rounded-full bg-emerald-400/10 text-2xl text-emerald-400"
              >✓</span
            >
            没有触发中的告警
          </div>
          <div class="mt-4 space-y-1.5">
            <div
              v-for="[ns, p] in pods.slice(0, 5)"
              :key="ns"
              class="flex items-center gap-2 text-xs"
            >
              <span class="w-20 shrink-0 truncate font-mono text-slate-400">{{ ns }}</span>
              <span class="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                <span
                  class="block h-full rounded-full bg-emerald-400"
                  :style="{ width: `${(p.running / Math.max(1, p.total)) * 100}%` }"
                ></span>
              </span>
              <span class="w-10 text-right font-mono text-slate-400"
                >{{ p.running }}/{{ p.total }}</span
              >
            </div>
          </div>
        </div>
      </div>

      <!-- 第三排：发布、证书、自愈、评测 -->
      <div class="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-6">
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">最近发布</div>
          <div class="mt-1 font-mono text-lg text-slate-100">{{ ov?.deploy?.sha ?? '—' }}</div>
          <div class="text-xs text-slate-500">{{ ago(ov?.deploy?.time) }} · GitOps</div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">HTTPS 证书</div>
          <div
            class="mt-1 font-mono text-lg"
            :class="(ov?.cert_days_left ?? 99) < 21 ? 'text-amber-300' : 'text-slate-100'"
          >
            {{ fmt(ov?.cert_days_left) }} 天
          </div>
          <div class="text-xs text-slate-500">后到期 · 自动续期</div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">告警自愈</div>
          <div class="mt-1 font-mono text-lg text-slate-100">
            {{ ov?.heal.fixed ?? '—'
            }}<span class="text-sm text-slate-500"> / {{ ov?.heal.total ?? '—' }}</span>
          </div>
          <div class="text-xs text-slate-500">已恢复 / 处理过</div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">异地备份</div>
          <div class="mt-1 font-mono text-lg text-slate-100">
            {{ ov?.backups?.length ?? '—' }} 个任务
          </div>
          <div class="text-xs text-slate-500">最久的 {{ ago(oldestBackup) }}成功 · R2</div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="text-xs text-slate-400">7 天告警</div>
          <div
            class="mt-1 font-mono text-lg"
            :class="alerts7d.length ? 'text-amber-300' : 'text-emerald-400'"
          >
            {{ alerts7d.length }} 种
          </div>
          <div class="truncate text-xs text-slate-500">
            {{ alerts7d.length ? alerts7d.map((a) => a.name).join('、') : '一周没有告警' }}
          </div>
        </div>
        <div class="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10">
          <div class="truncate text-xs text-slate-400">Agent 故障演练评测</div>
          <div class="mt-1 font-mono text-lg text-slate-100">
            {{ fmt(ov?.evals?.agent?.pass_rate, 1) }}<span class="text-sm">%</span>
          </div>
          <div class="text-xs text-slate-500">
            通过率 · {{ ov?.evals?.agent?.cases ?? '—' }} 题 · {{ ago(ov?.evals?.agent?.ts) }}
          </div>
        </div>
      </div>

      <!-- 第四排：外部拨测 -->
      <div
        v-if="ov?.probes?.length"
        class="mt-3 rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/10"
      >
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <div class="text-xs text-slate-400">外部拨测 · 每分钟从服务器往外探测一次</div>
          <div class="text-xs text-slate-500">
            {{ ov.probes.filter((p) => p.ok).length }}/{{ ov.probes.length }} 正常
          </div>
        </div>
        <div v-for="g in PROBE_GROUPS" :key="g.key" class="mt-3">
          <div class="text-xs text-slate-500">{{ g.name }}</div>
          <div class="mt-1.5 flex flex-wrap gap-2">
            <span
              v-for="p in ov.probes.filter((x) => x.group === g.key)"
              :key="p.name"
              class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-mono text-xs ring-1"
              :class="
                p.ok
                  ? 'bg-emerald-400/10 text-emerald-200 ring-emerald-400/30'
                  : 'bg-rose-500/15 text-rose-200 ring-rose-400/50'
              "
              :title="p.cert_days != null && g.key === 'own' ? `证书还剩 ${p.cert_days} 天` : ''"
            >
              <span
                class="h-1.5 w-1.5 rounded-full"
                :class="p.ok ? 'bg-emerald-400' : 'animate-pulse bg-rose-400'"
              ></span>
              {{ p.name }}
              <span class="text-slate-400">{{ p.ms != null ? `${p.ms}ms` : '—' }}</span>
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
