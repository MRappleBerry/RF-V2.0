from __future__ import annotations

import os
import shutil
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from function import tools as t


def main() -> int:
    db_info_path = os.path.join(WORKSPACE_ROOT, "automation", "db_info.txt")

    alert_records = t.get_alert_records_from_data(db_info_path, startswith="DLP")
    if not alert_records:
        print("[ODIN-TEST] No DLP alerts found in db_info.txt")
        return 1

    alert_name = str(alert_records[0].get("alert_name") or "").strip()
    if not alert_name:
        print("[ODIN-TEST] First DLP alert has empty alert_name")
        return 1

    edge_user_data_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft",
        "Edge",
        "User Data",
    )

    profile_directory = t.resolve_profile_directory(edge_user_data_dir, "")
    temp_user_data_dir: str | None = None
    driver = None

    try:
        temp_user_data_dir = t.copy_profile_for_automation(edge_user_data_dir, profile_directory)
        driver = t.build_driver(temp_user_data_dir, profile_directory)
        t.ensure_browser_window(driver)

        t.load_url_in_current_tab(driver, t.ODIN_URL)
        t.wait_for_servicenow_with_browser_refresh(driver, 45, portal_label="ODIN")

        print(f"[ODIN-TEST] Testing upload for alert: {alert_name}")
        uploaded_paths = t._upload_odin_artifact_files(driver, alert_name, 45)
        print(f"[ODIN-TEST] Uploaded path count: {len(uploaded_paths)}")
        for path in uploaded_paths:
            print(f"[ODIN-TEST] {os.path.basename(path)}")

        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[ODIN-TEST] Upload test failed: {exc}", file=sys.stderr)
        try:
            print(f"[ODIN-TEST] URL: {driver.current_url}")
            print(f"[ODIN-TEST] Title: {driver.title}")
        except Exception:
            pass

        try:
            root_inputs = driver.execute_script(
                """
                return Array.from(document.querySelectorAll('input[type="file"], input#nputFilesOdin, input#inputFilesOdin')).map((el) => ({
                  id: String(el.id || ''),
                  name: String(el.name || ''),
                  className: String(el.className || ''),
                  outer: String(el.outerHTML || '').slice(0, 220),
                }));
                """
            )
            print(f"[ODIN-TEST] Root file inputs found: {len(root_inputs or [])}")
            for item in root_inputs or []:
                print(f"[ODIN-TEST] root input id={item.get('id')!r} name={item.get('name')!r} class={item.get('className')!r}")
        except Exception as inner_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] Failed root input inspection: {inner_exc}", file=sys.stderr)

        try:
            frame_count = len(driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame"))
            print(f"[ODIN-TEST] iframe/frame count: {frame_count}")
            for frame_index in range(frame_count):
                try:
                    frames = driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame")
                    if frame_index >= len(frames):
                        continue
                    driver.switch_to.frame(frames[frame_index])
                    frame_inputs = driver.execute_script(
                        """
                        return Array.from(document.querySelectorAll('input[type="file"], input#nputFilesOdin, input#inputFilesOdin')).map((el) => ({
                          id: String(el.id || ''),
                          name: String(el.name || ''),
                          className: String(el.className || ''),
                          outer: String(el.outerHTML || '').slice(0, 220),
                        }));
                        """
                    )
                    print(f"[ODIN-TEST] frame[{frame_index}] file inputs found: {len(frame_inputs or [])}")
                    for item in frame_inputs or []:
                        print(
                            f"[ODIN-TEST] frame[{frame_index}] input id={item.get('id')!r} name={item.get('name')!r} class={item.get('className')!r}"
                        )
                except Exception as frame_exc:  # noqa: BLE001
                    print(f"[ODIN-TEST] frame[{frame_index}] inspection failed: {frame_exc}", file=sys.stderr)
                finally:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception as frames_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] Failed frame inspection: {frames_exc}", file=sys.stderr)

        click_upload_script = """
        function normalize(v) { return String(v || '').replace(/\\s+/g, ' ').trim().toLowerCase(); }
        function visible(el) {
          if (!el) return false;
          const style = window.getComputedStyle(el);
          if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        }
        const selectors = ['button', '[role="button"]', 'label', 'a', 'span', 'div'];
        let clicked = 0;
        for (const selector of selectors) {
          for (const node of document.querySelectorAll(selector)) {
            const text = normalize(node.innerText || node.textContent || '');
            const aria = normalize(node.getAttribute('aria-label') || '');
            const title = normalize(node.getAttribute('title') || '');
            const classes = normalize(node.className || '');
            const looksUpload =
              text.includes('upload') || text.includes('attach') || text.includes('file') || text.includes('paperclip') ||
              aria.includes('upload') || aria.includes('attach') || aria.includes('file') || aria.includes('paperclip') ||
              title.includes('upload') || title.includes('attach') || title.includes('file') || title.includes('paperclip') ||
              classes.includes('upload') || classes.includes('attach') || classes.includes('paperclip');
            if (!looksUpload || !visible(node)) continue;
            try {
              node.click();
              clicked += 1;
            } catch (_) {}
            if (clicked >= 5) return clicked;
          }
        }
        return clicked;
        """

        try:
            clicked_root = driver.execute_script(click_upload_script) or 0
            print(f"[ODIN-TEST] root upload-ish controls clicked: {clicked_root}")
        except Exception as click_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] root upload control click failed: {click_exc}", file=sys.stderr)

        try:
            frame_count = len(driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame"))
            for frame_index in range(frame_count):
                try:
                    frames = driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame")
                    if frame_index >= len(frames):
                        continue
                    driver.switch_to.frame(frames[frame_index])
                    clicked_frame = driver.execute_script(click_upload_script) or 0
                    print(f"[ODIN-TEST] frame[{frame_index}] upload-ish controls clicked: {clicked_frame}")
                except Exception as click_frame_exc:  # noqa: BLE001
                    print(f"[ODIN-TEST] frame[{frame_index}] upload control click failed: {click_frame_exc}", file=sys.stderr)
                finally:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception as click_frames_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] failed frame upload-control click pass: {click_frames_exc}", file=sys.stderr)

        try:
            root_inputs_after = driver.execute_script(
                "return Array.from(document.querySelectorAll('input[type=\"file\"], input#nputFilesOdin, input#inputFilesOdin')).length;"
            )
            print(f"[ODIN-TEST] Root file inputs after click pass: {int(root_inputs_after or 0)}")
        except Exception as after_root_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] Failed post-click root input inspection: {after_root_exc}", file=sys.stderr)

        try:
            frame_count = len(driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame"))
            for frame_index in range(frame_count):
                try:
                    frames = driver.find_elements(t.By.CSS_SELECTOR, "iframe,frame")
                    if frame_index >= len(frames):
                        continue
                    driver.switch_to.frame(frames[frame_index])
                    frame_inputs_after = driver.execute_script(
                        "return Array.from(document.querySelectorAll('input[type=\"file\"], input#nputFilesOdin, input#inputFilesOdin')).length;"
                    )
                    print(f"[ODIN-TEST] frame[{frame_index}] file inputs after click pass: {int(frame_inputs_after or 0)}")
                except Exception as after_frame_exc:  # noqa: BLE001
                    print(f"[ODIN-TEST] frame[{frame_index}] post-click inspection failed: {after_frame_exc}", file=sys.stderr)
                finally:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception as after_frames_exc:  # noqa: BLE001
            print(f"[ODIN-TEST] failed post-click frame inspections: {after_frames_exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if temp_user_data_dir and os.path.isdir(temp_user_data_dir):
            shutil.rmtree(temp_user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
