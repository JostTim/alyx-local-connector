from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from re import Match
from typing import Any, Dict, Literal, Tuple, Union, cast

from ...connector import Connector
from ...files import File
from .actions import Action, Trigger
from .records import Encapsulation, EncapsulationList
from .validators import Validator

# from ...web.specifics import get_dataset_types


class Abort(Exception): ...


class Delete: ...


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
    result: Path | None = None

    ready_to_be_registered = False
    ready_to_rename = False
    ready_to_delete = False
    abort = False

    def __post_init__(self):
        self.original_object = Path(PureWindowsPath(self.original_object))

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

    @property
    def final_path(self) -> Path | None:
        return self.result

    @final_path.setter
    def final_path(self, path: str | Path | None):
        path = Path(PureWindowsPath(path)) if path is not None else None
        self.set_result(path)

    def rename(self):
        if self.final_path is None:
            return
        self.original_object.rename(self.final_path)

    def delete(self):
        if self.final_path is None:
            return
        self.final_path.unlink()


class FileRecordList(EncapsulationList[FileRecord]):
    encapsulation_class = FileRecord

    def set_final_path(self):
        for file_record in self.elements:
            file_record.final_path = file_record.destination_file.fullpath

    def verify_file_names_or_raise_invalid_match(self):
        for file_record in self.elements:
            file_record.add_trigger("invalid_file_name")

    def add_invalid_file_name_trigger(self, validator: "Validator"):
        """Run this step only after having ran "evaluate" on a"""
        for file_record in self:
            if file_record.source_file is None:
                file_record.add_trigger("invalid_file_name")


