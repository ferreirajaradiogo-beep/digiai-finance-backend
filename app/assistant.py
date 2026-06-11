from __future__ import annotations

import calendar
from collections import Counter
from datetime import date
import logging
import re
import time

import httpx
from sqlalchemy.orm import Session

from .models import Account, Category, Transaction, User


DEFAULT_SUGGESTIONS = [
    "Como sincronizar meu app com o site?",
    "Resumo do meu mes",
    "Qual categoria mais pesa?",
    "Lance 4 parcelas de 180 reais",
    "Crie uma despesa chamada internet de 94,90 ate dezembro todo dia 3",
]

logger = logging.getLogger("notafacil.assistant")
ACTION_STOPWORDS = {
    "altere",
    "altere",
    "mude",
    "mudanca",
    "mudar",
    "data",
    "datas",
    "pagamento",
    "pagamentos",
    "para",
    "pro",
    "pros",
    "nos",
    "nas",
    "dia",
    "dias",
    "conta",
    "contas",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "o",
    "a",
    "os",
    "as",
    "e",
}

MONTH_NAME_TO_NUMBER = {
    "janeiro": 1,
    "jan": 1,
    "fevereiro": 2,
    "fev": 2,
    "marco": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "maio": 5,
    "mai": 5,
    "junho": 6,
    "jun": 6,
    "julho": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "setembro": 9,
    "set": 9,
    "outubro": 10,
    "out": 10,
    "novembro": 11,
    "nov": 11,
    "dezembro": 12,
    "dez": 12,
}


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _normalize_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "á": "a",
        "à": "a",
        "ã": "a",
        "â": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
        "ç": "c",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _safe_date(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _add_months(base_date: date, months: int, day: int | None = None) -> date:
    month_index = (base_date.month - 1) + months
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    return _safe_date(year, month, day or base_date.day)


def _format_money(value: float, currency: str = "BRL") -> str:
    symbol = "R$" if currency == "BRL" else currency
    formatted = f"{float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{symbol} {formatted}"


def _title_description(value: str) -> str:
    words = str(value or "").strip().split()
    if not words:
        return ""
    lower_words = {"de", "da", "do", "das", "dos", "e"}
    titled: list[str] = []
    for index, word in enumerate(words):
        normalized = word.lower()
        titled.append(normalized if index > 0 and normalized in lower_words else normalized.capitalize())
    return " ".join(titled)


def _parse_money_token(value: str) -> float | None:
    token = str(value or "").strip()
    if not token:
        return None
    if "," in token:
        token = token.replace(".", "").replace(",", ".")
    try:
        amount = round(float(token), 2)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _extract_amount(question: str) -> float | None:
    normalized = _normalize_text(question)
    money_pattern = r"(\d+(?:[.,]\d{1,2})?)"
    preferred_patterns = [
        rf"r\$\s*{money_pattern}",
        rf"(?:parcelas?\s+de|parcela\s+de|cada\s+parcela\s+de|cada\s+uma\s+de)\s*{money_pattern}",
        rf"(?:valor\s+de|no\s+valor\s+de|por|de)\s*{money_pattern}",
        rf"{money_pattern}\s*(?:reais|real)",
    ]
    for pattern in preferred_patterns:
        match = re.search(pattern, normalized)
        if match:
            amount = _parse_money_token(match.group(1))
            if amount:
                return amount
    decimal_match = re.search(r"\b(\d+[.,]\d{1,2})\b", normalized)
    if decimal_match:
        return _parse_money_token(decimal_match.group(1))
    integer_matches = re.findall(r"(?<!dia\s)(?<!parcelas\s)(?<!meses\s)\b(\d{2,6})\b", normalized)
    for token in integer_matches:
        amount = _parse_money_token(token)
        if amount and amount >= 10:
            return amount
    return None


def _extract_installment_count(question: str) -> int | None:
    normalized = _normalize_text(question)
    match = re.search(r"(\d+)\s+parcelas?", normalized)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:em|para)\s+(\d+)\s+(?:vezes|meses)", normalized)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:por|durante)\s+(\d+)\s+mes(?:es)?", normalized)
    if match:
        return int(match.group(1))
    return None


def _extract_day_of_month(question: str) -> int | None:
    normalized = _normalize_text(question)
    match = re.search(r"(?:dia|vencimento|pagamento|vencendo todo dia|vence todo dia|vencer todo dia|todo dia)\s+(?:para\s+)?(\d{1,2})", normalized)
    if not match:
        return None
    day = int(match.group(1))
    if 1 <= day <= 31:
        return day
    return None


def _extract_end_month(question: str) -> int | None:
    normalized = _normalize_text(question)
    for month_name, month_number in MONTH_NAME_TO_NUMBER.items():
        if re.search(rf"(?:ate|até|a|para|termina em|terminando em|fim em)\s+{month_name}\b", normalized):
            return month_number
    return None


def _extract_start_month(question: str) -> int | None:
    normalized = _normalize_text(question)
    for month_name, month_number in MONTH_NAME_TO_NUMBER.items():
        if re.search(rf"(?:de|a partir de|a partir do mes de|a partir do mês de|comece em|comeca em|começa em|inicie em|inicia em|em)\s+{month_name}\b", normalized):
            return month_number
    return None


