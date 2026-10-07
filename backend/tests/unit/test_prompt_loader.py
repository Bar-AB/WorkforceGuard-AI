from pathlib import Path

import pytest

from app.llm.prompt_loader import PROMPTS_DIR, Prompt, load_prompt


def test_load_prompt_reads_named_version(tmp_path: Path) -> None:
    (tmp_path / "greet.v1.md").write_text("Old text.", encoding="utf-8")
    (tmp_path / "greet.v2.md").write_text("Say hello — kindly.", encoding="utf-8")

    prompt = load_prompt("greet", 2, tmp_path)

    assert prompt == Prompt(name="greet", version=2, text="Say hello — kindly.")


def test_load_prompt_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"greet\.v3\.md"):
        load_prompt("greet", 3, tmp_path)


def test_prompt_label_is_name_dot_version() -> None:
    assert Prompt(name="explain_finding", version=1, text="x").label == "explain_finding.v1"


def test_explain_prompt_file_marks_data_untrusted_and_asks_json() -> None:
    text = load_prompt("explain_finding", 1).text

    assert PROMPTS_DIR.name == "prompts"
    assert "<data>" in text
    assert '{"text"' in text
    assert text.rstrip().endswith("/no_think")


def test_explain_prompt_v2_adds_plain_words_rule_and_drops_no_think() -> None:
    v1 = load_prompt("explain_finding", 1).text
    v2 = load_prompt("explain_finding", 2).text

    assert (
        "Do not mention the rule id or any code names; describe the finding in plain words." in v2
    )
    assert "/no_think" not in v2
    assert "<data>" in v2
    assert '{"text"' in v2
    assert v1.rstrip().endswith("/no_think")
