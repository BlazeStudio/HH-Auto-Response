// Общие приёмы плавности. Всё отключается, если в системе включено «уменьшить движение».

export const EASE = 'cubic-bezier(0.22, 0.8, 0.24, 1)';

export const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Выполняет change и плавно подводит высоту блока к новому содержимому. */
export function animateHeight(box: HTMLElement, change: () => void, duration = 440) {
  if (reducedMotion()) {
    change();
    return;
  }
  const from = box.getBoundingClientRect().height;
  change();
  const to = box.getBoundingClientRect().height;
  if (Math.abs(from - to) < 1) return;
  box.animate(
    [
      { height: `${from}px`, overflow: 'hidden' },
      { height: `${to}px`, overflow: 'hidden' },
    ],
    { duration, easing: EASE },
  );
}

/** Лёгкий толчок числа, когда оно изменилось. */
export function bump(element: Element | null) {
  if (!element || reducedMotion()) return;
  element.animate(
    [
      { opacity: 0.35, transform: 'translateY(0.28em)' },
      { opacity: 1, transform: 'none' },
    ],
    { duration: 420, easing: EASE },
  );
}

const counters = new WeakMap<Element, number>();

/** Плавно пересчитывает число в элементе от текущего значения к новому. */
export function tweenNumber(element: Element | null, to: number, duration = 480) {
  if (!element) return;
  const from = Number(element.textContent) || 0;
  cancelAnimationFrame(counters.get(element) ?? 0);
  if (from === to || reducedMotion()) {
    element.textContent = String(to);
    return;
  }
  const start = performance.now();
  const step = (now: number) => {
    const progress = Math.min(1, (now - start) / duration);
    const eased = 1 - (1 - progress) ** 3;
    element.textContent = String(Math.round(from + (to - from) * eased));
    if (progress < 1) counters.set(element, requestAnimationFrame(step));
  };
  counters.set(element, requestAnimationFrame(step));
}
