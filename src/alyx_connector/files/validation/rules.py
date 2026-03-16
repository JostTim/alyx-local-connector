from _collections_abc import dict_keys
from dataclasses import dataclass, field
from functools import reduce
from re import Pattern
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Iterator, Literal, cast

from .actions import Outcome
from .exceptions import ParsingError
from .typing import Evaluated, EvaluatedList, ExpressionFunction, MetaClass

if TYPE_CHECKING:
    from .validators import Validator


@dataclass
class Rules(Generic[Evaluated]):
    """A collection of rules"""

    rules: "dict[str, Rule[Evaluated]]"

    # properties for type checking :
    validator: "Validator" = field(init=False)

    @classmethod
    def parse_from_dict(cls, meta: MetaClass, dico: dict[str, dict]) -> "Rules":
        rules = {}
        for rule_name, rule_dict in dico.items():
            rules[rule_name] = meta.rule_class.parse_from_dict(meta, rule_name, rule_dict)
        return cls(rules)

    def bind_to(self, validator: "Validator"):
        self.validator = validator

    def __post_init__(self):
        for rule in self.rules.values():
            rule.bind_to(self)

    def __getitem__(self, name: str) -> "Rule":
        try:
            return self.rules[name]
        except KeyError:
            raise KeyError(f"No rule {name} found")

    def values(self) -> "Iterator[Rule]":
        for rule in self.rules.values():
            yield rule

    def keys(self) -> "dict_keys[str, Rule[Evaluated]]":
        return self.rules.keys()

    def items(self) -> "Iterator[tuple[str, Rule[Evaluated]]]":
        for name, rule in self.rules.items():
            yield name, rule

    def _finalize(self):
        for rule in self.rules.values():
            rule._finalize()

    def resolve_selected_rule(self, evaluated: Evaluated) -> Evaluated:
        matching_rules = evaluated.matching_rules

        if not len(matching_rules):
            evaluated.selected_rule = None
            return evaluated

        # no conflict between two matched rules, return the only matching rule
        if len(matching_rules) == 1:
            evaluated.selected_rule = next(iter(matching_rules))
            return evaluated

        # get a dict of "rule" : "list of overriding rules"
        overrides_dict: dict[str, list[str]] = {
            rule_name: self[rule_name].overrides for rule_name in matching_rules
        }

        overriden_rules: set[str] = set()
        for rule_name, overrides in overrides_dict.items():
            for overriden_name in overrides:
                if overriden_name in matching_rules:
                    overriden_rules.add(overriden_name)

        remaining_rules = matching_rules.difference(overriden_rules)
        if len(remaining_rules) == 0:
            raise ValueError(
                f"{','.join(set(matching_rules))} rules may be mutually overriding. Please double check your rules set."
            )
        elif len(remaining_rules) > 1:
            raise ValueError(
                f"{','.join(set(remaining_rules))} rules are matched together for one file and overridings are not"
                " defined for such cases. Please double check."
            )
        evaluated.selected_rule = next(iter(remaining_rules))
        return evaluated


