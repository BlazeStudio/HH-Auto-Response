// Вкладки по шаблону WAI-ARIA: щелчок, стрелки, Home и End.
// Подсветка выбранной вкладки скользит к новой, а высота блока меняется плавно.

import { animateHeight } from './motion';

interface ActivateOptions {
  focus?: boolean;
  animate?: boolean;
}

function placeGlider(list: HTMLElement) {
  const tab = list.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
  if (!tab) return;
  list.style.setProperty('--glide-x', `${tab.offsetLeft}px`);
  list.style.setProperty('--glide-y', `${tab.offsetTop}px`);
  list.style.setProperty('--glide-w', `${tab.offsetWidth}px`);
  list.style.setProperty('--glide-h', `${tab.offsetHeight}px`);
}

function initGlider(list: HTMLElement) {
  placeGlider(list);
  list.dataset.glide = 'ready';
  // Анимацию включаем со второго кадра, иначе подсветка приедет из левого верхнего угла
  requestAnimationFrame(() => requestAnimationFrame(() => (list.dataset.glide = 'on')));
  new ResizeObserver(() => placeGlider(list)).observe(list);
  document.fonts?.ready.then(() => placeGlider(list));
}

export function activateTab(tab: HTMLElement, { focus = false, animate = false }: ActivateOptions = {}) {
  const list = tab.closest<HTMLElement>('[role="tablist"]');
  if (!list) return;

  const swap = () => {
    list.querySelectorAll<HTMLElement>('[role="tab"]').forEach((item) => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(item.getAttribute('aria-controls') ?? '');
      if (panel) panel.hidden = !selected;
    });
  };

  const box = document.getElementById(tab.getAttribute('aria-controls') ?? '')?.parentElement;
  if (animate && box) animateHeight(box, swap);
  else swap();

  placeGlider(list);
  if (focus) tab.focus();
}

// Снимки на соседних вкладках подгружаем заранее, как только к вкладкам потянулись
function warmImages(tabs: HTMLElement[]) {
  tabs.forEach((tab) => {
    document
      .getElementById(tab.getAttribute('aria-controls') ?? '')
      ?.querySelectorAll<HTMLImageElement>('img[loading="lazy"]')
      .forEach((image) => {
        image.loading = 'eager';
      });
  });
}

export function initTabs() {
  document.querySelectorAll<HTMLElement>('[role="tablist"]').forEach((list) => {
    const tabs = [...list.querySelectorAll<HTMLElement>('[role="tab"]')];

    initGlider(list);

    const warm = () => warmImages(tabs);
    list.addEventListener('pointerenter', warm, { once: true });
    list.addEventListener('focusin', warm, { once: true });

    list.addEventListener('click', (event) => {
      const tab = (event.target as HTMLElement).closest<HTMLElement>('[role="tab"]');
      if (tab) activateTab(tab, { animate: true });
    });

    list.addEventListener('keydown', (event) => {
      const current = tabs.indexOf(document.activeElement as HTMLElement);
      if (current < 0) return;
      const moves: Record<string, number> = {
        ArrowRight: current + 1,
        ArrowDown: current + 1,
        ArrowLeft: current - 1,
        ArrowUp: current - 1,
        Home: 0,
        End: tabs.length - 1,
      };
      const next = moves[event.key];
      if (next === undefined) return;
      event.preventDefault();
      activateTab(tabs[(next + tabs.length) % tabs.length], { focus: true, animate: true });
    });
  });
}
