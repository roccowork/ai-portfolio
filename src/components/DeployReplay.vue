<script setup lang="ts">
// 回放最近一次真实发布：从 git push 到新版本上线，每一步的时间都来自真实记录。
// 上线时间来自 /status.json（集群里 ReplicaSet 的创建时间），其余来自 GitHub 公开 API（提交、CI 每个 job 和 step 的起止时间）。
// GitHub API 不可用（限流等）时只显示步骤，不显示时间
import { computed, onMounted, onUnmounted, ref } from 'vue';
import type { Deploy, Status } from '../lib/status';

const REPO = 'roccowork/ai-portfolio';
const API = `https://api.github.com/repos/${REPO}`;

interface Step {
  t: string;
  d: string;
  start?: string;
  end?: string;
}
interface GhStep {
  name: string;
  started_at: string;
  completed_at: string;
}
interface GhJob {
  name: string;
  started_at: string;
  completed_at: string;
  steps: GhStep[];
}

const deploys = ref<Deploy[]>([]);
const message = ref('');
const steps = ref<Step[]>([
  { t: 'git push', d: '推送到 main' },
  { t: 'CI 检查', d: '格式、lint、类型检查、单元测试' },
  { t: '构建镜像', d: '推到 GHCR，标签是 commit SHA' },
  { t: '写回 Git', d: '只改部署清单里的镜像标签' },
  { t: 'Argo CD 同步', d: '发现 Git 变了，同步到集群' },
  { t: '滚动更新', d: '新 Pod 就绪才接流量，旧的再下线' },
]);
const active = ref(-1);
const loaded = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;

const gh = async <T,>(path: string) => {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(String(r.status));
  return (await r.json()) as T;
};
const minT = (xs: (string | undefined)[]) => xs.filter(Boolean).sort()[0];
const maxT = (xs: (string | undefined)[]) => xs.filter(Boolean).sort().at(-1);

async function load() {
  try {
    const st = (await (await fetch('/status.json')).json()) as Status;
    deploys.value = st.deploys ?? [];
  } catch {
    deploys.value = [];
  }
  const latest = deploys.value.find((d) => d.state === 'current') ?? deploys.value[0];
  if (latest) {
    const s = steps.value;
    if (latest.time) s[5].start = latest.time;
    try {
      const c = await gh<{ sha: string; commit: { message: string; committer: { date: string } } }>(
        `/commits/${latest.sha}`,
      );
      message.value = c.commit.message.split('\n')[0];
      s[0].start = s[0].end = c.commit.committer.date;
      const runs = await gh<{ workflow_runs: { id: number; name: string }[] }>(
        `/actions/runs?head_sha=${c.sha}&per_page=5`,
      );
      const run = runs.workflow_runs.find((r) => r.name === 'CI');
      if (run) {
        const { jobs } = await gh<{ jobs: GhJob[] }>(`/actions/runs/${run.id}/jobs`);
        const checks = jobs.filter((j) => j.name !== 'release');
        s[1].start = minT(checks.map((j) => j.started_at));
        s[1].end = maxT(checks.map((j) => j.completed_at));
        const rel = jobs.find((j) => j.name === 'release')?.steps ?? [];
        const builds = rel.filter((x) => x.name.startsWith('Run docker/build-push'));
        s[2].start = minT(builds.map((x) => x.started_at));
        s[2].end = maxT(builds.map((x) => x.completed_at));
        const bump = rel.find((x) => x.name.includes('镜像标签'));
        s[3].start = bump?.started_at;
        s[3].end = bump?.completed_at;
        s[4].start = s[3].end;
        s[4].end = latest.time ?? undefined;
      }
    } catch {
      // GitHub API 限流或网络问题：只显示步骤
    }
  }
  loaded.value = true;
  play();
}

function play() {
  clearTimeout(timer);
  active.value = -1;
  const next = () => {
    active.value++;
    if (active.value < steps.value.length) timer = setTimeout(next, 1100);
  };
  timer = setTimeout(next, 400);
}
onMounted(load);
onUnmounted(() => clearTimeout(timer));

const hm = (iso?: string | null) =>
  iso
    ? new Date(iso).toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      })
    : '';
const secs = (a?: string, b?: string) =>
  a && b ? Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 1000)) : null;
const dur = (n: number | null) =>
  n == null ? '' : n >= 60 ? `${Math.floor(n / 60)} 分 ${n % 60} 秒` : `${n} 秒`;
