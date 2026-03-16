from dataclasses import dataclass
from pathlib import Path
from re import Match
from typing import Any, Dict, Literal, Tuple, Union, cast

from ...files import File
from .actions import Action, Trigger
from .records import Encapsulation, EncapsulationList
from .validators import Validator

# from ...web.specifics import get_dataset_types

ElementNames = Literal[
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

RenameElementRule = Union[
    str,
    Dict[
        Literal[
            "pattern",
            "eval",
            "search_on",
        ],
        str,
    ],
]

RulesConfig = Dict[
    Literal[
        "re_patterns",
        "rules",
        "excluded_folders",
        "excluded_filenames",
        "cleanup_folders",
    ],
    str | dict,
]


class FileRecord(Encapsulation[Path]):
    original_object: Path
    lookup_attrs_on = "source_file"

    delete = False
    include = False
    rename = False
    delete = False
    abort = False

    @property
    def source_file(self) -> "File":
        if not hasattr(self, "_source_file"):
            self._source_file = File.from_path(self.original_object)
        return self._source_file

    @property
    def destination_file(self) -> "File":
        if not hasattr(self, "_destination_file"):
            self._destination_file = self.source_file.copy()
        return self._destination_file


class FileRecordList(EncapsulationList[FileRecord]):
    encapsulation_class = FileRecord


class FileTrigger(Trigger[FileRecord]):
    defaults = {
        "rename_unchanged": "include",
        "rename_successfull": "include",
        "destination_exists": "abort",
        "rename_error": "abort",
        "rename_match_error": "abort",
        "exists_on_server": ["exclude", "no_rename"],
        "match": "rename",
    }


@dataclass
class MatchParams:
    file_record: "FileRecord"
    action: "FileAction"
    element: ElementNames
    pattern_name: str | None
    search_on: str
    eval_string: str | None

    @staticmethod
    def from_dict(
        file_record: "FileRecord",
        action: "FileAction",
        element: ElementNames,
        dico: dict[Literal["pattern", "eval", "search_on"], str],
    ) -> "MatchParams":
        pattern_name = dico.get("pattern", None)
        search_on = dico.get("search_on", "source_path")
        eval_string = dico.get("eval", None)
        return MatchParams(
            file_record,
            action,
            element,
            pattern_name,
            search_on,
            eval_string,
        )

    @property
    def search_string(self) -> str:
        if self.search_on == "source_path":
            searched_string = str(self.file_record.original_object)
        elif self.search_on == "source_filename":
            searched_string = str(Path(self.file_record.original_object).name)
        else:
            searched_string = str(self.file_record.source_file[self.search_on])
        return searched_string

    @property
    def _re_help_msg(self) -> str:
        return "Test pattern on https://regex101.com/"

    @property
    def _rule_msg(self) -> str:
        return f"Rule {self.action.rule.name}'s {self.element}"

    def match(self) -> tuple[str, bool]:
        if self.pattern_name is None:
            return self._register_error(ValueError("Pattern not defined"), re_help=False)

        match = self.action.validator.options.re_patterns[self.pattern_name].search(
            self.search_string
        )
        if not match:
            return self._register_error(ValueError("No Match Found"), re_help=False)
        if self.eval_string:
            return self.eval(match)

        return match[0], True

    def eval(self, match: Match[str]) -> tuple[str, bool]:
        eval_string = cast(str, self.eval_string)
        locals = {
            "match": match,
            "file_record": self.file_record,
            "action": self.action,
            "element": self.element,
            "pattern_name": self.pattern_name,
            "search_on": self.search_on,
        }
        try:
            # evaluation succeeds
            return str(eval(eval_string, locals)), True
        except IndexError as error:
            # evaluation fails with index error. The match doesn't have the right index, most likely
            return self._register_error(error, "Match Index Error in Evaluation", re_help=False)
        except Exception as error:
            # this occurs when there is probably an error with the eval statement of the rule.
            return self._register_error(error, "Evaluation Error", re_help=False)

    def _register_error(
        self,
        error: type[Exception] | Exception,
        message: str = "",
        re_help: bool = True,
    ) -> tuple[Literal[""], Literal[False]]:

        final_message = f"{self._rule_msg} {error}." + message
        if re_help:
            final_message += self._re_help_msg

        self.file_record.add_trigger("rename_match_error", message=final_message)

        # error_trigger = self.action.get_trigger_for("rename_match_error")
        # error_trigger.apply(self.file_record, self.file_record_list, message=final_message)
        return "", False  # we don't change the element's value


class FileAction(Action[FileRecord]):
    def _get_replaced_element_value(
        self,
        file_record: "FileRecord",
        element: ElementNames,
        replacement_or_pattern: RenameElementRule,
    ) -> Tuple[str, bool]:
        """Return a renamed element of the source file based on the element name an content.

        Args:
            file_record (FileRecord): The file to perform renaming on
            element (str): the elemnt to rename (object, attribute, etc.)
            rule (str | dict): _description_

        Raises:
            ValueError: _description_

        Returns:
            _type_: _description_
        """
        if isinstance(replacement_or_pattern, str):  # constant replacement
            return replacement_or_pattern, True
        return MatchParams.from_dict(
            file_record,
            self,
            element,
            replacement_or_pattern,
        ).match()

    # actions :
    def rename(self, file_record: "FileRecord") -> "FileRecord":
        file_record.rename = True
        # file_record.executed_actions.append(f"{source} -> rename")

        # will be set to false in the for loop after if any element renaming fails
        arguments = cast(dict[ElementNames, Any], self.arguments)
        rename_status = True
        for element, replacement_or_pattern in arguments.items():
            replacement, replacement_status = self._get_replaced_element_value(
                file_record, element, replacement_or_pattern
            )
            file_record.destination_file[element] = replacement
            rename_status = rename_status and replacement_status

        if not rename_status:  # if we got a rename_error above, we skip the next steps
            return file_record

        if file_record.destination_file.fullpath == file_record.original_object:
            file_record.rename = False
            file_record.add_trigger("rename_unchanged")
        else:
            file_record.add_trigger("rename_successfull")

        return file_record

    def exclude(
        self, file_record: "FileRecord", file_record_list: "FileRecordList"
    ) -> "FileRecord":
        file_record.include = False
        return file_record

    def include(
        self, file_record: "FileRecord", file_record_list: "FileRecordList"
    ) -> "FileRecord":
        file_record.include = True
        return file_record

    def delete(
        self, file_record: "FileRecord", file_record_list: "FileRecordList"
    ) -> "FileRecord":
        file_record.delete = True
        return file_record

    def abort(
        self, file_record: "FileRecord", file_record_list: "FileRecordList", message: str = ""
    ) -> tuple["FileRecord", str]:
        file_record.abort = True
        message += "Aborted"
        return file_record, message


class FileValidator(Validator):
    class Meta:
        encapsulation_list_class = FileRecordList
        action_class = FileAction
        trigger_class = FileTrigger
