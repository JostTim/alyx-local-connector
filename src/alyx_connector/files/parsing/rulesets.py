from dataclasses import dataclass
from pathlib import Path
from typing import Literal, List, Dict, Tuple, Optional, Union, Callable, Protocol, Iterator, cast


class ActionFunction(Protocol):
    def __call__(self, file_record: "FileRecord", source: str, *, message: str = "") -> "FileRecord": ...


class OperationFunction(Protocol):
    def __call__(
        self,
        value: str,
    ) -> bool: ...


@dataclass
class FileRecord:
    # source_path is the original path from disk scan. will never change
    source_path: Path

    match: bool = False
    matching_rules: list = field(default_factory=list)
    used_rule: str = ""

    valid_alf: bool = False
    path_conflicts: bool = False
    dataset_type_exists: bool = False
    rename: bool = False
    delete: bool = False
    include: bool = False
    abort: list = field(default_factory=list)

    frozen = False

    executed_actions = []

    # source file
    @property
    def source_file(self) -> File:
        source_file = getattr("self", "_source_file", None)
        if source_file is None:
            try:
                source_file = File.from_path(self.source_path)
            except Exception as e:
                raise ValueError(f"File from {self.source_path} was not parseable ! {type(e)}:{e}")
            self._source_file = source_file
        return source_file

    # was alf_info
    @property
    def destination_file(self) -> File:
        destination_file = getattr("self", "_destination_file", None)
        if destination_file is None:
            destination_file = self.source_file.copy()
            self._destination_file = destination_file
        return destination_file

    @property
    def final_path(self) -> Path:
        if not hasattr("self", "_final_path"):
            self._final_path = self.destination_file.fullpath if self.rename else self.source_path
        return self._final_path

    @property
    def final_file(self) -> "File":
        if not hasattr("self", "_final_file"):
            self._final_file = self.destination_file if self.rename else self.source_file
        return self._final_file

    @property
    def final_pathstring(self):
        if not hasattr("self", "_final_pathstring"):
            self._final_pathstring = str(self.final_path)
        return self._final_pathstring

    def actions_cascade(self, config: "Config"):
        for rule in config.rules.values():
            rule.actions_cascade(self)

    def finish_cascade(self, config: "Config"):
        # change the values of the filre record after all other actions have been resolved.
        # finish actions include calculating ,

        self.valid_alf = self.destination_file.is_dataset_type_valid

        if self.valid_alf and self.destination_file.dataset_type in config.dataset_types:
            self.dataset_type_exists = True

    @property
    def inclusion_accepted(self):
        if (
            self.include
            and self.valid_alf
            and self.dataset_type_exists
            and not self.path_conflicts
            and not self.abort
            and not self.delete
        ):
            return True
        return False

    @property
    def rename_accepted(self):
        if self.rename and self.valid_alf and not self.path_conflicts and not self.abort and not self.delete:
            return True
        return False

    def apply_changes(self, do_deletes=True, do_renames=True):  # apply changes to file_record
        if self.delete and self.rename:
            raise ValueError("Cannot rename AND delete the same entry.")

        if self.rename_accepted and do_renames:
            file_directory = self.destination_file.fullpath.parent
            file_directory.mkdir(parents=True, exist_ok=True)
            self.source_file.fullpath.rename(self.destination_file.fullpath)

        elif self.delete and do_deletes:
            self.source_file.fullpath.unlink()
            self.include = False

    def to_user_dict(self):
        return {
            "source_path": self.source_path,
            "dest_path": str(self.destination_file.fullpath) if self.rename_accepted else "",
            "info": self.info_message,
        }

    @property
    def info_message(
        self,
    ):  # message to make from action booleans to help the use understand what happened
        message = ""
        if self.inclusion_accepted and not self.rename_accepted:
            message = " included without change"

        if self.inclusion_accepted and self.rename_accepted:
            message = " renamed and included"

        if not self.inclusion_accepted and self.rename_accepted:
            message = " renamed and excluded"

        if not self.inclusion_accepted and not self.rename_accepted:
            message = " excluded without change"

        if not self.valid_alf:
            message = message + " dataset_type doesn't follow alyx format"

        if not self.dataset_type_exists:
            message = message + f" dataset_type:{self.destination_file.dataset_type} not existing"

        if self.delete:
            message = " auto deleted"

        if self.abort:
            message_prefix = " Aborting due to errors : "
            abort_messages = []
            for ab_msg in self.abort:
                if ab_msg == " filepath_conflicts":
                    ab_msg = " File name conflicts with a current file or another file that will be renamed identically"
                abort_messages.append(ab_msg)

            message = (
                message_prefix
                + ", ".join(abort_messages)
                # + " --- Without Abort, would have been "
                # + message
            )
        else:
            message = "Will be " + message

        return str(message)


