"""CSS-селекторы hh.ru.

Опираемся только на атрибуты data-qa: классы вида magritte-button___Pubhr_7-3-1_xhh
генерируются сборкой и меняются при каждом релизе hh, а data-qa стабильны.
Если hh поменяет вёрстку — править нужно только этот файл.
"""

# --- Выдача (страница поиска) ---
VACANCY_CARD = '[data-qa="vacancy-serp__vacancy"]'
VACANCY_TITLE_LINK = 'a[data-qa="serp-item__title"]'
VACANCY_TITLE_TEXT = '[data-qa="serp-item__title-text"]'
VACANCY_EMPLOYER = '[data-qa="vacancy-serp__vacancy-employer-text"]'
RESPONSE_BUTTON = '[data-qa="vacancy-serp__vacancy_response"]'
REMOTE_LABEL = '[data-qa="vacancy-label-work-schedule-remote"]'  # «Можно удалённо»
SNIPPET = '[data-qa="vacancy-serp__vacancy_snippet_responsibility"], [data-qa="vacancy-serp__vacancy_snippet_requirement"]'
PAGER_NEXT = '[data-qa="pager-next"]'  # бывает не всегда: hh иногда показывает только номера страниц
PAGER_PAGE = '[data-qa="pager-page"]'
SEARCH_HEADER = '[data-qa="vacancies-search-header"]'  # «Найдено 426 подходящих вакансий…»

# --- Окно отклика с сопроводительным (bottom-sheet / модалка) ---
MODAL_FORM = "#RESPONSE_MODAL_FORM_ID"
MODAL_SUBMIT = '[data-qa="vacancy-response-submit-popup"]'
MODAL_CLOSE = '[data-qa="response-popup-close"]'
MODAL_RESUME_TITLE = '[data-qa="resume-title"]'
HIDDEN_RESUME_WARNING = '[data-qa="hidden-resume-warning"]'
LETTER_INPUT = '[data-qa="vacancy-response-popup-form-letter-input"]'
GENERATE_LETTER = '[data-qa="generate-cover-letter"]'

# --- Быстрый отклик: «Ваш отклик отправлен работодателю» + «Приложить письмо» ---
LETTER_INFORMER = '[data-qa="vacancy-response-letter-informer"]'
LETTER_TOGGLE = '[data-qa="vacancy-response-letter-toggle"]'
# «Приложить письмо» открывает окно «Сопроводительное письмо»: поле LETTER_INPUT,
# кнопка GENERATE_LETTER и «Отправить» в подвале окна (вне формы, через атрибут form=)
LETTER_SUBMIT = '[data-qa="vacancy-response-letter-submit"]'

# --- Предупреждение «вакансия в другой стране/регионе» (role="alertdialog") ---
RELOCATION_TITLE = '[data-qa="relocation-warning-title"]'
RELOCATION_CONFIRM = '[data-qa="relocation-warning-confirm"]'  # «Все равно откликнуться»
RELOCATION_ABORT = '[data-qa="relocation-warning-abort"]'  # «Отменить»

# --- Страница с вопросами/тестом работодателя (открывается в той же вкладке,
#     кнопка «Откликнуться» там тоже MODAL_SUBMIT — отличаем по этим маркерам) ---
QUESTIONS = '[data-qa="employer-asking-for-test"], [data-qa="task-question"], [data-qa="task-body"]'
QUESTION_BLOCK = '[data-qa="task-body"]'  # один вопрос: текст + поле ответа (textarea) или варианты
QUESTION_TEXT = '[data-qa="task-question"]'
# «Сопроводительное письмо → Добавить» на странице вопросов — тот же LETTER_TOGGLE,
# отправка — та же MODAL_SUBMIT («Откликнуться»)

# --- Чаты (hh.ru/chat) ---
CHAT_CELL = 'a[data-qa^="chatik-open-chat-"]'  # data-qa="chatik-open-chat-<id>"
CHAT_TITLE = '[data-qa="chat-cell-title"]'  # вакансия
CHAT_SUBTITLE = '[data-qa="chat-cell-subtitle"]'  # компания
CHAT_UNREAD_BADGE = '[data-qa="chatik-info-badges"]'  # счётчик непрочитанных
# последнее сообщение: у «Отказ» класс last-message-color_red--<хеш>, data-qa нет
CHAT_LAST_MESSAGE = '[class*="last-message-color"], [class*="last-message--"]'
CHAT_ONLY_UNREAD = '[data-qa="chatik-checkbox-only-unread"]'
# Открытый чат: поле сообщения и кнопка отправки. Вёрстки открытого чата у нас ещё не было —
# ищем широко; если не найдётся кнопка, сообщение отправляется клавишей Enter.
CHAT_INPUT = 'textarea, [contenteditable="true"]'
CHAT_SEND = '[data-qa*="send" i], button[aria-label*="тправить"], button[title*="тправить"]'

# Любое всплывающее окно hh
DIALOG = '[role="dialog"], [role="alertdialog"]'

# --- Авторизация: форма входа, которую hh показывает гостю на /applicant/resumes ---
LOGIN_FORM = '[data-qa="account-login-form"]'

# --- Страница резюме (название должности), варианты от новых к старым ---
RESUME_PAGE_TITLE = (
    '[data-qa="resume-block-title-position"]',
    '[data-qa="resume-position-title"]',
)

# --- Капча ---
CAPTCHA = 'img[src*="captcha"], [data-qa*="captcha"]'
