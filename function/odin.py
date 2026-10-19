from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime



PROMPT_TEXT = """Output plain text only.
Do not add any introduction, explanation, disclaimer, or extra notes.
Use the exact section order and headings shown below.
Keep the tone concise, analyst-style, and operational.
Keep Summary of Assessment to one short paragraph.
What happened must contain exactly 3 bullet points.
Why it matters must contain exactly 3 bullet points.
Recommended next actions must contain exactly 3 numbered items.
Use the confidence rules exactly:
If Status is Resolved, Closed, or Complete: Confidence = 94%, Label = Validated
If Status is Open or Pending: Confidence = 78%, Label = Needs Review
Otherwise: Confidence = 82%, Label = Needs Review
Output format:

ODIN • AI Findings
Confidence
[CONFIDENCE]
[LABEL]

Summary of Assessment
[INCIDENT NAME] imported from db_info.txt. Severity [SEVERITY]. Current status [STATUS]. Primary actor [ACTOR]. Location [LOCATION]."""

DEFAULT_ODIN_URL = "https://odin.landpower.net/odin/21eccdcf-d2fc-4a36-ada7-ebf7b851643d"
CHAT_INPUT_SELECTOR = ".form-control.m-0.w-100.border-0.bg-transparent.chat-input.chat-input-normal"
UPLOAD_MODAL_OPEN_BUTTON_SELECTOR = "button.btn.btn-outline-secondary.btn-sm.border-0.round.p-2.me-1"
UPLOAD_MODAL_INPUT_SELECTOR = "input#inputFiles"
UPLOAD_MODAL_CONFIRM_BUTTON_SELECTOR = "button.btn.btn-secondary.btn-sm.btn-modal-primary"
SEND_BUTTON_SELECTOR = "button.btn.btn-outline-secondary.fw-bold.ms-1.me-1"
RESPONSE_SELECTOR = ".flex.flex-grow.flex-col.w-100"

SKIP_FILE_NAMES = {
	"odin-response.txt",
	"odin-response.json",
	"odin-response-api.raw.json",
}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Upload artifact files to ODIN, send a fixed prompt, and capture only the latest AI response per folder."
	)
	parser.add_argument(
		"--url",
		default=DEFAULT_ODIN_URL,
		help="ODIN URL to open.",
	)
	parser.add_argument(
		"--artifacts-root",
		default="artifacts",
		help="Root folder containing alert artifact subfolders.",
	)
	parser.add_argument(
		"--prompt",
		default=PROMPT_TEXT,
		help="Prompt text to send to ODIN.",
	)
	parser.add_argument(
		"--wait-timeout",
		type=int,
		default=180,
		help="Timeout in seconds when waiting for ODIN response.",
	)
	parser.add_argument(
		"--profile-directory",
		default="",
		help="Optional Edge profile directory name.",
	)
	parser.add_argument(
		"--reload-per-folder",
		action="store_true",
		help="Reload ODIN URL before each folder to reduce stale UI state.",
	)
	return parser.parse_args()


def _workspace_root() -> str:
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_artifacts_root(path_value: str) -> str:
	if os.path.isabs(path_value):
		return path_value
	return os.path.join(_workspace_root(), path_value)


def _artifact_files(folder_path: str) -> list[str]:
	files: list[str] = []
	for name in sorted(os.listdir(folder_path)):
		absolute_path = os.path.join(folder_path, name)
		if not os.path.isfile(absolute_path):
			continue
		if name.lower() in SKIP_FILE_NAMES:
			continue
		files.append(absolute_path)
	return files


def _wait_for_chat_input(driver, wait):
	from selenium.webdriver.common.by import By
	from selenium.webdriver.support import expected_conditions as EC

	return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CHAT_INPUT_SELECTOR)))


def _set_prompt_text(driver, prompt_text: str) -> None:
	input_element = driver.find_element("css selector", CHAT_INPUT_SELECTOR)
	try:
		input_element.click()
		input_element.clear()
		input_element.send_keys(prompt_text)
	except Exception:
		driver.execute_script(
			"""
			const element = arguments[0];
			const value = arguments[1];
			element.focus();
			element.value = '';
			element.dispatchEvent(new Event('input', { bubbles: true }));
			element.value = value;
			element.dispatchEvent(new Event('input', { bubbles: true }));
			element.dispatchEvent(new Event('change', { bubbles: true }));
			""",
			input_element,
			prompt_text,
		)