class Statement:
    allowed_operations: List[CheckOperation] = ["exact", "contain", "match"]
    allowed_elements = [
        "source_path",
        "subject",
        "date",
        "number",
        "root",
        "object",
        "attribute",
        "extension",
        "extra",
        "collection",
        "revision",
    ]
    conditions: List[str] | List[Pattern]
    operation: CheckOperation
    operation_method: OperationFunction

    def __init__(
        self, element: Element, operation_detail: str | List[str] | Dict[CheckFullOperation, str | list[str]], parent
    ):
        self.inverted = False
        self.parent = parent

        for el in self.allowed_elements:
            if element == el:
                self.tested_element = element
                break
        else:
            raise ValueError(
                f"Tested element is {self.tested_element} wich is invalid value. "
                f"If must be one of :({''.join(self.allowed_elements)})"
            )

        if isinstance(operation_detail, dict):
            # if the operation detail is a dict, if should contain a single key, wich is the check operation type
            if len(operation_detail) != 1:
                raise ValueError(
                    f"Content of tested element {self.tested_element} is a dict. "
                    "Then it must contain only one key ({''.join(self.allowed_operations)})"
                )

            operation = list(operation_detail.keys())[0]
            conditions = operation_detail[operation]

            for op in self.allowed_operations:
                if op in operation:
                    self.inverted = True if "_not" in operation else False
                    self.operation = op
                    break
            else:
                raise ValueError(
                    f"Content of tested element {self.tested_element} is a dict and the key present was : {operation}. "
                    "It must contain one of : ({''.join(self.allowed_operations)}). "
                )
        else:
            # If the operation detail is not a dict, then we asume that the check operation
            # is : "exact" matching of the string or strings
            self.operation = "exact"
            conditions = operation_detail

        if not isinstance(conditions, list):
            conditions = [conditions]

        if self.operation == "match":
            existing_patterns = self.patterns
            patterns = []
            for pattern_name in conditions:
                try:
                    patterns.append(existing_patterns[pattern_name])
                except KeyError:
                    raise ValueError(
                        f"Pattern {pattern_name} was asked in condition statement {self.tested_element} "
                        "but was not defined in re_patterns."
                    )
            conditions = patterns

        # self.operation is a string name of a method of the class that handles boolean matchong a condition.
        # self.operation_method is the actual bound method object that corresponds to that string
        self.operation_method = getattr(self, self.operation)
        self.conditions = conditions

    @property
    def patterns(self):
        return self.parent.patterns

    def evaluate(self, file_record: FileRecord):
        if self.tested_element == "source_path":
            value = str(file_record.source_path)
        else:
            value = file_record.source_file[self.tested_element]
        if value is None:
            return False
        value = str(value)
        # we invert the result of the boolean check if Statement.inverted is True, or not if not inverted
        return self.operation_method(value) != self.inverted

    def exact(self, value):
        return value in self.conditions

    def contain(self, value):
        for item in self.conditions:
            if item in value:
                return True
        return False

    def match(self, value):
        for pattern in self.conditions:
            if pattern.search(value):
                return True
        return False

    def __str__(self):
        return (
            f"{self.tested_element} {'~' if self.inverted else ''}{self.operation} -> "
            f"{', '.join([str(value) for value in self.conditions])}"
        )


