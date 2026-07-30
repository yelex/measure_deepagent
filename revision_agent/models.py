"""Модель карточки меры и справочник полей по ЖС.

Канонические имена полей и их набор на ЖС **должны** совпадать с
`LS_SPEC` в `scripts/score_against_golden.py` (см. docstring того
модуля) — иначе экспорт не будет матчиться со scorer'ом. Копия здесь
намеренная: scorer заморожен и не должен импортировать код агента (и
наоборот, агент не должен зависеть от internals eval-модуля).
"""

from __future__ import annotations

from dataclasses import dataclass, field

LS_FIELDS = {
    "вбд": ["measureId", "region", "categoryOfVeteran", "measureName",
            "measureSum", "measurePeriodicity", "measureTerms", "department"],
    "сво": ["measureId", "region", "categoryMobilized", "categoryContractor",
            "categoryVolunteer", "kidsOfMilitary", "measureName", "measureSum",
            "measurePeriodicity", "measureTerms", "department"],
    "инвалиды": ["measureId", "region", "cause_general_disease", "cause_war_trauma",
                 "cause_radiation", "cause_disabled_child", "measureName",
                 "measure_first_group", "measure_second_group", "measure_third_group",
                 "measure_disabled_child", "measurePeriodicity", "measureTerms",
                 "department"],
}


@dataclass
class MeasureCard:
    ls: str
    region: str
    measureName: str
    fields: dict = field(default_factory=dict)
    lsClassification: str = "target"
    sourceUrl: str = ""

    def to_export_dict(self) -> dict:
        out = {"region": self.region, "measureName": self.measureName,
               "lsClassification": self.lsClassification}
        out.update(self.fields)
        return out
