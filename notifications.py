import smtplib
from email.message import EmailMessage
from .config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, DEFAULT_FROM_NAME


def send_email(to_email: str, subject: str, html_body: str, text_body: str = None):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f"{DEFAULT_FROM_NAME} <{EMAIL_FROM}>"
    msg['To'] = to_email
    if text_body:
        msg.set_content(text_body)
    else:
        msg.set_content(html_body)
    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
