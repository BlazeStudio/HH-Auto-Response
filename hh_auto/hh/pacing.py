"""Отклики сериями с перерывами после капчи — чтобы hh просил её реже.

Капчу hh показывает почти всегда на «Откликнуться» в окне отклика, когда откликов много подряд. По логам
откликов: после перерыва в 15–20 минут проходит 4–10 откликов, а после короткой передышки — 2–4, и дальше
капча почти на каждом. Одно удлинение пауз между вакансиями не спасает: на отклик с письмом нейросети и так
уходит полминуты–минута.

Поэтому, пока капчи нет, программа откликается как раньше. После капчи — сериями: серия на отклик короче той,
на которой hh попросил капчу, между сериями — перерыв ([limits] captcha_rest минут). Серия прошла без капчи —
перерыв понемногу короче, а после двух таких подряд серия на отклик длиннее; снова капча — серия короче,
перерыв длиннее. Выученное помнит data/pacing.json: при новом запуске в тот же день капчу заново ловить
не нужно.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from ..core import control
from ..core.config import Limits
from ..core.logger import log
from ..web.browser import captcha_count

MIN_SERIES = 2  # короче серии не делаем: перерыв после каждого отклика — это уже не работа
MAX_SERIES = 12  # длиннее серия не отрастает: дальше снова до капчи
GROW_AFTER = 2  # столько серий подряд без капчи — серия на отклик длиннее
REST_GROWTH = 1.5  # капча — перерыв во столько раз длиннее…
REST_MAX = 4  # …но не длиннее captcha_rest × REST_MAX
REST_SHRINK = 0.85  # серия без капчи — перерыв во столько раз короче, но не короче captcha_rest
FORGET_AFTER = 12 * 3600  # с последней капчи прошло столько — начинаем с чистого листа
FILE = "pacing.json"


class Pacer:
    """Когда сделать перерыв в откликах. Зовут: sent() — отклик ушёл, rest_if_due() — после каждой вакансии,
    где нажимали «Откликнуться»."""

    def __init__(self, limits: Limits, path: Path | None = None):
        self.path = path
        self.base = max(limits.captcha_rest, 0) * 60  # с; 0 — серии выключены
        self.series: int | None = None  # откликов в серии; None — капчи не было, перерывов нет
        self.rest = self.base  # перерыв между сериями сейчас, с
        self.streak = 0  # откликов с последнего перерыва (или капчи)
        self.clean = 0  # серий подряд без капчи
        self.captcha_at = 0.0  # когда была последняя капча (time.time) — по ней выученное забывается
        self.seen = captcha_count()  # капчи сеанса, о которых уже знаем
        if self.base:
            self._load()

    @property
    def enabled(self) -> bool:
        return self.base > 0

    def sent(self) -> None:
        self.streak += 1

    def rest_if_due(self) -> bool:
        """Была капча — подстроить серию и сделать перерыв; серия набрана — перерыв. True — перерыв был
        (обычная пауза между вакансиями уже не нужна)."""
        if not self.enabled:
            self.seen = captcha_count()
            return False
        if captcha_count() > self.seen:
            self.seen = captcha_count()
            self._captcha()
            return True
        if self.series is None or self.streak < self.series:
            return False
        self.clean += 1
        if self.clean >= GROW_AFTER and self.series < MAX_SERIES:
            self.series += 1
            self.clean = 0
        self.rest = max(self.base, self.rest * REST_SHRINK)
        self._save()
        self._take_rest(f"после серии из {self.streak} откл. — чтобы hh не просил капчу")
        return True

    def _captcha(self) -> None:
        """hh попросил капчу на streak-м отклике после перерыва: серия — на отклик короче, перерыв — длиннее."""
        first = self.series is None
        self.series = max(MIN_SERIES, self.streak - 1)
        self.rest = self.base if first else min(self.base * REST_MAX, self.rest * REST_GROWTH)
        self.clean = 0
        self.captcha_at = time.time()
        self._save()
        log.info(f"  капча на {self.streak}-м отклике подряд — дальше откликаюсь сериями по {self.series} с перерывом "
                 f"~{self.rest / 60:.0f} мин. Выключить: «Лимиты и паузы» → «Перерыв после капчи» = 0")
        self._take_rest("после капчи")

    def _take_rest(self, why: str) -> None:
        seconds = self.rest * random.uniform(0.8, 1.2)
        minutes, rest = divmod(round(seconds), 60)
        log.info(f"  ⏸ перерыв {minutes} мин {rest:02d} с {why}")
        self.streak = 0
        control.sleep(seconds)

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path else {}
            at = float(data.get("at", 0))
            if time.time() - at > FORGET_AFTER or not data.get("series"):
                return
            self.series = max(MIN_SERIES, min(MAX_SERIES, int(data["series"])))
            self.rest = min(self.base * REST_MAX, max(self.base, float(data.get("rest", self.base))))
        except (OSError, ValueError, TypeError, AttributeError):
            return
        self.captcha_at = at
        log.info(f"  капча была в {time.strftime('%H:%M', time.localtime(at))} — откликаюсь сериями по {self.series} "
                 f"с перерывом ~{self.rest / 60:.0f} мин")

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"series": self.series, "rest": round(self.rest), "at": self.captcha_at}),
                                 encoding="utf-8")
        except OSError as e:
            log.debug(f"  серии откликов не записались в {self.path}: {e}")