@dataclass
class Rule(Generic[Evaluated]):
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
    outcome: "Outcome"
    overrides: list[str] = field(default_factory=list)

    # Non init fields
    active: bool = field(default=True, init=False)
    rules: "Rules" = field(init=False)

    @classmethod
    def parse_from_dict(cls, meta: MetaClass, name: str, dico: dict):
        if "if" not in dico.keys():
            raise ParsingError(f"An if field must be defined in the rule {name}")
        condition = meta.condition_class.parse_from_dict(meta, dico["if"])
        outcome = meta.outcome_class.parse_from_dict(meta, dico.get("on", {}))
        overrides = dico.get("overrides", [])
        return cls(name, condition, outcome, overrides)

    def __post_init__(self):
        self.overrides = self.overrides if isinstance(self.overrides, list) else [self.overrides]
        self.condition.bind_to(self)
        self.outcome.bind_to(self)

    def bind_to(self, rules: "Rules") -> "Rule":
        self.rules = rules
        return self

    @property
    def validator(self) -> "Validator":
        return self.rules.validator

    def _finalize(self):
        self.condition._finalize()
        self.outcome._finalize()

    def is_matching_rule(self, evaluated: Evaluated):
        return evaluated.selected_rule == self.name

    def evaluate(self, evaluated: Evaluated) -> Evaluated:
        if not self.active:
            return evaluated

        boolean_return = self.condition.evaluate(evaluated)
        if boolean_return:
            evaluated.add_matched_rule(self.name)
        return evaluated

    def apply(
        self, evaluated: Evaluated, evaluated_list: EvaluatedList, allowed_triggers: list[str]
    ) -> Evaluated:
        if not self.is_matching_rule(evaluated) or not self.active:
            return evaluated
        return self.outcome.apply(evaluated, evaluated_list, allowed_triggers)


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

    reduction: Literal["all", "any"] = field(default="all")
    expressions: "list[Expression]" = field(default_factory=list)
    conditions: "list[Condition]" = field(default_factory=list)
    inverted: bool = field(default=False)
    depth: int = field(default=0)

    @classmethod
    def parse_from_dict(
        cls,
        meta: MetaClass,
        dico: dict,
        reduction: Literal["all", "any"] = "all",
        inverted: bool = False,
        depth: int = 0,
    ):
        expressions, conditions = [], []
        for key, value in dico.items():
            if any([key in reduction for reduction in ["all", "any"]]):
                # We found a condition (a group of expressions and / or conditions)
                condition = cls._parse_subcondition_from_dict(meta, key, value, depth)
                conditions.append(condition)
            else:
                # We found an expression
                expression = meta.expression_class.parse_from_dict(meta, key, value)
                expressions.append(expression)
        return cls(reduction, expressions, conditions, inverted, depth)

    @staticmethod
    def _parse_subcondition_from_dict(
        meta: MetaClass, key: str, dico: dict, depth: int
    ) -> "Condition":
        # reductions defined in dict can be :
        # "all", "any", "all_not", "any_not"
        reduction = cast(Literal["all", "any"], key.removesuffix("_not"))
        inverted = key.endswith("_not")
        condition = meta.condition_class.parse_from_dict(
            meta, dico, reduction, inverted, depth + 1
        )
        return condition

    def __post_init__(self):
        self.reduction_method = any if self.reduction == "any" else all
        for condition in self.conditions:
            condition.bind_to(self)
        for expression in self.expressions:
            expression.bind_to(self)

    def bind_to(self, rule_or_condition: "Rule | Condition") -> "Condition":
        self.parent = rule_or_condition
        return self

    def _finalize(self):
        pass

    @property
    def rule(self) -> "Rule":
        return reduce(lambda condition, _: condition.parent, range(self.depth), self)  # ty:ignore[invalid-return-type]

    @property
    def validator(self) -> "Validator":
        return self.rule.validator

    def evaluate(self, evaluated: Evaluated) -> bool:
        """Agregates all the boolean returs of the current
        condition's subconditions and expressions, then
        reduce it with the reduction method (any or all)
        then invert the boolean result if the condition is in
        inverted mode
        """
        evaluations: list[bool] = []
        for condition in self.conditions:
            boolean_return = condition.evaluate(evaluated)
            evaluations.append(boolean_return)
        for expression in self.expressions:
            boolean_return = expression.evaluate(evaluated)
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
    operation: Literal["is", "in", "in_any_of", "contain", "contain_any_of", "match"] = "is"
    inverted: bool = False

    # Attributes for finalizing validator creation
    _operation_names: ClassVar[list[str]] = [
        "is",
        "in",
        "in_any_of",
        "contain",
        "contain_any_of",
        "match",
    ]

    # type checking of methods
    operation_method: ExpressionFunction = field(init=False)
    match_patterns: list[Pattern] = field(init=False, default_factory=list)

    @classmethod
    def parse_from_dict(
        cls, meta: MetaClass, attribute: str, reference_dico: Any | dict
    ) -> "Expression":

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
            patterns = []
            reference = self.reference if isinstance(self.reference, list) else [self.reference]
            for pattern_name in reference:
                try:
                    patterns.append(self.validator.options.re_patterns[pattern_name])
                except KeyError:
                    raise ValueError(
                        f"Pattern {pattern_name} was asked in condition statement {self.attribute} "
                        "but was not defined in re_patterns."
                    )

            self.match_patterns = patterns

        if self.operation == "isin":
            self.reference = (
                self.reference if isinstance(self.reference, list) else [self.reference]
            )

        self.operation_method = getattr(self, f"_{self.operation}")

    def bind_to(self, condition: "Condition") -> "Expression":
        self.condition = condition
        return self

    def _finalize(self):
        pass

    @property
    def rule(self) -> "Rule":
        return self.condition.rule

    @property
    def validator(self) -> "Validator":
        return self.rule.validator

    def evaluate(self, evaluated: Evaluated) -> bool:
        # Obtain the value to compare to, from the evaluated object

        # either from it as an attribute, if one exists
        if hasattr(evaluated.looked_up_object, self.attribute):
            value = getattr(evaluated.looked_up_object, self.attribute)

        # or else in a dict like form, if the object implements get
        elif hasattr(evaluated.looked_up_object, "get"):
            value = evaluated.looked_up_object.get(self.attribute, Ellipsis)

        # or finally with the [] indexing method, if the object implements it
        elif hasattr(evaluated.looked_up_object, "__getitem__"):
            try:
                value = evaluated.looked_up_object[self.attribute]
            except KeyError:
                value = Ellipsis
        else:
            value = Ellipsis

        # if no method worked or the object was not found, evaluation failed,
        # we return False
        if value is Ellipsis:
            return False

        value = str(value)
        # TODO do we keep str comparison or
        # do we swtich to Any and find a way to let user configure
        # if value should be converted or not ?

        # Finally we invert the result of the boolean check if
        # Expression.inverted is True, or we don't invert it otherwise
        return self.operation_method(value) != self.inverted

    def _is(self, value: str):
        """The value of the object must be exactly the one defined by the rule.
        This is the default operation if no operation name is defined."""
        return value == self.reference

    def _in(self, value: str):
        """The value of the object must be IN the one(s) defined by the rule.
        Wether the value defined by the rule is a list or a string, it must implement
        __contains__
        """
        return value in self.reference

    def _in_any_of(self, value: str):
        """The value of the object must be in one of the values defined by the rule.
        You can see this as a"""
        for ref_item in self.reference:
            if value in ref_item:
                return True
        return False

    def _contains(self, value: str):
        return self.reference in value

    def _contains_any_of(self, value: str):
        """The value of the object must be in one of the values defined by the rule.
        You can see this as a"""
        for ref_item in self.reference:
            if ref_item in value:
                return True
        return False

    def _match(self, value: str):
        for pattern in self.match_patterns:
            if pattern.search(value):
                return True
        return False
