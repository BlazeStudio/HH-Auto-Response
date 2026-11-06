// Мелочи интерфейса: граница шапки, световое пятно на карточках, копирование, меню на телефоне,
// плавная прокрутка к разделам, боковая навигация и отклик кнопки скачивания.

import { reducedMotion } from './motion';

function initHeader() {
  const header = document.querySelector<HTMLElement>('[data-header]');
  if (!header) return;
  const update = () => header.toggleAttribute('data-scrolled', window.scrollY > 8);
  update();
  window.addEventListener('scroll', update, { passive: true });
}

function initSpotlight() {
  if (!window.matchMedia('(hover: hover)').matches) return;
  document.querySelectorAll<HTMLElement>('.card-hover').forEach((card) => {
    card.addEventListener('pointermove', (event) => {
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--mx', `${event.clientX - rect.left}px`);
      card.style.setProperty('--my', `${event.clientY - rect.top}px`);
    });
  });
}

function selectText(element: Element) {
  const range = document.createRange();
  range.selectNodeContents(element);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

// Копирует либо значение из data-copy, либо команду из соседнего <code>
function initCopy() {
  document.addEventListener('click', async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-copy]');
    if (!button) return;
    const code = button.parentElement?.querySelector('code');
    const text = button.dataset.copy || code?.textContent;
    if (!text) return;
    const idleLabel = button.dataset.idleLabel ?? button.getAttribute('aria-label') ?? '';
    button.dataset.idleLabel = idleLabel;
    try {
      await navigator.clipboard.writeText(text);
      button.setAttribute('data-done', '');
      button.setAttribute('aria-label', 'Скопировано');
      window.setTimeout(() => {
        button.removeAttribute('data-done');
        button.setAttribute('aria-label', idleLabel);
      }, 1800);
    } catch {
      // Буфер обмена недоступен: выделяем текст, чтобы скопировать его вручную
      const visibleText = code ?? button.parentElement?.querySelector('a');
      if (visibleText) selectText(visibleText);
    }
  });
}

function initMenu() {
  const menu = document.querySelector<HTMLDetailsElement>('[data-menu]');
  if (!menu) return;
  menu.addEventListener('click', (event) => {
    if ((event.target as HTMLElement).closest('a')) menu.open = false;
  });
  document.addEventListener('click', (event) => {
    if (menu.open && !menu.contains(event.target as Node)) menu.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') menu.open = false;
  });
}

// Кнопка скачивания ведёт прямо на файл, поэтому со страницы никто не уходит.
// Подпись на пару секунд подтверждает, что щелчок сработал.
function initDownload() {
  document.addEventListener('click', (event) => {
    const link = (event.target as HTMLElement).closest<HTMLElement>('[data-download-cta][data-asset]');
    const label = link?.querySelector('[data-cta-label]');
    if (!link || !label || link.hasAttribute('data-done')) return;
    const idleText = label.textContent;
    link.setAttribute('data-done', '');
    label.textContent = 'Скачивание началось';
    window.setTimeout(() => {
      link.removeAttribute('data-done');
      label.textContent = idleText;
    }, 3200);
  });
}

function scrollTargetTop(target: HTMLElement) {
  const padding = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0;
  const margin = parseFloat(getComputedStyle(target).scrollMarginTop) || 0;
  const top = target.getBoundingClientRect().top + window.scrollY - padding - margin;
  const limit = document.documentElement.scrollHeight - window.innerHeight;
  return Math.max(0, Math.min(top, limit));
}

// Браузерная прокрутка на длинной странице пролетает слишком резко, поэтому ведём её сами.
// Любое действие посетителя сразу её прерывает.
function glideTo(target: HTMLElement) {
  const start = window.scrollY;
  const duration = Math.min(1150, 520 + Math.abs(scrollTargetTop(target) - start) * 0.11);
  const startedAt = performance.now();
  let frame = 0;

  const stop = () => {
    cancelAnimationFrame(frame);
    ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach((name) => window.removeEventListener(name, stop));
  };
  ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach((name) =>
    window.addEventListener(name, stop, { passive: true }),
  );

  const step = (now: number) => {
    const progress = Math.min(1, (now - startedAt) / duration);
    const eased = progress < 0.5 ? 4 * progress ** 3 : 1 - (-2 * progress + 2) ** 3 / 2;
    // Цель считаем на каждом кадре: страница могла сдвинуться, пока грузились снимки
    const top = start + (scrollTargetTop(target) - start) * eased;
    window.scrollTo({ top, behavior: 'instant' });
    if (progress < 1) {
      frame = requestAnimationFrame(step);
      return;
    }
    stop();
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
  };
  frame = requestAnimationFrame(step);
}

function initAnchors() {
  if (reducedMotion()) return;
  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href*="#"]');
    if (!link || !link.hash || link.origin !== location.origin || link.pathname !== location.pathname) return;
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (!target) return;
    event.preventDefault();
    history.pushState(null, '', link.hash);
    glideTo(target);
  });
}

function initRail() {
  const rail = document.querySelector<HTMLElement>('[data-rail]');
  if (!rail) return;

  const stops = [...rail.querySelectorAll<HTMLAnchorElement>('a')].flatMap((link) => {
    const section = document.getElementById(link.hash.slice(1));
    return section ? [{ link, section }] : [];
  });
  if (!stops.length) return;

  let current: (typeof stops)[number] | undefined;
  let queued = false;

  const update = () => {
    queued = false;
    const line = window.innerHeight * 0.42;
    const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
    let active = stops[0];
    for (const stop of stops) {
      if (stop.section.getBoundingClientRect().top <= line) active = stop;
    }
    if (atBottom) active = stops[stops.length - 1];
    if (active === current) return;
    current?.link.removeAttribute('aria-current');
    active.link.setAttribute('aria-current', 'location');
    current = active;
  };

  window.addEventListener(
    'scroll',
    () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(update);
    },
    { passive: true },
  );
  window.addEventListener('resize', update);
  update();
}

export function initUi() {
  initHeader();
  initSpotlight();
  initCopy();
  initMenu();
  initDownload();
  initAnchors();
  initRail();
}