class RuleConditions:
    def __init__(
        self,
        rule_dict: dict,
        parent: "Rule | RuleConditions",
        rule_name: Optional[str] = None,
        rule_type: Literal["all", "any"] = "all",
        inverted: bool = False,
    ):
        allowed_types: List[Literal["all", "any"]] = ["all", "any"]
        rule_methods = [all, any]
        if rule_type not in allowed_types:
            raise ValueError(f"rule_type must be one of : {','. join(allowed_types)}")

        self.parent = parent
        self.rule_name = rule_name
        self.rule_type = rule_type
        self.rule_method = rule_methods[
            allowed_types.index(self.rule_type)
        ]  # get the actual python object corresponding to the rule type
        self.inverted = inverted
        self.sub_rules: List[RuleConditions | Statement] = []

        for key, value in rule_dict.items():
            allowed_types = ["all", "any"]
            for ty in allowed_types:
                if ty in key:
                    _rule_type = ty
                    _inverted = True if "_not" in key else False
                    sub_rule = RuleConditions(value, self, rule_type=_rule_type, inverted=_inverted)
                    break
            else:
                sub_rule = Statement(key, value, self)

            self.sub_rules.append(sub_rule)

    @property
    def patterns(self):
        return self.parent.patterns

    def evaluate(self, file_record: FileRecord | str | Path) -> FileRecord | bool:
        if isinstance(file_record, (str, Path)):
            file_record = FileRecord(Path(file_record))
        evaluations = []
        for condition in self.sub_rules:
            boolean_return = condition.evaluate(file_record)
            evaluations.append(boolean_return)
        boolean_return = self.rule_method(evaluations)

        boolean_return = not boolean_return if self.inverted else boolean_return
        if self.rule_name is None:
            return boolean_return

        file_record.match |= boolean_return
        if boolean_return:
            file_record.matching_rules.append(self.rule_name)
        return file_record

    def __str__(self):
        spacer = "\n    -  "
        line_return = "\n"
        sub_rules_str = spacer + spacer.join(str(value) for value in self.sub_rules)
        type_str = self.rule_type
        type_str += "~" if self.inverted else ""
        return f"{'    Conditions :' if self.rule_name else '    ' + type_str}{sub_rules_str}"


