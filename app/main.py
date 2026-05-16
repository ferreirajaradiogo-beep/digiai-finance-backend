import json
import random
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, extract
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, SessionLocal, engine, get_db
from .email_service import send_reset_code
from .models import Account, Backup, Category, Transaction, User
from .schemas import (
    AccountIn,
    AccountOut,
    BackupIn,
    CategoryIn,
    CategoryOut,
    ForgotPasswordIn,
    LoginIn,
    PlanUpdateIn,
    RegisterIn,
    ResetPasswordIn,
    SettingsIn,
    SyncIn,
    TokenResponse,
    TransactionIn,
    TransactionOut,
)
from .security import create_access_token, get_current_user, hash_password, verify_password

settings = get_settings()
FREE_THEME_KEYS = {"black", "white", "ocean"}


def normalize_plan_value(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"free", "gratis", "gratuito"}:
        return "free"
    if normalized == "pro":
        return "pro"
    raise HTTPException(status_code=400, detail="Plano invalido")

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def init_db():
    Base.metadata.create_all(bind=engine)


def normalize_beta_users_to_free() -> None:
    db = SessionLocal()
    try:
        users = db.query(User).all()
        changed = False
        for user in users:
            if user.plan != "free":
                user.plan = "free"
                changed = True
            if user.currency != "BRL":
                user.currency = "BRL"
                changed = True
            if user.theme_key not in FREE_THEME_KEYS:
                user.theme_key = "black"
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db()
    normalize_beta_users_to_free()


def public_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "plan": user.plan,
        "language": user.language,
        "currency": user.currency,
        "theme_key": user.theme_key,
        "monthly_limit": user.monthly_limit,
        "email_verified": user.email_verified,
    }


def create_default_records(db: Session, user: User) -> None:
    accounts = [Account(user_id=user.id, name="Conta principal", kind="banco", color="#41ead4")]
    if user.plan != "free":
        accounts.extend(
            [
                Account(user_id=user.id, name="Carteira", kind="carteira", color="#d8ff78"),
                Account(user_id=user.id, name="Cartao", kind="cartao", color="#ff8a5b"),
            ]
        )
    categories = [
        Category(user_id=user.id, name="Mercado", color="#41ead4"),
        Category(user_id=user.id, name="Cartao", color="#ff8a5b"),
        Category(user_id=user.id, name="Casa", color="#f9dc5c"),
        Category(user_id=user.id, name="Lazer", color="#c084fc"),
        Category(user_id=user.id, name="Renda", color="#d8ff78"),
    ]
    db.add_all(accounts + categories)


def assert_user_resource(resource, user: User, label: str):
    if not resource or resource.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"{label} nao encontrado")


def enforce_free_limits(db: Session, user: User, date_value=None):
    if user.plan != "free":
        return
    if date_value:
        count = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == user.id,
                extract("month", Transaction.date) == date_value.month,
                extract("year", Transaction.date) == date_value.year,
            )
            .count()
        )
        if count >= 20:
            raise HTTPException(status_code=403, detail="Plano gratis permite 20 lancamentos por mes")


@app.get("/health")
def health():
    return {"ok": True, "app": "NotaFacil API"}


@app.get("/")
def root():
    return {
        "ok": True,
        "app": "NotaFacil API",
        "message": "Backend online. Acesse /docs para testar os endpoints.",
        "account": "A mesma conta pode ser usada no app mobile, site e programa desktop.",
    }


@app.post("/auth/register", response_model=TokenResponse)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    username = data.username.strip().lower()
    email = data.email.strip().lower()
    if db.query(User).filter((User.username == username) | (User.email == email)).first():
        raise HTTPException(status_code=409, detail="Usuario ou e-mail ja cadastrado")

    user = User(username=username, email=email, password_hash=hash_password(data.password), plan="free")
    db.add(user)
    db.flush()
    create_default_records(db, user)
    db.commit()
    db.refresh(user)
    return {"access_token": create_access_token(user), "user": public_user(user)}


@app.post("/auth/login", response_model=TokenResponse)
def login(data: LoginIn, db: Session = Depends(get_db)):
    login_value = data.username.strip().lower()
    user = db.query(User).filter((User.username == login_value) | (User.email == login_value)).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuario ou senha invalidos")
    return {"access_token": create_access_token(user), "user": public_user(user)}


@app.post("/auth/forgot-password")
def forgot_password(data: ForgotPasswordIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.strip().lower()).first()
    if not user:
        return {"ok": True}

    code = str(random.randint(100000, 999999))
    user.reset_code = code
    user.reset_expires_at = datetime.utcnow() + timedelta(minutes=20)
    db.commit()
    sent = send_reset_code(user.email, code)
    response = {"ok": True, "email_sent": sent}
    if settings.debug_reset_code:
        response["debug_code"] = code
    return response


@app.post("/auth/reset-password")
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email.strip().lower()).first()
    if not user or user.reset_code != data.code:
        raise HTTPException(status_code=400, detail="Codigo invalido")
    if not user.reset_expires_at or user.reset_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Codigo expirado")

    user.password_hash = hash_password(data.new_password)
    user.reset_code = None
    user.reset_expires_at = None
    db.commit()
    return {"ok": True}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return public_user(user)


