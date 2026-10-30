import { defineConfig } from 'astro/config';
import vue from '@astrojs/vue';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://ops.liuxd.dev',
  output: 'static',
  // 命令页的 URL 以 / 结尾，和 nginx 的 try_files 目录规则一致
  trailingSlash: 'always',
  // 2026-10-30：平台实况并进运维 Agent 的值班大屏，旧地址跳过去
  redirects: { '/live': '/ops-agent/' },
  integrations: [vue(), sitemap()],
  vite: {
    plugins: [tailwindcss()],
  },
});
