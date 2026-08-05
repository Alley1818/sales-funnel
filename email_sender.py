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
    """Build commercial proposal HTML — professional corporate template.

    Inline CSS only, mobile-responsive (max-width 600px), no emojis.
    Template variables: {company_name}, {industry}.
    """
    safe_name = html.escape(company_name, quote=True)
    safe_industry = html.escape(industry, quote=True)
    return f'''<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;">
<tr><td align="center" style="padding:24px 12px;">

<!-- Container -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">

<!-- Header -->
<tr><td style="background-color:#1a1a2e;padding:32px 28px;">
  <p style="margin:0;font-size:13px;letter-spacing:1.5px;text-transform:uppercase;color:#6b7280;">Technomax</p>
  <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;color:#ffffff;">Коммерческое предложение</h1>
  <p style="margin:6px 0 0;font-size:14px;color:#9ca3af;">Подготовлено для {safe_name}</p>
</td></tr>

<!-- Body -->
<tr><td style="padding:28px;">

  <!-- Greeting -->
  <p style="margin:0 0 16px;font-size:15px;line-height:1.65;color:#1f2937;">Здравствуйте!</p>
  <p style="margin:0 0 24px;font-size:15px;line-height:1.65;color:#374151;">
    Благодарим за интерес к сотрудничеству. Мы подготовили для <strong>{safe_name}</strong>
    персональное предложение по автоматизации процессов в сфере
    <strong>{safe_industry}</strong> с использованием технологий искусственного интеллекта.
  </p>

  <!-- Divider -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="border-top:1px solid #e5e7eb;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr></table>

  <!-- Section: Наши решения -->
  <h2 style="margin:24px 0 12px;font-size:17px;font-weight:700;color:#1a1a2e;">Наши решения</h2>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
    <tr><td style="padding:8px 0;font-size:14px;color:#374151;line-height:1.55;">
      <span style="display:inline-block;width:20px;color:#2563eb;font-weight:700;">1.</span>
      Голосовые AI-боты для автоматического обзвона клиентов и сбора обратной связи
    </td></tr>
    <tr><td style="padding:8px 0;font-size:14px;color:#374151;line-height:1.55;">
      <span style="display:inline-block;width:20px;color:#2563eb;font-weight:700;">2.</span>
      Интеллектуальные чат-боты для WhatsApp и Telegram с поддержкой диалогов
    </td></tr>
    <tr><td style="padding:8px 0;font-size:14px;color:#374151;line-height:1.55;">
      <span style="display:inline-block;width:20px;color:#2563eb;font-weight:700;">3.</span>
      Полная интеграция с CRM и автоматическая квалификация лидов
    </td></tr>
    <tr><td style="padding:8px 0;font-size:14px;color:#374151;line-height:1.55;">
      <span style="display:inline-block;width:20px;color:#2563eb;font-weight:700;">4.</span>
      Автоматизация цикла продаж и напоминаний для повторных продаж
    </td></tr>
    <tr><td style="padding:8px 0;font-size:14px;color:#374151;line-height:1.55;">
      <span style="display:inline-block;width:20px;color:#2563eb;font-weight:700;">5.</span>
      Аналитика эффективности и отчёты по воронке в реальном времени
    </td></tr>
  </table>

  <!-- Divider -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="border-top:1px solid #e5e7eb;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr></table>

  <!-- Section: Почему Technomax -->
  <h2 style="margin:24px 0 12px;font-size:17px;font-weight:700;color:#1a1a2e;">Почему Technomax</h2>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
    <tr><td style="padding:10px 14px;font-size:14px;color:#374151;line-height:1.55;border-left:3px solid #2563eb;margin-bottom:8px;">
      <strong>Экспертиза в {safe_industry}</strong> -- более 50 успешных внедрений в вашей отрасли
    </td></tr>
    <tr><td style="height:8px;font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:10px 14px;font-size:14px;color:#374151;line-height:1.55;border-left:3px solid #2563eb;">
      <strong>Быстрое внедрение</strong> -- от 3 рабочих дней до запуска пилотного проекта
    </td></tr>
    <tr><td style="height:8px;font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:10px 14px;font-size:14px;color:#374151;line-height:1.55;border-left:3px solid #2563eb;">
      <strong>Поддержка 24/7</strong> -- персональный менеджер и техническая поддержка
    </td></tr>
    <tr><td style="height:8px;font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:10px 14px;font-size:14px;color:#374151;line-height:1.55;border-left:3px solid #2563eb;">
      <strong>Гибкие тарифы</strong> -- оплата за результат, без скрытых комиссий
    </td></tr>
  </table>

  <!-- Divider -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="border-top:1px solid #e5e7eb;height:1px;font-size:0;line-height:0;">&nbsp;</td></tr></table>

  <!-- Section: Следующие шаги -->
  <h2 style="margin:24px 0 12px;font-size:17px;font-weight:700;color:#1a1a2e;">Следующие шаги</h2>
  <p style="margin:0 0 20px;font-size:14px;line-height:1.65;color:#374151;">
    Мы готовы провести бесплатную демонстрацию решения, адаптированного под ваш бизнес.
    Ответьте на это письмо или свяжитесь с нами по указанным ниже контактам, чтобы
    назначить удобное время.
  </p>
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
    <tr>
      <td style="background-color:#2563eb;border-radius:6px;">
        <a href="https://technomax.pro" style="display:inline-block;padding:12px 28px;font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;">Записаться на демо</a>
      </td>
    </tr>
  </table>

  <!-- Signature -->
  <div style="margin-top:32px;padding-top:20px;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:14px;font-weight:600;color:#1a1a2e;">С уважением,</p>
    <p style="margin:4px 0 0;font-size:14px;color:#6b7280;">Команда Technomax</p>
  </div>
</td></tr>

<!-- Footer -->
<tr><td style="background-color:#1a1a2e;padding:20px 28px;">
  <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.6;">
    Technomax -- AI-решения для бизнеса<br>
    Web: <a href="https://technomax.pro" style="color:#60a5fa;text-decoration:none;">technomax.pro</a>
    &nbsp;|&nbsp; Email: <a href="mailto:info@technomax.pro" style="color:#60a5fa;text-decoration:none;">info@technomax.pro</a>
  </p>
  <p style="margin:8px 0 0;font-size:11px;color:#6b7280;">
    Вы получили это письмо, так как оставили заявку на нашем сайте. Если вы не хотите
    получать рассылки, просто ответьте "Отписаться".
  </p>
</td></tr>

</table>
<!-- /Container -->

</td></tr>
</table>
</body>
</html>'''