@app.patch("/me/settings")
def update_settings(data: SettingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.plan == "free":
        if data.currency is not None and str(data.currency).strip().upper() != "BRL":
            raise HTTPException(status_code=403, detail="Plano gratis permite apenas BRL")
        if data.theme_key is not None and str(data.theme_key).strip().lower() not in FREE_THEME_KEYS:
            raise HTTPException(status_code=403, detail="Plano gratis permite apenas temas basicos")
    for field in ("language", "currency", "theme_key", "monthly_limit"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return public_user(user)


@app.patch("/me/plan")
def update_plan(data: PlanUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    next_plan = normalize_plan_value(data.plan)
    user.plan = next_plan
    if next_plan == "free":
        user.currency = "BRL"
        if user.theme_key not in FREE_THEME_KEYS:
            user.theme_key = "ocean"
    db.commit()
    db.refresh(user)
    return public_user(user)


@app.get("/accounts", response_model=list[AccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Account).filter(Account.user_id == user.id).order_by(Account.id).all()


@app.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(data: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.plan == "free" and db.query(Account).filter(Account.user_id == user.id).count() >= 1:
        raise HTTPException(status_code=403, detail="Plano gratis permite apenas 1 conta")
    account = Account(user_id=user.id, **data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@app.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    assert_user_resource(account, user, "Conta")
    for key, value in data.model_dump().items():
        setattr(account, key, value)
    db.commit()
    db.refresh(account)
    return account


@app.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    assert_user_resource(account, user, "Conta")
    if db.query(Account).filter(Account.user_id == user.id).count() <= 1:
        raise HTTPException(status_code=400, detail="Mantenha pelo menos uma conta")
    db.delete(account)
    db.commit()
    return {"ok": True}


@app.get("/categories", response_model=list[CategoryOut])
def list_categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Category).filter(Category.user_id == user.id).order_by(Category.id).all()


@app.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(data: CategoryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.plan == "free" and db.query(Category).filter(Category.user_id == user.id).count() >= 10:
        raise HTTPException(status_code=403, detail="Plano gratis permite ate 10 categorias")
    category = Category(user_id=user.id, **data.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@app.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, data: CategoryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    assert_user_resource(category, user, "Categoria")
    for key, value in data.model_dump().items():
        setattr(category, key, value)
    db.commit()
    db.refresh(category)
    return category


@app.delete("/categories/{category_id}")
def delete_category(category_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    assert_user_resource(category, user, "Categoria")
    if db.query(Category).filter(Category.user_id == user.id).count() <= 1:
        raise HTTPException(status_code=400, detail="Mantenha pelo menos uma categoria")
    db.delete(category)
    db.commit()
    return {"ok": True}


@app.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = [Transaction.user_id == user.id]
    if month:
        filters.append(extract("month", Transaction.date) == month)
    if year:
        filters.append(extract("year", Transaction.date) == year)
    return db.query(Transaction).filter(and_(*filters)).order_by(Transaction.date.desc(), Transaction.id.desc()).all()


@app.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(data: TransactionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.get(Account, data.account_id)
    category = db.get(Category, data.category_id)
    assert_user_resource(account, user, "Conta")
    assert_user_resource(category, user, "Categoria")
    enforce_free_limits(db, user, data.date)

    value = abs(data.value)
    if data.type == "despesa":
        value = -value
    transaction = Transaction(user_id=user.id, **data.model_dump(exclude={"value"}), value=value)
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


@app.put("/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, data: TransactionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = db.get(Transaction, transaction_id)
    assert_user_resource(transaction, user, "Lancamento")
    assert_user_resource(db.get(Account, data.account_id), user, "Conta")
    assert_user_resource(db.get(Category, data.category_id), user, "Categoria")
    value = abs(data.value)
    if data.type == "despesa":
        value = -value
    for key, item in data.model_dump(exclude={"value"}).items():
        setattr(transaction, key, item)
    transaction.value = value
    db.commit()
    db.refresh(transaction)
    return transaction


@app.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = db.get(Transaction, transaction_id)
    assert_user_resource(transaction, user, "Lancamento")
    db.delete(transaction)
    db.commit()
    return {"ok": True}


@app.post("/backup")
def save_backup(data: BackupIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    backup = Backup(user_id=user.id, payload=json.dumps(data.payload, ensure_ascii=False))
    db.add(backup)
    db.commit()
    return {"ok": True, "backup_id": backup.id}


@app.post("/sync/push")
def sync_push(data: SyncIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    created = {"accounts": 0, "categories": 0, "transactions": 0}
    for account_data in data.accounts:
        db.add(Account(user_id=user.id, **account_data.model_dump()))
        created["accounts"] += 1
    for category_data in data.categories:
        db.add(Category(user_id=user.id, **category_data.model_dump()))
        created["categories"] += 1
    db.flush()
    for transaction_data in data.transactions:
        db.add(Transaction(user_id=user.id, **transaction_data.model_dump()))
        created["transactions"] += 1
    db.commit()
    return {"ok": True, "created": created}


@app.get("/sync/pull")
def sync_pull(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "user": public_user(user),
        "accounts": [AccountOut.model_validate(item).model_dump() for item in list_accounts(user, db)],
        "categories": [CategoryOut.model_validate(item).model_dump() for item in list_categories(user, db)],
        "transactions": [TransactionOut.model_validate(item).model_dump(mode="json") for item in list_transactions(None, None, user, db)],
    }
