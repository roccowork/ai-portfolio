<script setup lang="ts">
// AI 助手：右下角的对话窗口。回答依据 ai/knowledge/ 下的作品资料（/api/ai/ask，RAG），
// 访客留言后提交到 /api/ai/lead，后台推送到我的 Telegram。
import { nextTick, onMounted, ref } from 'vue';
import { postJSON } from '../lib/ai';

interface Msg {
  role: 'user' | 'assistant';
  content: string;
}

const open = ref(false);
const view = ref<'chat' | 'lead' | 'done'>('chat');
const msgs = ref<Msg[]>([
  {
    role: 'assistant',
    content:
      '你好，我是这个作品集的 AI 助手，回答依据本站的作品资料。可以问我做过哪些项目、用了什么技术、某个设计为什么这样做。',
  },
]);
const input = ref('');
const busy = ref(false);
const error = ref('');
const list = ref<HTMLElement | null>(null);
const quick = ['有哪些代表作？', '运维 Agent 怎么保证安全？', '用到了哪些技术栈？'];

const lead = ref({ name: '', contact: '', company: '', need: '' });

function show() {
  open.value = true;
  nextTick(scrollDown);
}

onMounted(() => {
  window.addEventListener('reception:open', show);
  const w = window as { __chatPending?: boolean };
  if (w.__chatPending) {
    w.__chatPending = false;
    show();
  }
});

function scrollDown() {
  if (list.value) list.value.scrollTop = list.value.scrollHeight;
}

async function send(text = input.value) {
  const q = text.trim();
  if (!q || busy.value) return;
  error.value = '';
  input.value = '';
  // 只带最近几轮对话，控制费用；第一条欢迎语不带
  const history = msgs.value.slice(1).slice(-6);
  msgs.value.push({ role: 'user', content: q });
  busy.value = true;
  await nextTick(scrollDown);
  try {
    const r = await postJSON<{ answer: string }>('/api/ai/ask', {
      question: q.slice(0, 300),
      history,
    });
    msgs.value.push({ role: 'assistant', content: r.answer });
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
    await nextTick(scrollDown);
  }
}