def _extract_start_date(question: str, day: int) -> date:
    normalized = _normalize_text(question)
    today = date.today()
    if any(term in normalized for term in ["mes que vem", "mês que vem", "proximo mes", "próximo mês"]):
        return _add_months(today, 1, day)

    start_month = _extract_start_month(question)
    if start_month:
        start_year = today.year if start_month >= today.month else today.year + 1
        return _safe_date(start_year, start_month, day)

    return _safe_date(today.year, today.month, day)


def _extract_description_base(question: str) -> str:
    normalized = _normalize_text(question)
    match = re.search(
        r"(?:com\s+o\s+nome\s+de|com\s+o\s+nome|nome|chamada|chamado|descricao|descrição)\s+[\"']?([a-z0-9 ]{3,60}?)[\"']?(?:\s+vencendo|\s+vence|\s+no dia|\s+dia|\s+no valor|\s+valor|\s+de\s+\d|\s+a partir|\s+ate|\s+mensal|\s+todo|\s+por\s+\d|$)",
        normalized,
    )
    if match:
        candidate = match.group(1).strip()
        if candidate:
            return _title_description(candidate)

    known_descriptions = {
        "agua": "Agua",
        "internet": "Internet",
        "aluguel": "Aluguel",
        "cartao": "Cartao",
        "mercado": "Mercado",
        "energia": "Energia",
        "luz": "Luz",
        "telefone": "Telefone",
    }
    for key, label in known_descriptions.items():
        if key in normalized:
            return label

    match = re.search(r"(?:chamada|chamado|descricao|descrição)\s+([a-z0-9 ]{3,40}?)(?:\s+de\s+\d|\s+no valor|\s+valor|\s+a partir|\s+ate|\s+vencendo|\s+por\s+\d|$)", normalized)
    if match:
        return _title_description(match.group(1).strip())
    match = re.search(
        r"(?:lance|lancar|crie|criar|cadastre|cadastrar|registre|registrar)\s+(?:uma|um|a|o)?\s*(?:despesa|receita|lancamento|lançamento)?\s*(?:chamada|chamado)?\s*([a-z0-9 ]{3,40}?)(?:\s+(?:de|no valor|valor|por|a partir|ate|vencendo|todo|mensal|recorrente)|$)",
        normalized,
    )
    if match:
        candidate = match.group(1).strip()
        if candidate and candidate not in {"mensal", "recorrente"} and not re.fullmatch(r"\d+[.,]?\d*", candidate):
            return _title_description(candidate)
    return "Parcela planejada"


def _is_recurring_request(question: str) -> bool:
    normalized = _normalize_text(question)
    return (
        any(term in normalized for term in ["recorrente", "todo mes", "todo mês", "mensal", "todo dia", "a partir do mes que vem", "a partir do mês que vem"])
        or _extract_end_month(question) is not None
        or re.search(r"(?:por|durante)\s+\d+\s+mes(?:es)?", normalized) is not None
    )


def _build_recurring_preview(db: Session, user: User, question: str) -> dict | None:
    normalized = _normalize_text(question)
    if not _is_recurring_request(question):
        return None

    amount = _extract_amount(question)
    end_month = _extract_end_month(question)
    duration_months = _extract_installment_count(question)
    if not amount or (not end_month and not duration_months):
        return None

    today = date.today()
    day = _extract_day_of_month(question) or today.day
    start_date = _extract_start_date(question, day)
    if start_date < today and not _extract_start_month(question):
        start_date = _add_months(start_date, 1, day)
    if duration_months:
        count = duration_months
        end_date = _add_months(start_date, count - 1, day)
    else:
        end_year = start_date.year if end_month >= start_date.month else start_date.year + 1
        end_date = _safe_date(end_year, end_month, day)
    if end_date < start_date:
        return None

    count = duration_months or ((end_date.year - start_date.year) * 12) + (end_date.month - start_date.month) + 1
    if count <= 0:
        return None

    account, account_warning = _find_account_and_warning(db, user, question)
    category, category_warning = _find_category_and_warning(db, user, question)
    if not account or not category:
        return None

    description_base = _extract_description_base(question)
    warnings = [item for item in [account_warning, category_warning] if item]
    summary = (
        f"Vou criar {count} despesa(s) mensais de {_format_money(amount)} "
        f"para {description_base}, de {start_date.strftime('%d/%m/%Y')} ate {end_date.strftime('%d/%m/%Y')}, "
        f"na conta {account.name} e categoria {category.name}."
    )
    return {
        "action_type": "create_installments",
        "summary": summary,
        "confirmation_label": "Confirmar recorrencia",
        "warnings": warnings,
        "payload": {
            "count": count,
            "amount": amount,
            "start_date": start_date.isoformat(),
            "day": day,
            "account_id": account.id,
            "category_id": category.id,
            "description_base": description_base,
            "type": "despesa",
        },
    }


