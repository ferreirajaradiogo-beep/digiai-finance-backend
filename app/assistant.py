from __future__ import annotations

from collections import Counter
from datetime import date
import logging
import time

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
            "Para sincronizar, use a mesma conta no app e no site e mantenha a API em "
            "https://notafacil-api.onrender.com. Se aparecer token invalido, saia da conta "
            "e entre novamente. Se ainda faltar dado, abra o app por alguns segundos para ele puxar o servidor."
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
            "Eu consigo ajudar com sincronizacao, conta, plano, senha e leitura do seu mes. "
            "Pergunte algo direto, como 'como sincronizar meu app?', 'qual categoria mais pesa?' "
            "ou 'o que muda do Free para o Pro?'."
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
        "Se a pergunta for sobre dados financeiros, use apenas o contexto fornecido e cite os numeros exatos. "
        "Se a pergunta envolver sincronizacao, login, plano ou senha, explique passos concretos e curtos. "
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
