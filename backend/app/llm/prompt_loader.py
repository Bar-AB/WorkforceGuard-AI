from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROMPTS_DIR: Final[Path] = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def label(self) -> str:
        return f"{self.name}.v{self.version}"


def load_prompt(name: str, version: int, directory: Path = PROMPTS_DIR) -> Prompt:
    text = (directory / f"{name}.v{version}.md").read_text(encoding="utf-8")
    return Prompt(name=name, version=version, text=text)
