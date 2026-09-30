"""Send a single campaign batch (max 10 recipients) to unsent buyers."""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from outreach import run_campaign
from config import CAMPAIGN_BATCH_SIZE, DEFAULT_SUBJECT, DEFAULT_BODY

print("=== Sending Email Campaign ===")
print(f"Subject: {DEFAULT_SUBJECT}")
print(f"Maximum recipients per campaign: {CAMPAIGN_BATCH_SIZE}")

summary = run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)

print("\n=== Campaign Complete ===")
print(summary["message"])
