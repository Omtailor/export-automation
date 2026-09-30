# B2B Buyer Discovery & Email Outreach System

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0%2B-black.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-Educational%20%2F%20Demonstration-orange.svg)](LICENSE)

**A comprehensive B2B lead generation and email outreach automation system for Product Zone International**, a wholesale supplier of decorative glassware and home décor products.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Data Management](#data-management)
- [Email Validation](#email-validation)
- [Campaign Management](#campaign-management)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security & Compliance](#security--compliance)

---

## Overview

This system automates the entire B2B buyer discovery and outreach workflow for Product Zone International, specializing in:

- Decorative jars and glass containers
- Votive and tea light holders
- Candlestick holders and candle accessories
- Glass bottles and vases
- Frosted glass cups
- Decorative home hardware

### Core Workflow

1. **Multi-Source Discovery** - Search across 6 working platforms using 30+ buyer-intent queries (Facebook/LinkedIn are stubs requiring API access)
2. **Contact Extraction** - Crawl business websites to extract email addresses
3. **Validation Pipeline** - Multi-layer email validation (syntax, domain, MX, optional SMTP)
4. **Deduplication** - Remove duplicates and previously contacted addresses
5. **Batched Outreach** - Send campaigns in controlled batches (max 10 emails per run)
6. **Logging & Tracking** - Comprehensive send history with success/failure tracking

### Technical Highlights

- **Zero Infrastructure** - CSV-based data persistence, no database required
- **Rate-Limited Sending** - Configurable delays and daily limits
- **Concurrent Protection** - Process locks prevent duplicate sends
- **Graceful Degradation** - Selenium fallback when JavaScript rendering is needed
- **Comprehensive Testing** - 25+ automated smoke tests

---

## Key Features

### Discovery & Extraction

| Feature | Description |
|---------|-------------|
| **Multi-Platform Search** | Google, Alibaba, IndiaMART, Instagram, Yellow Pages, Directories (Facebook/LinkedIn are stubs requiring API access) |
| **30+ Buyer Queries** | Configurable B2B intent queries (importer, distributor, wholesaler, buyer) |
| **Smart Crawling** | Probes contact pages: /contact, /about, /wholesale, /import, /purchasing |
| **Email Extraction** | Handles plain emails, mailto links, and common obfuscation patterns |
| **WHOIS Integration** | Extracts domain registration emails (lower confidence, reviewed separately) |
| **Selenium Fallback** | Optional JavaScript rendering for sites requiring it |

### Validation & Quality

| Feature | Description |
|---------|-------------|
| **Syntax Validation** | RFC-compliant email format checking |
| **Disposable Domain Filter** | Blocks 219 disposable email domains |
| **MX Record Verification** | Ensures domain can receive email |
| **SMTP Verification** | Optional mailbox existence check (three-valued: verified/rejected/unknown) |
| **System Address Blocking** | Filters noreply, abuse, postmaster, etc. |
| **Placeholder Detection** | Rejects test@example.com and similar artificial addresses |

### Outreach & Campaigns

| Feature | Description |
|---------|-------------|
| **Batched Sending** | Configurable batch size (default: 10 emails per run) |
| **Rate Limiting** | Random delays between sends (2-4 seconds default) |
| **Daily Limits** | Configurable daily send cap (default: 100) |
| **Universal Messaging** | Same email template for all recipients with PDF attachment |
| **Failed Retry** | Failed recipients remain in queue for next campaign |
| **Comprehensive Logging** | Every attempt logged with status, timestamp, error details |
| **Dashboard UI** | Flask-based web interface for campaign management |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SEARCH LAYER                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │  Google  │ │ Alibaba  │ │LinkedIn  │ │Facebook  │  ...     │
│  │ (Serper) │ │IndiaMART │ │Directory │ │Instagram │          │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │
└───────┼────────────┼────────────┼────────────┼─────────────────┘
        │            │            │            │
        └────────────┴────────────┴────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  EXTRACTION LAYER                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Website Crawler (requests + BeautifulSoup)              │  │
│  │  • Contact/About/Wholesale/Purchasing pages              │  │
│  │  • Email pattern extraction                              │  │
│  │  • WHOIS domain lookup (optional)                        │  │
│  │  • Selenium fallback for JS-heavy sites                  │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                 VALIDATION LAYER                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Validation Pipeline                                      │  │
│  │  1. Syntax check                                         │  │
│  │  2. Image extension guard                                │  │
│  │  3. Domain length validation                            │  │
│  │  4. Disposable domain filter                             │  │
│  │  5. System address blocking                              │  │
│  │  6. Placeholder detection                                │  │
│  │  7. MX record verification                                │  │
│  │  8. SMTP verification (optional)                         │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                DEDUPLICATION LAYER                               │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  • Case-insensitive email normalization                  │  │
│  │  • Remove duplicates within current run                  │  │
│  │  • Exclude addresses in buyers.csv                       │  │
│  │  • Exclude addresses in sent_log.csv                     │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                  DATA PERSISTENCE                                │
│  ┌──────────────────┐  ┌──────────────────┐                    │
│  │   buyers.csv     │  │  sent_log.csv    │                    │
│  │  (unsent only)   │  │  (all attempts)  │                    │
│  └──────────────────┘  └──────────────────┘                    │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                   OUTREACH LAYER                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Campaign Runner                                          │  │
│  │  • Load next batch (max 10)                               │  │
│  │  • Build universal email + PDF attachment                │  │
│  │  • Send via Gmail SMTP                                    │  │
│  │  • Rate-limited sends                                     │  │
│  │  • Log success/failure                                    │  │
│  │  • Remove successful from buyers.csv                      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- pip package manager
- Gmail account with 2-Step Verification enabled
- Serper API key (for Google Search)
- Google Chrome (optional, for Selenium fallback)

### Installation

```bash
# Clone the repository
git clone https://github.com/Omtailor/export-automation.git
cd export-automation

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root:

```env
# Gmail Configuration
GMAIL_EMAIL=your_gmail_address@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password

# API Keys
SERPER_API_KEY=your_serper_api_key

# Search Settings
MAX_RESULTS_PER_SOURCE=20
MAX_SEARCH_PAGES=3
MAX_PAGES_PER_DOMAIN=10

# Campaign Settings
CAMPAIGN_BATCH_SIZE=10
DAILY_SEND_LIMIT=100
MIN_SEND_DELAY=2
MAX_SEND_DELAY=4

# Company Information
COMPANY_NAME=Product Zone International
PRODUCT_NAME=Decorative Glassware & Home Décor Collection
```

### Gmail Setup

1. Enable 2-Step Verification on your Gmail account
2. Go to Google Account → Security → App Passwords
3. Generate an App Password for this application
4. Add both the Gmail address and App Password to your `.env` file:
   - `GMAIL_EMAIL=your_gmail_address@gmail.com`
   - `GMAIL_APP_PASSWORD=your_gmail_app_password`

### Run the Application

```bash
# Start the Flask dashboard
python app.py

# Access the dashboard at http://127.0.0.1:5000
```

---

## Installation

### System Requirements

- **Operating System**: Windows, macOS, or Linux
- **Python**: 3.10 or higher
- **Memory**: 2GB RAM minimum
- **Disk Space**: 500MB for application and data
- **Network**: Internet connection for search and email sending

### Step-by-Step Installation

1. **Clone the Repository**
   ```bash
   git clone <repository-url>
   cd export-automation
   ```

2. **Create Virtual Environment**
   ```bash
   python -m venv .venv
   ```

3. **Activate Virtual Environment**
   
   **Windows (PowerShell):**
   ```bash
   .venv\Scripts\Activate.ps1
   ```
   
   **Windows (Command Prompt):**
   ```bash
   .venv\Scripts\activate.bat
   ```
   
   **macOS/Linux:**
   ```bash
   source .venv/bin/activate
   ```

4. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure Environment Variables**
   
   Copy the example environment file and customize it:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your credentials.

6. **Prepare Assets**
   
   Place your company presentation PDF at:
   ```
   assets/company_presentation.pdf
   ```

7. **Verify Installation**
   ```bash
   python smoke_test.py
   ```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root with the following variables:

#### Required Variables

```env
# Gmail Authentication (required - must be set in .env)
GMAIL_EMAIL=your_gmail_address@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password

# Search API
SERPER_API_KEY=your_serper_api_key
```

**Note**: `GMAIL_EMAIL` and `GMAIL_APP_PASSWORD` are required and must be configured in `.env`. The application will fail to start if these are not set. These credentials are never hardcoded in the source code.

#### Optional Variables

```env
# Search Configuration
SEARCH_QUERIES=                        # Custom queries (comma-separated)
MAX_RESULTS_PER_SOURCE=20              # Results per search source
MAX_SEARCH_PAGES=3                     # Pages to fetch per query
MAX_PAGES_PER_DOMAIN=10                # Max pages to crawl per website

# Campaign Configuration
CAMPAIGN_BATCH_SIZE=10                 # Emails per campaign run
DAILY_SEND_LIMIT=100                   # Maximum emails per day
MIN_SEND_DELAY=2                       # Minimum delay between sends (seconds)
MAX_SEND_DELAY=4                       # Maximum delay between sends (seconds)
CC_EMAIL=                              # CC recipient for all campaigns

# Email Validation
ENABLE_SMTP_VERIFICATION=false         # Enable SMTP mailbox verification

# Company Information
COMPANY_NAME=Product Zone International
PRODUCT_NAME=Decorative Glassware & Home Décor Collection
PRODUCT_TERM=decorative glassware      # Term for marketplace searches

# Presentation
PRESENTATION_PATH=assets/company_presentation.pdf

# Flask
FLASK_SECRET_KEY=dev-secret-key-change-in-production
```

### Search Queries

The system includes 30+ pre-configured B2B buyer-intent queries targeting:

- Importers, distributors, wholesalers, buyers
- Specific product categories (glassware, home décor, candles, vases)
- Geographic markets (default: USA)
- Business types (boutiques, gift shops, event décor companies)

Customize queries via the `SEARCH_QUERIES` environment variable or modify `config.py`.

### Sender Configuration

Configure the sender email address via the `GMAIL_EMAIL` environment variable in `.env`. The system requires a Gmail account with 2-Step Verification enabled and an App Password for SMTP authentication.

**Important**: Gmail credentials are never hardcoded in the source code. Both `GMAIL_EMAIL` and `GMAIL_APP_PASSWORD` must be configured in the `.env` file only.

---

## Usage

### Web Dashboard

#### Start the Dashboard

```bash
python app.py
```

Access the dashboard at: http://127.0.0.1:5000

#### Dashboard Features

1. **Find New Leads**
   - Runs search, extraction, validation, and deduplication
   - Adds new buyers to `buyers.csv`
   - Excludes previously contacted addresses
   - No emails are sent during this process

2. **Send Campaign**
   - Sends up to 10 emails to the next unsent buyers
   - Attaches company presentation PDF
   - Logs all attempts (success/failure)
   - Removes successful sends from queue

3. **View Report**
   - Campaign statistics (sent, failed, remaining)
   - Detailed send history
   - Failed recipient information
   - Download CSV report

### Command Line Interface

#### Search Only (No Emails)

```bash
python app.py --search
```

or

```bash
python main.py --search-only
```

#### Send Campaign

```bash
python send_campaign.py
```

#### Full Pipeline

```bash
# Run discovery + one campaign batch
python main.py

# Discovery only (dry run)
python main.py --dry-run
```

### Workflow Example

```bash
# Step 1: Find new leads
python app.py --search

# Step 2: Review leads in dashboard
# Open http://127.0.0.1:5000

# Step 3: Send campaign (repeat as needed)
python send_campaign.py

# Step 4: Review results
# Check "View Report" in dashboard
```

---

## Project Structure

```
export-automation/
├── activity_logging/
│   ├── __init__.py
│   └── activity_logger.py          # CSV persistence and data management
├── assets/
│   └── company_presentation.pdf    # Email attachment
├── data/
│   ├── buyers.csv                  # Unsent buyer records (not tracked)
│   ├── sent_log.csv                # Send attempt history (not tracked)
│   ├── flagged_emails.csv          # Rejected validation results (not tracked)
│   ├── buyers.sample.csv           # Format reference
│   ├── sent_log.sample.csv         # Format reference
│   └── flagged_emails.sample.csv   # Format reference
├── extraction/
│   ├── __init__.py
│   └── data_extractor.py           # Website crawling and email extraction
├── outreach/
│   ├── __init__.py
│   ├── gmail_auth.py               # Gmail SMTP authentication
│   ├── gmail_sender.py             # Email campaign execution
│   └── attachment_handler.py       # PDF attachment handling
├── search/
│   ├── __init__.py
│   ├── google_search.py            # Serper-powered Google search
│   ├── facebook_search.py          # Facebook business search
│   ├── linkedin_search.py          # LinkedIn company search
│   ├── directory_search.py          # Business directory search
│   ├── website_search.py           # Known buyer site search
│   ├── alibaba_search.py            # Alibaba marketplace search
│   ├── indiamart_search.py          # IndiaMART marketplace search
│   ├── instagram_search.py          # Instagram business search
│   └── yellowpages_search.py       # Yellow Pages directory search
├── templates/
│   ├── dashboard.html              # Main dashboard UI
│   ├── leads_result.html           # Search results display
│   ├── send_form.html              # Campaign send form
│   └── report.html                 # Campaign report view
├── validation/
│   ├── __init__.py
│   ├── email_validator.py          # Email validation pipeline
│   └── disposable_domains.txt      # Disposable domain blocklist
├── app.py                          # Flask application and CLI
├── config.py                       # Central configuration
├── main.py                         # CLI pipeline runner
├── send_campaign.py                # Standalone campaign sender
├── smoke_test.py                   # Automated test suite
├── requirements.txt                # Python dependencies
├── .env                            # Environment variables (not in git)
├── .env.example                    # Environment template
├── .gitignore                      # Git ignore rules
└── README.md                       # This file
```

---

## Data Management

### buyers.csv

Stores unsent buyer records only. Records are removed upon successful email delivery.

**Columns:**
- `buyer_name` - Contact person name
- `company_name` - Company name
- `email` - Email address (normalized lowercase)
- `website` - Source website URL
- `country` - Country code
- `source_platform` - Discovery platform (google, alibaba, etc.)
- `discovered_at` - ISO timestamp of discovery

### sent_log.csv

Comprehensive log of all send attempts with success/failure status.

**Columns:**
- `email` - Recipient email address
- `status` - "sent" or "failed"
- `sent_at` - ISO timestamp of send attempt
- `campaign_subject` - Email subject line
- `buyer_name` - Contact person name
- `company_name` - Company name
- `country` - Country code
- `product` - Product category
- `source_platform` - Discovery platform
- `attachment` - Attachment filename
- `error_message` - Error details (if failed)

### Data Integrity

- **Atomic Writes**: CSV updates use temp files with atomic replace
- **Concurrency Protection**: Process locks prevent concurrent modifications
- **Normalization**: All emails normalized to lowercase
- **Deduplication**: Multi-layer duplicate prevention
- **Sent History**: `sent_log.csv` is source of truth for contacted addresses

### Sample CSV Files

The repository includes sample CSV files (`*.sample.csv`) in the `data/` directory:
- `buyers.sample.csv` - Format reference for buyer records
- `sent_log.sample.csv` - Format reference for send history
- `flagged_emails.sample.csv` - Format reference for rejected emails

**Important**: These sample files contain only placeholder data and are for format reference only. They must not be used for actual email campaigns. The application will automatically create the actual `*.csv` files with correct headers when first run.

---

## Email Validation

### Validation Pipeline

Emails pass through multiple validation layers:

1. **Syntax Check** - RFC-compliant format validation
2. **Image Extension Guard** - Rejects filename artifacts (e.g., user@image.png)
3. **Domain Length** - Validates DNS label lengths (max 63 chars)
4. **Disposable Domain Filter** - Blocks 1000+ temporary email domains
5. **System Address Blocking** - Filters noreply, abuse, postmaster, etc.
6. **Placeholder Detection** - Rejects test@example.com patterns
7. **MX Record Verification** - Ensures domain can receive email
8. **SMTP Verification** (Optional) - Checks mailbox existence

### SMTP Verification

Three-valued result:
- **verified** - Server confirms mailbox exists (250 response)
- **rejected** - Server confirms mailbox does not exist (550 response)
- **unknown** - Timeout, error, or inconclusive response (email kept)

**Note**: Only explicit rejections discard emails; unknown/timeout results are retained.

### Disposable Domain Blocklist

The system includes a comprehensive blocklist of disposable email domains. Additional domains can be added to `validation/disposable_domains.txt`.

### Flagged Emails

Rejected emails are logged to `data/flagged_emails.csv` with rejection reasons for manual review.

---

## Campaign Management

### Batch Size

Default: 10 emails per campaign run. Configurable via `CAMPAIGN_BATCH_SIZE`.

### Rate Limiting

- Random delay between sends (2-4 seconds default)
- Configurable via `MIN_SEND_DELAY` and `MAX_SEND_DELAY`
- Daily send limit (default: 100 emails)

### Partial Failure Handling

If a batch contains failures:
- Successful sends are removed from `buyers.csv`
- Failed sends remain in `buyers.csv` for retry
- All attempts logged to `sent_log.csv`

Example: 10 emails sent, 3 failed
- `sent_log.csv`: 7 sent, 3 failed
- `buyers.csv`: 3 failed addresses remain

### Concurrent Protection

Process-wide lock prevents duplicate sends from concurrent runs (e.g., double-clicked button).

### Email Content

**Universal Template**: All recipients receive the same email with:
- Fixed subject line (configurable)
- Fixed body text (configurable)
- Company presentation PDF attachment
- No per-recipient personalization

---

## Testing

### Smoke Tests

Run the automated test suite:

```bash
python smoke_test.py
```

### Test Coverage

Automated tests covering:
- Configuration validation
- Module imports
- Email validation (syntax, disposable, MX, SMTP)
- Obfuscated email patterns
- Deduplication logic
- Sent-history exclusion
- Batch size limits
- Partial-failure handling
- Sender identity verification
- PDF attachment handling
- Search isolation
- Concurrent-run protection
- Malformed CSV handling
- Template rendering

### Test Output

Tests use monkeypatched SMTP - no real emails are sent during testing.

---

## Troubleshooting

### Dashboard Won't Start

**Problem**: `python app.py` fails to start

**Solutions**:
1. Check Python version (3.10+ required)
2. Verify all dependencies installed: `pip install -r requirements.txt`
3. Check port 5000 is not in use
4. Review error logs for specific issues

### No Search Results

**Problem**: Search returns zero results

**Solutions**:
1. Verify `SERPER_API_KEY` is valid in `.env`
2. Check internet connectivity
3. Confirm Serper API service is operational
4. Review search query configuration

### Gmail Authentication Error

**Problem**: "Gmail authentication failed"

**Solutions**:
1. Verify 2-Step Verification is enabled on Gmail account
2. Confirm App Password (not regular password) is in `.env`
3. Check Gmail account is not locked
4. Ensure `GMAIL_EMAIL` is correctly set in `.env`

### No Emails Found

**Problem**: Extraction finds zero email addresses

**Solutions**:
1. Some sites don't expose email addresses publicly
2. Check if Selenium is needed for JavaScript-heavy sites
3. Review website crawling logs for access issues
4. Verify MAX_PAGES_PER_DOMAIN is sufficient

### SMTP Verification Timeouts

**Problem**: SMTP verification is slow or times out

**Solutions**:
1. Set `ENABLE_SMTP_VERIFICATION=false` in `.env`
2. SMTP verification is optional; system works without it
3. Increase timeout in `email_validator.py` if needed

### CSV File Corruption

**Problem**: CSV files appear corrupted

**Solutions**:
1. System uses atomic writes - corruption is rare
2. Check disk space availability
3. Restore from backup if available
4. Re-run search to rebuild `buyers.csv`

### Selenium Errors

**Problem**: Selenium ChromeDriver fails

**Solutions**:
1. Selenium is optional - system works without it
2. Install Google Chrome if needed
3. Check ChromeDriver version compatibility
4. Falls back to requests/BeautifulSoup automatically

---

## Security & Compliance

### Best Practices

- **Business Contacts Only**: Target relevant business prospects
- **Public Information**: Use publicly available contact details
- **Respect Terms**: Adhere to website terms of service
- **Opt-Out Handling**: Provide unsubscribe mechanisms where required
- **Rate Limiting**: Respect sending limits and delays
- **Data Privacy**: Never expose credentials or customer data

### Credential Security

- Never commit `.env` file to version control
- Use App Passwords, not regular passwords
- Rotate credentials periodically
- Restrict API key permissions as needed

### Anti-Spam Compliance

- Provide clear sender identification
- Include physical address in emails
- Honor unsubscribe requests
- Comply with CAN-SPAM, GDPR, and local regulations
- Maintain accurate sender information

### System Security

- Sender email configured via `GMAIL_EMAIL` in `.env` (never hardcoded in source)
- App Password configured via `GMAIL_APP_PASSWORD` in `.env` (never hardcoded in source)
- Configuration validation at startup ensures credentials are present
- Process locks prevent concurrent duplicate sends
- Atomic file writes prevent data corruption
- No external infrastructure dependencies

### Authentication Handling

- Sources requiring authentication fail gracefully
- No CAPTCHA bypassing
- No authentication circumvention
- WHOIS-derived addresses excluded from automated outreach

---

## License

This project is intended for educational and demonstration purposes. Add an appropriate open-source license (e.g., MIT, Apache 2.0) before broader distribution or commercial use.

---

## Support

For issues, questions, or contributions:
- Review the troubleshooting section
- Check existing issues in the repository
- Consult the code documentation
- Run smoke tests to verify installation

---

## Acknowledgments

Built for Product Zone International - Wholesale Decorative Glassware & Home Décor Collection.

**Technologies Used**:
- Python 3.10+
- Flask 3.0+
- Requests & BeautifulSoup
- Selenium (optional)
- Serper API
- dnspython
- python-whois

---

*Last Updated: September 2026*