def _find_account_and_warning(db: Session, user: User, question: str) -> tuple[Account | None, str | None]:
    accounts = db.query(Account).filter(Account.user_id == user.id).order_by(Account.id).all()
    if not accounts:
        return None, "Nenhuma conta disponivel para executar a acao."

    normalized = _normalize_text(question)
    for account in accounts:
        if _normalize_text(account.name) in normalized:
            return account, None

    return accounts[0], f"Conta escolhida automaticamente: {accounts[0].name}."


def _find_category_and_warning(db: Session, user: User, question: str) -> tuple[Category | None, str | None]:
    categories = db.query(Category).filter(Category.user_id == user.id).order_by(Category.id).all()
    if not categories:
        return None, "Nenhuma categoria disponivel para executar a acao."

    normalized = _normalize_text(question)
    for category in categories:
        if _normalize_text(category.name) in normalized:
            return category, None

    preferred_names = ["cartao", "casa", "mercado"]
    for preferred in preferred_names:
        for category in categories:
            if _normalize_text(category.name) == preferred and preferred in normalized:
                return category, f"Categoria escolhida automaticamente: {category.name}."

    if "cartao" in normalized:
        for category in categories:
            if _normalize_text(category.name) == "cartao":
                return category, f"Categoria escolhida automaticamente: {category.name}."

    return categories[0], f"Categoria escolhida automaticamente: {categories[0].name}."


def _build_installments_preview(db: Session, user: User, question: str) -> dict | None:
    normalized = _normalize_text(question)
    if "parcela" not in normalized:
        return None

    count = _extract_installment_count(question)
    amount = _extract_amount(question)
    if not count or not amount:
        return None

    account, account_warning = _find_account_and_warning(db, user, question)
    category, category_warning = _find_category_and_warning(db, user, question)
    if not account or not category:
        return None

    today = date.today()
    day = _extract_day_of_month(question) or today.day
    start_date = _extract_start_date(question, day)
    end_date = _add_months(start_date, count - 1, day)
    description_base = _extract_description_base(question)

    warnings = [item for item in [account_warning, category_warning] if item]
    summary = (
        f"Vou criar {count} parcelas mensais de {_format_money(amount)} "
        f"de {start_date.strftime('%d/%m/%Y')} ate {end_date.strftime('%d/%m/%Y')}, "
        f"na conta {account.name} e categoria {category.name}."
    )
    return {
        "action_type": "create_installments",
        "summary": summary,
        "confirmation_label": "Confirmar parcelas",
        "warnings": warnings,
        "payload": {
            "count": count,
            "amount": amount,
            "start_date": start_date.isoformat(),
            "day": day,
            "account_id": account.id,
            "category_id": category.id,
            "description_base": description_base,
            "type": "despesa",
        },
    }


def _is_create_transaction_request(question: str) -> bool:
    normalized = _normalize_text(question)
    create_terms = ["lance", "lancar", "crie", "criar", "cadastre", "cadastrar", "registre", "registrar", "pague", "pagar", "agende", "agendar", "coloque", "colocar", "bote", "botar"]
    finance_terms = ["despesa", "receita", "lancamento", "lançamento", "gasto", "compra", "entrada"]
    return any(term in normalized for term in create_terms) and (
        any(term in normalized for term in finance_terms) or _extract_amount(question) is not None
    )


def _build_single_transaction_preview(db: Session, user: User, question: str) -> dict | None:
    if not _is_create_transaction_request(question):
        return None

    amount = _extract_amount(question)
    if not amount:
        return None

    account, account_warning = _find_account_and_warning(db, user, question)
    category, category_warning = _find_category_and_warning(db, user, question)
    if not account or not category:
        return None

    normalized = _normalize_text(question)
    tx_type = "receita" if any(term in normalized for term in ["receita", "entrada", "ganho", "salario", "salário"]) else "despesa"
    signed_amount = amount if tx_type == "receita" else amount
    day = _extract_day_of_month(question) or date.today().day
    start_date = _extract_start_date(question, day)
    description_base = _extract_description_base(question)

    warnings = [item for item in [account_warning, category_warning] if item]
    summary = (
        f"Vou criar 1 {tx_type} de {_format_money(signed_amount)} para {description_base}, "
        f"em {start_date.strftime('%d/%m/%Y')}, na conta {account.name} e categoria {category.name}."
    )
    return {
        "action_type": "create_installments",
        "summary": summary,
        "confirmation_label": "Confirmar lancamento",
        "warnings": warnings,
        "payload": {
            "count": 1,
            "amount": amount,
            "start_date": start_date.isoformat(),
            "day": day,
            "account_id": account.id,
            "category_id": category.id,
            "description_base": description_base,
            "type": tx_type,
        },
    }


def _extract_reschedule_keyword(question: str) -> str | None:
    normalized = _normalize_text(question)
    tokens = [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 3 and token not in ACTION_STOPWORDS]
    for token in tokens:
        if token not in {"token", "api", "site", "app", "plano", "gratis", "pro"}:
            return token
    return None


