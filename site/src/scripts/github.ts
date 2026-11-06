// Определяет систему посетителя и подтягивает с GitHub свежую версию и звёзды.
// Если GitHub недоступен, на странице просто останутся значения из сборки.

import { REPO, assetUrl, type AssetKey } from '../config';
import { activateTab } from './tabs';

type Os = 'windows' | 'macos' | 'linux' | 'mobile';

const CACHE_KEY = 'hh-auto:github';
const CACHE_TTL = 30 * 60 * 1000;

interface Snapshot {
  at: number;
  stars: number | null;
  version: string | null;
}

function detectOs(): Os {
  const agent = navigator.userAgent;
  if (/Android|iPhone|iPad|iPod/i.test(agent)) return 'mobile';
  if (/Windows/i.test(agent)) return 'windows';
  if (/Macintosh|Mac OS X/i.test(agent)) return 'macos';
  if (/Linux|X11|CrOS/i.test(agent)) return 'linux';
  return 'windows';
}

function applyOs(os: Os) {
  const labels: Record<Os, string> = {
    windows: 'Скачать для Windows',
    macos: 'Скачать для macOS',
    linux: 'Скачать для Linux',
    mobile: 'Как установить на компьютер',
  };
  const assets: Partial<Record<Os, AssetKey>> = {
    windows: 'windows',
    macos: 'macos-arm64',
    linux: 'linux',
  };
  const version = document.documentElement.dataset.release ?? '';

  document.querySelectorAll<HTMLAnchorElement>('[data-download-cta]').forEach((link) => {
    const label = link.querySelector('[data-cta-label]');
    if (label) label.textContent = labels[os];
    const asset = assets[os];
    if (asset) {
      link.dataset.asset = asset;
      link.href = assetUrl(version, asset);
    } else {
      delete link.dataset.asset;
      link.href = '/#install';
    }
  });

  if (os === 'macos' || os === 'linux') {
    const tab = document.querySelector<HTMLElement>(`[data-install] [data-os="${os}"]`);
    if (tab) activateTab(tab);
  }
}

function readCache(): Snapshot | null {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Snapshot) : null;
    return parsed && Date.now() - parsed.at < CACHE_TTL ? parsed : null;
  } catch {
    return null;
  }
}

function writeCache(snapshot: Snapshot) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(snapshot));
  } catch {
    // Хранилище отключено: в следующий раз просто запросим заново
  }
}

async function fetchSnapshot(): Promise<Snapshot | null> {
  const api = `https://api.github.com/repos/${REPO}`;
  const getJson = (url: string) =>
    fetch(url, { headers: { Accept: 'application/vnd.github+json' } }).then((response) => {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    });
  const [repo, release] = await Promise.allSettled([getJson(api), getJson(`${api}/releases/latest`)]);
  if (repo.status === 'rejected' && release.status === 'rejected') return null;
  return {
    at: Date.now(),
    stars: repo.status === 'fulfilled' ? repo.value.stargazers_count : null,
    version: release.status === 'fulfilled' ? release.value.tag_name : null,
  };
}

function applySnapshot({ stars, version }: Snapshot) {
  if (stars !== null) {
    document.querySelectorAll<HTMLElement>('[data-stars]').forEach((element) => {
      element.textContent = String(stars);
      element.hidden = false;
    });
  }

  const built = document.documentElement.dataset.release;
  if (!version || version === built) return;

  document.documentElement.dataset.release = version;
  document.querySelectorAll('[data-version]').forEach((element) => {
    element.textContent = version;
  });
  document.querySelectorAll<HTMLAnchorElement>('a[data-asset]').forEach((link) => {
    link.href = assetUrl(version, link.dataset.asset as AssetKey);
  });
  if (built) {
    document.querySelectorAll('[data-release-text]').forEach((element) => {
      element.textContent = (element.textContent ?? '').replaceAll(built, version);
    });
  }
}

export function initGithub() {
  applyOs(detectOs());
  const cached = readCache();
  if (cached) {
    applySnapshot(cached);
    return;
  }
  fetchSnapshot()
    .then((snapshot) => {
      if (!snapshot) return;
      writeCache(snapshot);
      applySnapshot(snapshot);
    })
    .catch(() => {
      // Нет сети или GitHub не отвечает: страница уже показывает данные из сборки
    });
}
