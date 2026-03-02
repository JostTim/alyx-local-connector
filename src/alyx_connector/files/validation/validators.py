import pkgutil
import json
import re
import shutil
from pathlib import Path
from pandas import Series
from re import Pattern
from typing import cast, Dict, Optional, List, TYPE_CHECKING

from ...files import find_files
from ...web.specifics import get_dataset_types
from .records import FileRecordList, FileRecord
from .typing import RulesConfig, Patterns

if TYPE_CHECKING:
    from .rules import Rule
    from ...connector import Connector


class Validator:
    patterns: Dict[str, Pattern]
    rules: Dict[str, Rule]

    def __init__(
        self,
        rules_path: Optional[str | Path] = None,
        connector: "Optional[Connector]" = None,
        session: Optional[Series] = None,
    ):
        from alyx_connector import Connector

        if session is not None and connector and not rules_path:
            # TODO
            if session["projects"].iloc[0]:
                pass

        if rules_path is None:
            data = pkgutil.get_data(__name__, "./rules.json")
            if data is None:
                raise ValueError("Could not find a rules.json file !")
            rules_config: RulesConfig = json.loads(data.decode("utf-8"))
        else:
            rules_config: RulesConfig = json.load(open(rules_path, "r"))

        self.connector = connector if connector else Connector()

        patterns: Patterns = rules_config.get("re_patterns", {})  # type: ignore
        compiled_paterns = {}
        for pattern_name, pattern in patterns.items():
            compiled_paterns[pattern_name] = re.compile(pattern)
        self.patterns = compiled_paterns

        rules: Dict[str, Rule] = rules_config["rules"]  # type: ignore

        self.rules = {
            rule_name: Rule(rule_dict, rule_name, self) for rule_name, rule_dict in rules.items()
        }

        self.excluded_folders = rules_config.get("excluded_folders", [])

        self.excluded_filenames = rules_config.get("excluded_filenames", [])

        self.cleanup_folders = rules_config.get("cleanup_folders", [])

        self.dataset_types = get_dataset_types(self.connector)
        if len(self.dataset_types) == 0:
            raise ValueError(
                "dataset_types is empty. Cannot register any dataset if no dataset type exists. Maybe one's connector"
                " has a problem ?"
            )

    def registration_pipeline(self, session):
        file_records = self.evaluate_session(session)
        if not self.is_applicable(file_records):
            print("Some conflicts are present, cannot continue.")
            return file_records
        selected_records = self.apply_to_files(file_records)
        records_groups = self.apply_to_alyx(selected_records, session)
        return records_groups

    def evaluate_session(self, session: Series | str) -> FileRecordList:

        if isinstance(session, Series):
            search_folder = Path(session["path"])
        else:  # session is a string, not a Series
            session = cast(
                Series, self.connector.search(id=session, no_cache=True, details=True)["path"]
            )
            search_folder = Path(session["path"])

        if not Path(search_folder).exists():
            raise ValueError(
                "The session.path must exist and correspond to an existing repository"
            )

        files_list = find_files(search_folder, relative=False, levels=-1, get="files")

        file_records = self.evaluate(files_list)
        file_records.session = session
        return file_records

    def evaluate(self, file_list: List[str] | List[Path]) -> FileRecordList:
        file_records = FileRecordList([FileRecord(Path(file_path)) for file_path in file_list])

        for file_record in file_records:
            skip = False  # If we find that a find is in the excluded folders, we just discard it (not even try to match)
            if file_record.destination_file.collection:
                if any(
                    [
                        collection in self.excluded_folders
                        for collection in file_record.destination_file.collection.parts
                    ]
                ):
                    skip = True
            if file_record.source_path.name in self.excluded_filenames:  # same for filenames
                skip = True
            if skip:
                continue

            for rule in self.rules.values():
                rule.evaluate(file_record)
        self.actions_cascade(file_records)
        return file_records

    def actions_cascade(self, file_records: FileRecordList):
        file_records.actions_cascade(self)
        file_records.finish_cascade(self)
        file_records.check_duplicate_final_paths()
        return file_records

    def is_applicable(self, file_records):
        status = [len(file_record.abort) >= 1 for file_record in file_records]
        return not (any(status))

    def apply_to_files(
        self, file_records: FileRecordList, do_deletes=True, do_renames=True, do_cleanup=True
    ):
        # DELETING and RENAMING

        for file_record in file_records:
            file_record.apply_changes(do_deletes, do_renames)

        # just cleaning up empty folders after renaming.
        # could be improved to do recursive search.
        # this implementation may miss nested empty folders
        if do_cleanup:
            session_path = file_records[0].source_file.session_path
            folders_list = find_files(session_path, relative=True, levels=-1, get="folders")
            for folder in folders_list:
                if str(folder) in self.cleanup_folders:
                    _folder_path = session_path / folder
                    files_in_dir = find_files(_folder_path, relative=True, levels=-1, get="files")
                    if len(files_in_dir):
                        continue  # folder is not empty, we cannot clean it up
                    shutil.rmtree(_folder_path, ignore_errors=True)

        return file_records

    def apply_to_alyx(self, file_records: FileRecordList, session: Optional[Series] = None):

        if session is None:
            session = file_records.session

        if session is None:
            raise ValueError("A session must be provided")

        selected_records: List[FileRecord] = []
        for file_record in file_records:
            if file_record.inclusion_accepted:
                selected_records.append(file_record)

        files_list = [
            file_record.destination_file.fullpath
            if file_record.rename
            else file_record.source_path
            for file_record in selected_records
        ]

        self.connector.register.files(session, files_list)

    def __str__(self):
        spacer = "\n- "
        rules_str = spacer + spacer.join([str(rule) + "\n" for rule in self.rules.values()])
        return f"One-Registrator with configured rules : \n{rules_str}"
