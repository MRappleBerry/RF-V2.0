"""Selenium scraping starter with manual sign-in and CSV export.

Usage example:
python selenium_scrape_template.py --url "https://security.microsoft.com"

Then:
1) Sign in manually in the opened browser window.
2) Navigate to the page that has the data table.
3) Press Enter in terminal when ready.
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import List

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual-login Selenium scraper")
    parser.add_argument("--url", required=True, help="Start URL (portal landing page)")
    parser.add_argument(
        "--table-selector",
        default="table",
        help="CSS selector for the table element (default: table)",
    )
    parser.add_argument(
        "--row-selector",
        default="tbody tr",
        help="CSS selector for table rows inside the table (default: tbody tr)",
    )
    parser.add_argument(
        "--cell-selector",
        default="th, td",
        help="CSS selector for header/data cells in each row (default: th, td)",
    )
    parser.add_argument(
        "--ready-selector",
        default="",
        help="Optional CSS selector to wait for after login/navigation",
    )
    parser.add_argument(
        "--output",
        default="scraped_data.csv",
        help="Output CSV path (default: scraped_data.csv)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Wait timeout in seconds (default: 30)",
    )
    return parser.parse_args()


def wait_for_ready(driver: webdriver.Chrome, selector: str, timeout: int) -> None:
    if not selector:
        return
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
    )


def extract_rows(
    driver: webdriver.Chrome,
    table_selector: str,
    row_selector: str,
    cell_selector: str,
    timeout: int,
) -> List[List[str]]:
    table = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, table_selector))
    )

    rows = table.find_elements(By.CSS_SELECTOR, row_selector)
    data: List[List[str]] = []

    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, cell_selector)
        values = [c.text.strip() for c in cells]
        if any(values):
            data.append(values)

    return data


def write_csv(path: str, rows: List[List[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    driver = webdriver.Chrome()
    driver.maximize_window()

    try:
        driver.get(args.url)
        print("Browser opened. Complete login/MFA and navigate to the data page.")
        input("Press Enter here when the page is ready to scrape...")

        wait_for_ready(driver, args.ready_selector, args.timeout)
        rows = extract_rows(
            driver,
            args.table_selector,
            args.row_selector,
            args.cell_selector,
            args.timeout,
        )

        if not rows:
            print("No rows found. Adjust selectors and run again.")
            return 2

        write_csv(args.output, rows)
        print(f"Saved {len(rows)} rows to {args.output}")
        return 0

    except TimeoutException:
        print("Timed out waiting for page elements. Check selectors and timeout.")
        return 1
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
