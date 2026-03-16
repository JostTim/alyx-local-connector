from dataclasses import dataclass
from pathlib import Path
from re import Pattern, compile
from typing import Generic, Type

from .actions import Action, Outcome, Trigger
from .records import EncapsulationList
from .rules import Condition, Expression, Rule, Rules
from .typing import Evaluated, EvaluatedList, MetaClass


class Options:
    validator: "Validator"

    @classmethod
    def parse_from_dict(cls, meta: MetaClass, dico: dict) -> "Options":
        return cls(**dico)

    def __init__(self, **kwargs):
        for name, content in kwargs.items():
            if hasattr(self, name):
                setattr(self, f"_{name}", content)
            else:
                print(f"Setting {name}")
                setattr(self, name, content)

    @property
    def re_patterns(self) -> dict[str, Pattern]:
        self._re_patterns = getattr(self, "_re_patterns", {})
        for key, value in self._re_patterns.items():
            if isinstance(value, str):
                self._re_patterns[key] = compile(value)
        return self._re_patterns

    def bind_to(self, validator: "Validator") -> "Options":
        self.validator = validator
        return self

    def _finalize(self):
        pass


@dataclass
class Validator(Generic[Evaluated]):
    rules: "Rules"
    options: "Options"

    class Meta:
        encapsulation_list_class: Type[EncapsulationList] = EncapsulationList
        options_class: Type[Options] = Options
        rules_class: Type[Rules] = Rules
        rule_class: Type[Rule] = Rule
        condition_class: Type[Condition] = Condition
        expression_class: Type[Expression] = Expression
        outcome_class: Type[Outcome] = Outcome
        trigger_class: Type[Trigger] = Trigger
        action_class: Type[Action] = Action

    @classmethod
    def from_json_file(cls, pathlike: str | Path) -> "Validator":
        import json

        with open(pathlike, "r", encoding="utf-8") as f:
            return cls.parse_from_dict(json.load(f))

    @classmethod
    def from_json_string(cls, string: str) -> "Validator":
        import json

        return cls.parse_from_dict(json.loads(string))

    @classmethod
    def from_toml_file(cls, pathlike: str | Path) -> "Validator":
        import tomllib

        with open(pathlike, "rb") as f:
            return cls.parse_from_dict(tomllib.load(f))

    @classmethod
    def from_yaml_file(cls, pathlike: str | Path) -> "Validator":
        from yaml import load

        from .rules_io import SafeLoader

        with open(pathlike, "r", encoding="utf-8") as f:
            return cls.parse_from_dict(load(f, SafeLoader))

    @classmethod
    def parse_from_dict(cls, dico: dict) -> "Validator":
        dico = dico.copy()
        meta = cls._resolve_meta()
        rules = meta.rules_class.parse_from_dict(meta, dico.pop("rules", {}))
        options = meta.options_class.parse_from_dict(meta, dico)
        return cls(rules, options)

    def __post_init__(self):
        self.rules.bind_to(self)
        self.options.bind_to(self)
        self._finalize()

    def _finalize(self):
        self.options._finalize()
        self.rules._finalize()

    @property
    def _meta(self) -> "Meta":
        if not hasattr(self, "_meta_memory"):
            self._meta_memory = self.__class__._resolve_meta()
        return self._meta_memory

    @classmethod
    def _resolve_meta(cls) -> "Meta":
        """
        Return an object with the effective Meta options for `cls`,
        where user overrides (cls.Meta) override base defaults.
        """

        # collect Meta classes from base -> derived so derived wins
        metas = []
        for classtype in reversed(cls.__mro__):
            meta = classtype.__dict__.get("Meta")
            if meta is not None:
                metas.append(meta)

        # merge attributes (skip dunder/private)
        merged = {}
        for meta in metas:
            for key, value in meta.__dict__.items():
                if not key.startswith("_"):
                    merged[key] = value

        # return a simple 'Meta' class instance
        return type("Meta", (), merged)  # ty:ignore[invalid-return-type]

    def apply(self, evaluated_list: EvaluatedList, allowed_triggers: list[str]):
        for evaluated, list_without_evaluated in evaluated_list.iter_pop():
            for rule in self.rules.values():
                rule.apply(evaluated, list_without_evaluated, allowed_triggers)

    def evaluate(self, evaluated_list: EvaluatedList):
        for evaluated in evaluated_list:
            for rule in self.rules.values():
                rule.evaluate(evaluated)
            self.rules.resolve_selected_rule(evaluated)
