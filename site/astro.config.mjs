import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

// Адрес сайта нужен для canonical, Open Graph и sitemap.
// Свой домен задаётся переменной SITE_URL в настройках проекта Vercel,
// без неё берётся продакшен-адрес, который Vercel сообщает при сборке.
const vercelUrl = process.env.VERCEL_PROJECT_PRODUCTION_URL;
const site =
  process.env.SITE_URL ??
  (vercelUrl ? `https://${vercelUrl}` : 'https://hh-auto-response.vercel.app');

export default defineConfig({
  site,
  output: 'static',
  trailingSlash: 'never',
  // Сжатие HTML съедает пробелы между текстом и ссылкой на соседних строках. Gzip на Vercel всё равно сожмёт файл.
  compressHTML: false,
  build: {
    format: 'file',
    // Стили и скрипты только отдельными файлами: так работает строгий CSP из vercel.json.
    inlineStylesheets: 'never',
  },
  devToolbar: { enabled: false },
  integrations: [sitemap()],
  vite: {
    plugins: [tailwindcss()],
    build: { assetsInlineLimit: 0 },
  },
});
