// Демо в блоке «Анализ резюме»: отмечаем навыки и смотрим, как меняются оценка и число вакансий.
// Веса оценки те же, что в приложении: навыки 45, опыт 20, название 15, оформление 20.

import { tweenNumber } from './motion';

const WEIGHTS = { skills: 0.45, exp: 0.2, title: 0.15, format: 0.2 };
const BASE = { skills: 85, exp: 93, title: 100, format: 75, matched: 366, topSkills: 16 };
// Вакансии частично пересекаются, поэтому каждый следующий навык открывает чуть меньше.
const OVERLAP = 0.85;
const SKILL_POINTS = 3;

export function initAnalysis() {
  const root = document.querySelector<HTMLElement>('[data-analysis]');
  if (!root) return;

  const toggles = [...root.querySelectorAll<HTMLButtonElement>('[data-skill]')];
  const score = root.querySelector('[data-score]');
  const matched = root.querySelector('[data-matched]');
  const skillsValue = root.querySelector('[data-bar-value="skills"]');
  const skillsFill = root.querySelector<HTMLElement>('[data-bar-fill="skills"]');
  const skillsNote = root.querySelector('[data-bar-note="skills"]');
  const picked: HTMLButtonElement[] = [];

  const gainOf = (toggle: HTMLButtonElement, position: number) =>
    Math.round(Number(toggle.dataset.gain) * OVERLAP ** position);

  function render() {
    const total = picked.reduce((sum, toggle, index) => sum + gainOf(toggle, index), BASE.matched);
    const skills = Math.min(100, BASE.skills + picked.length * SKILL_POINTS);
    const scoreValue = Math.round(
      WEIGHTS.skills * skills + WEIGHTS.exp * BASE.exp + WEIGHTS.title * BASE.title + WEIGHTS.format * BASE.format,
    );

    tweenNumber(score, scoreValue);
    tweenNumber(matched, total);
    tweenNumber(skillsValue, skills);
    skillsFill?.style.setProperty('--v', `${skills}%`);
    if (skillsNote) skillsNote.textContent = `${BASE.topSkills + picked.length} из 25 самых востребованных`;

    toggles.forEach((toggle) => {
      const position = picked.indexOf(toggle);
      const gain = gainOf(toggle, position >= 0 ? position : picked.length);
      const label = toggle.querySelector('[data-skill-gain]');
      if (label) label.textContent = `+${gain}`;
    });
  }

  toggles.forEach((toggle) => {
    toggle.addEventListener('click', () => {
      const position = picked.indexOf(toggle);
      if (position >= 0) picked.splice(position, 1);
      else picked.push(toggle);
      toggle.setAttribute('aria-pressed', String(position < 0));
      render();
    });
  });
}
