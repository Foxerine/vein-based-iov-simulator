from sqlmodel import SQLModel


class TokenResponse(SQLModel):
    access_token: str
    token_type: str

class TokenData(SQLModel):
    username: str | None = None
