import { REPO, fallbackVersion } from '../config';

export interface RepoSnapshot {
  version: string;
  stars: number | null;
}

async function getJson(path: string) {
  const headers: Record<string, string> = { Accept: 'application/vnd.github+json' };
  // Необязательно. С токеном у GitHub выше лимит запросов на общих IP сборщика.
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
  const response = await fetch(`https://api.github.com/repos/${REPO}${path}`, {
    headers,
    signal: AbortSignal.timeout(6000),
  });
  if (!response.ok) throw new Error(`GitHub API ${response.status}`);
  return response.json();
}

async function load(): Promise<RepoSnapshot> {
  const [repo, release] = await Promise.allSettled([getJson(''), getJson('/releases/latest')]);
  return {
    stars: repo.status === 'fulfilled' ? repo.value.stargazers_count : null,
    version: release.status === 'fulfilled' ? release.value.tag_name : fallbackVersion,
  };
}

let snapshot: Promise<RepoSnapshot> | undefined;

// Версия и звёзды попадают в HTML уже при сборке, а в браузере обновляются скриптом.
// Без сети сборка не падает: версия берётся из config.ts, счётчик звёзд просто не показывается.
export function getRepoSnapshot(): Promise<RepoSnapshot> {
  snapshot ??= load();
  return snapshot;
}
