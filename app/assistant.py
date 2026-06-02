from __future__ import annotations

from collections import Counter
from datetime import date
import logging

import httpx
from sqlalchemy.orm import Session

from .models import Category, Transaction, User


DEFAULT_SUGGESTIONS = [
    "Como sincronizar meu app com o site?",
    "Resumo do meu mes",
    "Qual categoria mais pesa?",
]

logger = logging.getLogger("notafacil.assistant")


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


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

    return {
        "month_label": today.strftime("%m/%Y"),
        "income": _round_money(income),
        "expense": _round_money(expense),
        "balance": _round_money(income - expense),
        "top_category_name": top_category_name,
        "top_category_value": _round_money(top_category_value),
        "entries_count": len(month_transactions),
        "currency": user.currency or "BRL",
        "monthly_limit": _round_money(user.monthly_limit or 0),
    }


def _detect_mode(question: str) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ["sincron", "token", "api", "site", "app", "conecta", "login"]):
        return "account"
    if any(term in normalized for term in ["plano", "pro", "free", "gratis", "pago", "senha"]):
        return "help"
    if any(term in normalized for term in ["gasto", "receita", "saldo", "mercado", "categoria", "mes", "mês", "limite"]):
        return "finance"
    return "help"


def _build_local_answer(question: str, snapshot: dict, user: User) -> dict:
    mode = _detect_mode(question)

    if mode == "account":
        answer = (
            "Use a mesma conta no app e no site, mantendo a URL da API em "
            "https://notafacil-api.onrender.com. Se aparecer token invalido, "
            "saia da conta e entre novamente para gerar uma sessao nova."
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
                used_pct = round((snapshot["expense"] / snapshot["monthly_limit"]) * 100) if snapshot["monthly_limit"] else 0
                limit_copy = f" Voce ja usou cerca de {used_pct}% do limite mensal."
            answer = (
                f"No periodo atual ({snapshot['month_label']}), voce registrou {snapshot['entries_count']} lancamentos. "
                f"Receitas: {snapshot['income']:.2f} {snapshot['currency']}. "
                f"Gastos: {snapshot['expense']:.2f} {snapshot['currency']}. "
                f"Saldo: {snapshot['balance']:.2f} {snapshot['currency']}. "
                f"A categoria com maior peso foi {snapshot['top_category_name']} "
                f"({snapshot['top_category_value']:.2f} {snapshot['currency']}).{limit_copy}"
            )
    else:
        answer = (
            "Eu consigo ajudar com sincronizacao, conta, plano, senha e leitura do seu mes. "
            "Pergunte algo como 'como sincronizar meu app?', 'qual categoria mais pesa?' "
            "ou 'qual a diferenca entre Free e Pro?'."
        )

    return {
        "answer": answer,
        "provider": "local",
        "mode": mode,
        "provider_reason": "local_fallback",
        "suggestions": DEFAULT_SUGGESTIONS,
    }


def _build_openai_input(question: str, user: User, snapshot: dict) -> tuple[str, str]:
    system_prompt = (
        "Voce e o assistente do produto DiGiaI Caixa. Responda em portugues do Brasil, "
        "de forma objetiva, amigavel e pratica. Nao invente recursos que o produto nao tem. "
        "Se a pergunta for sobre dados financeiros, use apenas o contexto fornecido. "
        "Se a pergunta envolver sincronizacao, login, plano ou senha, explique passos concretos."
    )
    user_prompt = (
        f"Usuario: {user.username}\n"
        f"Plano: {user.plan}\n"
        f"Idioma: {user.language}\n"
        f"Moeda: {snapshot['currency']}\n"
        f"Mes atual: {snapshot['month_label']}\n"
        f"Lancamentos no periodo: {snapshot['entries_count']}\n"
        f"Receitas: {snapshot['income']}\n"
        f"Gastos: {snapshot['expense']}\n"
        f"Saldo: {snapshot['balance']}\n"
        f"Categoria com maior gasto: {snapshot['top_category_name']} ({snapshot['top_category_value']})\n\n"
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


def _build_openai_answer(question: str, user: User, snapshot: dict, settings) -> dict | None:
    if not settings.openai_api_key:
        logger.info("assistant fallback: missing OPENAI_API_KEY")
        return None

    system_prompt, user_prompt = _build_openai_input(question, user, snapshot)
    body = {
        "model": settings.openai_model,
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
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        body_preview = exc.response.text[:400] if exc.response is not None else ""
        logger.warning("assistant fallback: openai http error status=%s body=%s", exc.response.status_code if exc.response is not None else "unknown", body_preview)
        return None
    except Exception as exc:
        logger.warning("assistant fallback: openai request failed error=%r", exc)
        return None

    answer = _extract_openai_text(data)
    if not answer:
        logger.warning("assistant fallback: openai returned empty output")
        return None

    return {
        "answer": answer,
        "provider": "openai",
        "mode": _detect_mode(question),
        "provider_reason": "openai_success",
        "suggestions": DEFAULT_SUGGESTIONS,
    }


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

    snapshot = _build_finance_snapshot(db, user)
    openai_answer = _build_openai_answer(normalized_question, user, snapshot, settings)
    if openai_answer:
        return openai_answer
    local_answer = _build_local_answer(normalized_question, snapshot, user)
    if not settings.openai_api_key:
        local_answer["provider_reason"] = "missing_api_key"
    return local_answer
