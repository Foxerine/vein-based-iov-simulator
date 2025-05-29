from typing import Type, Any, Literal

from sqlalchemy import ClauseElement
from sqlmodel import SQLModel, desc, asc

from config import config
from .table_base import T

class TableViewRequest(SQLModel):
    offset: int | None = 0
    limit: int | None = config.max_allowed_table_view_limit
    desc: bool | None = True
    order: Literal["created_at", "updated_at"] | None = "created_at"

    def __init__(self, **data: Any):
        super().__init__(**data)
        self._clause = None

    def clause(self, cls: Type[T]) -> ClauseElement:
        if self._clause is None:
            if self.order == "created_at":
                if self.desc:
                    self._clause = desc(cls.created_at)
                else:
                    self._clause = asc(cls.created_at)
            else:  # order == "updated_at"
                if self.desc:
                    self._clause = desc(cls.updated_at)
                else:
                    self._clause = asc(cls.updated_at)
        return self._clause
