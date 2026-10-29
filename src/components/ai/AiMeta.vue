<script setup lang="ts">
import type { Meta } from '../../lib/ai';

defineProps<{ meta: Meta }>();
</script>

<template>
  <!-- 每次调用都把模型、token、费用和耗时摆出来：LLMOps 的第一步就是看得见成本 -->
  <p class="mt-4 border-t border-slate-200 pt-3 font-mono text-xs text-slate-500">
    模型 {{ meta.model }} · 输入 {{ meta.tokens_in }} / 输出 {{ meta.tokens_out }} tokens · 约
    {{ (meta.cost * 100).toFixed(3) }} 分钱 · {{ meta.seconds }} 秒
  </p>
</template>
