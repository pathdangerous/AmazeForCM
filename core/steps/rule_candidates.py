"""The rule templates offered to the RuleRecommenderAgent (phase 6 plan, assumption 7; phase 5 note 3).

For each mapped element at most five templates that fit its type, and for the feed one `one_row_per_key` rule;
never the glossary, never the SQL of the feed's other rules. The SQL reads the table `curated` and returns the
`_row` of every row that breaks the rule (assumption 5). The mock agent turns each offer into a rule as it is.

present        a string or date/datetime element has a value
not_negative   an integer or decimal element is zero or more
in_whole_lots  the traded volume is a multiple of 100
not_in_future  a datetime element is not later than now
one_row_per_key an identifier and the price date together appear once
"""

from __future__ import annotations

from core.domain.rules import MAX_TEMPLATES, RuleTemplateOffer
from core.engine.curate import quoted

IDENTIFIERS = ("instrument_id", "isin", "cusip", "sedol", "figi", "ticker")
LOT_ELEMENTS = ("volume",)
KEY_DATE = "price_date"


def _words(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _select(where: str) -> str:
    return f"SELECT _row FROM curated WHERE {where}"


def element_offers(key: str, data_type: str) -> list[RuleTemplateOffer]:
    """The templates that fit this element's type, in a fixed order, at most MAX_TEMPLATES."""
    column, offers = quoted(key), []

    def offer(template: str, suffix: str, description: str, sql: str) -> None:
        offers.append(RuleTemplateOffer(template=template, rule_key=f"{key}_{suffix}", description=description,
                                        sql=sql, element_keys=[key]))

    if data_type in ("string", "date", "datetime"):
        offer("present", "present", f"{_words(key)} is present.", _select(f"{column} IS NULL"))
    if data_type in ("integer", "decimal"):
        offer("not_negative", "not_negative", f"{_words(key)} is not negative.", _select(f"{column} < 0"))
    if data_type == "integer" and key in LOT_ELEMENTS:
        offer("in_whole_lots", "in_whole_lots", f"{_words(key)} is a multiple of 100.", _select(f"{column} % 100 <> 0"))
    if data_type == "datetime":
        offer("not_in_future", "not_in_future", f"{_words(key)} is not in the future.",
              _select(f"{column} > CAST(current_timestamp AS TIMESTAMP)"))
    return offers[:MAX_TEMPLATES]


def feed_offers(element_keys: list[str]) -> list[RuleTemplateOffer]:
    """One row per key: the first identifier the feed maps, with the price date when it maps one."""
    identifier = next((k for k in IDENTIFIERS if k in element_keys), None)
    if identifier is None:
        return []
    keys = [identifier] + ([KEY_DATE] if KEY_DATE in element_keys else [])
    partition = ", ".join(quoted(k) for k in keys)
    sql = f"SELECT _row FROM (SELECT _row, count(*) OVER (PARTITION BY {partition}) AS n FROM curated) WHERE n > 1"
    return [RuleTemplateOffer(template="one_row_per_key", rule_key="one_row_per_" + "_and_".join(keys),
                              description=f"There is one row per {' and '.join(_words(k).lower() for k in keys)}.",
                              sql=sql, element_keys=keys)]
