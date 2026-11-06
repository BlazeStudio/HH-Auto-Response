import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

// На GitHub Pages сайт лежит в подпапке: https://blazestudio.github.io/HH-Auto-Response/.
// SITE_URL и BASE_PATH подставляет .github/workflows/pages.yml из настроек Pages, поэтому для своего домена
// здесь ничего менять не придётся. Локально сайт открывается от корня.
const site = process.env.SITE_URL || 'https://blazestudio.github.io';
const base = process.env.BASE_PATH || '/';

export default defineConfig({
  site,
  base,
  output: 'static',
  trailingSlash: 'never',
  // Сжатие HTML съедает пробелы между текстом и ссылкой на соседних строках. Gzip на хостинге всё равно сожмёт файл.
  compressHTML: false,
  build: {
    format: 'file',
    // Стили и скрипты только отдельными файлами: так работает строгий CSP из Base.astro.
    inlineStylesheets: 'never',
  },
  devToolbar: { enabled: false },
  integrations: [sitemap()],
  vite: {
    plugins: [tailwindcss()],
    build: { assetsInlineLimit: 0 },
  },
});
