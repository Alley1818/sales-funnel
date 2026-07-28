"""
Email sender for sales funnel — VK Mail (mail.ru) SMTP.
"""
import os
import html
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("email_sender")


@dataclass
class EmailConfig:
    host: str = "smtp.mail.ru"
    port: int = 465
    username: str = ""
    password: str = ""
    from_name: str = "Technomax"
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> "EmailConfig":
        return cls(
            host=os.environ.get("SMTP_HOST", "smtp.mail.ru"),
            port=int(os.environ.get("SMTP_PORT", "465")),
            username=os.environ.get("SMTP_USERNAME", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            from_name=os.environ.get("SMTP_FROM_NAME", "Technomax"),
            use_ssl=os.environ.get("SMTP_USE_SSL", "true").lower() in ("1", "true"),
        )


@dataclass
class EmailResult:
    success: bool
    error: str | None = None


class EmailSender:
    """Send emails via VK Mail SMTP."""

    def __init__(self, config: EmailConfig | None = None):
        self.config = config or EmailConfig.from_env()

    def send(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str = "",
        attachments: list[str] | None = None,
    ) -> EmailResult:
        if not self.config.username or not self.config.password:
            return EmailResult(success=False, error="SMTP credentials not configured")

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = f"{self.config.from_name} <{self.config.username}>"
            msg["To"] = to_email
            msg["Subject"] = subject

            # Body
            alt = MIMEMultipart("alternative")
            if body_text:
                alt.attach(MIMEText(body_text, "plain", "utf-8"))
            alt.attach(MIMEText(body_html, "html", "utf-8"))
            msg.attach(alt)

            # Attachments
            if attachments:
                for filepath in attachments:
                    path = Path(filepath)
                    if not path.exists():
                        continue
                    part = MIMEBase("application", "octet-stream")
                    with open(path, "rb") as f:
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={path.name}")
                    msg.attach(part)

            # Send
            if self.config.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.config.host, self.config.port, context=context, timeout=30) as server:
                    server.login(self.config.username, self.config.password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as server:
                    server.starttls()
                    server.login(self.config.username, self.config.password)
                    server.send_message(msg)

            logger.info("Email sent to %s", to_email)
            return EmailResult(success=True)

        except smtplib.SMTPAuthenticationError as e:
            return EmailResult(success=False, error=f"Auth failed: {e}")
        except Exception as e:
            logger.exception("Email error")
            return EmailResult(success=False, error=str(e))


def build_kp_html(company_name: str, industry: str) -> str:
    """Build commercial proposal HTML."""
    safe_name = html.escape(company_name, quote=True)
    safe_industry = html.escape(industry, quote=True)
    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto;">
    <div style="background: #1a1a2e; color: white; padding: 24px; border-radius: 8px 8px 0 0;">
        <h1 style="margin: 0; font-size: 20px;">Коммерческое предложение</h1>
        <p style="margin: 8px 0 0; opacity: 0.8; font-size: 14px;">Для {safe_name}</p>
    </div>
    <div style="padding: 24px; border: 1px solid #e2e5ea; border-top: none; border-radius: 0 0 8px 8px;">
        <p>Здравствуйте!</p>
        <p>Мы специализируемся на AI-решениях для автоматизации бизнес-процессов
        в сфере <strong>{safe_industry}</strong> и хотели бы предложить сотрудничество.</p>
        <h3 style="margin-top: 20px;">Что мы предлагаем:</h3>
        <ul>
            <li>Голосовые AI-боты для обзвона клиентов</li>
            <li>Чат-боты для WhatsApp и Telegram</li>
            <li>CRM-интеграция и аналитика</li>
            <li>Автоматизация продаж и коллекций</li>
        </ul>
        <h3>Наши преимущества:</h3>
        <ul>
            <li>Опыт работы в вашей отрасли</li>
            <li>Внедрение от 3 дней</li>
            <li>Поддержка 24/7</li>
            <li>Гибкие тарифы</li>
        </ul>
        <p>Будем рады обсудить детали в удобное для вас время.</p>
        <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #e2e5ea;">
            <p style="margin: 0;"><strong>С уважением,</strong></p>
            <p style="margin: 4px 0; color: #666;">Команда Technomax</p>
        </div>
    </div>
    </body>
    </html>
    """