async function submitLead() {
  error.value = '';
  if (!lead.value.contact.trim() || !lead.value.need.trim()) {
    error.value = '请填写联系方式和留言';
    return;
  }
  busy.value = true;
  try {
    const chat = msgs.value
      .slice(1)
      .map((m) => `${m.role === 'user' ? '访客' : 'AI'}：${m.content}`)
      .join('\n')
      .slice(-3000);
    await postJSON('/api/ai/lead', { ...lead.value, chat });
    view.value = 'done';
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}

const field =
  'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 focus:ring-1 focus:ring-slate-900';
</script>

<template>
  <div class="fixed right-4 bottom-4 z-50 sm:right-6 sm:bottom-6">
    <div
      v-if="open"
      class="mb-3 flex h-[min(560px,calc(100vh-7rem))] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-slate-200"
    >
      <div class="flex items-center gap-3 bg-slate-950 px-4 py-3 text-white">
        <span
          class="grid size-8 place-items-center rounded-lg bg-amber-400 text-xs font-bold text-slate-950"
          >AI</span
        >
        <div class="leading-tight">
          <p class="text-sm font-semibold">作品集 AI 助手</p>
          <p class="flex items-center gap-1.5 text-xs text-slate-400">
            <span class="size-1.5 rounded-full bg-emerald-400"></span>在线 · 通常几秒内回复
          </p>
        </div>
        <button
          type="button"
          class="ml-auto text-xl text-slate-400 hover:text-white"
          aria-label="关闭"
          @click="open = false"
        >
          ×
        </button>
      </div>

      <template v-if="view === 'chat'">
        <div ref="list" class="flex-1 space-y-3 overflow-y-auto bg-slate-50 p-4">
          <div
            v-for="(m, i) in msgs"
            :key="i"
            :class="m.role === 'user' ? 'flex justify-end' : 'flex'"
          >
            <p
              class="max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap"
              :class="
                m.role === 'user'
                  ? 'bg-slate-900 text-white'
                  : 'bg-white text-slate-700 ring-1 ring-slate-200'
              "
            >
              {{ m.content }}
            </p>
          </div>
          <p v-if="busy" class="text-xs text-slate-400">正在思考……</p>
          <div v-if="msgs.length === 1" class="flex flex-wrap gap-2 pt-1">
            <button
              v-for="q in quick"
              :key="q"
              type="button"
              class="rounded-full bg-white px-3 py-1.5 text-xs text-slate-600 ring-1 ring-slate-200 hover:ring-slate-400"
              @click="send(q)"
            >
              {{ q }}
            </button>
          </div>
        </div>
        <p v-if="error" class="px-4 pt-2 text-xs text-red-600">{{ error }}</p>
        <button
          type="button"
          class="mx-4 mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 ring-1 ring-amber-200 hover:bg-amber-100"
          @click="view = 'lead'"
        >
          招聘或交流？给我留言 →
        </button>
        <form class="flex gap-2 p-4" @submit.prevent="send()">
          <input
            v-model="input"
            :class="field"
            class="mt-0"
            maxlength="300"
            placeholder="输入你的问题……"
          />
          <button
            type="submit"
            :disabled="busy || !input.trim()"
            class="shrink-0 rounded-lg bg-slate-900 px-4 text-sm font-medium text-white disabled:opacity-40"
          >
            发送
          </button>
        </form>
      </template>

      <form
        v-else-if="view === 'lead'"
        class="flex-1 space-y-3 overflow-y-auto p-5"
        @submit.prevent="submitLead"
      >
        <p class="text-sm text-slate-600">留下联系方式和留言，我会尽快回复。</p>
        <label class="block text-xs font-medium text-slate-700">
          称呼
          <input v-model="lead.name" :class="field" maxlength="40" placeholder="如：王女士" />
        </label>
        <label class="block text-xs font-medium text-slate-700">
          联系方式 <span class="text-red-500">*</span>
          <input
            v-model="lead.contact"
            :class="field"
            maxlength="80"
            placeholder="微信号 / 手机 / 邮箱"
          />
        </label>
        <label class="block text-xs font-medium text-slate-700">
          公司
          <input v-model="lead.company" :class="field" maxlength="80" placeholder="选填" />
        </label>
        <label class="block text-xs font-medium text-slate-700">
          留言 <span class="text-red-500">*</span>
          <textarea
            v-model="lead.need"
            :class="field"
            rows="3"
            maxlength="1000"
            placeholder="如：我们在招 AI 应用开发工程师，想约个时间聊聊"
          />
        </label>
        <p v-if="error" class="text-xs text-red-600">{{ error }}</p>
        <div class="flex gap-2 pt-1">
          <button
            type="button"
            class="rounded-lg px-4 py-2 text-sm text-slate-600 ring-1 ring-slate-300"
            @click="view = 'chat'"
          >
            返回对话
          </button>
          <button
            type="submit"
            :disabled="busy"
            class="flex-1 rounded-lg bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {{ busy ? '提交中……' : '提交' }}
          </button>
        </div>
        <p class="text-xs text-slate-400">信息只用于回复你，不会提供给第三方。</p>
      </form>

      <div v-else class="flex flex-1 flex-col items-center justify-center p-8 text-center">
        <p class="text-3xl">✓</p>
        <p class="mt-3 font-semibold text-slate-900">已收到，谢谢！</p>
        <p class="mt-2 text-sm text-slate-600">我会尽快回复。在这之前，还可以继续问 AI 助手。</p>
        <button type="button" class="mt-6 text-sm text-slate-900 underline" @click="view = 'chat'">
          继续对话
        </button>
      </div>
    </div>

    <button
      v-if="!open"
      type="button"
      class="flex items-center gap-2 rounded-full bg-slate-950 py-3 pr-5 pl-3 text-sm font-medium text-white shadow-xl ring-1 ring-white/10 transition hover:bg-slate-800"
      @click="show"
    >
      <span
        class="grid size-7 place-items-center rounded-full bg-amber-400 text-xs font-bold text-slate-950"
        >AI</span
      >
      问 AI 助手
    </button>
  </div>
</template>