class RuleActions:
    allowed_actions = ["rename", "include", "delete", "exclude", "abort"]
    allowed_triggers = ["match", "destination_exists", "rename_unchanged", "rename_error", "rename_successfull"]
    triggers: Dict[Triggers, Actions]

    def __init__(self, rule_dict: dict, rule_name: str, parent: "Rule"):
        self.rule_name = rule_name
        self.parent = parent
        if "on" not in rule_dict.keys():
            raise ValueError(f'The rule {rule_name} defined no "on" triggers !')

        self.triggers = rule_dict["on"]

        if "match" not in self.triggers.keys():
            raise ValueError(
                f"match action was not defined in rule {rule_name}. Must be defined. "
                "Set on : match to null if you wish to keep the rule but make it inactive."
            )

        if self.triggers["match"] is None:
            self.parent.active = False

        for key, value in self.triggers.items():
            if value == "rename" and "rename" not in rule_dict.keys():
                raise ValueError(
                    f"Trigger {key} in rule {rule_name} was defined with action rename, "
                    "but rename rules are not defined"
                )
            if key not in self.allowed_triggers:
                raise ValueError(
                    f"Trigger {key} in rule {rule_name} is invalid. Valid keys are {','.join(self.allowed_triggers)}"
                )
            if value not in self.allowed_actions:
                raise ValueError(
                    f"Action {value} in trigger {key} in rule {rule_name} is invalid. Valid keys are"
                    f" {','.join(self.allowed_actions)}"
                )

        self.rename_rule: dict[Element, str | dict] = rule_dict.get("rename", {})
        for key, value in self.rename_rule.items():
            if isinstance(value, dict):
                pattern_name = value.get("pattern", None)
                if pattern_name and pattern_name not in self.patterns.keys():
                    raise KeyError(
                        f"Pattern {pattern_name} was specified in rename action of rule : {self.rule_name} but that "
                        "pattern was not defined in re_patterns."
                    )
            elif not isinstance(value, str):
                raise ValueError(
                    f"{rule_name} rule errror in 'rename' with {key}. The content of a renaming the rule must be either"
                    " a constant string or a dictionnary. See documentation for more details."
                )

    @property
    def patterns(self):
        return self.parent.patterns

    def actions_cascade(self, file_record: FileRecord):
        if not file_record.match:
            return file_record

        match_action = self.triggers.get("match", None)
        if match_action is None:
            return file_record

        if not self.is_matching_rule(file_record):
            return file_record

        file_record.used_rule += self.rule_name + " "

        self.get_action_function_for("match", default="abort")(file_record, "match")

        if not file_record.destination_file.is_dataset_type_valid:
            self.get_action_function_for("invalid_alf_format", default="abort")(file_record, "invalid_alf_format")

        return file_record

    def is_matching_rule(self, file_record: FileRecord):
        matching_rules = file_record.matching_rules

        # no conflict between two matched rules, return the only matching rule
        if len(matching_rules) == 1:
            return self.rule_name == matching_rules[0]

        # get a dict of "rule" : "list of overriding rules"
        overrides_dict = {rule_name: self.parent.parent.rules[rule_name].overrides for rule_name in matching_rules}

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

        if self.rule_name == list(remaining_rules)[0]:
            return True
        return False

    def rename_element(
        self, file_record: FileRecord, element: Element, rule: RenameElementRule
    ) -> Tuple[str | None, bool]:
        """Return a renamed element of the source file based on the element name an content.

        Args:
            file_record (FileRecord): The file to perform renaming on
            element (str): the elemnt to rename (ocject, attribute, etc.)
            rule (str | dict): _description_

        Raises:
            ValueError: _description_

        Returns:
            _type_: _description_
        """
        if isinstance(rule, str):  # constant replacement
            return rule, True
        # then, it must be a dictionnary with pattern defined
        pattern_name = rule["pattern"]
        search_on = rule.get("search_on", "source_path")
        if search_on == "source_path":
            searched_string = str(file_record.source_path)
        elif search_on == "source_filename":
            searched_string = str(file_record.source_path.name)
        else:
            searched_string = file_record.source_file[search_on]  # TODO make a list check in __init__ for that

        match = self.patterns[pattern_name].search(searched_string)

        # we make objects that we can use in case there is a matching or evaluation error.
        # action_if_error is a bound method instance of the current class,
        # that corresponds by name to what the user entered in "rename_error" : "" in rule in the json file.
        # defaults to the abort method.
        action_if_error = self.get_action_function_for("rename_error", default="abort")
        message_error_prefix = (
            f"{element} matching error. Searched on {search_on}, with pattern {pattern_name}, matched {match}."
        )

        if eval_string := rule.get("eval", None):
            try:
                return eval(eval_string), True
            # this occurs when there is most likely not match.
            # Examples : NoneType is not supscriptable if match = None (TypeError)
            # or match[3] does not exist because match contains only two elements (IndexError)
            except (IndexError, TypeError) as e:
                action_if_error(
                    file_record,
                    "no_match",
                    message=message_error_prefix
                    + f" Error : {e}. No match have been found. Test on https://regex101.com/",
                )
                return "", False

            # this occurs when there is probably an error with the eval statement of the rule.
            except Exception as e:
                action_if_error(
                    file_record,
                    "evaluation_string_invalid",
                    message=message_error_prefix + f" Error : {e}. Evaluation string is probably invalid.",
                )
                return "", False
        else:  # eval is not specified. We then expect to use the first element of match as rename
            try:
                return match[0], True
            # this occurs when there is most likely not match.
            except (IndexError, TypeError) as e:
                action_if_error(
                    file_record,
                    "no_match_first_element",
                    message=message_error_prefix
                    + f" Error : {e}. No match have been found for first element. Test on https://regex101.com/",
                )
                return "", False  # we backtrack and don't change the element's value

    # actions :
    def rename(self, file_record: FileRecord, source: str, *, message: str = "") -> FileRecord:
        file_record.rename = True
        file_record.executed_actions.append(f"{source} -> rename")
        rename_status = True  # will be set to false in the for loop after if any element renaming fails
        for element, rule in self.rename_rule.items():
            file_record.destination_file[element], element_status = self.rename_element(file_record, element, rule)
            rename_status = rename_status and element_status

        if not rename_status:  # if we got a rename_error above, we skip the next steps
            return file_record

        if file_record.destination_file.fullpath == file_record.source_path:
            file_record.rename = False
            self.get_action_function_for("rename_unchanged", default="include")(file_record, "rename_unchanged")
        else:
            self.get_action_function_for("rename_successfull", default="include")(file_record, "rename_successfull")

        return file_record

    def exclude(self, file_record: FileRecord, source: str, *, message: str = "") -> FileRecord:
        file_record.include = False
        file_record.executed_actions.append(f"{source} -> exclude")
        return file_record

    def include(self, file_record: FileRecord, source: str, *, message: str = "") -> FileRecord:
        file_record.include = True
        file_record.executed_actions.append(f"{source} -> include")
        return file_record

    def delete(self, file_record: FileRecord, source: str, *, message: str = "") -> FileRecord:
        file_record.delete = True
        file_record.executed_actions.append(f"{source} -> delete")
        return file_record

    def abort(self, file_record: FileRecord, source: str, *, message: str = "") -> FileRecord:
        file_record.abort.append(message)
        file_record.executed_actions.append(f"{source} -> abort")
        return file_record

    def get_action_function_for(self, trigger_name: Triggers, *, default: Actions) -> ActionFunction:
        return getattr(self, self.triggers.get(trigger_name, default))

    def __str__(self):
        spacer = "\n    -  "
        triggers_str = spacer + spacer.join([str(key) + " : " + str(value) for key, value in self.triggers.items()])
        rename_rule = spacer.join([str(key) + " : " + str(value) for key, value in self.rename_rule.items()])
        if rename_rule:
            rename_rule = "\n    Rename Rule :" + spacer + rename_rule
        override_rule = spacer.join(self.parent.overrides)
        if override_rule:
            override_rule = "\n    Overrides :" + spacer + override_rule
        return f"    Actions triggers :{triggers_str}{rename_rule}{override_rule}"


class Rule:
    def __init__(self, rule_dict: dict, rule_name: str, parent: "Config"):
        if "if" not in rule_dict.keys():
            raise ValueError(f"An if field must be defined in the rule {rule_name}")
        self.rule_name = rule_name
        self.parent = parent
        self.rule_conditions = RuleConditions(rule_dict["if"], self, rule_name=rule_name)
        self.rule_actions = RuleActions(rule_dict, rule_name, self)
        overrides = rule_dict.get("overrides", [])
        self.overrides = overrides if isinstance(overrides, list) else [overrides]
        self.active = True

    @property
    def patterns(self):
        return self.parent.patterns

    def evaluate(self, file_record):
        return self.rule_conditions.evaluate(file_record) if self.active else file_record

    def actions_cascade(self, file_record) -> FileRecord:
        return self.rule_actions.actions_cascade(file_record) if self.active else file_record

    def __str__(self):
        return f"Rule : {self.rule_name}" + "\n" + str(self.rule_actions) + "\n" + str(self.rule_conditions)
