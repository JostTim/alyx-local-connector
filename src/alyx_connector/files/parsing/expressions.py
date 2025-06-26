from enum import EnumMeta as _EnumMeta, Enum as _EnumParent, _EnumDict
from re import compile as compile_expression, Pattern
from pathlib import Path

from typing import overload, Any


class _NoAliasEnumMeta(_EnumMeta):
    """NoAliasEnumMeta is a metaclass for creating enumerations that prevent aliasing of enum members.

    This metaclass ensures that each member of the enumeration has a unique value and that no two members
    can share the same value. It also provides a mechanism to handle ambiguous values when retrieving enum members.

    This contraption is necessary because we rely on the name of en Enum to generate the name of it's group when
    we resolve the regexp, and we want to have several groups with the same regexp pattern.

    Attributes:
        _cls_name (str): The name of the class being created.

    Methods:
        __prepare__(metacls, cls, bases):
            Prepares the class dictionary for the enumeration.

        __new__(metacls, cls, bases, classdict):
            Creates a new enumeration class, ensuring that members do not alias each other.

        __call__(cls, value, *args, **kwargs):
            Retrieves the enum member corresponding to the given value,
            raising an error if the value is ambiguous or invalid.
    """

    @classmethod
    def __prepare__(metacls, cls, bases):
        classdict = _EnumDict()
        classdict._cls_name = cls  # type: ignore
        return classdict

    def __new__(metacls, cls, bases, classdict):
        original_members = {key: classdict[key] for key in classdict._member_names}

        # Create a new temporary dict to avoid modifying classdict directly
        temp_classdict = _EnumDict()
        temp_classdict._cls_name = cls  # type: ignore
        for key, value in classdict.items():
            if key in original_members:
                temp_classdict[key] = (key, original_members[key])  # wrap to avoid aliasing
            else:
                temp_classdict[key] = value

        enum_class = super().__new__(metacls, cls, bases, temp_classdict)

        # Restore original values
        for member in enum_class:
            member._value_ = original_members[member.name]  # type: ignore

        # Rebuild _value2member_map_ to allow duplicates
        enum_class._value2member_map_ = {}
        for member in enum_class:
            enum_class._value2member_map_.setdefault(member.value, []).append(member)  # type: ignore

        return enum_class

    def __call__(cls, value, *args, **kwargs):
        members = cls._value2member_map_.get(value, [])
        if members:
            if len(members) == 1:  # type: ignore
                return members[0]  # type: ignore
            raise ValueError(f"Ambiguous value {value!r} matches multiple members: {members}")
        raise ValueError(f"{value!r} is not a valid {cls.__name__}")


class Enum(_EnumParent, metaclass=_NoAliasEnumMeta):
    pass


class Resolver:

    bracket_group = compile_expression(r"{{(.*?)}}")

    @classmethod
    def to_string(cls, variable: str | Enum) -> str:
        value = cls.named(variable) if isinstance(variable, Enum) else variable

        if not isinstance(value, str):
            raise ValueError(
                f"Something weird happened with resolution of {variable} into {value} of type {type(value)}"
            )

        return value

    @overload
    @classmethod
    def parse(cls, expression: str | Enum) -> Pattern: ...

    @overload
    @classmethod
    def parse(cls, expression: str | Enum, compile=False) -> str: ...

    @overload
    @classmethod
    def parse(cls, expression: str | Enum, compile=True) -> Pattern: ...

    @classmethod
    def parse(cls, expression: str | Enum, compile: bool = True) -> str | Pattern:

        expression = cls.to_string(expression)

        results = cls.bracket_group.findall(expression)
        if not results:
            return expression if not compile else compile_expression(expression)

        evaluated_results = [eval(result) for result in results]

        resolved_results = [cls.parse(cls.to_string(result), compile=False) for result in evaluated_results]

        result_iterator = iter(resolved_results)
        expression = cls.bracket_group.sub(lambda _: next(result_iterator), expression)
        return expression if not compile else compile_expression(expression)

    @classmethod
    def named(cls, enumeration: Enum) -> str:
        if not isinstance(enumeration, (Part, NamedExp)):
            return enumeration.value

        return f"(?P<{enumeration.name}>{enumeration.value})"


class Exp(Enum):
    sep = r"(?:/|\\)"  # Exp.separator
    double_sep = r"(?://|\\\\)"
    relaxed_sep = r"(?:/|\\|_)"

    lab = r"\w+"
    unallowed_path_characters = r"<>:\"|?\*"
    slashes = r"\\/"
    dot = r"\."
    path_character = r"[^{{Exp.slashes}}{{Exp.unallowed_path_characters}}]"
    path_character_no_dot = r"[^{{Exp.dot}}{{Exp.slashes}}{{Exp.unallowed_path_characters}}]"
    path_character_with_slash = r"[^{{Exp.unallowed_path_characters}}]"


