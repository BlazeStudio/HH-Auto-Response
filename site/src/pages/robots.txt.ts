import type { APIRoute } from 'astro';
import { sitePath } from '../config';

export const GET: APIRoute = ({ site }) => {
  const sitemap = new URL(sitePath('/sitemap-index.xml'), site);
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${sitemap.href}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