def _open_upload_modal(driver, wait) -> None:
	from selenium.webdriver.common.by import By
	from selenium.webdriver.support import expected_conditions as EC

	open_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, UPLOAD_MODAL_OPEN_BUTTON_SELECTOR)))
	try:
		open_button.click()
	except Exception:
		driver.execute_script("arguments[0].click();", open_button)

	wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, UPLOAD_MODAL_INPUT_SELECTOR)))


def _upload_files(driver, wait, file_paths: list[str]) -> None:
	from selenium.webdriver.common.by import By
	from selenium.webdriver.support import expected_conditions as EC

	_open_upload_modal(driver, wait)
	upload_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, UPLOAD_MODAL_INPUT_SELECTOR)))

	driver.execute_script(
		"""
		const element = arguments[0];
		if (element) {
			element.style.display = 'block';
			element.style.visibility = 'visible';
			element.style.opacity = '1';
		}
		""",
		upload_input,
	)

	upload_input.send_keys("\n".join(file_paths))

	expected_count = len(file_paths)
	wait.until(
		lambda d: int(
			d.execute_script(
				"""
				const el = arguments[0];
				return el && el.files ? el.files.length : 0;
				""",
				upload_input,
			)
			or 0
		)
		>= expected_count
	)

	confirm_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, UPLOAD_MODAL_CONFIRM_BUTTON_SELECTOR)))
	try:
		confirm_button.click()
	except Exception:
		driver.execute_script("arguments[0].click();", confirm_button)

	# Wait for the upload modal to be fully closed before proceeding
	from selenium.webdriver.common.by import By
	from selenium.webdriver.support import expected_conditions as EC
	try:
		wait.until(EC.invisibility_of_element_located((By.ID, "filesUploadModal")))
	except Exception:
		# Fallback: small sleep if modal doesn't disappear in time
		time.sleep(1)


def _clear_modal_upload_files(driver) -> None:
	driver.execute_script(
		"""
		const input = document.querySelector('input#inputFiles');
		if (!input) return;
		input.value = '';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new Event('change', { bubbles: true }));
		"""
	)


def _snapshot_last_response(driver) -> tuple[int, str]:
	elements = driver.find_elements("css selector", RESPONSE_SELECTOR)
	if not elements:
		return 0, ""
	text = (elements[-1].text or "").strip()
	return len(elements), text


def _click_send_button(driver, wait) -> None:
	from selenium.webdriver.common.by import By
	from selenium.webdriver.support import expected_conditions as EC

	button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, SEND_BUTTON_SELECTOR)))
	button.click()


def _wait_for_latest_response(driver, previous_count: int, previous_text: str, timeout_seconds: int) -> str:
	deadline = time.time() + max(10, timeout_seconds)
	candidate_text = ""
	stable_hits = 0
	last_seen = ""

	while time.time() < deadline:
		elements = driver.find_elements("css selector", RESPONSE_SELECTOR)
		if not elements:
			time.sleep(1)
			continue

		latest_text = (elements[-1].text or "").strip()
		has_new_block = len(elements) > previous_count
		has_changed_text = bool(latest_text) and latest_text != previous_text

		if has_new_block or has_changed_text:
			candidate_text = latest_text
			if latest_text == last_seen:
				stable_hits += 1
			else:
				stable_hits = 1
				last_seen = latest_text

			if candidate_text and stable_hits >= 3:
				return candidate_text

		time.sleep(1)

	if candidate_text:
		return candidate_text
	raise TimeoutError("Timed out waiting for ODIN latest response.")