class NamedExp(Enum):
    drive = r"(?:^[a-zA-Z]:{{Exp.sep}})|(?:{{Exp.double_sep}}{{Exp.path_character}}+{{Exp.sep}})"


class Part(Enum):
    # Components of a filename :
    object = r"(?:.)?{{Exp.path_character_no_dot}}+"
    attribute = r"{{Exp.path_character_no_dot}}+"
    extra = r"{{Exp.path_character}}+"
    extension = r"(?:\w|-)+"

    # Components of a session collection path
    collection = r"{{Exp.path_character_with_slash}}+?"
    revision = r"{{Exp.path_character}}+"

    # Components of a session UID path
    subject = r"{{Exp.path_character}}+"
    date = r"\d{4}-\d{2}-\d{2}"
    number = r"\d{1,3}"

    # Other components upstream of the session UID path
    root = r"{{NamedExp.drive}}?{{Exp.path_character_with_slash}}*?"


class Component(Enum):

    filename = (
        r"{{Part.object}}"
        r"(?:(?:{{Exp.dot}}{{Part.attribute}})?"
        r"(?:{{Exp.dot}}{{Part.extra}})*{{Exp.dot}}"
        r"{{Part.extension}}?)?$"
    )
    collection_subpath = r"(?:{{Part.collection}}{{Exp.sep}})?(?:#{{Part.revision}}#{{Exp.sep}})?"
    session_name = r"{{Part.subject}}{{Exp.sep}}{{Part.date}}{{Exp.sep}}{{Part.number}}"

    internal_path = r"{{Component.collection_subpath}}{{Component.filename}}"
    session_path = r"{{Part.root}}{{Exp.sep}}{{Component.session_name}}"

    session_folders = r"{{Component.session_name}}{{Exp.sep}}{{Component.collection_subpath}}"
    relative_path = r"{{Component.session_name}}{{Exp.sep}}{{Component.internal_path}}"

    fullpath = r"{{Component.session_path}}{{Exp.sep}}{{Component.internal_path}}"
    session_alias = r"{{Part.subject}}{{Exp.relaxed_sep}}{{Part.date}}{{Exp.relaxed_sep}}{{Part.number}}"


class Expressions(Enum):
    sep = Resolver.parse(Exp.sep)
    drive = Resolver.parse(NamedExp.drive)

    # Components of a filename :
    object = Resolver.parse(Part.object)
    attribute = Resolver.parse(Part.attribute)
    extra = Resolver.parse(Part.extra)
    extension = Resolver.parse(Part.extension)

    # Components of a session collection path
    collection = Resolver.parse(Part.collection)
    revision = Resolver.parse(Part.revision)

    # Components of a session UID path
    subject = Resolver.parse(Part.subject)
    date = Resolver.parse(Part.date)
    number = Resolver.parse(Part.number)

    # Other components upstream of the session UID path
    lab = Resolver.parse(Exp.lab)
    root = Resolver.parse(Part.root)

    # Path components
    filename = Resolver.parse(Component.filename)
    collection_subpath = Resolver.parse(Component.collection_subpath)
    session_name = Resolver.parse(Component.session_name)

    # Path larger parts
    internal_path = Resolver.parse(Component.internal_path)
    session_path = Resolver.parse(Component.session_path)
    session_folders = Resolver.parse(Component.session_folders)
    relative_path = Resolver.parse(Component.relative_path)
    fullpath = Resolver.parse(Component.fullpath)

    # Usefull
    session_alias = Resolver.parse(Component.session_alias)


class Matcher:

    @classmethod
    def resolve_pattern(cls, pattern: Expressions | str):
        if isinstance(pattern, str):
            pattern_enum: Expressions | None = getattr(Expressions, pattern, None)
            if pattern_enum is None:
                raise AttributeError(f"Expressions has no patern defined for the name {pattern}")
        else:
            pattern_enum = pattern
        return pattern_enum

    @classmethod
    def ensure_string(cls, string: Any) -> str:
        if not isinstance(string, str):
            return str(string)
        return string

    @classmethod
    def search(cls, pattern: Expressions | str, string: str | Path) -> dict:
        pattern_enum = cls.resolve_pattern(pattern)
        string = cls.ensure_string(string)

        expression_patern: Pattern = pattern_enum.value
        match = expression_patern.search(string)
        return match.groupdict() if match is not None else {}

    @classmethod
    def match(cls, pattern: Expressions | str, string: str | Path) -> str | None:
        pattern_enum = cls.resolve_pattern(pattern)
        string = cls.ensure_string(string)
        expression_patern: Pattern = pattern_enum.value

        match = expression_patern.search(string)
        if match:
            return match.group()  # we return the whole first group, wich contains the largest pattern found
        return None
