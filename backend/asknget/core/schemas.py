"""Schemas shared by every router."""

from ninja import Schema


class MessageOut(Schema):
    """Generic success body for actions that return no resource."""

    detail: str


class ErrorOut(Schema):
    """Shape of every 4xx body raised via ninja.errors.HttpError."""

    detail: str


class IdOut(Schema):
    id: str