def _build_reschedule_preview(db: Session, user: User, question: str) -> dict | None:
    normalized = _normalize_text(question)
    if not any(term in normalized for term in ["altere", "mude", "remarque", "adiar", "adiare"]):
        return None

    new_day = _extract_day_of_month(question)
    keyword = _extract_reschedule_keyword(question)
    if not new_day or not keyword:
        return None

    future_transactions = (
        db.query(Transaction, Category.name.label("category_name"))
        .outerjoin(Category, Category.id == Transaction.category_id)
        .filter(Transaction.user_id == user.id, Transaction.type == "despesa", Transaction.date >= date.today())
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )

    matched_ids: list[int] = []
    matched_titles: list[str] = []
    for tx, category_name in future_transactions:
        haystack = _normalize_text(f"{tx.description} {category_name or ''}")
        if keyword in haystack:
            matched_ids.append(tx.id)
            matched_titles.append(tx.description)

    if not matched_ids:
        return None

    count = len(matched_ids)
    title_preview = matched_titles[0] if matched_titles else keyword
    summary = (
        f"Vou alterar {count} lancamento(s) futuro(s) ligado(s) a {title_preview} "
        f"para o dia {new_day}."
    )
    return {
        "action_type": "reschedule_future_transactions",
        "summary": summary,
        "confirmation_label": "Confirmar ajuste",
        "warnings": [],
        "payload": {
            "transaction_ids": matched_ids,
            "new_day": new_day,
            "keyword": keyword,
        },
    }


def _build_action_preview(db: Session, user: User, question: str) -> dict | None:
    normalized = _normalize_text(question)
    if not any(term in normalized for term in ["lance", "crie", "cadastre", "registre", "pague", "agende", "coloque", "bote", "parcela", "altere", "mude", "remarque", "adiar", "recorrente", "todo mes", "todo dia", "mensal", "mes que vem", "ate"]):
        return None
    return (
        _build_installments_preview(db, user, question)
        or _build_recurring_preview(db, user, question)
        or _build_reschedule_preview(db, user, question)
        or _build_single_transaction_preview(db, user, question)
    )


def _build_finance_snapshot(db: Session, user: User) -> dict:
    today = date.today()
    month_transactions = (
        db.query(Transaction, Category.name.label("category_name"))
        .outerjoin(Category, Category.id == Transaction.category_id)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= today.replace(day=1),
            Transaction.date <= today,
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )

    income = 0.0
    expense = 0.0
    category_totals: Counter[str] = Counter()

    for tx, category_name in month_transactions:
        amount = float(tx.value or 0)
        if amount >= 0:
            income += amount
        else:
            expense += abs(amount)
            category_totals[str(category_name or "Sem categoria")] += abs(amount)

    top_category_name, top_category_value = ("Sem despesas", 0.0)
    if category_totals:
        top_category_name, top_category_value = category_totals.most_common(1)[0]
    top_categories = [
        {"name": name, "value": _round_money(value)}
        for name, value in category_totals.most_common(3)
    ]
    monthly_limit = _round_money(user.monthly_limit or 0)
    limit_used_pct = 0
    if monthly_limit > 0:
        limit_used_pct = round((expense / monthly_limit) * 100)

    return {
        "month_label": today.strftime("%m/%Y"),
        "income": _round_money(income),
        "expense": _round_money(expense),
        "balance": _round_money(income - expense),
        "top_category_name": top_category_name,
        "top_category_value": _round_money(top_category_value),
        "top_categories": top_categories,
        "entries_count": len(month_transactions),
        "currency": user.currency or "BRL",
        "monthly_limit": monthly_limit,
        "limit_used_pct": limit_used_pct,
    }


def _detect_mode(question: str) -> str:
    normalized = question.lower()
    if _is_operational_capability_question(normalized) or _is_assistant_identity_question(normalized):
        return "action"
    if any(term in normalized for term in ["sincron", "token", "api", "site", "app", "conecta", "login"]):
        return "account"
    if any(term in normalized for term in ["plano", "pro", "free", "gratis", "pago", "senha"]):
        return "help"
    if any(term in normalized for term in ["gasto", "receita", "saldo", "mercado", "categoria", "mes", "mês", "limite"]):
        return "finance"
    return "help"


def _is_operational_capability_question(question: str) -> bool:
    normalized = _normalize_text(question)
    has_capability_word = any(term in normalized for term in ["pode", "consegue", "faz", "cria", "criar", "lanca", "lancar", "cadastro", "cadastra"])
    has_transaction_word = any(term in normalized for term in ["lancamento", "lancamentos", "despesa", "despesas", "receita", "receitas", "parcela", "parcelas", "recorrente"])
    addressed_assistant = any(term in normalized for term in ["assistente", "voce", "vc", "tu"])
    return has_transaction_word and (has_capability_word or addressed_assistant)


def _is_assistant_identity_question(question: str) -> bool:
    normalized = _normalize_text(question)
    has_assistant_word = any(term in normalized for term in ["assistente", "ia", "bot"])
    has_identity_word = any(term in normalized for term in ["voce", "vc", "tu", "sou", "falo", "falando"])
    return has_assistant_word and has_identity_word