def _save_folder_output(folder_path: str, prompt_text: str, uploaded_files: list[str], latest_response: str) -> None:

	now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

	def extract_section(text, heading, next_headings=None):
		import re
		# Build a regex to find the heading and capture everything up to the next heading or end
		if not next_headings:
			pattern = rf"{re.escape(heading)}[\s:]*([\s\S]+)"
		else:
			next_pattern = r"|".join([re.escape(h) for h in next_headings])
			pattern = rf"{re.escape(heading)}[\s:]*([\s\S]+?)(?:{next_pattern}|$)"
		m = re.search(pattern, text, re.IGNORECASE)
		if not m:
			return ""
		# Clean up: remove any trailing heading text from the result
		section = m.group(1).strip()
		if next_headings:
			for nh in next_headings:
				idx = section.lower().find(nh.lower())
				if idx != -1:
					section = section[:idx].strip()
		return section

	# Extract fields from the response
	confidence = ""
	confidence_label = ""
	import re
	conf_match = re.search(r"ODIN • AI Findings\s*Confidence\s*(\d+%)\s*([\w ]+)", latest_response, re.IGNORECASE)
	if conf_match:
		confidence = conf_match.group(1).strip()
		confidence_label = conf_match.group(2).strip()

	what_happened = extract_section(
		latest_response,
		"What happened",
		next_headings=["Why it matters", "Recommended next actions"],
	)
	why_it_matters = extract_section(
		latest_response,
		"Why it matters",
		next_headings=["Recommended next actions"],
	)
	recommended_next_actions = extract_section(
		latest_response,
		"Recommended next actions",
		next_headings=None,
	)

	output_payload = {
		"Odin Summary": latest_response,
		"Odin Confidence": confidence,
		"Odin Confidence Label": confidence_label,
		"Odin What Happened": what_happened,
		"Odin Why It Matters": why_it_matters,
		"Odin Recommended Next Actions": recommended_next_actions,
		"Odin Raw Response": latest_response,
		"Odin Uploaded Files": "\n".join(os.path.basename(path) for path in uploaded_files),
		"Odin Prompt": prompt_text,
		"Odin Response Updated At": now_text,
	}

	txt_path = os.path.join(folder_path, "odin-response.txt")
	json_path = os.path.join(folder_path, "odin-response.json")
	html_path = os.path.join(folder_path, "odin-response.html")

	with open(txt_path, "w", encoding="utf-8") as file_obj:
		file_obj.write(latest_response)

	with open(json_path, "w", encoding="utf-8") as file_obj:
		json.dump(output_payload, file_obj, indent=2, ensure_ascii=False)

	# Save the HTML of the last ODIN response block
	try:
		from selenium.webdriver.common.by import By
		elements = driver.find_elements(By.CSS_SELECTOR, RESPONSE_SELECTOR)
		if elements:
			last_html = elements[-1].get_attribute("outerHTML")
			with open(html_path, "w", encoding="utf-8") as file_obj:
				file_obj.write(last_html)
	except Exception:
		pass


def run() -> int:
	args = parse_args()
	workspace_root = _workspace_root()

	if workspace_root not in sys.path:
		sys.path.insert(0, workspace_root)

	from selenium.webdriver.support.ui import WebDriverWait
	from function import tools as t

	artifacts_root = _resolve_artifacts_root(args.artifacts_root)
	if not os.path.isdir(artifacts_root):
		print(f"Artifacts root not found: {artifacts_root}", file=sys.stderr)
		return 1

	folder_paths = [
		os.path.join(artifacts_root, name)
		for name in sorted(os.listdir(artifacts_root))
		if os.path.isdir(os.path.join(artifacts_root, name))
	]
	if not folder_paths:
		print("No artifact folders found.")
		return 0

	edge_user_data_dir = os.path.join(
		os.environ.get("LOCALAPPDATA", ""),
		"Microsoft",
		"Edge",
		"User Data",
	)

	temp_user_data_dir: str | None = None
	driver = None

	try:
		profile_directory = t.resolve_profile_directory(edge_user_data_dir, args.profile_directory)
		temp_user_data_dir = t.copy_profile_for_automation(edge_user_data_dir, profile_directory)
		driver = t.build_driver(temp_user_data_dir, profile_directory)
		t.ensure_browser_window(driver)

		wait = WebDriverWait(driver, 45)
		t.load_url_in_current_tab(driver, args.url)
		_wait_for_chat_input(driver, wait)

		print(f"[ODIN] Processing {len(folder_paths)} artifact folders")
		success_count = 0

		for folder_path in folder_paths:
			folder_name = os.path.basename(folder_path)
			files_to_upload = _artifact_files(folder_path)
			if not files_to_upload:
				print(f"[ODIN] Skipping '{folder_name}': no files to upload")
				continue

			if args.reload_per_folder:
				t.load_url_in_current_tab(driver, args.url)
				_wait_for_chat_input(driver, wait)

			print(f"[ODIN] Folder: {folder_name}")
			print(f"[ODIN] Uploading {len(files_to_upload)} files")

			_set_prompt_text(driver, args.prompt)
			previous_count, previous_text = _snapshot_last_response(driver)
			_upload_files(driver, wait, files_to_upload)
			_click_send_button(driver, wait)

			latest_response = _wait_for_latest_response(
				driver,
				previous_count=previous_count,
				previous_text=previous_text,
				timeout_seconds=args.wait_timeout,
			)

			_save_folder_output(folder_path, args.prompt, files_to_upload, latest_response)
			_clear_modal_upload_files(driver)
			print(f"[ODIN] Saved latest response for '{folder_name}'")
			success_count += 1

		print(f"[ODIN] Completed: {success_count}/{len(folder_paths)}")
		return 0 if success_count == len(folder_paths) else 2

	except Exception as exc:  # noqa: BLE001
		print(f"[ODIN] Automation failed: {exc}", file=sys.stderr)
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
	raise SystemExit(run())
