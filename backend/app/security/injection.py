from app.errors import InvalidInputError


def wrap_untrusted(text: str) -> str:
    if "\x00" in text:
        raise InvalidInputError("Untrusted text must not contain NUL characters.")
    escaped = text.replace("<", "\\u003c")
    return f"<data>\n{escaped}\n</data>"