const total = computed(() => secs(steps.value[0].start, steps.value[5].start));
const day = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '—';
const latestSha = computed(
  () => (deploys.value.find((d) => d.state === 'current') ?? deploys.value[0])?.sha,
);
</script>

<template>
  <div class="rounded-3xl bg-slate-900 p-5 text-slate-200 ring-1 ring-white/10 sm:p-8">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="text-sm font-semibold text-white">回放：最近一次真实发布</p>
        <p v-if="latestSha" class="mt-1 font-mono text-xs text-slate-400">
          <a
            :href="`https://github.com/${REPO}/commit/${latestSha}`"
            target="_blank"
            rel="noopener"
            class="text-amber-300 hover:underline"
            >{{ latestSha }}</a
          >
          <span v-if="message" class="ml-2 font-sans">{{ message }}</span>
        </p>
      </div>
      <button
        class="rounded-full px-4 py-1.5 text-sm font-semibold ring-1 ring-white/25 hover:bg-white/10"
        @click="play"
      >
        ↻ 重放
      </button>
    </div>

    <p v-if="!loaded" class="mt-6 text-sm text-slate-400">读取发布记录……</p>
    <template v-else>
      <!-- 进度条 -->
      <div class="mt-6 h-1 overflow-hidden rounded-full bg-white/10">
        <div
          class="h-full rounded-full bg-gradient-to-r from-amber-400 to-emerald-400 transition-all duration-1000 ease-out"
          :style="{ width: `${Math.min(100, ((active + 1) / steps.length) * 100)}%` }"
        ></div>
      </div>
      <ol class="mt-5 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <li
          v-for="(s, i) in steps"
          :key="s.t"
          class="relative rounded-2xl p-4 ring-1 transition-all duration-500"
          :class="
            i < active
              ? 'bg-emerald-400/10 ring-emerald-400/40'
              : i === active
                ? 'bg-amber-400/15 ring-amber-300 shadow-[0_0_24px_rgba(251,191,36,.25)]'
                : 'bg-white/[0.03] opacity-50 ring-white/10'
          "
        >
          <div class="flex items-center justify-between font-mono text-xs">
            <span :class="i <= active ? 'text-amber-300' : 'text-slate-500'">0{{ i + 1 }}</span>
            <span v-if="i < active" class="text-emerald-400">✓</span>
            <span v-else-if="i === active" class="flex h-2 w-2">
              <span
                class="absolute h-2 w-2 animate-ping rounded-full bg-amber-300 opacity-75"
              ></span>
              <span class="relative h-2 w-2 rounded-full bg-amber-300"></span>
            </span>
          </div>
          <div class="mt-1 font-semibold text-white">{{ s.t }}</div>
          <p class="mt-1 text-xs leading-relaxed text-slate-400">{{ s.d }}</p>
          <p v-if="s.start && i <= active" class="mt-2 font-mono text-xs text-slate-300">
            {{ hm(s.start) }}
            <span v-if="secs(s.start, s.end)" class="text-emerald-300">
              · {{ dur(secs(s.start, s.end)) }}</span
            >
          </p>
        </li>
      </ol>
      <p
        v-if="total && active >= steps.length - 1"
        class="mt-5 text-sm text-slate-300 transition-opacity"
      >
        从 push 到新版本开始上线：<b class="font-mono text-emerald-300">{{ dur(total) }}</b>
        <span class="text-slate-500"
          >（时间来自 GitHub Actions 记录和集群里新 ReplicaSet 的创建时间）</span
        >
      </p>

      <div v-if="deploys.length" class="mt-6 border-t border-white/10 pt-4">
        <p class="text-xs text-slate-400">最近的发布（网站）</p>
        <ul class="mt-2 grid gap-1 font-mono text-xs sm:grid-cols-2">
          <li v-for="d in deploys.slice(0, 6)" :key="d.sha" class="flex gap-3">
            <span class="text-slate-500">{{ day(d.time) }}</span>
            <a
              :href="`https://github.com/${REPO}/commit/${d.sha}`"
              target="_blank"
              rel="noopener"
              class="text-amber-300 hover:underline"
              >{{ d.sha }}</a
            >
            <span :class="d.state === 'current' ? 'text-emerald-400' : 'text-slate-500'">{{
              d.state === 'current' ? '当前版本' : d.state === 'not-ready' ? '未就绪' : '已替换'
            }}</span>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>