def _looks_like_operational_request(question: str) -> bool:
    normalized = _normalize_text(question)
    action_terms = [
        "lance",
        "lancar",
        "crie",
        "criar",
        "cadastre",
        "cadastrar",
        "registre",
        "registrar",
        "pague",
        "pagar",
        "agende",
        "agendar",
        "coloque",
        "colocar",
        "bote",
        "botar",
        "altere",
        "alterar",
        "mude",
        "mudar",
        "remarque",
        "remarcar",
        "adiar",
    ]
    finance_terms = [
        "lancamento",
        "lancamentos",
        "despesa",
        "despesas",
        "receita",
        "receitas",
        "gasto",
        "gastos",
        "compra",
        "compras",
        "parcela",
        "parcelas",
        "pagamento",
        "pagamentos",
        "vencimento",
        "vencendo",
        "mensal",
        "recorrente",
    ]
    return any(term in normalized for term in action_terms) and any(term in normalized for term in finance_terms)


def _build_operational_capability_answer(user: User) -> str:
    plan_now = "Pro" if str(user.plan or "free").lower() == "pro" else "Gratis"
    gate = (
        "Na sua conta Pro, eu consigo preparar e executar acoes depois da sua confirmacao."
        if plan_now == "Pro"
        else "Na conta Gratis eu explico e analiso; as acoes automaticas ficam no Pro."
    )
    return (
        f"Posso ajudar com lancamentos, sim. {gate} "
        "Hoje eu consigo criar parcelas futuras, criar despesas mensais ate um mes final e remarcar vencimentos futuros. "
        "Exemplos que funcionam: 'Lance 4 parcelas de 180 reais', "
        "'Crie uma despesa chamada internet de 94,90 ate dezembro vencendo todo dia 3' ou "
        "'Altere a data dos pagamentos de agua para dia 12'. "
        "Quando eu entender a acao, vou mostrar uma previa e so executo depois que voce confirmar."
    )


def _build_operational_parse_help(user: User) -> str:
    if str(user.plan or "free").lower() != "pro":
        return (
            "Eu entendi que voce quer uma acao operacional, mas a execucao automatica fica no plano Pro. "
            "Mesmo assim, posso ajudar com leitura financeira, suporte e sincronizacao."
        )
    return (
        "Eu consigo fazer essa acao, mas faltou algum detalhe para montar a previa com seguranca. "
        "Tente escrever com valor e periodo, por exemplo: 'lance mercado 50 reais', "
        "'pague internet de 94,90 por 6 meses vencendo todo dia 5', "
        "'agende aluguel 1200 mensal começando em julho ate dezembro dia 10' "
        "ou 'altere a data dos pagamentos de agua para dia 12'. "
        "Quando eu reconhecer tudo, vou mostrar o card de confirmacao antes de executar."
    )


def _build_local_answer(question: str, snapshot: dict, user: User) -> dict:
    mode = _detect_mode(question)
    normalized = question.lower()

    if mode == "action":
        answer = _build_operational_capability_answer(user)
    elif mode == "account":
        answer = (
            "Para sincronizar, use a mesma conta no app e no site e mantenha a API em "
            "https://notafacil-api.onrender.com. Se aparecer token invalido, saia da conta "
            "e entre novamente. Se ainda faltar dado, abra o app por alguns segundos para ele puxar o servidor."
        )
    elif mode == "help" and any(term in normalized for term in ["plano", "free", "gratis", "pro"]):
        plan_now = "Pro" if user.plan == "pro" else "Gratis"
        answer = (
            f"Sua conta hoje esta no plano {plan_now}. "
            "No Gratis, o app permite 1 conta, ate 10 categorias, ate 20 lancamentos por mes e moeda BRL. "
            "No Pro, esses limites principais deixam de travar seu uso. "
            "Vale subir para o Pro quando voce precisar de mais contas, mais categorias ou quando o limite mensal de lancamentos comecar a atrapalhar."
        )
    elif mode == "finance":
        if snapshot["entries_count"] == 0:
            answer = (
                "Ainda nao ha dados suficientes neste mes para uma leitura financeira. "
                "Registre receitas e despesas no app ou no site e eu consigo resumir melhor."
            )
        else:
            limit_copy = ""
            if snapshot["monthly_limit"] > 0:
                limit_copy = f" Voce ja usou cerca de {snapshot['limit_used_pct']}% do limite mensal."
            action_copy = ""
            if snapshot["balance"] < 0:
                action_copy = " Seu saldo do periodo ficou negativo, entao vale rever a categoria mais pesada primeiro."
            elif snapshot["top_category_value"] > 0:
                action_copy = f" Se quiser cortar gasto, comece por {snapshot['top_category_name']}."
            answer = (
                f"No periodo atual ({snapshot['month_label']}), voce registrou {snapshot['entries_count']} lancamentos. "
                f"Receitas: {snapshot['income']:.2f} {snapshot['currency']}. "
                f"Gastos: {snapshot['expense']:.2f} {snapshot['currency']}. "
                f"Saldo: {snapshot['balance']:.2f} {snapshot['currency']}. "
                f"A categoria com maior peso foi {snapshot['top_category_name']} "
                f"({snapshot['top_category_value']:.2f} {snapshot['currency']}).{limit_copy}{action_copy}"
            )
    else:
        answer = (
            "Eu consigo ajudar com sincronizacao, conta, plano, senha, leitura do mes e algumas acoes operacionais. "
            "Pergunte algo direto, como 'qual categoria mais pesa?', 'lance 4 parcelas de 180 reais' "
            "ou 'crie uma despesa chamada internet ate dezembro todo dia 3'."
        )

    return {
        "answer": answer,
        "provider": "local",
        "mode": mode,
        "provider_reason": "local_fallback",
        "suggestions": DEFAULT_SUGGESTIONS,
    }


