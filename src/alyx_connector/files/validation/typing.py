from typing import TYPE_CHECKING, Dict, Literal, Protocol, Type, TypedDict, TypeVar, Union

if TYPE_CHECKING:
    from .actions import Action, Outcome, Trigger
    from .records import Encapsulation, EncapsulationList
    from .rules import Condition, Expression, Rule, Rules
    from .validators import Options

EncapsulatedObject = TypeVar("EncapsulatedObject")
Evaluated = TypeVar("Evaluated", bound="Encapsulation")
EvaluatedList = TypeVar("EvaluatedList", bound="EncapsulationList")


class MetaClass(Protocol):
    options_class: "Type[Options]"
    rules_class: "Type[Rules]"
    rule_class: "Type[Rule]"
    condition_class: "Type[Condition]"
    expression_class: "Type[Expression]"
    outcome_class: "Type[Outcome]"
    trigger_class: "Type[Trigger]"
    action_class: "Type[Action]"


class ExpressionFunction(Protocol):
    def __call__(self, value: str) -> bool: ...


class ActionFunction(Protocol):
    def __call__(
        self, evaluated: "Evaluated", evaluated_list: "EvaluatedList", *, message: str = ""
    ) -> tuple[Evaluated, str] | Evaluated | None: ...


class ExecutionInfo(TypedDict):
    action: str
    trigger: str
    message: str
    completed: bool
    traceback: str
