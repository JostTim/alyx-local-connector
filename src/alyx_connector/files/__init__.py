from pathlib import Path
import re
from .parsing import File

from typing import List, Literal, Optional, overload


@overload
def find_files(
    input_path: str | Path,
    re_pattern: Optional[re.Pattern] = None,
    relative: bool = False,
    levels: int = -1,
    get: Literal["files", "dirs", "folders", "all"] = "all",
    parts: Literal["all"] = "all",
    sort: bool = True,
) -> List[Path]: ...


@overload
def find_files(
    input_path: str | Path,
    re_pattern: Optional[re.Pattern] = None,
    relative: bool = False,
    levels: int = -1,
    get: Literal["files", "dirs", "folders", "all"] = "all",
    parts: Literal["name"] = "name",
    sort: bool = True,
) -> List[str]: ...


def find_files(
    input_path: str | Path,
    re_pattern: Optional[re.Pattern] = None,
    relative: bool = False,
    levels: int = -1,
    get: Literal["files", "dirs", "folders", "all"] = "all",
    parts: Literal["all", "name"] = "all",
    sort: bool = True,
) -> List[str] | List[Path]:
    """
    Get full path of files from all folders under the ``input_path`` (including itself).
    Can return specific files with optionnal conditions
    Args:
        input_path (str): A valid path to a folder.
            This folder is used as the root to return files found
            (possible condition selection by giving to re_callback a function taking a regexp
            pattern and a string as argument, an returning a boolean).
    Returns:
        list: List of the file fullpaths found under ``input_path`` folder and subfolders.
    """
    # if levels = -1, we get  everything whatever the depth
    # (at least up to 32767 subfolders, but this should be fine...)

    if levels == -1:
        levels = 32767
    current_level = 0
    output_list: List[Path] = []

    if re_pattern is not None:
        re_pattern = re.compile(re_pattern)

    input_path = Path(input_path)

    def _recursive_search(_input_path: Path):
        nonlocal current_level
        for sub_path in _input_path.iterdir():
            # fullpath = os.path.join(_input_path, subdir)
            if sub_path.is_file():
                if (get == "all" or get == "files") and (re_pattern is None or re_pattern.match(str(sub_path))):
                    output_list.append(sub_path.absolute())

            else:
                if (get == "all" or get == "dirs" or get == "folders") and (
                    re_pattern is None or re_pattern.match(str(sub_path))
                ):
                    output_list.append(sub_path.absolute())
                if current_level < levels:
                    current_level += 1
                    _recursive_search(sub_path)
        current_level -= 1

    if input_path.is_file():
        raise ValueError(f"Can only list files in a directory. A file was given : {input_path}")

    _recursive_search(input_path)

    if relative:
        return [file.relative_to(file, input_path) for file in output_list]
    if parts == "name":
        return [file.name for file in output_list]
    return output_list
