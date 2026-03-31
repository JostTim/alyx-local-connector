from dataclasses import dataclass, field
from inspect import getfullargspec
from traceback import format_exc
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from .typing import ActionFunction, Evaluated, EvaluatedList, ExecutionInfo, MetaClass

if TYPE_CHECKING:
    from .rules import Rule
    from .validators import Validator


@dataclass
class Outcome(Generic[Evaluated]):
    triggers: "dict[str, Trigger]"

    # methods for type checking
    rule: "Rule" = field(init=False)

    @classmethod
    def parse_from_dict(cls, meta: MetaClass, dico: dict) -> "Outcome":
        triggers = {}
        for key, value in dico.items():
            trigger = meta.trigger_class.parse_from_dict(meta, key, value)
            triggers[trigger.name] = trigger
        return cls(triggers)

    def __post_init__(self):
        for triggers in self.triggers.values():
            triggers.bind_to(self)

    def _finalize(self):
        if "match" not in self.triggers.keys():
            raise ValueError(
                f"match action was not defined in rule {self.rule.name}. Must be defined. "
                "Set on : match to null if you wish to keep the rule but make it inactive."
            )

        allowed_triggers = set(self.validator._meta.trigger_class.defaults.keys())
        allowed_triggers.add("match")

        for trigger_name in self.triggers.keys():
            if trigger_name not in allowed_triggers:
                raise ValueError(
                    f"Trigger {trigger_name} in rule {self.rule.name} is invalid. Valid keys are {','.join(allowed_triggers)}"
                )

    def bind_to(self, rule: "Rule") -> "Outcome":
        self.rule = rule
        return self

    @property
    def validator(self) -> "Validator":
        return self.rule.validator

    def get_trigger_for(self, trigger_name: str, fallback_to_default=True) -> "Trigger":
        if trigger_name in self.triggers.keys():
            return self.triggers[trigger_name]
        if not fallback_to_default:
            raise ValueError(f"No trigger {trigger_name} was found for rule {self.rule.name}")

        return self.get_default_for(trigger_name)

    def get_default_for(self, trigger_name: str) -> "Trigger":
        meta = self.validator._meta
        dico = meta.trigger_class.defaults[trigger_name]
        return meta.trigger_class.parse_from_dict(meta, trigger_name, dico).bind_to(self)

    def apply(
        self, evaluated: Evaluated, evaluated_list: EvaluatedList, allowed_triggers: list[str]
    ) -> "Evaluated":

        while evaluated.has_allowed_pending_triggers(allowed_triggers):
            for trigger_name, trigger_kwargs in evaluated.allowed_pending_triggers(
                allowed_triggers
            ):
                trigger = self.get_trigger_for(
                    trigger_name, fallback_to_default=False if trigger_name == "name" else True
                )
                trigger.apply(evaluated, evaluated_list, **trigger_kwargs)
                # trigger.apply adds the trigger name to evaluated's executed_triggers dict,
                # so it won't trigger again on that encapsulated object
        return evaluated


@dataclass
class Trigger(Generic[Evaluated]):
    """
    example for defaults :


    """

    name: str
    actions: "dict[str, Action]"

    # Non initializable properties

    # default trigger names to actions mapping
    defaults: dict[str, str | list[str] | dict[str, dict[str, Any]]] = field(
        default_factory=dict, init=False
    )

    # typing properties
    outcome: "Outcome" = field(init=False)

    @classmethod
    def parse_from_dict(
        cls, meta: MetaClass, name: str, dico: dict[str, dict[str, Any]] | list[str] | str
    ) -> "Trigger":

        actions: dict[str, Action] = {}
        if isinstance(dico, dict):
            for action_name, arguments in dico.items():
                actions[action_name] = meta.action_class(action_name, arguments)
        elif isinstance(dico, list):
            for action_name in dico:
                actions[action_name] = meta.action_class(action_name)
        else:
            action_name = str(dico)
            actions = {action_name: meta.action_class(action_name)}

        return cls(name, actions)

    def __post_init__(self):
        for action in self.actions.values():
            action.bind_to(self)

    def bind_to(self, outcome: "Outcome") -> "Trigger":
        self.outcome = outcome
        return self

    def _finalize(self):
        # TODO make this modular using connected parent / child links and Validator's Meta Class
        allowed_actions = [
            "rename",
            "include",
            "delete",
            "exclude",
            "abort",
        ]

        for action_name in self.actions.keys():
            if action_name not in allowed_actions:
                raise ValueError(
                    f"Action {action_name} in trigger {self.name} in rule "
                    f"{self.rule.name} is invalid. Valid keys are "
                    f"{','.join(allowed_actions)}"
                )

    @property
    def rule(self) -> "Rule":
        return self.outcome.rule

    @property
    def validator(self) -> "Validator":
        return self.rule.validator

    # def get_trigger_for(self, name: str, fallback_to_default=True) -> "Trigger[Evaluated]":
    #     return self.outcome.get_trigger_for(name, fallback_to_default)

    # def get_default_for(self, name: str) -> "Trigger[Evaluated]":
    #     return self.outcome.get_default_for(name)

    def apply(self, evaluated: Evaluated, evaluated_list: EvaluatedList, **kwargs) -> "Evaluated":
        for action in self.actions.values():
            evaluated = action.apply(evaluated, evaluated_list, **kwargs)
        return evaluated


