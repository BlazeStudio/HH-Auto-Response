// Вкладки по шаблону WAI-ARIA: щелчок, стрелки, Home и End.

export function activateTab(tab: HTMLElement, focus = false) {
  const list = tab.closest('[role="tablist"]');
  if (!list) return;
  list.querySelectorAll<HTMLElement>('[role="tab"]').forEach((item) => {
    const selected = item === tab;
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(item.getAttribute('aria-controls') ?? '');
    if (panel) panel.hidden = !selected;
  });
  if (focus) tab.focus();
}

export function initTabs() {
  document.querySelectorAll<HTMLElement>('[role="tablist"]').forEach((list) => {
    const tabs = [...list.querySelectorAll<HTMLElement>('[role="tab"]')];

    list.addEventListener('click', (event) => {
      const tab = (event.target as HTMLElement).closest<HTMLElement>('[role="tab"]');
      if (tab) activateTab(tab);
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
      activateTab(tabs[(next + tabs.length) % tabs.length], true);
    });
  });
}
