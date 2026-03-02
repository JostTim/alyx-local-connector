from pathlib import Path
from pandas import Series, DataFrame
from dataclasses import dataclass, field

from typing import cast, List, Optional, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from ...files import File
    from .validators import Validator


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

    executed_actions: list = field(default_factory=list)

    # source file
    @property
    def source_file(self) -> File:
        source_file = getattr(self, "_source_file", None)
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
        if not hasattr(self, "_destination_file"):
            self._destination_file = self.source_file.copy()
        return self._destination_file

    @property
    def final_path(self) -> Path:
        if not hasattr(self, "_final_path"):
            self._final_path = self.destination_file.fullpath if self.rename else self.source_path
        return self._final_path

    @property
    def final_file(self) -> "File":
        if not hasattr(self, "_final_file"):
            self._final_file = self.destination_file if self.rename else self.source_file
        return self._final_file

    @property
    def final_pathstring(self):
        if not hasattr(self, "_final_pathstring"):
            self._final_pathstring = str(self.final_path)
        return self._final_pathstring

    def actions_cascade(self, config: "Validator"):
        for rule in config.rules.values():
            rule.actions_cascade(self)

    def finish_cascade(self, config: "Validator"):
        # change the values of the file record after all other actions have been resolved.
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
        if (
            self.rename
            and self.valid_alf
            and not self.path_conflicts
            and not self.abort
            and not self.delete
        ):
            return True
        return False

    def apply_changes(self, do_deletes=True, do_renames=True):  # apply changes to file_record
        if self.delete and self.rename:
            raise ValueError("Cannot rename AND delete the same entry.")

        if self.rename_accepted and do_renames:
            dest_fullpath = self.destination_file.fullpath
            source_fullpath = self.source_file.fullpath
            if dest_fullpath is None:
                raise ValueError("Destination fullpath cannot be None if renaming is accepted")
            if source_fullpath is None:
                raise ValueError("Source fullpath cannot be None if renaming is accepted")
            file_directory = dest_fullpath.parent
            file_directory.mkdir(parents=True, exist_ok=True)
            source_fullpath.rename(dest_fullpath)

        elif self.delete and do_deletes:
            source_fullpath = self.source_file.fullpath
            if source_fullpath is None:
                raise ValueError("Source fullpath cannot be None if renaming is accepted")
            source_fullpath.unlink()
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
                message_prefix + ", ".join(abort_messages)
                # + " --- Without Abort, would have been "
                # + message
            )
        else:
            message = "Will be " + message

        return str(message)


class FileRecordList:
    def __init__(self, records: List["FileRecord"], session: Optional[Series] = None):
        self.records = records
        self.session = session

    def to_dataframe(self, details=True) -> DataFrame:
        dicts = []

        if details:
            for file_record in self.records:
                dico = file_record.__dict__
                dico["info"] = file_record.info_message
                dicts.append(dico)
        else:
            for file_record in self.records:
                dicts.append(file_record.to_user_dict())

        return DataFrame(dicts)

    def actions_cascade(self, config: "Validator"):

        for record in self.records:
            record.source_file  # verify we can pase the path as a valid file system compatible item
            record.actions_cascade(config)

    def finish_cascade(self, config: "Validator"):
        for record in self.records:
            record.finish_cascade(config)

    def check_duplicate_final_paths(self):
        # checking if destination_file conflicts with a file already existing
        # of other file's destination_file.fullpath.
        paths = Series([fr.final_pathstring for fr in self.records if not fr.delete])
        duplicates = paths[paths.duplicated(keep=False)].unique()

        for file_record in self.records:
            if file_record.final_pathstring in duplicates:
                file_record.abort.append("filepath_conflicts")
                file_record.path_conflicts = True

    def __repr__(self):
        return repr(self.to_dataframe(details=True))

    def _repr_mimebundle_(self, include=None, exclude=None):
        from IPython.core.getipython import get_ipython
        from IPython.core.formatters import DisplayFormatter

        df = self.to_dataframe(details=True)
        ipython = get_ipython()
        if ipython is None or ipython.display_formatter is None:
            return None
        formatter = cast(DisplayFormatter, ipython.display_formatter).format
        return formatter(df, include=include, exclude=exclude)

    # Delegate list methods explicitly
    def __getitem__(self, index: int) -> "FileRecord":
        return self.records[index]

    def __len__(self):
        return len(self.records)

    def __iter__(self) -> Iterator["FileRecord"]:
        return iter(self.records)

    def append(self, item: "FileRecord"):
        self.records.append(item)

    def extend(self, items: List["FileRecord"]):
        self.records.extend(items)

    def stats(self):
        return self.to_dataframe().groupby(["used_rule", "info"]).size()
