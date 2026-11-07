// Всё, что может поменяться без правки вёрстки: ссылки, контакты, имена файлов релиза.
// Импортируется и на сервере при сборке, и в браузере, поэтому здесь только чистые данные.

export const REPO = 'BlazeStudio/HH-Auto-Response';
const repoUrl = `https://github.com/${REPO}`;

export const links = {
  repo: repoUrl,
  releases: `${repoUrl}/releases`,
  latest: `${repoUrl}/releases/latest`,
  changelog: `${repoUrl}/blob/main/CHANGELOG.md`,
  license: `${repoUrl}/blob/main/LICENSE.md`,
  contributing: `${repoUrl}/blob/main/CONTRIBUTING.md`,
  readme: `${repoUrl}#readme`,
  issues: `${repoUrl}/issues`,
  bugReport: `${repoUrl}/issues/new?template=bug_report.yml`,
  featureRequest: `${repoUrl}/issues/new?template=feature_request.yml`,
  ollamaGuide: `${repoUrl}/blob/main/docs/ollama.md`,
};

export const contacts = {
  author: 'Антон Васильев',
  telegram: { label: '@terminallover', url: 'https://t.me/terminallover' },
  email: 'praim916@mail.ru',
  website: { label: 'anton-vas.digital', url: 'https://www.anton-vas.digital/' },
  github: { label: 'BlazeStudio', url: 'https://github.com/BlazeStudio' },
};

// На GitHub Pages сайт лежит в подпапке, поэтому все внутренние ссылки и файлы из public/ идут через эту функцию.
const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
export const sitePath = (target: string) => `${basePath}${target}`;

export const nav = [
  { label: 'Возможности', href: sitePath('/#features') },
  { label: 'Установка', href: sitePath('/#install') },
  { label: 'Анализ резюме', href: sitePath('/#analysis') },
  { label: 'FAQ', href: sitePath('/#faq') },
];

// Версия на случай, если GitHub API недоступен при сборке.
export const fallbackVersion = '1.1.1-beta';

// Имена архивов задаёт .github/workflows/build.yml: HH-Auto-Response-<версия>-<суффикс>.
export const assetSuffix = {
  windows: 'windows.zip',
  'macos-arm64': 'macos-arm64.zip',
  'macos-x86_64': 'macos-x86_64.zip',
  linux: 'linux-x86_64.tar.gz',
} as const;

export type AssetKey = keyof typeof assetSuffix;

export const assetName = (version: string, key: AssetKey) =>
  `HH-Auto-Response-${version}-${assetSuffix[key]}`;

export const assetUrl = (version: string, key: AssetKey) =>
  `${repoUrl}/releases/download/${version}/${assetName(version, key)}`;

export const assetLatestUrl = (version: string, key: AssetKey) =>
  `${repoUrl}/releases/latest/download/${assetName(version, key)}`;