def _build_model_prompts(question: str, user: User, snapshot: dict) -> tuple[str, str]:
    top_categories_text = ", ".join(
        f"{item['name']} ({item['value']:.2f} {snapshot['currency']})" for item in snapshot["top_categories"]
    ) or "nenhuma categoria com gasto"
    plan_rules = (
        "Plano Free: 1 conta, ate 10 categorias, ate 20 lancamentos por mes e moeda BRL. "
        "Plano Pro: sem esses limites principais."
    )
    system_prompt = (
        "Voce e o assistente do produto DiGiaI Caixa. Responda em portugues do Brasil, "
        "de forma objetiva, amigavel e pratica. Nao invente recursos que o produto nao tem. "
        "Prefira respostas curtas, com 3 a 6 frases ou bullets curtos. "
        "Nao comece com saudacoes longas nem repita o nome do usuario sem necessidade. "
        "Evite abertura como 'Ola' ou 'Oi' quando a pergunta for operacional. "
        "Se a pergunta for sobre dados financeiros, use apenas o contexto fornecido e cite os numeros exatos. "
        "Em perguntas financeiras, nao pare no numero bruto: acrescente uma interpretacao curta e uma acao pratica quando fizer sentido. "
        "Exemplo de estilo: diga qual categoria pesou mais, o que isso sugere sobre o mes e onde vale olhar primeiro para reduzir gasto. "
        "Se a pergunta envolver sincronizacao, login, plano ou senha, explique passos concretos e curtos. "
        "Quando a pergunta for sobre sincronizacao, priorize estes passos reais do produto: usar a mesma conta no app e no site, manter a API em https://notafacil-api.onrender.com, refazer login se aparecer token invalido e abrir o app por alguns segundos para puxar o servidor. "
        "Quando a pergunta for sobre plano, explique as regras reais de Free e Pro sem inventar beneficios. "
        "Em perguntas de plano, diga primeiro em qual plano a conta esta hoje, depois explique objetivamente o limite do Gratis e o que o Pro destrava. "
        "Quando a pergunta envolver criar, editar, parcelar, recorrencia ou vencimento de lancamentos, nunca diga que o produto nao faz isso e nunca invente botoes como 'Despesa Recorrente'. "
        "Nesses casos, explique que o assistente pode preparar uma previa para confirmacao quando o comando tiver valor, data ou periodo, conta e categoria reconheciveis. "
        "Use exemplos reais: 'Lance 4 parcelas de 180 reais', 'Crie uma despesa chamada internet de 94,90 a partir do mes que vem ate dezembro vencendo todo dia 3' e 'Altere a data dos pagamentos de agua para dia 12'. "
        "Se fizer sentido, diga quando vale o upgrade na pratica, mas sem pressionar nem prometer recursos que nao existem. "
        "Se faltar contexto, diga isso claramente em vez de improvisar."
    )
    user_prompt = (
        f"Usuario: {user.username}\n"
        f"Plano: {user.plan}\n"
        f"Regras do produto: {plan_rules}\n"
        f"Idioma: {user.language}\n"
        f"Moeda: {snapshot['currency']}\n"
        f"Mes atual: {snapshot['month_label']}\n"
        f"Lancamentos no periodo: {snapshot['entries_count']}\n"
        f"Receitas: {snapshot['income']}\n"
        f"Gastos: {snapshot['expense']}\n"
        f"Saldo: {snapshot['balance']}\n"
        f"Limite mensal: {snapshot['monthly_limit']}\n"
        f"Uso do limite: {snapshot['limit_used_pct']}%\n"
        f"Categoria com maior gasto: {snapshot['top_category_name']} ({snapshot['top_category_value']})\n\n"
        f"Top categorias: {top_categories_text}\n\n"
        f"Pergunta do usuario: {question}"
    )
    return system_prompt, user_prompt


def _extract_openai_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()

    parts: list[str] = []
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _extract_openrouter_text(payload: dict) -> str:
    for choice in payload.get("choices") or []:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            if parts:
                return "\n\n".join(parts).strip()
    return ""


