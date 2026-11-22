<script setup lang="ts">
// 显示 Telegram 格式（HTML 子集）的详情：只放行 b、i、code、pre 和 https 链接，其他标签只留文字，防止注入
import { computed } from 'vue';

const props = defineProps<{ html: string }>();
const ALLOWED = new Set(['B', 'STRONG', 'I', 'EM', 'CODE', 'PRE', 'U', 'S']);

function clean(src: Node, out: Node) {
  for (const n of Array.from(src.childNodes)) {
    if (n.nodeType === Node.TEXT_NODE) {
      out.appendChild(document.createTextNode(n.textContent || ''));
    } else if (n instanceof Element) {
      let el: Element | null = null;
      if (ALLOWED.has(n.tagName)) el = document.createElement(n.tagName.toLowerCase());
      else if (n.tagName === 'A' && /^https:\/\//.test(n.getAttribute('href') || '')) {
        el = document.createElement('a');
        el.setAttribute('href', n.getAttribute('href')!);
        el.setAttribute('target', '_blank');
        el.setAttribute('rel', 'noopener');
      }
      clean(n, el ? out.appendChild(el) : out);
    }
  }
}

const safe = computed(() => {
  const doc = new DOMParser().parseFromString(`<div>${props.html}</div>`, 'text/html');
  const box = document.createElement('div');
  clean(doc.body.firstChild || doc.body, box);
  return box.innerHTML;
});
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- 上面已经过滤成白名单标签 -->
  <div class="tg" v-html="safe"></div>
</template>

<style scoped>
.tg {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.65;
  color: rgb(226 232 240);
}
.tg :deep(b) {
  color: #fde68a;
}
.tg :deep(code) {
  border-radius: 4px;
  background: rgb(30 41 59);
  padding: 0 4px;
}
.tg :deep(a) {
  color: #93c5fd;
  text-decoration: underline;
}
</style>
