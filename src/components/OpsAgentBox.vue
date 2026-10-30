<script setup lang="ts">
// 运维 Agent 控制台：访客提问，后端的 Agent 用只读工具查本站真实的服务器。
// 走 /api/ai/opsagent/stream（Server-Sent Events）：思考、调用工具、程序核对、结论一步步实时显示
import { nextTick, ref } from 'vue';
import type { Meta } from '../lib/ai';

interface Line {
  kind: 'thinking' | 'tool' | 'check';
  text: string;
  detail?: string;
  status?: 'running' | 'done';
  index?: number;
}

const TOOL_NAMES: Record<string, string> = {
  list_alerts: '查看告警',
  cluster_overview: '查看服务器概况',
  list_pods: '查看服务运行状态',
  pod_events: '查看最近的事件',
  promql: '查询监控指标',
  pod_logs: '查看日志',
  search_runbook: '查运维手册',
};

const examples = [
  '服务器现在健康吗？',
  '最近有什么告警？',
  '哪个服务最占内存？',
  '网站最近一小时的访问量和错误率怎么样？',
  '网站出问题了怎么回滚？',
];
const question = ref('');
const busy = ref(false);
const error = ref('');
const answer = ref('');
const shown = ref(''); // 打字机效果：结论逐字出现
const meta = ref<Meta | null>(null);
const lines = ref<Line[]>([]);
const started = ref(0);
const elapsed = ref(0);
const log = ref<HTMLElement | null>(null);

function argText(args: Record<string, unknown>): string {
  if (args.query) return String(args.query);
  if (args.namespace) return `${args.namespace}${args.pod ? ` / ${args.pod}` : ''}`;
  return '';
}

async function scroll() {
  await nextTick();
  log.value?.scrollTo({ top: log.value.scrollHeight, behavior: 'smooth' });
}

interface AgentEvent {
  type: 'thinking' | 'tool' | 'check' | 'answer' | 'error';
  round?: number;
  index?: number;
  tool?: string;
  args?: Record<string, unknown>;
  status?: 'running' | 'done';
  summary?: string;
  detail?: string;
  answer?: string;
  meta?: Meta;
}