def _extract_gemini_text(payload: dict) -> str:
    parts: list[str] = []
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for item in content.get("parts") or []:
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _call_openai(system_prompt: str, user_prompt: str, settings) -> tuple[dict | None, str | None]:
    body = {
        "model": settings.assistant_model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ],
    }

    try:
        with httpx.Client(timeout=25.0) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {settings.assistant_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        body_preview = exc.response.text[:400] if exc.response is not None else ""
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        logger.warning("assistant fallback: openai http error status=%s body=%s", status_code, body_preview)
        return None, "provider_http_error"
    except Exception as exc:
        logger.warning("assistant fallback: openai request failed error=%r", exc)
        return None, "provider_request_failed"

    answer = _extract_openai_text(data)
    if not answer:
        logger.warning("assistant fallback: openai returned empty output")
        return None, "provider_empty_output"

    return {"answer": answer, "provider": "openai"}, None


def _call_openrouter(system_prompt: str, user_prompt: str, settings, model: str) -> tuple[dict | None, str | None, int | None, int | None]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.assistant_api_key}",
        "Content-Type": "application/json",
    }
    if settings.assistant_site_url:
        headers["HTTP-Referer"] = settings.assistant_site_url
    if settings.assistant_app_title:
        headers["X-Title"] = settings.assistant_app_title

    try:
        with httpx.Client(timeout=25.0) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        body_preview = exc.response.text[:400] if exc.response is not None else ""
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        retry_after_seconds = None
        try:
            payload = exc.response.json() if exc.response is not None else {}
            metadata = ((payload or {}).get("error") or {}).get("metadata") or {}
            retry_after_raw = metadata.get("retry_after_seconds")
            if retry_after_raw is not None:
                retry_after_seconds = int(float(retry_after_raw))
        except Exception:
            retry_after_seconds = None
        logger.warning("assistant fallback: openrouter model=%s http error status=%s body=%s", model, status_code, body_preview)
        return None, "provider_http_error", int(status_code) if isinstance(status_code, int) else None, retry_after_seconds
    except Exception as exc:
        logger.warning("assistant fallback: openrouter model=%s request failed error=%r", model, exc)
        return None, "provider_request_failed", None, None

    answer = _extract_openrouter_text(data)
    if not answer:
        logger.warning("assistant fallback: openrouter model=%s returned empty output", model)
        return None, "provider_empty_output", None, None

    return {"answer": answer, "provider": "openrouter", "model_used": model}, None, None, None


