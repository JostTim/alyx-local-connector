from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Tuple, Protocol
from .typing import ElementNames, RenameElementRule

if TYPE_CHECKING:
    from .rules import Rule
    from .records import FileRecord


class ActionFunction(Protocol):
    def __call__(
        self, file_record: "FileRecord", source: str, *, message: str = ""
    ) -> "FileRecord": ...


@dataclass
class Actions:
    # allowed_actions = ["rename", "include", "delete", "exclude", "abort"]
    # allowed_triggers = [
    #     "match",
    #     "destination_exists",
    #     "rename_unchanged",
    #     "rename_error",
    #     "rename_successfull",
    # ]
    # triggers: Dict[TriggerNames, ActionNames]
    # parent: "Rule"

    actions: "list[Action]"

    @classmethod
    def parse_from_dict(cls, dico: dict) -> "Actions":
        actions = []
        for key, value in dico.items():
            actions.append(Action.parse_from_dict(key, value))
        return cls(actions)

    def __post_init__(self):
        for action in self.actions:
            action.bind_to(self)

        if not any(action.trigger == "match" for action in self.actions):
            raise ValueError(
                f"match action was not defined in rule {self.rule.name}. Must be defined. "
                "Set on : match to null if you wish to keep the rule but make it inactive."
            )
        allowed_triggers = [
            "match",
            "destination_exists",
            "rename_unchanged",
            "rename_error",
            "rename_successfull",
        ]
        allowed_actions = [
            "rename",
            "include",
            "delete",
            "exclude",
            "abort",
        ]

        for action in self.actions:
            if action.trigger not in allowed_triggers:
                raise ValueError(
                    f"Trigger {action.trigger} in rule {self.rule.name} is invalid. Valid keys are {','.join(allowed_triggers)}"
                )
            if action.method_name not in allowed_actions:
                raise ValueError(
                    f"Action {action.method_name} in trigger {action.trigger} in rule {self.rule.name} is invalid. Valid keys are"
                    f" {','.join(allowed_actions)}"
                )

    def bind_to(self, rule: "Rule") -> "Actions":
        self.rule = rule
        return self

    @property
    def patterns(self):
        return self.rule.patterns

    def get_action_for(self, trigger: str) -> "Action | None":
        for action in self.actions:
            if action.trigger == trigger:
                return action
        return None

    def get_action_function_for(self, trigger: str, *, default: str) -> ActionFunction:
        action = self.get_action_for(trigger)
        action = action or Action(trigger, default)  # if get_action_for returned None
        action.bind_to(self)
        return action.method

    def actions_cascade(self, file_record: FileRecord):
        if not file_record.match:
            return file_record

        match_action = self.get_action_for("match")
        if match_action is None:
            return file_record

        if not self.rule.is_matching_rule(file_record):
            return file_record

        file_record.used_rule += self.rule.name + " "

        self.get_action_function_for("match", default="abort")(file_record, "match")

        if not file_record.destination_file.is_dataset_type_valid:
            self.get_action_function_for("invalid_alf_format", default="abort")(
                file_record, "invalid_alf_format"
            )

        return file_record

    def actions_to_dict(self) -> dict:
        return {action.trigger: action.method_name for action in self.actions}

    def __str__(self):
        spacer = "\n    -  "
        triggers_str = spacer + spacer.join(
            [str(key) + " : " + str(value) for key, value in self.actions_to_dict().items()]
        )
        rename_action = self.get_action_for("rename")
        rename_arguments = rename_action.arguments if rename_action else {}
        rename_rule = spacer.join(
            [str(key) + " : " + str(value) for key, value in rename_arguments.items()]
        )
        if rename_rule:
            rename_rule = "\n    Rename Rule :" + spacer + rename_rule
        override_rule = spacer.join(self.rule.overrides)
        if override_rule:
            override_rule = "\n    Overrides :" + spacer + override_rule
        return f"    Actions triggers :{triggers_str}{rename_rule}{override_rule}"


@dataclass
class Action:
    trigger: str
    method_name: str
    arguments: dict = field(default_factory=dict)

    @classmethod
    def parse_from_dict(cls, trigger: str, dico: dict | str) -> "Action":
        if isinstance(dico, dict):
            method_name = next(iter(dico.keys()))
            arguments = dico[method_name]
        else:
            method_name = dico
            arguments = {}
        return cls(trigger, method_name, arguments)

    def __post_init__(self):
        self.method = getattr(self, self.method_name)

        if self.method_name == "rename":
            # self.rename_rule: dict[ElementNames, str | dict] = rule_dict.get("rename", {})
            for key, value in self.arguments.items():
                if isinstance(value, dict):
                    pattern_name = value.get("pattern", None)
                    if pattern_name and pattern_name not in self.rule.patterns.keys():
                        raise KeyError(
                            f"Pattern {pattern_name} was specified in rename action of rule : {self.rule.name} but that "
                            "pattern was not defined in re_patterns."
                        )
                elif not isinstance(value, str):
                    raise ValueError(
                        f"{self.rule.name} rule errror in 'rename' with {key}. The content of a renaming the rule must be either"
                        " a constant string or a dictionnary. See documentation for more details."
                    )

    def bind_to(self, actions: Actions) -> "Action":
        self.actions = actions
        self.rule = actions.rule
        return self

    def rename_element(
        self, file_record: FileRecord, element: ElementNames, rule: RenameElementRule
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
            searched_string = file_record.source_file[
                search_on
            ]  # TODO make a list check in __init__ for that

        match = self.rule.patterns[pattern_name].search(searched_string)

        # we make objects that we can use in case there is a matching or evaluation error.
        # action_if_error is a bound method instance of the current class,
        # that corresponds by name to what the user entered in "rename_error" : "" in rule in the json file.
        # defaults to the abort method.
        action_if_error = self.actions.get_action_function_for("rename_error", default="abort")
        message_error_prefix = f"{element} matching error. Searched on {search_on}, with pattern {pattern_name}, matched {match}."

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
                    message=message_error_prefix
                    + f" Error : {e}. Evaluation string is probably invalid.",
                )
                return "", False
        else:  # eval is not specified. We then expect to use the first element of match as rename
            try:
                return match[0], True  # ty:ignore[not-subscriptable]
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
        rename_status = (
            True  # will be set to false in the for loop after if any element renaming fails
        )
        for element, rule in self.arguments.items():
            file_record.destination_file[element], element_status = self.rename_element(
                file_record, element, rule
            )
            rename_status = rename_status and element_status

        if not rename_status:  # if we got a rename_error above, we skip the next steps
            return file_record

        if file_record.destination_file.fullpath == file_record.source_path:
            file_record.rename = False
            self.actions.get_action_function_for("rename_unchanged", default="include")(
                file_record, "rename_unchanged"
            )
        else:
            self.actions.get_action_function_for("rename_successfull", default="include")(
                file_record, "rename_successfull"
            )

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
