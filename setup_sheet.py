import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Platform",
    "URL",
    "Raw Text",
    "Review Date",
    "Support Reply",
    "Support Reply Date",
    "Customer Order Status",
    "Journey Stage",
    "Review Type",
]

def get_worksheet(company_name: str):
    """Authenticate and return a worksheet tab for the given company.
    Creates the tab (and header row) if it doesn't exist yet."""
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url(SHEET_URL)

    # Sheet tab names can't exceed 100 chars and can't contain some symbols
    tab_name = company_name.strip()[:100]

    try:
        worksheet = spreadsheet.worksheet(tab_name)
        print(f"Found existing tab: {tab_name}")
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(HEADERS))
        worksheet.append_row(HEADERS)
        print(f"Created new tab: {tab_name} with headers")

    return worksheet


if __name__ == "__main__":
    # quick test
    ws = get_worksheet("Test Company")
    print("Success. Current row count:", len(ws.get_all_values()))