# AGENTS.md — svo-veterans-actualization

## Что это

Агент актуализации мер социальной поддержки для трёх ЖС: **люди с
инвалидностью**, **участники СВО**, **ветераны боевых действий**. Проверяет
актуальность уже известных мер (условия, размер, ведомство) через открытый
интернет и НПА-источники. Построен на **deepagents harness** поверх
LangGraph, ReAct-цикл + структурный верификатор с обязательным
цитированием источника.

Развитие паттерна `revision_agent` (см. родственный проект `auto` /
`feat/npa-revision-react`) — та же идея, перенесённая на домен с более
высокой ценой ошибки (люди реально теряют/недополучают выплаты), поэтому
здесь строже требования к верификации и трассировке.

## Стек

- **Python 3.12**, venv в `.venv/`. На этой машине голый `python3`/
  `.venv/bin/python` может резолвиться в Homebrew-сборку с
  ABI-рассинхроном `pyexpat`/`libexpat` (падает `openpyxl`, `pip`) — см.
  `IMPROVEMENT_BACKLOG.md` B006. Используй `pyenv exec python3` (или
  создавай `.venv` явно через `pyenv exec python3 -m venv .venv`), пока
  это не починено на уровне хоста
- **deepagents** (`create_deep_agent`) — harness: planning, filesystem
  backend, subagents, skills, memory, human-in-the-loop
- **LangGraph** — рантайм под капотом deepagents
- LLM-провайдер — TBD (Claude / GigaChat / GLM — зафиксировать после теста
  на качестве цитирования; ReAct-поиск с обязательным URL хуже работает на
  слабых моделях)
- Поисковый инструмент — TBD (Tavily / собственный wrapper над web-поиском
  с allowlist доменов, см. ниже)
- **FastAPI** — если нужен внешний триггер прогонов, иначе достаточно CLI

## Структура

```
svo_veterans_actualization/
  AGENTS.md
  PROGRAM.md                      — история экспериментов, параметры (по аналогии с revision_agent/PROGRAM.md)
  skills/
    measure_extraction/SKILL.md   — структура карточки меры, поля по ЖС
    trusted_sources/SKILL.md      — allowlist доменов + правила цитирования
    citation_verification/SKILL.md — как подтверждать/опровергать находку
  agent/
    main_agent.py                 — create_deep_agent(...)
    subagents/
      search_subagent.py          — поиск и сбор кандидатов-источников
      verification_subagent.py    — сверка условия/суммы с найденным текстом, двойное подтверждение
    tools/
      web_search_allowlist.py     — поиск ограничен доверенными доменами
      cntd_citation_parser.py     — парсинг citation-блока cntd (structured diff)
      review_queue.py             — append-only запись находок
  data/
    measures_registry.json        — реестр известных мер (аналог golden_standard.json)
    output/
      trace_measure_<key>.md      — трейс на каждую меру, обязателен всегда
      review_queue.json           — append-only, накопительная очередь находок
      tuning_log.jsonl            — append-only, журнал экспериментов с конфигом
```

## Запуск

```bash
cd svo_veterans_actualization
source .venv/bin/activate
python -m agent.main_agent
```

Прогон актуализации — **всегда** с трейсом и логом в файл, без исключений
(на любом объёме, не только на `--measure`):

```bash
.venv/bin/python scripts/run_actualization.py --force-all --trace --pace 2 \
    > data/output/full_run_$(date +%Y%m%d_%H%M%S).log 2>&1
```

Причина: находки недетерминированы (LLM + живой поиск), без сохранённого
трейса невозможно постфактум отличить легитимное расхождение от артефакта
прогона.

## Архитектура агента

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

