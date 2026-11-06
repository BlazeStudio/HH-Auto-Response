// Оживляет окно приложения в первом экране: журнал растёт, счётчики меняются.
// Строки журнала взяты из настоящего приложения, компании вымышленные.
// Страница полностью готова и без этого скрипта: в HTML уже лежит состояние «Отправлено 42 отклика».

type Tone = '' | 'l-ai' | 'l-ok' | 'l-skip' | 'l-dim';
type Effect = 'sent' | 'letter' | 'skip';

interface Step {
  text: string;
  tone?: Tone;
  seconds: number;
  effect?: Effect;
}

interface Vacancy {
  title: string;
  company: string;
  steps: Step[];
}

const SENT_WITH_LETTER = '✓ ОТКЛИК ОТПРАВЛЕН с сопроводительным письмом';
const SENT_WITH_FORM = '✓ ОТКЛИК ОТПРАВЛЕН с ответами ИИ на анкету и письмом';

const vacancies: Vacancy[] = [
  {
    title: 'Python-разработчик (FastAPI)',
    company: 'ООО «Пример Софт»',
    steps: [
      { text: '… ИИ пишет письмо под вакансию', tone: 'l-ai', seconds: 6 },
      { text: SENT_WITH_LETTER, tone: 'l-ok', seconds: 9, effect: 'letter' },
    ],
  },
  {
    title: 'Python-разработчик, сопровождение 1С',
    company: 'ООО «Демо Ритейл»',
    steps: [
      { text: '… ИИ-фильтр сверяет карточку с вашим описанием', tone: 'l-ai', seconds: 2 },
      { text: 'пропущена: ИИ-фильтр, вы просили без 1С', tone: 'l-skip', seconds: 1, effect: 'skip' },
    ],
  },
  {
    title: 'Middle Python Developer',
    company: 'АО «Демо Лаб»',
    steps: [
      { text: 'реакция hh: вопросы работодателя', seconds: 8 },
      { text: '… ИИ заполняет анкету (4 вопр.)', tone: 'l-ai', seconds: 2 },
      { text: SENT_WITH_FORM, tone: 'l-ok', seconds: 38, effect: 'letter' },
    ],
  },
  {
    title: 'Backend-разработчик в продуктовую команду',
    company: 'ООО «Тест Системс»',
    steps: [
      { text: 'реакция hh: вакансия в другом регионе, работа удалённая, соглашаюсь', seconds: 7 },
      { text: SENT_WITH_LETTER, tone: 'l-ok', seconds: 6, effect: 'letter' },
    ],
  },
  {
    title: 'Python-разработчик, пишите в Telegram',
    company: 'ООО «Пример Софт»',
    steps: [{ text: 'пропущена: просят писать в Telegram', tone: 'l-skip', seconds: 1, effect: 'skip' }],
  },
  {
    title: 'Python Engineer (Data Platform)',
    company: 'АО «Демо Лаб»',
    steps: [
      { text: '… ИИ пишет письмо под вакансию', tone: 'l-ai', seconds: 5 },
      { text: SENT_WITH_LETTER, tone: 'l-ok', seconds: 8, effect: 'letter' },
    ],
  },
];

const RUN_LIMIT = 100;
const MAX_LINES = 16;
const PAGE_SIZE = 20;

function plural(count: number) {
  const tens = count % 100;
  const units = count % 10;
  if (tens >= 11 && tens <= 14) return 'откликов';
  if (units === 1) return 'отклик';
  if (units >= 2 && units <= 4) return 'отклика';
  return 'откликов';
}

