# Skill: Measure Extraction

## Карточка меры — структура полей по ЖС

### ВБД (ветераны боевых действий)
- `measureName` — название меры
- `categoryOfVeteran` — категория получателя (текст)
- `measureSum` — размер (число или описание)
- `measurePeriodicity` — периодичность
- `measureTerms` — условия (кому, при каких условиях)
- `department` — ведомство/организация

### СВО
- `measureName` — название меры
- `categoryMobilized` — 0/1, мобилизованные
- `categoryContractor` — 0/1, контрактники
- `categoryVolunteer` — 0/1, добровольцы
- `kidsOfMilitary` — 0/1, дети участников СВО
- `measureSum`, `measurePeriodicity`, `measureTerms`, `department`

### Инвалиды
- `measureName` — название меры
- `cause_general_disease` — 0/1, общее заболевание
- `cause_war_trauma` — 0/1, военная травма
- `cause_radiation` — 0/1, радиация
- `cause_disabled_child` — 0/1, ребёнок-инвалид
- `measure_first_group`, `measure_second_group`, `measure_third_group` — размер по группам
- `measure_disabled_child` — размер для ребёнка-инвалида
- `measurePeriodicity`, `measureTerms`, `department`

## Правила извлечения
1. Только из текста источника. Не придумывать.
2. Если поле не упомянуто — null, не угадывать.
3. Булевы поля: 1 если текст подтверждает, 0 если явно исключает, null если не касается.
4. Суммы — числа без копеек и символов валюты.