agent = create_deep_agent(
    model=<llm>,
    tools=[web_search_allowlist, cntd_citation_parser, write_to_review_queue],
    subagents=[search_subagent, verification_subagent],
    skills=["./skills/"],
    system_prompt=<домейн-инструкция: см. skills/*>,
    backend=FilesystemBackend(root_dir=".", virtual_mode=True),
    memory=["/memory/AGENTS.md"],
    interrupt_on={"write_to_review_queue": True},  # human-in-the-loop на high-impact находки
)
```

**Цикл на одну меру:**

1. `initializer` — читает текущую карточку меры из `measures_registry.json`
2. `search_subagent` (ReAct) — ищет актуальный статус меры по allowlist
   доменов, собирает кандидатов-источников
3. `verification_subagent` — сверяет найденный текст с текущими полями
   карточки; фиксирует `field_change` только при цитировании конкретного
   URL/фрагмента
4. **Гейт двойного подтверждения**: если находка меняет размер выплаты или
   условие назначения — нужно ≥2 независимых источника (например, текст
   НПА + региональный портал соцзащиты), иначе находка помечается как
   `low_confidence` и не идёт в основной реестр
5. `review_queue.enqueue()` — запись находки (append-only, синхронно на
   диск после каждой меры — не полагаться на буферизацию логов)

## Доверенные источники (allowlist)

- cntd.ru, consultant.ru, garant.ru — тексты НПА
- gosuslugi.ru (включая раздел мер для участников СВО)
- sfr.gov.ru — Социальный фонд России (бывшие ПФР + ФСС, куда переехала
  часть выплат — старые НПА могут ссылаться на «Пенсионный фонд»)
- региональные порталы соцзащиты (per-субъект, самая нестабильная часть —
  фиксировать в `trusted_sources/SKILL.md` по мере добавления регионов)
- mos.ru — отдельно из-за объёма московских региональных мер

Поиск вне allowlist — не выполнять; агент не должен ходить на форумы,
агрегаторы льгот сомнительного качества и т.п.

## Автономность и guardrails

- `interrupt_on` на запись high-impact находок (изменение суммы/условия) —
  человек подтверждает перед фиксацией в реестре
- Низкий риск (например, изменилось только ведомство-исполнитель) можно
  оставить в полностью автономном режиме
- **Проверка «не завис ли прогон» — не по хвосту лог-файла** (stdout-редирект
  в фоне буферизуется). Смотреть mtime/новые записи в `review_queue.json`
  или свежие `trace_measure_*.md`. Признак реального зависания — не «лог не
  растёт», а «review_queue и трейсы не растут» + низкое накопленное
  CPU-время процесса
- Логи/трейсы — регенерируемые, гитигнорены, можно чистить перед новым
  прогоном
- `review_queue.json` и `tuning_log.jsonl` — append-only, не трогать без
  явного запроса пользователя

## Конвенции

- Не придумывать поля/значения — если источник не подтверждает, поле
  остаётся как есть с пометкой `не подтверждено повторно`, а не
  перезаписывается
- Одна находка = одно изменение одного поля одной меры (не смешивать
  несколько находок в одну запись очереди)
- Цитирование URL обязательно на каждую находку, без исключений
- Разделение по ЖС (инвалидность / СВО / ветераны БД) — как в исходном
  экстракторе, категории не смешивать

## Известные риски (перенесённые из соседнего проекта)

- Precision у похожего экстрактора была 33–60% (phantom measures) — в этом
  домене цена ложного срабатывания выше, отсюда гейт двойного подтверждения
- LLM read timeout на больших документах — закладывать таймауты и ретраи
  в `search_subagent`
- Региональные НПА меняются чаще федеральных — не кэшировать долго

## Переменные окружения

- `LLM_PROVIDER` — выбор модели
- `SEARCH_API_KEY` — ключ поискового инструмента
- `TRUSTED_DOMAINS_PATH` — путь к allowlist (если выносить из SKILL.md в
  отдельный конфиг)

## TODO / открытые вопросы

- [ ] Зафиксировать модель после теста качества цитирования (Claude / GigaChat / GLM)
- [ ] Определиться с поисковым инструментом (Tavily vs собственный wrapper)
- [ ] Список регионов первой очереди для region-specific мер
- [ ] Формат `measures_registry.json` — согласовать с текущим golden_standard, если нужна совместимость
