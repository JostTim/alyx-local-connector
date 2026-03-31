from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generator, Generic, Iterable, Iterator, cast

from pandas import DataFrame, Series

from .typing import EncapsulatedObject, Evaluated, ExecutionInfo

if TYPE_CHECKING:
    from .validators import Validator


@dataclass
class Encapsulation(Generic[EncapsulatedObject]):
    original_object: EncapsulatedObject
    result: Any = field(default=..., init=False)

    # attributes for type checking
    lookup_attrs_on: str = field(default="original_object", init=False)
    matching_rules: set[str] = field(default_factory=set, init=False)
    selected_rule: str | None = field(default=None, init=False)
    planned_triggers: set[str] = field(default_factory=set, init=False)
    planned_triggers_args: dict[str, Any] = field(default_factory=dict, init=False)
    executed_triggers: dict[str, list[ExecutionInfo]] = field(default_factory=dict, init=False)
    frozen: bool = field(default=False, init=False)
    # def apply(self, validator: "Validator"):
    #     for rule in validator.rules.values():
    #         rule.apply(self)

    # def evaluate(self, validator: "Validator"):
    #     for rule in validator.rules.values():
    #         rule.evaluate(self)

    @property
    def match(self) -> bool:
        return bool(self.matching_rules)

    def add_matched_rule(self, rule_name: str):
        self.matching_rules.add(rule_name)
        self.add_trigger("match")

    def add_trigger(self: "Evaluated", trigger: str | list[str], **kwargs) -> "Evaluated":
        triggers = trigger if not isinstance(trigger, list) else trigger
        for trigger in triggers:
            self.planned_triggers.add(trigger)
            cast(dict, self.planned_triggers_args.setdefault(trigger, {})).update(**kwargs)
        return self

    @property
    def looked_up_object(self) -> Any:
        return getattr(self, self.lookup_attrs_on)

    @property
    def pending_triggers(self) -> set[str]:
        return self.planned_triggers.difference(self.executed_triggers.keys())

    def allowed_pending_triggers(
        self, allowed_triggers: list[str]
    ) -> Generator[tuple[str, dict[str, Any]], None, None]:
        for trigger in self.pending_triggers:
            if trigger not in allowed_triggers:
                continue
            yield trigger, self.planned_triggers_args[trigger]

    def has_allowed_pending_triggers(self, allowed_triggers: list[str]):
        # if allowed_pending_triggers has at least an item to yield, we return True
        if next(self.allowed_pending_triggers(allowed_triggers), None) is not None:
            return True
        return False

    def to_series(self) -> Series:
        return Series(
            {
                "original_object": self.original_object,
                "result": self.result,
                "match": self.match,
                "matching_rules": self.matching_rules,
                "selected_rule": self.selected_rule,
                "planned_triggers": self.planned_triggers,
                "executed_triggers": self.executed_triggers,
                "executed_triggers_df": self.executed_triggers_to_dataframe(),
            }
        )

    def executed_triggers_to_dataframe(self) -> DataFrame:
        return DataFrame([item for key, value in self.executed_triggers.items() for item in value])

    def set_result(self, result: Any, frozen: bool = True):
        if self.frozen == True:
            raise ValueError(
                "Cannot set again the result if frozen is set tu true."
                "This is a safeguard, it means the code doesn't behave "
                "as you expected and result is trying to be set several times."
            )
        self.result = result
        self.frozen = True

    def __hash__(self) -> int:
        if not hasattr(self, "_hash"):
            self._hash = hash(str(self.original_object))
        # same input (original_object) should behave exaclut identically,
        # follow same rules and getsame output and henceforth, give the
        # same hash. It should thus also be impossible to have twice the
        # same Encapsulation object inside an EncapsulationList.
        return self._hash

    def __eq__(self, other: "Evaluated") -> bool:
        return self.__hash__() == other.__hash__()


@dataclass
class EncapsulationList(Generic[Evaluated]):
    elements: list[Evaluated] = field(default_factory=list)

    encapsulation_class = Encapsulation

    @classmethod
    def from_iterable(cls, iterable: Iterable):
        return cls([cls.encapsulation_class(item) for item in iterable])

    def __post_init__(self):
        self.elements = list(set(self.elements))

    # def apply(self, validator: "Validator"):
    #     for element in self.elements:
    #         element.apply(validator)

    # def evaluate(self, validator: "Validator"):
    #     for element in self.elements:
    #         element.evaluate(validator)
    # def apply(self, validator: "Validator"):
    #     for element in self.elements:
    #         element.apply(validator)

    # def evaluate(self, validator: "Validator"):
    #     for element in self.elements:
    #         element.evaluate(validator)
    def to_dataframe(self):
        series = [element.to_series() for element in self.elements]
        return DataFrame(series)

    # Delegate list methods explicitly
    def __getitem__(self, index: int) -> "Evaluated":
        return self.elements[index]

    def __len__(self):
        return len(self.elements)

    def __iter__(self) -> Iterator["Evaluated"]:
        return iter(self.elements)

    def iter_pop(self):
        for i in range(len(self.elements)):
            elements = self.elements.copy()
            element = elements.pop(i)
            yield element, elements

    def append(self, item: "Evaluated"):
        self.elements.append(item)

    def extend(self, items: list["Evaluated"]):
        self.elements.extend(items)