export function initWindowSim() {
  const root = document.querySelector<HTMLElement>('[data-sim]');
  if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const log = root.querySelector<HTMLElement>('[data-sim-log]');
  const sentEl = root.querySelector('[data-sent]');
  const sentWord = root.querySelector('[data-sent-word]');
  const sentRun = root.querySelector('[data-sent-run]');
  const letterEl = root.querySelector('[data-sim-letter]');
  const skipEl = root.querySelector('[data-sim-skip]');
  const statusEl = root.querySelector('[data-sim-status]');
  const statusWrap = root.querySelector<HTMLElement>('[data-sim-status-wrap]');
  const segments = [...root.querySelectorAll<HTMLElement>('[data-segs] i')];
  const toggle = root.querySelector<HTMLButtonElement>('[data-sim-toggle]');
  const toggleLabel = root.querySelector('[data-sim-toggle-label]');
  if (!log || !toggle) return;

  let sent = Number(sentEl?.textContent) || 42;
  let letters = Number(letterEl?.textContent) || 40;
  let skipped = Number(skipEl?.textContent) || 13;
  let clock = 14 * 3600 + 4 * 60 + 51;
  let page = 3;
  let position = 7;
  let vacancyIndex = 0;
  let queue: Step[] = [];
  let userPaused = false;
  let visible = true;
  let timer: number | undefined;

  const timeLabel = () =>
    [Math.floor(clock / 3600) % 24, Math.floor(clock / 60) % 60, clock % 60]
      .map((part) => String(part).padStart(2, '0'))
      .join(':');

  function addLine(text: string, tone: Tone = '') {
    const line = document.createElement('div');
    line.className = 'log-line';
    line.setAttribute('data-new', '');
    const time = document.createElement('time');
    time.textContent = timeLabel();
    const body = document.createElement('span');
    body.textContent = text;
    if (tone) body.className = tone;
    line.append(time, body);
    log!.append(line);
    while (log!.children.length > MAX_LINES) log!.firstElementChild?.remove();
  }

  function applyEffect(effect?: Effect) {
    if (effect === 'skip') {
      skipped += 1;
      if (skipEl) skipEl.textContent = String(skipped);
      return;
    }
    if (!effect) return;
    sent = sent >= RUN_LIMIT ? 42 : sent + 1;
    letters += 1;
    if (sentEl) sentEl.textContent = String(sent);
    if (sentWord) sentWord.textContent = plural(sent);
    if (sentRun) sentRun.textContent = String(sent);
    if (letterEl) letterEl.textContent = String(letters);
    segments.forEach((segment, index) => segment.classList.toggle('on', (index + 1) * 4 <= sent));
  }

  function nextVacancy() {
    const vacancy = vacancies[vacancyIndex % vacancies.length];
    vacancyIndex += 1;
    position += 1;
    if (position > PAGE_SIZE) {
      position = 1;
      page += 1;
      addLine(`дальше: страница ${page}, найдено 426 вакансий`, 'l-dim');
    }
    clock += 9;
    addLine(`[стр. ${page}, ${position}/${PAGE_SIZE}] ▶ «${vacancy.title}» — ${vacancy.company}`);
    queue = [...vacancy.steps];
  }

  function tick() {
    const step = queue.shift();
    if (step) {
      clock += step.seconds;
      addLine(step.text, step.tone);
      applyEffect(step.effect);
    } else {
      nextVacancy();
    }
    schedule();
  }

  function schedule() {
    window.clearTimeout(timer);
    timer = undefined;
    if (userPaused || !visible || document.hidden) return;
    timer = window.setTimeout(tick, 1100 + Math.random() * 900);
  }

  function renderPauseState() {
    root!.toggleAttribute('data-paused', userPaused);
    if (statusEl) statusEl.textContent = userPaused ? 'На паузе' : 'Идёт работа';
    statusWrap?.classList.toggle('text-ok', !userPaused);
    statusWrap?.classList.toggle('text-warn', userPaused);
    if (toggleLabel) toggleLabel.textContent = userPaused ? 'Продолжить' : 'Пауза';
    toggle!.querySelector('svg')?.classList.toggle('opacity-40', userPaused);
  }

  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    userPaused = !userPaused;
    renderPauseState();
    schedule();
  });

  document.addEventListener('visibilitychange', schedule);
  new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      schedule();
    },
    { threshold: 0.1 },
  ).observe(root);
}
