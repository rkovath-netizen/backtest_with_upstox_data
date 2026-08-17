import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_trade_email(subject, body, sender_email, sender_password, receiver_email="ramkov199@gmail.com"):
    """Dispatches a real-time email alert for entries and exits."""
    if not sender_email or not sender_password:
        return False
        
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        # Use SSL for Gmail
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email alert: {e}")
        return False
