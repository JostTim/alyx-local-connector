from pathlib import Path
from re import split, compile
from uuid import UUID as OriginalUUID
from datetime import datetime
from pandas import Series
from copy import copy

from .expressions import Part, Matcher

from typing import Optional, Any, TypedDict, List, Tuple, Unpack, Union, cast


class ObjectError(ValueError):
    """Error when parsing or retrieving an object related to a file in alyx"""


class ExtraError(ValueError):
    """Error when parsing or retrieving an extra or extras related to a file in alyx"""


class PartsDict(TypedDict, total=False):
    root: Union[str, Path]
    drive: Union[str, Path]
    subject: str
    date: Union[datetime, str]
    number: Union[str, int]
    collection: Union[str, Path]
    revision: Union[str, Path]
    object: str
    attribute: str
    extra: Union[str, List[str], Tuple[str, ...]]
    extension: str


class UUID(OriginalUUID):

    @classmethod
    def from_any(cls, value: str | int | bytes | OriginalUUID) -> "UUID":

        if not isinstance(value, (UUID, str, bytes, int)):
            raise TypeError
        if isinstance(value, OriginalUUID):
            return cls(hex=value.hex)
        if isinstance(value, str):
            return cls(value)
        typename = type(cls).__name__
        try:
            return cls(**{typename: value})  # type: ignore
        except ValueError:
            raise ValueError

    @classmethod
    def is_uuid(cls, value: Any, versions=(4,)) -> bool:
        try:
            uuid = cls.from_any(value)
            return uuid.version in versions
        except Exception as e:
            return False


