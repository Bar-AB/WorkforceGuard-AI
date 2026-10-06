from pydantic import SecretStr
from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError


def parse_secret_database_url(value: SecretStr) -> URL:
    try:
        return make_url(value.get_secret_value())
    except ArgumentError:
        raise ValueError("is not a valid database URL") from None