@dataclass
class Action(Generic[Evaluated]):
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    # attribute types for type checking, not available in __init__
    method: ActionFunction = field(init=False)
    trigger: "Trigger[Evaluated]" = field(init=False)

    def __post_init__(self):
        self.method = getattr(self, self.name)

    def _finalize(self):
        # generalize this (or move a part in file implementation)
        # and to _finalize
        if self.name == "rename":
            # self.rename_rule: dict[ElementNames, str | dict] = rule_dict.get("rename", {})
            for key, value in self.arguments.items():
                if isinstance(value, dict):
                    pattern_name = value.get("pattern", None)
                    if (
                        pattern_name
                        and pattern_name not in self.validator.options.re_patterns.keys()
                    ):
                        raise KeyError(
                            f"Pattern {pattern_name} was specified in rename action of rule : {self.rule.name} but that "
                            "pattern was not defined in re_patterns."
                        )
                elif not isinstance(value, str):
                    raise ValueError(
                        f"{self.rule.name} rule errror in 'rename' with {key}. The content of a renaming the rule must be either"
                        " a constant string or a dictionnary. See documentation for more details."
                    )

    def bind_to(self, trigger: "Trigger[Evaluated]") -> "Action":
        self.trigger = trigger
        return self

    @property
    def rule(self) -> "Rule":
        return self.trigger.rule

    @property
    def validator(self) -> "Validator":
        return self.rule.validator

    # def get_trigger_for(self, trigger_name: str, fallback_to_default=True) -> "Trigger":
    #     return self.trigger.outcome.get_trigger_for(trigger_name, fallback_to_default)

    # def get_default_for(self, trigger_name: str) -> "Trigger":
    #     return self.trigger.outcome.get_default_for(trigger_name)

    def _get_encapsulated_list_arg(self) -> str | None:
        if not hasattr(self, "_encapsulated_list_arg"):
            argspec = getfullargspec(self.method)
            args = [
                arg
                for arg in argspec.args
                if argspec.annotations.get(arg, None)
                == self.validator._meta.encapsulation_list_class.__name__
            ]
            self._encapsulated_list_arg = args[0] if len(args) == 1 else None
        return self._encapsulated_list_arg

    def apply(self, evaluated: Evaluated, evaluated_list: EvaluatedList, **kwargs) -> "Evaluated":

        encapsulated_arg = self._get_encapsulated_list_arg()
        if encapsulated_arg:
            kwargs.update({encapsulated_arg: evaluated_list})
        message = cast(str, kwargs.get("message", ""))
        try:
            output = self.method(evaluated, **kwargs)
            if isinstance(output, tuple):
                # we extract message from the response's output
                _, message = cast(tuple[Any, str], output)
            completed = True
            traceback = ""
        except Exception as e:
            traceback = f"Error : {e} : Traceback : {format_exc()}"
            completed = False

        execution_infos: ExecutionInfo = {
            "trigger": self.trigger.name,
            "action": self.name,
            "message": message,
            "completed": completed,
            "traceback": traceback,
        }

        evaluated.executed_triggers.setdefault(self.trigger.name, []).append(execution_infos)
        return evaluated
