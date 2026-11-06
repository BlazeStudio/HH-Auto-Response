// Мелочи интерфейса: граница шапки, световое пятно на карточках, копирование команд, меню на телефоне.

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

function initCopy() {
  document.addEventListener('click', async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-copy]');
    const code = button?.parentElement?.querySelector('code');
    if (!button || !code) return;
    try {
      await navigator.clipboard.writeText(code.textContent ?? '');
      button.setAttribute('data-done', '');
      button.setAttribute('aria-label', 'Скопировано');
      window.setTimeout(() => {
        button.removeAttribute('data-done');
        button.setAttribute('aria-label', 'Скопировать команду');
      }, 1600);
    } catch {
      // Буфер обмена недоступен: выделяем команду, чтобы скопировать её вручную
      selectText(code);
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

export function initUi() {
  initHeader();
  initSpotlight();
  initCopy();
  initMenu();
}
