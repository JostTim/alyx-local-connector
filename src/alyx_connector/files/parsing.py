from pathlib import Path
from ..utils import Bunch


class ParsedFile(Bunch):

    def to_path(self) -> Path:
        return Path()

    def __str__(self):
        return str(self.to_path())
