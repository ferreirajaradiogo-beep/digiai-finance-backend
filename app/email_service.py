import smtplib
from email.message import EmailMessage

from .config import get_settings


def send_reset_code(email_to: str, code: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from:
        return False

    message = EmailMessage()
    message["Subject"] = "Codigo de recuperacao - NotaFacil"
    message["From"] = settings.smtp_from
    message["To"] = email_to
    message.set_content(
        f"Seu codigo de recuperacao do NotaFacil e: {code}\n\n"
        "Se voce nao pediu isso, ignore este e-mail."
    )

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    return True
