<script setup lang="ts">
// 运维 Agent 值班台：访客提问，后端的 Agent 用只读工具查本站真实的服务器（/api/ai/opsagent），返回排查步骤和结论
import { ref } from 'vue';
import { postJSON } from '../lib/ai';

interface Step {
  tool: string;
  summary: string;
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
const steps = ref<Step[]>([]);

async function ask(q = question.value) {
  const text = q.trim();
  if (text.length < 2 || busy.value) return;
  question.value = text;
  busy.value = true;
  error.value = '';
  answer.value = '';
  steps.value = [];
  try {
    const r = await postJSON<{ answer: string; steps: Step[] }>('/api/ai/opsagent', {
      question: text,
    });
    // 模型偶尔还会带 Markdown 符号，这里按纯文本显示，去掉粗体和代码标记
    answer.value = r.answer.replace(/\*\*|`/g, '');
    steps.value = r.steps;
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200 sm:p-8">
    <form class="flex flex-col gap-3 sm:flex-row" @submit.prevent="ask()">
      <input
        v-model="question"
        maxlength="200"
        placeholder="问它关于这台服务器的任何问题……"
        class="flex-1 rounded-xl border border-slate-300 px-4 py-3 outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900"
      />
      <button
        type="submit"
        :disabled="busy || question.trim().length < 2"
        class="rounded-xl bg-slate-900 px-6 py-3 font-medium text-white transition hover:bg-slate-700 disabled:opacity-40"
      >
        {{ busy ? '排查中……' : '开始排查' }}
      </button>
    </form>
    <div class="mt-4 flex flex-wrap gap-2">
      <button
        v-for="q in examples"
        :key="q"
        type="button"
        :disabled="busy"
        class="rounded-full bg-slate-50 px-3 py-1.5 text-sm text-slate-600 ring-1 ring-slate-200 hover:ring-slate-400 disabled:opacity-50"
        @click="ask(q)"
      >
        {{ q }}
      </button>
    </div>

    <p v-if="busy" class="mt-8 text-sm text-slate-500">
      Agent 正在调用工具查服务器，一般需要 10～30 秒……
    </p>
    <p v-if="error" class="mt-8 text-sm text-red-600">{{ error }}</p>

    <div v-if="steps.length" class="mt-8">
      <p class="text-sm font-semibold text-slate-900">排查过程</p>
      <ol class="mt-3 space-y-2">
        <li v-for="(s, i) in steps" :key="i" class="flex gap-3 text-sm">
          <span
            class="grid size-6 shrink-0 place-items-center rounded-full bg-amber-100 text-xs font-semibold text-amber-800"
            >{{ i + 1 }}</span
          >
          <span class="text-slate-700">
            {{ TOOL_NAMES[s.tool] || s.tool }}
            <span class="text-slate-400">· {{ s.summary }}</span>
          </span>
        </li>
      </ol>
    </div>
    <div v-if="answer" class="mt-8 rounded-xl bg-slate-50 p-5 ring-1 ring-slate-200">
      <p class="text-sm font-semibold text-slate-900">结论</p>
      <p class="mt-2 leading-relaxed whitespace-pre-wrap text-slate-700">{{ answer }}</p>
    </div>
  </div>
</template>
