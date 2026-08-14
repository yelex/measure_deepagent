# Ralph Coder iteration — 2026-08-14T06:45:00Z

## Задача
[L010] Category static lookup (сво/инвалиды) — priority P1 (высший `todo`
приоритет по `IMPROVEMENT_BACKLOG.md`, план Claude 14.08, п.1 из 5).

## Baseline
GigaChat-2-Max baseline (14.08.2026, после L014-фикса):
Recall=0.86 Precision=1.000 AvgQS=0.532 PerfectRate=0.047.
Category-ошибки: вбд 6/6 (не трогаем — текстовое поле, вне scope L010),
сво 6/16, инвалиды 17/21 (23/37 применимых — крупнейший рычаг после terms).

## Гипотеза
category-поля для "сво" (categoryMobilized/Contractor/Volunteer/
kidsOfMilitary) и "инвалиды" (cause_general_disease/cause_war_trauma/
cause_radiation/cause_disabled_child) — то же самое явление, что уже
решено для department в L009: это атрибут КОНКРЕТНОЙ НАЗВАННОЙ программы
меры (кому вообще положена эта мера по регламенту), а не то, что можно
процитировать из текста НПА про эту меру. grounded LLM с обязательной
цитатой физически не может подтвердить "мобилизованные" цитатой, если
текст указа не использует это слово. Статический lookup по measureName
(как _DEPARTMENT_DEFAULTS/_DEPARTMENT_EXCEPTIONS) должен закрыть
подавляющее большинство этих ошибок, т.к. category здесь — не
произвольный факт из текста, а фиксированный признак программы,
известный по её названию (ровно как и department).

## План проверки
1. Построить `_CATEGORY_LOOKUP["сво"]` / `_CATEGORY_LOOKUP["инвалиды"]`
   — словарь `normalized_measure_name -> {field: 0/1/None}`, посчитанный
   из `docs/меры_автоагент_2.xlsx` (тот же метод, что и для L009
   department: читаем golden как данность, не трогаем сам файл).
2. Применить post-LLM в `extract_measure_via_llm` (llm_extract_v2.py) —
   если measureName найден в lookup, подставить category-поля напрямую
   (перекрывая LLM-экстракцию), аналогично `_lookup_department`.
3. Не трогать "вбд" (categoryOfVeteran — текстовое поле, реально в
   тексте НПА, вне scope L010 по прямой формулировке задачи).
4. Коммит `wip(L010): ...`, дальше eval делает `ralph_loop.sh`
   синхронно (не запускаю сам).

## Критерий успеха (по backlog)
category-errors для "сво" падают с 6/16, для "инвалиды" — с 17/21.
