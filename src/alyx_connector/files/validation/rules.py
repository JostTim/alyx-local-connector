from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Any, Literal, Dict, TYPE_CHECKING
# reveal_type

from .records import FileRecord
from .actions import Actions


if TYPE_CHECKING:
    from .validators import Validator


class ParsingError(ValueError): ...


@dataclass
class Rule:
    """A rule is defined as an ensemble of subdictionaries, and is defined by it's name (it's key)
    inside the toplevel rules config.

    rule_dict is the dictionnary that is *inside* the rule, whereas rule_name is the name of the rule,
    it's key in the toplevel rules configuration dictionnary.

    An esample rule, named imaging_data_rule2 that can be parsed can be this one :

    ```json
    "imaging_data_rule2": {
            "if": {
                "object": "imaging",
                "attribute": "frames",
                "extra": {
                    "exact_not": ""
                }
            },
            "on": {
                "match": "include"
            },
            "overrides": [
                "imaging_data_rule1"
            ]
        }
    ```

    To parse it, one would pass :

    ```python
    rule_dictionnary = {"imaging_data_rule2": {
            "if": {
                "object": "imaging",
                "attribute": "frames",
                "extra": {
                    "exact_not": ""
                }
            },
            "on": {
                "match": "include"
            },
            "overrides": [
                "imaging_data_rule1"
            ]
        }}
    Rule(rule_dictionnary["imaging_data_rule2"], "imaging_data_rule2")
    ```

    """

    name: str
    condition: "Condition"
    actions: "Actions"
    overrides: list[str] = field(default_factory=list)

    # active: bool

    @classmethod
    def parse_from_dict(cls, name: str, dico: dict):
        if "if" not in dico.keys():
            raise ParsingError(f"An if field must be defined in the rule {name}")
        condition = Condition(dico["if"])
        actions = Actions(dico.get("on", {}))
        overrides = dico.get("overrides", [])
        return cls(name, condition, actions, overrides)

    def __post_init__(self):
        self.overrides = self.overrides if isinstance(self.overrides, list) else [self.overrides]
        self.condition.bind_to(self)
        self.actions.bind_to(self)
        self.active = True

    def bind_to(self, validator: "Validator") -> "Rule":
        self.validator = validator
        return self

    @property
    def patterns(self) -> Dict[str, Pattern]:
        return self.validator.patterns

    def evaluate(self, file_record: FileRecord) -> FileRecord:
        return self.condition.evaluate(file_record) if self.active else file_record

    def actions_cascade(self, file_record) -> FileRecord:
        return self.actions.actions_cascade(file_record) if self.active else file_record

    def __str__(self):
        return f"Rule : {self.name}" + "\n" + str(self.condition) + "\n" + str(self.actions)

    def is_matching_rule(self, file_record: FileRecord):
        matching_rules = file_record.matching_rules

        # no conflict between two matched rules, return the only matching rule
        if len(matching_rules) == 1:
            return self.name == matching_rules[0]

        # get a dict of "rule" : "list of overriding rules"
        overrides_dict = {
            rule_name: self.validator.rules[rule_name].overrides for rule_name in matching_rules
        }

        overriden_rules = set()
        for rule_name, overrides in overrides_dict.items():
            for overriden_name in overrides:
                if overriden_name in matching_rules:
                    overriden_rules.add(overriden_name)

        remaining_rules = set(matching_rules).difference(overriden_rules)
        if len(remaining_rules) == 0:
            raise ValueError(
                f"{','.join(set(matching_rules))} rules may be mutually overriding. Please double check your rules set."
            )
        if len(remaining_rules) > 1:
            raise ValueError(
                f"{','.join(set(remaining_rules))} rules are matched together for one file and overridings are not"
                " defined for such cases. Please double check."
            )

        if self.name == list(remaining_rules)[0]:
            return True
        return False


