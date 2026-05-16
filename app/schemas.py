from datetime import date

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6)


class LoginIn(BaseModel):
    username: str
    password: str


class ForgotPasswordIn(BaseModel):
    email: str


class ResetPasswordIn(BaseModel):
    email: str
    code: str
    new_password: str = Field(min_length=6)


class SettingsIn(BaseModel):
    language: str | None = None
    currency: str | None = None
    theme_key: str | None = None
    monthly_limit: float | None = None


class PlanUpdateIn(BaseModel):
    plan: str = Field(min_length=3, max_length=20)


class PasswordUpdateIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "conta"
    color: str = "#41ead4"


class AccountOut(AccountIn):
    id: int

    model_config = {"from_attributes": True}


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = "#41ead4"


class CategoryOut(CategoryIn):
    id: int

    model_config = {"from_attributes": True}


class TransactionIn(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    value: float
    type: str = Field(pattern="^(receita|despesa)$")
    date: date
    account_id: int
    category_id: int


class TransactionOut(TransactionIn):
    id: int

    model_config = {"from_attributes": True}


class BackupIn(BaseModel):
    payload: dict


class SyncIn(BaseModel):
    accounts: list[AccountIn] = []
    categories: list[CategoryIn] = []
    transactions: list[TransactionIn] = []
