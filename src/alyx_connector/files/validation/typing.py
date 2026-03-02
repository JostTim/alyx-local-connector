from typing import Literal, Dict, Union

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

TriggerNames = Literal[
    "match",
    "destination_exists",
    "invalid_alf_format",
    "rename_unchanged",
    "rename_error",
    "rename_successfull",
]


ActionNames = Literal[
    "rename",
    "include",
    "delete",
    "exclude",
    "abort",
]

CheckFullOperation = Literal[
    "exact",
    "contain",
    "match",
    "exact_not",
    "contain_not",
    "match_not",
]
CheckOperation = Literal[
    "exact",
    "contain",
    "match",
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

Rules = Dict[
    Literal[
        "if",
        "on",
        "overrides",
        ActionNames,
    ],
    dict | list,
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

Patterns = Dict[str, str]
