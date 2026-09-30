import smtplib
import os
from dotenv import load_dotenv

from config import GMAIL_EMAIL

load_dotenv()


def get_smtp_connection():
    """
    Establish and return an SMTP_SSL connection to Gmail using the
    application password stored in .env for the configured sender account.

    Returns:
        smtplib.SMTP_SSL: Authenticated SMTP connection object

    Raises:
        Exception: If login fails or credentials are missing
    """
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_app_password:
        raise Exception("GMAIL_APP_PASSWORD must be set in .env file")

    try:
        smtp_server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        smtp_server.login(GMAIL_EMAIL, gmail_app_password)
        return smtp_server
    except smtplib.SMTPAuthenticationError:
        raise Exception(
            "Gmail SMTP authentication failed. Please check the app password "
            f"for {GMAIL_EMAIL}. Ensure 2-Step Verification is enabled and "
            "the App Password is correct."
        )
    except smtplib.SMTPConnectError:
        raise Exception(
            "Failed to connect to Gmail SMTP server. "
            "Check your internet connection and ensure smtp.gmail.com is accessible."
        )
    except smtplib.SMTPException as e:
        raise Exception(f"Gmail SMTP error: {str(e)}")
    except Exception as e:
        raise Exception(f"Failed to connect to Gmail SMTP: {str(e)}")


if __name__ == "__main__":
    print("Testing Gmail SMTP connection...")
    try:
        smtp = get_smtp_connection()
        print("SUCCESS: SMTP connection successful")
        smtp.quit()
    except Exception as e:
        print(f"FAILED: SMTP connection failed: {e}")