function onEvent(e: AgentEvent) {
  if (e.type === 'thinking') {
    lines.value = lines.value.filter((l) => l.kind !== 'thinking');
    lines.value.push({
      kind: 'thinking',
      text:
        e.round === 1
          ? '正在理解问题，决定先查什么'
          : `第 ${e.round} 轮：根据查到的结果，决定下一步`,
    });
  } else if (e.type === 'tool') {
    lines.value = lines.value.filter((l) => l.kind !== 'thinking');
    const name = TOOL_NAMES[e.tool ?? ''] || e.tool || '';
    const found = lines.value.find((l) => l.kind === 'tool' && l.index === e.index);
    if (found) Object.assign(found, { status: e.status, detail: e.summary });
    else
      lines.value.push({
        kind: 'tool',
        index: e.index,
        text: name,
        detail: argText(e.args || {}),
        status: e.status,
      });
  } else if (e.type === 'check') {
    lines.value = lines.value.filter((l) => l.kind !== 'thinking');
    lines.value.push({ kind: 'check', text: '程序核对', detail: e.detail });
  } else if (e.type === 'answer') {
    lines.value = lines.value.filter((l) => l.kind !== 'thinking');
    answer.value = String(e.answer).replace(/\*\*|`/g, ''); // 模型偶尔带 Markdown 符号，按纯文本显示
    meta.value = e.meta ?? null;
    type();
  } else if (e.type === 'error') {
    error.value = e.detail ?? '出错了';
  }
  scroll();
}

function type() {
  const full = answer.value;
  const step = Math.max(1, Math.ceil(full.length / 120)); // 最多约 2.5 秒打完
  let i = 0;
  const t = window.setInterval(() => {
    i = Math.min(full.length, i + step);
    shown.value = full.slice(0, i);
    if (i >= full.length) window.clearInterval(t);
    scroll();
  }, 20);
}

async function ask(q = question.value) {
  const text = q.trim();
  if (text.length < 2 || busy.value) return;
  question.value = text;
  busy.value = true;
  error.value = answer.value = shown.value = '';
  meta.value = null;
  lines.value = [];
  started.value = Date.now();
  const timer = window.setInterval(
    () => (elapsed.value = (Date.now() - started.value) / 1000),
    100,
  );
  try {
    const r = await fetch('/api/ai/opsagent/stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question: text }),
    });
    if (!r.ok || !r.body) {
      let detail = '';
      try {
        detail = (await r.json()).detail;
      } catch {
        // 不是 JSON
      }
      throw new Error(
        typeof detail === 'string' && detail
          ? detail
          : r.status === 429
            ? '请求太频繁，请稍后再试'
            : `服务暂时不可用（${r.status}）`,
      );
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut: number;
      while ((cut = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const data = chunk.split('\n').find((l) => l.startsWith('data: '));
        if (data) onEvent(JSON.parse(data.slice(6)) as AgentEvent);
      }
    }
    if (!answer.value && !error.value) error.value = '连接中断了，请再试一次';
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    window.clearInterval(timer);
    busy.value = false;
  }
}
</script>

<template>
  <div class="overflow-hidden rounded-2xl bg-slate-900/80 ring-1 ring-white/10">
    <!-- 标题栏 -->
    <div
      class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-white/10 px-4 py-3 text-xs"
    >
      <span class="flex shrink-0 items-center gap-2 font-mono text-slate-300">
        <span class="flex gap-1.5"
          ><i class="size-2.5 rounded-full bg-rose-400/80"></i
          ><i class="size-2.5 rounded-full bg-amber-300/80"></i
          ><i class="size-2.5 rounded-full bg-emerald-400/80"></i
        ></span>
        ops-agent@k3s
      </span>
      <span class="font-mono text-slate-500">
        <template v-if="busy">排查中 {{ elapsed.toFixed(1) }}s</template>
        <template v-else-if="meta"
          >{{ meta.model }} · {{ meta.tokens_in + meta.tokens_out }} tokens · ¥{{
            meta.cost.toFixed(4)
          }}
          · {{ meta.seconds.toFixed(1) }}s</template
        >
        <template v-else>只读 · 每人每天 10 次</template>
      </span>
    </div>

    <!-- 输入 -->
    <form class="flex flex-col gap-2 p-4 sm:flex-row" @submit.prevent="ask()">
      <div
        class="flex flex-1 items-center gap-2 rounded-xl bg-black/40 px-3 ring-1 ring-white/10 focus-within:ring-emerald-400/60"
      >
        <span class="font-mono text-emerald-400">❯</span>
        <input
          v-model="question"
          maxlength="200"
          placeholder="问它关于这台服务器的任何问题……"
          class="w-full bg-transparent py-3 text-slate-100 placeholder:text-slate-500 outline-none"
        />
      </div>
      <button
        type="submit"
        :disabled="busy || question.trim().length < 2"
        class="rounded-xl bg-emerald-500 px-6 py-3 font-medium text-slate-950 transition hover:bg-emerald-400 disabled:opacity-40"
      >
        {{ busy ? '排查中……' : '开始排查' }}
      </button>
    </form>
    <div class="flex flex-wrap gap-2 px-4 pb-4">
      <button
        v-for="q in examples"
        :key="q"
        type="button"
        :disabled="busy"
        class="rounded-full bg-white/5 px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/10 hover:ring-emerald-400/50 disabled:opacity-40"
        @click="ask(q)"
      >
        {{ q }}
      </button>
    </div>

    <!-- 实时排查过程 + 结论 -->
    <div
      v-if="lines.length || answer || error"
      ref="log"
      class="max-h-[460px] overflow-y-auto border-t border-white/10 bg-black/30 p-4 font-mono text-sm"
    >
      <ol class="space-y-2">
        <li v-for="(l, i) in lines" :key="i" class="flex gap-3">
          <span class="mt-0.5 w-4 shrink-0 text-center">
            <span
              v-if="l.kind === 'thinking' || l.status === 'running'"
              class="inline-block size-3 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent"
            ></span>
            <span v-else-if="l.kind === 'check'" class="text-amber-300">!</span>
            <span v-else-if="l.detail?.startsWith('被程序拦下')" class="text-rose-400">✕</span>
            <span v-else class="text-emerald-400">✓</span>
          </span>
          <span class="min-w-0">
            <span
              :class="
                l.kind === 'thinking'
                  ? 'text-slate-400'
                  : l.kind === 'check'
                    ? 'text-amber-300'
                    : 'text-slate-100'
              "
              >{{ l.text }}</span
            >
            <span v-if="l.detail" class="ml-2 break-all text-xs text-slate-500">{{
              l.detail
            }}</span>
          </span>
        </li>
      </ol>
      <p v-if="error" class="mt-3 text-rose-400">{{ error }}</p>
      <div
        v-if="answer"
        class="mt-4 rounded-xl bg-emerald-400/5 p-4 font-sans ring-1 ring-emerald-400/20"
      >
        <p class="text-xs font-semibold tracking-widest text-emerald-300">结论</p>
        <p class="mt-2 leading-relaxed whitespace-pre-wrap text-slate-100">
          {{ shown }}<span v-if="shown.length < answer.length" class="animate-pulse">▍</span>
        </p>
      </div>
    </div>
  </div>
</template>
