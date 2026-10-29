import { defineConfig } from 'astro/config';
import vue from '@astrojs/vue';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://ops.liuxd.dev',
  output: 'static',
  // 命令页的 URL 以 / 结尾，和 nginx 的 try_files 目录规则一致
  trailingSlash: 'always',
  integrations: [vue(), sitemap()],
  vite: {
    plugins: [tailwindcss()],
  },
});
