from dataclasses import dataclass, field
from pathlib import Path
from pandas import Series, DataFrame
from ...files.parsing.files import File


@dataclass
class ActionRequests:

    rename = False
    delete = False
    include = False
    abort = False


@dataclass
class Issues:

    path_conflicts = False
    parseable = False
    dataset_type_exists = False

    sources: list[str] = field(default_factory=list)


class FileStatus:

    def __init__(self):
        self.issues = Issues()
        self.requests = ActionRequests()


class FileRecordSeries(Series):

    def __new__(cls, source_path: str | Path):
        data = {}
        data["source_path"] = Path(source_path)
        data["matching_rules"] = []
        data["used_rule"] = None
        return super(FileRecordSeries, cls).__new__(cls, data)


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