class FileTrigger(Trigger[FileRecord]):
    defaults = {
        ### HERE ARE THE RENAME PARSING (STAGE 1)
        # if a problem occured when processing a rename
        # (the name of the desination file was not changed in the was expected)
        # we abort the whole process, to avoid mising duplication renaming errors, etc.
        "invalid_file_name": "abort",
        "parsing_error": "abort",
        "parsing_match_error": "abort",
        # if parsing of the new name succeeds, check destination
        # (on hard drive, look for conflicts
        "parsing_success": "_check_name_conflicts",  # TODO make some triggers to action relationships, not overriteable in rules ?
        ### HERE ARE THE RENAME CHECKS (STAGE 2)
        # if rename leads to no change, check server
        "rename_conflict": "abort",
        # if the new file name didn't existed not conflicted, we allow the file to be changing name
        "rename_unchanged": "register",
        # if two files or more are to be renamed identically, stop the process
        "rename_allowed": [
            "_rename",
            "register",
        ],  # TODO make some action names not possible to bind in rules, only in defaults. (like execute_rename)
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
            return self._register_error(
                ValueError("Pattern not defined"), re_help=False
            )

        match = self.action.validator.options.re_patterns[
            self.pattern_name
        ].search(self.search_string)
        if not match:
            return self._register_error(
                ValueError("No Match Found"), re_help=False
            )
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
            return self._register_error(
                error, "Match Index Error in Evaluation", re_help=False
            )
        except Exception as error:
            # this occurs when there is probably an error with the eval statement of the rule.
            return self._register_error(
                error, "Evaluation Error", re_help=False
            )

    def _register_error(
        self,
        error: type[Exception] | Exception,
        message: str = "",
        re_help: bool = True,
    ) -> tuple[Literal[""], Literal[False]]:

        final_message = f"{self._rule_msg} {error}." + message
        if re_help:
            final_message += self._re_help_msg

        self.file_record.add_trigger(
            "parsing_match_error", message=final_message
        )

        # error_trigger = self.action.get_trigger_for("rename_match_error")
        # error_trigger.apply(self.file_record, self.file_record_list, message=final_message)
        return "", False  # we don't change the element's value


class FileAction(Action[FileRecord]):
    available_actions = ["rename", "register", "delete", "abort", "ignore"]

    def _rename(self, file_record: "FileRecord") -> "FileRecord":
        file_record.ready_to_rename = True
        return file_record

    def _check_name_conflicts(
        self, file_record: "FileRecord", file_record_list: "FileRecordList"
    ):
        # check if there is a duplicate in the file_record_list

        if file_record.final_path is None:
            return file_record.add_trigger("invalid_file_name")

        other_final_paths = [
            rec.final_path for rec in file_record_list if not rec.abort
        ]
        if file_record.final_path in other_final_paths:
            return file_record.add_trigger("rename_conflict")

        if file_record.final_path == file_record.original_object:
            return file_record.add_trigger("rename_unchanged")

        # else, rename is allowed
        return file_record.add_trigger("rename_allowed")

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

        # will be set to false in the for loop after if any element renaming fails
        arguments = cast(dict[ElementNames, Any], self.arguments)
        rename_status = True
        for element, replacement_or_pattern in arguments.items():
            replacement, replacement_status = self._get_replaced_element_value(
                file_record, element, replacement_or_pattern
            )
            file_record.destination_file[element] = replacement
            rename_status = rename_status and replacement_status

        if not rename_status:
            # if we got a rename_error above, we skip the next steps
            return file_record.add_trigger("parsing_error")
        return file_record.add_trigger("parsing_success")

    def register(self, file_record: "FileRecord") -> "FileRecord":
        file_record.ready_to_be_registered = True
        return file_record

    def delete(
        self, file_record: "FileRecord", file_record_list: "FileRecordList"
    ) -> "FileRecord":
        file_record.ready_to_delete = True
        return file_record

    def abort(self, file_record: "FileRecord") -> "FileRecord":
        file_record.abort = True
        return file_record

    def ignore(self, file_record: "FileRecord") -> "FileRecord":
        file_record.ready_to_delete = False
        file_record.ready_to_rename = False
        file_record.ready_to_be_registered = False
        return file_record


class FileValidator(Validator):
    class Meta:
        encapsulation_list_class = FileRecordList
        action_class = FileAction
        trigger_class = FileTrigger

    def evaluation_stage(self, file_record_list: "FileRecordList"):
        # fist, run evaluation, and actions that work on file rename parsing.

        self.evaluate(file_record_list)
        file_record_list.add_invalid_file_name_trigger(self)
        self.apply(
            file_record_list,
            [
                "match",
                "parsing_error",
                "parsing_match_error",
            ],
        )
        file_record_list.set_final_path()

        # once the final path for each file is set, we can proceed to check for conflicts (in )
        self.apply(file_record_list, ["parsing_success"])
        self.apply(
            file_record_list,
            ["rename_allowed", "rename_conflicts", "rename_unchanged"],
        )

        # then we deal with cases raised after conflicts check.
        # TODO finish here
        return file_record_list

    def execute_stage(self, file_record_list: "FileRecordList"):
        if any(record.abort for record in file_record_list if record.abort):
            raise ValueError(
                "Cannot execute actions, as some evaluated items are erroring."
            )

        iter(
            record.rename()
            for record in file_record_list
            if record.ready_to_rename
        )
        iter(
            record.delete()
            for record in file_record_list
            if record.ready_to_delete
        )
        files_to_register = [
            record.final_path
            for record in file_record_list
            if record.ready_to_be_registered and record.final_path is not None
        ]
        iter(files_to_register)
        connector = Connector()
        connector.remote.rest("dataset", "create", data={})

    def cleanup():
        # just cleaning up empty folders after renaming.
        # could be improved to do recursive search.
        # this implementation may miss nested empty folders

        session_path = file_records[0].source_file.session_path
        folders_list = find_files(
            session_path, relative=True, levels=-1, get="folders"
        )
        for folder in folders_list:
            if str(folder) in self.cleanup_folders:
                _folder_path = session_path / folder
                files_in_dir = find_files(
                    _folder_path, relative=True, levels=-1, get="files"
                )
                if len(files_in_dir):
                    continue  # folder is not empty, we cannot clean it up
                shutil.rmtree(_folder_path, ignore_errors=True)