class File:

    partsnames = Part._member_names_

    _unallowed_chars = ["?", "*", ":", '"', "<", ">"]
    _unallowed_chars_pattern = compile(rf'[{"".join([char for char in _unallowed_chars])}]')

    def _check_unallowed_chars(self, variable: Any):
        if not variable:
            return
        if bool(self._unallowed_chars_pattern.search(str(variable))):
            raise ValueError(f"A path cannot contain any of these characters {self._unallowed_chars}")

    def copy(self) -> "File":
        return copy(self)

    def __new__(cls, path: Optional[str | Path] = None, **kwargs: Unpack[PartsDict]):
        if path is None and not kwargs:
            return super().__new__(cls)
        if path is not None:
            obj = cls.from_path(path, pattern="fullpath")
            return obj
        return cls.from_parts(**kwargs)

    @classmethod
    def from_parts(cls, **kwargs: Unpack[PartsDict]) -> "File":
        obj = cls()
        for partname, partvalue in kwargs.items():
            if partname == "drive":
                continue  # drive is a named group in the regexp, but we don't want to have a setter for it. Read only
            obj._raise_if_not_partname(partname)
            setattr(obj, partname, partvalue)
        return obj

    @classmethod
    def from_path(cls, fullpath: str | Path, pattern="fullpath") -> "File":
        parts = cast(PartsDict, Matcher.search(pattern, fullpath))
        return cls.from_parts(**parts)

    def __setitem__(self, partname: str, value: Path | str | int | None):
        self._raise_if_not_partname(partname)
        setattr(self, partname, value)

    def __getitem__(self, partname: str) -> Path | str | int | None:
        self._raise_if_not_partname(partname)
        return getattr(self, partname)

    def _raise_if_not_partname(self, partname: str):
        if partname not in self.partsnames:
            raise ValueError(f"Argument {partname} not allowed in File. Allowed part names are {self.partsnames}")

    @property
    def root(self) -> Optional[Path]:
        return getattr(self, "_root", None)

    @root.setter
    def root(self, root: Optional[str | Path]):
        if root is None:
            self._root = None
            self._drive = None
            return
        rootpath = Path(root)
        if not rootpath.is_absolute():
            raise ValueError("The root part must be an absolute path")
        drive = Matcher.search("drive", rootpath)
        drive = drive["drive"] if drive else ""
        tested_path = str(rootpath).replace(drive, "") if ":" in drive else str(rootpath)
        self._check_unallowed_chars(tested_path)

        self._drive = Path(drive) if drive else None
        self._root = rootpath

    @property
    def drive(self) -> Optional[Path]:
        return getattr(self, "_drive", None)

    @drive.setter
    def drive(self, _: Any):
        raise ValueError("Drive is a read only filepart")

    @property
    def subject(self) -> Optional[str]:
        return getattr(self, "_subject", None)

    @subject.setter
    def subject(self, subject: Optional[str]):
        self._check_unallowed_chars(subject)
        self._subject = subject if subject is not None else None

    @property
    def date(self) -> Optional[str]:
        return getattr(self, "_date", None)

    @date.setter
    def date(self, date: Optional[datetime | str]):
        if isinstance(date, datetime):
            date = date.strftime("%Y-%m-%d")
        self._check_unallowed_chars(date)
        self._date = date if date else None

    @property
    def number(self) -> Optional[str]:
        return getattr(self, "_number", None)

    @number.setter
    def number(self, number: Optional[str | int]):
        self._check_unallowed_chars(number)
        self._number = str(int(number)).zfill(3) if number else None

    @property
    def collection(self) -> Optional[Path]:
        return getattr(self, "_collection", None)

    @collection.setter
    def collection(self, collection: Optional[str | Path]):
        self._check_unallowed_chars(collection)
        if str(collection).startswith(("/", "\\")):
            collection = str(collection)[1:]
        if str(collection).endswith(("/", "\\")):
            collection = str(collection)[:-1]
        self._collection = Path(collection) if collection else None

    @property
    def revision(self) -> Optional[Path]:
        return getattr(self, "_revision", None)

    @revision.setter
    def revision(self, revision: Optional[str | Path]):
        self._check_unallowed_chars(revision)
        if str(revision).startswith(("/", "\\")):
            revision = str(revision)[1:]
        if str(revision).endswith(("/", "\\")):
            revision = str(revision)[:-1]
        if any([slash in str(revision) for slash in ["/", "\\"]]):
            raise ValueError("A revision must be a single folder. Multiple revision folders are not allowed")
        self._revision = Path(revision) if revision else None

    @property
    def object(self) -> Optional[str]:
        return getattr(self, "_object", None)

    @object.setter
    def object(self, object: Optional[str]):
        self._check_unallowed_chars(object)
        if "." in str(object):
            raise ValueError("attribute must not contain a `.`")
        self._object = object if object else None

    @property
    def attribute(self) -> Optional[str]:
        return getattr(self, "_attribute", None)

    @attribute.setter
    def attribute(self, attribute: Optional[str]):
        self._check_unallowed_chars(attribute)
        if "." in str(attribute):
            raise ValueError("attribute must not contain a `.`")
        self._attribute = attribute if attribute else None

    @property
    def dataset_type(self) -> str:
        if self.object is None:
            raise ValueError("Cannot compute the dataset_type if object is None")
        return f"{self.object}.{self.attribute}" if self.attribute else self.object

    @property
    def is_dataset_type_valid(self) -> bool:
        if self.object is None:
            raise ValueError("Cannot compute the dataset_type if object is None")
        return False if not self.attribute else True

    @property
    def extra(self) -> Optional[str]:
        return getattr(self, "_extra", None)

    @extra.setter
    def extra(self, extra: Optional[str | List[str] | Tuple[str, ...]]):
        if isinstance(extra, (list, tuple)):
            self.extras = extra
            return
        self._check_unallowed_chars(extra)
        self._extra = extra if extra else None

    @property
    def extras(self) -> list[str]:
        return str(self.extra).split(".") if self.extra else []

    @extras.setter
    def extras(self, extras: List[str] | Tuple[str, ...]):
        self.extra = ".".join(extras)

    @property
    def extension(self) -> Optional[str]:
        return getattr(self, "_extension", None)

    @extension.setter
    def extension(self, extension: Optional[str]):
        self._check_unallowed_chars(extension)
        if str(extension).startswith("."):
            extension = str(extension)[1:]
        if "." in str(extension):
            raise ValueError("extension must not contain a `.`")
        self._extension = extension if extension is not None else None

    @property
    def session_name_as_path(self) -> Path:
        return Path(self.session_name)

    @property
    def session_name(self) -> str:
        if self.subject is None or self.date is None or self.number is None:
            raise ValueError(
                "Cannot create a session name from a File that doesn't specify both a subject, date and number"
            )
        return str(Path(self.subject) / self.date / self.number)

    @session_name.setter
    def session_name(self, name: str | Series | OriginalUUID):
        if isinstance(name, OriginalUUID):
            raise NotImplementedError
        if isinstance(name, Series):
            self.subject = name.subject
            self.date = name.date
            self.number = name.number
        else:
            match = Matcher.search("session_alias", name)
            self.subject = match["subject"]
            self.date = match["date"]
            self.number = match["number"]

    @property
    def session_alias(self) -> str:
        if self.subject is None or self.date is None or self.number is None:
            raise ValueError(
                "Cannot create a session alias from a File that doesn't specify both a subject, date and number"
            )
        return "_".join([self.subject, self.date, self.number])

    @property
    def filename(self) -> str:
        if self.object is None:
            raise ObjectError("Cannot resolve a filename if at least the 'object' attribute is not set.")
        if (
            (self.extras and not self.attribute)
            or (self.extras and not self.extension)
            or (self.attribute and not self.extension)
        ):
            raise ExtraError(
                "Cannot create a valid reparseable filename if extras are set but an attribute or sextension is not"
            )
        parts = [part for part in [self.object, self.attribute] + self.extras + [self.extension] if part]
        return ".".join(parts)

    @filename.setter
    def filename(self, filename: str):
        filename_components = Matcher.search("filename", filename)
        if not filename_components:
            raise ValueError(f"{filename} is not a valid file for alyx file system")
        for key, value in filename_components.items():
            setattr(self, key, value)

    @property
    def collection_subpath(self) -> Path:
        revision = f"#{self.revision}#" if self.revision else ""
        collection = self.collection if self.collection else ""
        return Path(collection) / revision

    @property
    def internal_path(self) -> Path:
        return self.collection_subpath / self.filename

    @property
    def relative_path(self) -> Path:
        return self.session_name_as_path / self.internal_path

    @property
    def session_folders(self) -> Path:
        return self.session_name_as_path / self.collection_subpath

    @property
    def session_path(self) -> Path:
        if self.root is None:
            raise ValueError("Cannot compute session path for a file with no root set.")
        return Path(self.root) / self.session_name_as_path

    @property
    def fullpath(self) -> Path:
        return self.session_path / self.internal_path

    def has_session(self) -> bool:
        try:
            return bool(self.session_name)
        except ValueError:
            return False

    def _repr_html_(self):
        try:
            filename = self.fullpath
        except Exception:
            filename = "Invalid fullpath"
        header_text = f"{self.__class__.__name__} : {filename}"
        return (
            f"""
        <div class="table-container">
            <table>
                <thead>
                    <th class="title" colspan="10">{header_text}<button id="toggle-headers-btn">&#x25BC;</button></th>
                </thead>
                <thead class="head">
                    <tr>
                        <th rowspan="3">root</th>
                        <th>subject</th>
                        <th>date</th>
                        <th>number</th>
                        <th>collection</th>
                        <th>revision</th>
                        <th>object</th>
                        <th>attribute</th>
                        <th>extra</th>
                        <th>extension</th>
                    </tr>
                    <tr class="foldable-header">
                        <th colspan="9">relative_path</th>
                    </tr>
                    <tr class="foldable-header">
                        <th colspan="3">session_name</th>
                        <th colspan="2">collection_subpath</th>
                        <th colspan="4">filename</th>
                    </tr>
                    <tr class="foldable-header">
                        <th colspan="4">session_path</th>
                        <th colspan="6">internal_path</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>{self.root or ""}</td>
                        <td>{self.subject or ""}</td>
                        <td>{self.date or ""}</td>
                        <td>{self.number or ""}</td>
                        <td>{self.collection or ""}</td>
                        <td>{self.revision or ""}</td>
                        <td>{self.object or ""}</td>
                        <td>{self.attribute or ""}</td>
                        <td>{self.extra or ""}</td>
                        <td>{self.extension or ""}</td>
                    </tr>
                </tbody>
            </table>
        </div>"""
            + """
        <style>
            .table-container{
                border: 1px solid rgb(173, 173, 173);
                border-radius: 5px;
                overflow: hidden;
                display: inline-block;
            }
            .title{
                text-align: left;
                white-space: pre;
                font-weight: bolder;
                position:relative;
            }
            table {
                font-family: consolas;
                border-collapse: collapse;
            }
            th, td {
                border: 1px solid rgb(160 160 160);
            }
            thead:first-of-type th {
                border-top: none;
            }
            td {
                border-bottom: 0px;
            }
            th:first-of-type, td:first-of-type {
                border-left: none;
            }
            td:last-of-type, th:last-of-type {
                border-right: none;
            }
            thead > tr > th:hover, tbody > tr > td:hover{
                background-color: rgba(111, 110, 160, 0.267);
            }
            #toggle-headers-btn {
                position:absolute;
                right:8px;
                top:4px;
                font-size:0.9em;
            }
            .foldable-header {
                display: none;
                transition: display 0.2s;
            }
            .foldable-header.visible {
                display: table-row;
            }
            #toggle-headers-btn {
                background: none;
                border: none;
                cursor: pointer;
                padding: 0 4px;
                color: #444;
                transition: color 0.2s;
            }
            #toggle-headers-btn:hover {
                color: #222;
            }
        </style>
        <script>
            (function(){
                var btn = document.getElementById('toggle-headers-btn');
                var rows = document.querySelectorAll('.foldable-header');
                var expanded = false;
                function setRows(show) {
                    rows.forEach(function(row){
                        if(show) row.classList.add('visible');
                        else row.classList.remove('visible');
                    });
                    btn.innerHTML = show ? '&#x25B2;' : '&#x25BC;';
                }
                btn.addEventListener('click', function(){
                    expanded = !expanded;
                    setRows(expanded);
                });
                setRows(false);
            })();
        </script>
        """
        )

    def __str__(self):
        return self.fullpath.__str__()

    def __repr__(self):
        return self.__str__()

    def dromedize(self):
        """
        Dromedize object and attribute
        """
        raise NotImplementedError("")

    def to_dict(self) -> dict:
        return dict(
            root=self.root,
            drive=self.drive,
            subject=self.subject,
            date=self.date,
            number=self.number,
            collection=self.collection,
            revision=self.revision,
            object=self.object,
            attribute=self.attribute,
            extra=self.extra,
            extension=self.extension,
        )

    def to_series(self) -> Series:
        return Series(self.to_dict())