def _call_gemini(system_prompt: str, user_prompt: str, settings) -> tuple[dict | None, str | None]:
    body = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
    }

    model = str(getattr(settings, "assistant_model", "") or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    try:
        with httpx.Client(timeout=25.0) as client:
            response = client.post(
                url,
                headers={
                    "x-goog-api-key": settings.assistant_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        body_preview = exc.response.text[:400] if exc.response is not None else ""
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        logger.warning("assistant fallback: gemini model=%s http error status=%s body=%s", model, status_code, body_preview)
        return None, "provider_http_error"
    except Exception as exc:
        logger.warning("assistant fallback: gemini model=%s request failed error=%r", model, exc)
        return None, "provider_request_failed"

    answer = _extract_gemini_text(data)
    if not answer:
        logger.warning("assistant fallback: gemini model=%s returned empty output", model)
        return None, "provider_empty_output"

    return {"answer": answer, "provider": "gemini", "model_used": model}, None


def _iter_openrouter_models(settings) -> list[str]:
    models: list[str] = []
    primary = str(getattr(settings, "assistant_model", "") or "").strip()
    if primary:
        models.append(primary)
    for item in getattr(settings, "assistant_fallback_models", []) or []:
        candidate = str(item or "").strip()
        if candidate and candidate not in models:
            models.append(candidate)
    return models


def _build_remote_answer(question: str, user: User, snapshot: dict, settings) -> tuple[dict | None, str | None]:
    provider = str(getattr(settings, "assistant_provider", "local") or "local").strip().lower()
    if provider == "local":
        logger.info("assistant fallback: local provider selected")
        return None, "local_provider_selected"

    if not settings.assistant_api_key:
        logger.info("assistant fallback: missing ASSISTANT_API_KEY")
        return None, "missing_api_key"

    system_prompt, user_prompt = _build_model_prompts(question, user, snapshot)

    if provider == "openai":
        return _call_openai(system_prompt, user_prompt, settings)

    if provider == "gemini":
        return _call_gemini(system_prompt, user_prompt, settings)

    if provider == "openrouter":
        models = _iter_openrouter_models(settings)
        last_reason = "provider_http_error"
        max_retry_after = 0

        for pass_index in range(2):
            for index, model in enumerate(models):
                answer, reason, status_code, retry_after_seconds = _call_openrouter(system_prompt, user_prompt, settings, model)
                if answer:
                    return answer, None
                if reason:
                    last_reason = reason
                if retry_after_seconds:
                    max_retry_after = max(max_retry_after, retry_after_seconds)
                should_try_next = index < len(models) - 1 and status_code in {400, 429, 503}
                if should_try_next:
                    logger.info("assistant retry: openrouter switching model after status=%s from=%s", status_code, model)
                    continue
                break
            if pass_index == 0 and max_retry_after > 0:
                wait_seconds = max(2, min(max_retry_after, 12))
                logger.info("assistant retry: waiting %ss before retrying OpenRouter free pool", wait_seconds)
                time.sleep(wait_seconds)
                max_retry_after = 0
                continue
            break
        return None, last_reason

    logger.warning("assistant fallback: unsupported provider=%s", provider)
    return None, "unsupported_provider"


def execute_assistant_action(action_type: str, payload: dict, db: Session, user: User) -> dict:
    if str(user.plan or "free").lower() != "pro":
        raise ValueError("Esse assistente operacional para criar ou alterar lancamentos fica disponivel no plano Pro.")

    action_type = str(action_type or "").strip().lower()
    payload = payload or {}

    if action_type == "create_installments":
        account = db.get(Account, int(payload.get("account_id") or 0))
        category = db.get(Category, int(payload.get("category_id") or 0))
        if not account or account.user_id != user.id:
            raise ValueError("Conta invalida para criar as parcelas.")
        if not category or category.user_id != user.id:
            raise ValueError("Categoria invalida para criar as parcelas.")

        count = int(payload.get("count") or 0)
        amount = float(payload.get("amount") or 0)
        start_date = date.fromisoformat(str(payload.get("start_date")))
        day = int(payload.get("day") or start_date.day)
        description_base = str(payload.get("description_base") or "Parcela planejada").strip() or "Parcela planejada"
        tx_type = str(payload.get("type") or "despesa").strip().lower()
        if tx_type not in {"despesa", "receita"}:
            tx_type = "despesa"
        if count <= 0 or amount <= 0:
            raise ValueError("Quantidade de parcelas e valor precisam ser maiores que zero.")

        created = 0
        for index in range(count):
            tx_date = _add_months(start_date, index, day)
            signed_value = amount if tx_type == "receita" else round(amount * -1, 2)
            tx = Transaction(
                user_id=user.id,
                account_id=account.id,
                category_id=category.id,
                description=description_base if count == 1 else f"{description_base} {index + 1}/{count}",
                value=signed_value,
                type=tx_type,
                date=tx_date,
            )
            db.add(tx)
            created += 1

        db.commit()
        return {
            "ok": True,
            "summary": f"{created} parcela(s) criada(s) com sucesso em {account.name} / {category.name}.",
            "affected_count": created,
        }

    if action_type == "reschedule_future_transactions":
        new_day = int(payload.get("new_day") or 0)
        ids = [int(item) for item in (payload.get("transaction_ids") or [])]
        if not ids or not (1 <= new_day <= 31):
            raise ValueError("Nao consegui validar os lancamentos ou o novo dia.")

        rows = (
            db.query(Transaction)
            .filter(Transaction.user_id == user.id, Transaction.id.in_(ids))
            .order_by(Transaction.date.asc(), Transaction.id.asc())
            .all()
        )
        updated = 0
        for tx in rows:
            tx.date = _safe_date(tx.date.year, tx.date.month, new_day)
            updated += 1

        db.commit()
        return {
            "ok": True,
            "summary": f"{updated} lancamento(s) futuro(s) remarcado(s) para o dia {new_day}.",
            "affected_count": updated,
        }

    raise ValueError("Acao do assistente ainda nao suportada.")


def build_assistant_reply(question: str, db: Session, user: User, settings) -> dict:
    normalized_question = str(question or "").strip()
    if not normalized_question:
        return {
            "answer": "Escreva uma pergunta sobre sincronizacao, plano, senha ou sobre seus gastos do mes.",
            "provider": "local",
            "mode": "help",
            "provider_reason": "empty_question",
            "suggestions": DEFAULT_SUGGESTIONS,
        }

    action_preview = _build_action_preview(db, user, normalized_question)
    if action_preview:
        if str(user.plan or "free").lower() != "pro":
            return {
                "answer": "Esse comando transacional do assistente fica disponivel no plano Pro. Posso continuar ajudando com suporte, leitura financeira e sincronizacao.",
                "provider": "local",
                "mode": "help",
                "provider_reason": "plan_upgrade_required",
                "suggestions": DEFAULT_SUGGESTIONS,
            }
        return {
            "answer": f"{action_preview['summary']} Confirme abaixo para executar.",
            "provider": "local",
            "mode": "action",
            "provider_reason": "action_preview_ready",
            "suggestions": DEFAULT_SUGGESTIONS,
            "action_preview": action_preview,
        }

    snapshot = _build_finance_snapshot(db, user)
    if _is_operational_capability_question(normalized_question) or _is_assistant_identity_question(normalized_question):
        local_answer = _build_local_answer(normalized_question, snapshot, user)
        local_answer["provider_reason"] = "operational_capability"
        return local_answer
    if _looks_like_operational_request(normalized_question):
        return {
            "answer": _build_operational_parse_help(user),
            "provider": "local",
            "mode": "action",
            "provider_reason": "action_parse_incomplete",
            "suggestions": DEFAULT_SUGGESTIONS,
        }

    remote_answer, provider_error = _build_remote_answer(normalized_question, user, snapshot, settings)
    if remote_answer:
        return {
            **remote_answer,
            "mode": _detect_mode(normalized_question),
            "provider_reason": f"{remote_answer['provider']}_success",
            "suggestions": DEFAULT_SUGGESTIONS,
        }

    local_answer = _build_local_answer(normalized_question, snapshot, user)
    if provider_error:
        local_answer["provider_reason"] = provider_error
    return local_answer
