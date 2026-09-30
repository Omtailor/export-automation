import os
from email.message import EmailMessage


def attach_presentation(msg: EmailMessage, filepath: str) -> EmailMessage:
    """
    Attach a PDF presentation to an EmailMessage.

    Args:
        msg: EmailMessage object to attach the file to
        filepath: Path to the PDF file to attach

    Returns:
        EmailMessage: The same EmailMessage object with the attachment added

    Raises:
        FileNotFoundError: If the file does not exist
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Presentation file not found: {filepath}")

    filename = os.path.basename(filepath)

    with open(filepath, "rb") as f:
        file_data = f.read()

    msg.add_attachment(
        file_data, maintype="application", subtype="pdf", filename=filename
    )

    return msg


if __name__ == "__main__":
    # Test attachment handler
    from dotenv import load_dotenv

    load_dotenv()

    test_path = os.getenv("PRESENTATION_PATH", "assets/company_presentation.pdf")
    print(f"Testing attachment handler with: {test_path}")

    try:
        msg = EmailMessage()
        msg = attach_presentation(msg, test_path)
        print(f"SUCCESS: Successfully attached: {test_path}")
        # Check if attachment was added by iterating through parts
        attachment_count = sum(1 for part in msg.iter_attachments())
        print(f"  Attachment count: {attachment_count}")
    except FileNotFoundError as e:
        print(f"FAILED: File not found: {e}")
    except Exception as e:
        print(f"FAILED: Error: {e}")
