from enum import EnumMeta as _EnumMeta, Enum as _EnumParent, _EnumDict
from re import compile


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

    bracket_group = compile(r"{{(.*?)}}")

    @classmethod
    def to_string(cls, variable: str | Enum) -> str:
        value = cls.named(variable) if isinstance(variable, Enum) else variable

        if not isinstance(value, str):
            raise ValueError(
                f"Something weird happened with resolution of {variable} into {value} of type {type(value)}"
            )

        return value

    @classmethod
    def parse(cls, expression: str | Enum) -> str:

        expression = cls.to_string(expression)

        results = cls.bracket_group.findall(expression)
        if not results:
            return expression

        evaluated_results = [eval(result) for result in results]

        resolved_results = [cls.parse(cls.to_string(result)) for result in evaluated_results]

        result_iterator = iter(resolved_results)
        return cls.bracket_group.sub(lambda _: next(result_iterator), expression)

    @classmethod
    def named(cls, enumeration: Enum) -> str:
        if not isinstance(enumeration, NamedPattern):
            return enumeration.value

        return f"(?P<{enumeration.name}>{enumeration.value})"


class NamedPattern(Enum):
    # Components of a filename :
    object = r"[^\.\\/<>:\"|?\*]+"
    attribute = r"[^\.\\/<>:\"|?\*]+"
    extra = r"[^\\/<>:\"|?\*]+"
    extension = r"\w+"

    # Components of a session collection path
    collection = r"[^<>:\"|?\*]+?"
    revision = r"[^\\/<>:\"|?\*]+"

    # Components of a session UID path
    subject = r"[^\\/<>:\"|?\*]+"
    date = r"\d{4}-\d{2}-\d{2}"
    number = r"\d{1,3}"

    # Other components upstream of the session UID path
    lab = r"\w+"
    root = r"^[^<>:\"|?\*]+?"


class PathPattern(Enum):
    separator = r"(?:/|\\)"

    filename = (
        r"{{NamedPattern.object}}"
        r"(?:(?:\.{{NamedPattern.attribute}})?"
        r"(?:\.{{NamedPattern.extra}})*\."
        r"{{NamedPattern.extension}}?)?$"
    )


class Expressions(Enum):
    separator = Resolver.parse(PathPattern.separator)

    # Components of a filename :
    object = Resolver.parse(NamedPattern.object)
    attribute = Resolver.parse(NamedPattern.attribute)
    extra = Resolver.parse(NamedPattern.extra)
    extension = Resolver.parse(NamedPattern.extension)

    # Components of a session collection path
    collection = Resolver.parse(NamedPattern.collection)
    revision = Resolver.parse(NamedPattern.revision)

    # Components of a session UID path
    subject = Resolver.parse(NamedPattern.subject)
    date = Resolver.parse(NamedPattern.date)
    number = Resolver.parse(NamedPattern.number)

    # Other components upstream of the session UID path
    lab = Resolver.parse(NamedPattern.lab)
    root = Resolver.parse(NamedPattern.root)

    filename = Resolver.parse(PathPattern.filename)
