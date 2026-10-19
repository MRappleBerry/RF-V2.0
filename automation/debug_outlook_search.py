import os
import shutil
import sys
import tempfile
import time

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNCTION_DIR = os.path.join(WORKSPACE_ROOT, "function")
if FUNCTION_DIR not in sys.path:
    sys.path.insert(0, FUNCTION_DIR)

import tools  # noqa: E402


def main() -> int:
    keep_open = "--keep-open" in sys.argv

    records = tools.get_alert_records_from_data(
        os.path.join(WORKSPACE_ROOT, "automation", "db_info.txt"),
        startswith="DLP",
    )
    exchange_record = next(
        (record for record in records if "exchange" in str(record.get("location") or "").lower()),
        None,
    )
    if not exchange_record:
        print("No Exchange alert found in automation/db_info.txt", file=sys.stderr)
        return 1

    alert_name = str(exchange_record.get("alert_name") or "").strip()
    email_subject = tools.extract_email_subject_from_alert_name(alert_name)
    if not email_subject:
        print(f"Could not extract email subject from alert: {alert_name}", file=sys.stderr)
        return 1

    print(f"Exchange alert: {alert_name}")
    print(f"Extracted subject: {email_subject}")

    user_data_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "Edge",
        "User Data",
    )
    if not os.path.isdir(user_data_dir):
        print(f"Edge user data directory not found: {user_data_dir}", file=sys.stderr)
        return 1

    profile_directory = tools.resolve_profile_directory(user_data_dir, "")
    print(f"Using Edge profile: {profile_directory}")

    temp_user_data_dir = None
    driver = None
    try:
        temp_user_data_dir = tools.copy_profile_for_automation(user_data_dir, profile_directory)
        driver = tools.build_driver(temp_user_data_dir, profile_directory)
        tools.ensure_browser_window(driver)
        driver.get("https://outlook.office.com/")
        tools.wait_for_page_load(driver, 60)

        outlook_handle = driver.current_window_handle
        before_url = driver.current_url
        print(f"Outlook initial URL: {before_url}")

        search_ok = tools.search_outlook_for_alert_subject(
            driver,
            alert_name,
            60,
            outlook_handle=outlook_handle,
        )
        print(f"search_outlook_for_alert_subject returned: {search_ok}")

        driver.switch_to.window(outlook_handle)
        deadline = time.time() + 20
        final_state = {}
        while time.time() < deadline:
            try:
                final_state = driver.execute_script(
                    """
                    const input = document.querySelector('#topSearchInput')
                        || document.querySelector('input[aria-label*="Search"]');
                    const headings = Array.from(document.querySelectorAll('h1, h2, [role="heading"]'))
                        .map(el => String(el.innerText || el.textContent || '').trim())
                        .filter(Boolean)
                        .slice(0, 8);
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
                        .map(el => ({
                            text: String(el.innerText || el.textContent || '').trim(),
                            aria: String(el.getAttribute('aria-label') || ''),
                        }))
                        .filter(item => item.text || item.aria)
                        .filter(item => /search|result/i.test(item.text) || /search|result/i.test(item.aria))
                        .slice(0, 8);
                    return {
                        url: String(window.location.href || ''),
                        title: String(document.title || ''),
                        inputValue: input ? String(input.value || '') : '',
                        activeTag: document.activeElement ? String(document.activeElement.tagName || '') : '',
                        activeId: document.activeElement ? String(document.activeElement.id || '') : '',
                        headings,
                        buttons,
                        bodyHasSearchResults: /search results/i.test(String(document.body.innerText || '')),
                        bodyHasSubject: document.body.innerText
                            ? String(document.body.innerText).toLowerCase().includes(arguments[0].toLowerCase())
                            : false,
                    };
                    """,
                    email_subject,
                )
            except Exception:
                final_state = {}

            url_text = str(final_state.get("url") or "").lower()
            title_text = str(final_state.get("title") or "").lower()
            if (
                "search" in url_text
                or email_subject.lower() in title_text
                or bool(final_state.get("bodyHasSearchResults"))
            ):
                break
            time.sleep(1)

        print("Final Outlook state:")
        print(final_state)
        return 0 if search_ok else 1
    finally:
        if driver is not None:
            if keep_open:
                try:
                    input("Debug run completed. Press Enter to close the browser...")
                except EOFError:
                    pass
            try:
                driver.quit()
            except Exception:
                pass
        if temp_user_data_dir and os.path.isdir(temp_user_data_dir):
            shutil.rmtree(temp_user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
