import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import astro from 'eslint-plugin-astro';
import vue from 'eslint-plugin-vue';

export default [
  { ignores: ['dist/', '.astro/', 'node_modules/', 'ai/.venv/'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...astro.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    // .vue 文件里的 <script lang="ts"> 交给 TypeScript 解析器
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
    // 未定义变量由 TypeScript 检查（typescript-eslint 官方建议关闭 no-undef）
    rules: { 'no-undef': 'off' },
  },
  {
    // deploy/ 下的脚本运行在 Node 里（如告警转发器），声明 Node 的全局变量
    files: ['deploy/**/*.mjs'],
    languageOptions: {
      globals: {
        process: 'readonly',
        console: 'readonly',
        Buffer: 'readonly',
        fetch: 'readonly',
        AbortSignal: 'readonly',
        URL: 'readonly',
        setTimeout: 'readonly',
      },
    },
  },
  {
    // Service Worker 运行在独立的 worker 环境里
    files: ['public/sw.js'],
    languageOptions: {
      globals: {
        self: 'readonly',
        caches: 'readonly',
        fetch: 'readonly',
        URL: 'readonly',
        Response: 'readonly',
      },
    },
  },
  {
    // 格式交给 Prettier，关掉和它冲突的 Vue 模板排版规则
    rules: {
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/html-indent': 'off',
    },
  },
];