@dataclass
class Condition:
    """A Condition is defined as a dictionnary nested inside a Rule dictionnary,
    and obtained from the "if" key inside that parent dictionnary.

    They serve as a collection of statements (or nested Condition)
    that evaluate as True or False (inside self.sub_rules).
    They define whether any or all of the evaluated items inside it, should be
    True, for itself to evaluate as True.

    Example :

    ```json
    {
        "if" : {
            "object": "imaging",
            "attribute": "frames",
            "extra": {
                "exact_not": ""
            }
        }
    }
    ```

    Here the content of the "if" key would be passed to Condition as rule_dict,
    like this :

    ```python
    rule_content ={
        "if" : {
            "object": "imaging",
            "attribute": "frames",
            "extra": {
                "exact_not": ""
            }
        }
    }

    Condition(rule_content["if"])
    ```
    """

    reduction: Literal["all", "any", "all_not", "any_not"] = "all"
    expressions: "list[Expression]" = field(default_factory=list)
    conditions: "list[SubCondition]" = field(default_factory=list)
    inverted: bool = False

    @classmethod
    def parse_from_dict(
        cls,
        dico: dict,
        reduction: Literal["all", "any"] = "all",
        inverted: bool = False,
    ):
        expressions, conditions = [], []
        for key, value in dico.items():
            if any([key in reduction for reduction in ["all", "any"]]):
                reduction = key.removesuffix("_not")
                inverted = key.endswith("_not")
                conditions.append(SubCondition.parse_from_dict(value, reduction, inverted))
            else:
                expressions.append(Expression.parse_from_dict(key, value))

        return cls(reduction, expressions, expressions, inverted)

    def __post_init__(self):

        self.reduction_method = any if self.reduction == "any" else all
        for condition in self.conditions:
            condition.bind_to(self)
        for expression in self.expressions:
            expression.bind_to(self)

    def bind_to(self, rule_or_condition: "Rule | Condition") -> "Condition":
        if isinstance(rule_or_condition, Rule):
            self.rule = rule_or_condition
            self.is_subcondition = False
        else:
            self.rule = rule_or_condition.rule
            self.is_subcondition = True
        self.parent = rule_or_condition
        return self

    def evaluate(self, file_record: FileRecord | str | Path) -> FileRecord:
        if isinstance(file_record, (str, Path)):
            file_record = FileRecord(Path(file_record))

        evaluations = []
        for condition in self.conditions:
            boolean_return = condition.evaluate(file_record)
            evaluations.append(boolean_return)
        for expression in self.expressions:
            boolean_return = expression.evaluate(file_record)
            evaluations.append(boolean_return)

        boolean_return = self.reduction_method(evaluations)
        boolean_return = not boolean_return if self.inverted else boolean_return
        file_record.match |= boolean_return
        if boolean_return:
            file_record.matching_rules.append(self.rule.name)
        return file_record

    def __str__(self):
        spacer = "\n    -  "
        sub_rules_str = spacer + spacer.join(
            str(value) for value in (self.conditions + self.expressions)
        )
        type_str = self.reduction
        type_str += "~" if self.inverted else ""
        return f"{'    Conditions :' if self.rule.name else '    ' + type_str}{sub_rules_str}"


@dataclass
class SubCondition(Condition):
    def evaluate(self, file_record: FileRecord | str | Path) -> bool:  # ty:ignore[invalid-method-override]
        if isinstance(file_record, (str, Path)):
            file_record = FileRecord(Path(file_record))

        evaluations = []
        for condition in self.conditions:
            boolean_return = condition.evaluate(file_record)
            evaluations.append(boolean_return)
        for expression in self.expressions:
            boolean_return = expression.evaluate(file_record)
            evaluations.append(boolean_return)
        boolean_return = self.reduction_method(evaluations)

        boolean_return = not boolean_return if self.inverted else boolean_return
        return boolean_return


@dataclass
class Expression:
    """Expressions are the operations that return a boolean as they are evaluated as
    True, or False.
    They are the deepest building block in a Rule pattern. They are
    implementing either an "exact" check (where the value they carry must be exactly a
    given value) a "contain" check (where the value they carry must be found inside a
    given value or set of values) and a "match" check (where the value they carry must
    match a given pattern in regex language).
    The value they carry is actually defined as an attribute (TODO enlarge it to also be
    an index in a mapping) of the object evaluated by the rule.

    They can be written as any of these :
    "attribute": "frames",
        "extra": {
            "exact_not": ""
        }

    """

    attribute: str
    reference: Any
    operation: Literal["exact", "isin", "contain", "match"] = "exact"
    inverted: bool = False

    @classmethod
    def parse_from_dict(cls, attribute: str, reference_dico: Any | dict) -> "Expression":

        if isinstance(reference_dico, dict):
            # if the operation detail is a dict, if should contain a single key, wich is the check operation type
            operation = next(iter(reference_dico.keys()))
            reference = reference_dico[operation]
            inverted = operation.endswith("_not")
            operation = operation.removesuffix("_not")
            return cls(attribute, reference, operation, inverted)

        return cls(attribute, reference_dico)

    def __post_init__(self):

        if self.operation == "match":
            patterns: list[Pattern] = []
            reference = self.reference if isinstance(self.reference, list) else [self.reference]
            for pattern_name in reference:
                try:
                    patterns.append(self.rule.patterns[pattern_name])
                except KeyError:
                    raise ValueError(
                        f"Pattern {pattern_name} was asked in condition statement {self.attribute} "
                        "but was not defined in re_patterns."
                    )
            self.match_patterns = patterns

        if self.operation == "contain":
            self.reference = (
                self.reference if isinstance(self.reference, list) else [self.reference]
            )

        self.operation_method = getattr(self, self.operation)

    def bind_to(self, condition: "Condition") -> "Expression":
        self.condition = condition
        self.rule = condition.rule
        return self

    def evaluate(self, file_record: FileRecord) -> bool:
        if self.attribute == "source_path":
            value = str(file_record.source_path)
        else:
            value = file_record.source_file[self.attribute]
        if value is None:
            return False
        value = str(value)
        # we invert the result of the boolean check if TestExpression.inverted is True, or not if not inverted
        return self.operation_method(value) != self.inverted

    def exact(self, value):
        return value == self.reference

    def isin(self, value):
        return value in self.reference

    def contain(self, value):
        for item in self.reference:
            if item in value:
                return True
        return False

    def match(self, value):
        for pattern in self.match_patterns:
            if pattern.search(value):
                return True
        return False

    def __str__(self):
        return (
            f"{self.attribute} {'~' if self.inverted else ''}{self.operation} -> "
            f"{', '.join([str(value) for value in self.reference])}"
        )
