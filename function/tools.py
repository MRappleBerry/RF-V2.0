from __future__ import annotations
"""Open Microsoft 365 pages in Edge tabs and type into search inputs.

This script reuses an existing local Edge profile so authenticated sessions can be
reused (subject to corporate policy/MFA/session expiration).
"""

import argparse
import csv
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Callable, List, Tuple
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape


REQUIRED_RUNTIME_MODULES = ("selenium", "openpyxl", "pypdf")


def _get_workspace_root() -> str:
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_runtime_installer_path() -> str:
	return os.path.join(_get_workspace_root(), "automation", "install_runtime_requirements.py")


def _find_missing_runtime_modules() -> list[str]:
	return [
		module_name
		for module_name in REQUIRED_RUNTIME_MODULES
		if importlib.util.find_spec(module_name) is None
	]


def ensure_runtime_requirements_installed() -> None:
	missing_modules = _find_missing_runtime_modules()
	if not missing_modules:
		return

	installer_path = _get_runtime_installer_path()
	if not os.path.isfile(installer_path):
		raise FileNotFoundError(f"Runtime installer not found: {installer_path}")

	print(
		"Missing Python runtime dependencies detected: "
		+ ", ".join(missing_modules)
		+ ". Installing automatically..."
	)
	subprocess.run(
		[sys.executable, installer_path, "--current-python"],
		check=True,
	)
	importlib.invalidate_caches()

	remaining_missing = _find_missing_runtime_modules()
	if remaining_missing:
		raise RuntimeError(
			"Automatic runtime installation completed, but required modules are still missing: "
			+ ", ".join(remaining_missing)
		)

openpyxl = None
OPENPYXL_IMPORT_ATTEMPTED = False
pypdf = None
PYPDF_IMPORT_ATTEMPTED = False

ensure_runtime_requirements_installed()

from selenium import webdriver
from selenium.common.exceptions import (
	ElementClickInterceptedException,
	TimeoutException,
	WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


Locator = Tuple[By, str]


@dataclass
class AlertWorkbookLookupCacheEntry:
	workbook_path: str
	workbook_file_name: str
	value_lookup: dict[str, list[str]]
	digit_lookup: dict[str, list[str]]
	sheet_headers: dict[str, list[str]]
	row_values: dict[tuple[str, int], list[str]]
	searched_values: set[str]
	searched_digits: set[str]


PURVIEW_COLUMN_ORDER = [
	"AlertName",
	"Severity",
	"AlertStatus",
	"TimeOfAlert",
	"Actor",
	"Locations",
]

PURVIEW_COLUMN_LABELS = {
	"AlertName": "Alert Name",
	"Severity": "Severity",
	"AlertStatus": "Status",
	"TimeOfAlert": "Time Detected",
	"Actor": "User",
	"Locations": "Location",
}

EMAIL_SUBJECT_ALERT_PATTERN = re.compile(
	r"match for email with subject\s+'([^']+)'",
	re.IGNORECASE,
)
ALERT_SINGLE_QUOTED_TEXT_PATTERN = re.compile(r"'([^']+)'")
INCIDENTS_URL = "https://security.microsoft.com/incidents"
ODIN_URL = "https://odin.landpower.net/"
OUTLOOK_ALERT_SENDER_FILTER = "from:Office365Alerts@microsoft.com"
ODIN_DB_INFO_FIELD_ORDER = [
	"Odin Summary",
	"Odin Confidence",
	"Odin Confidence Label",
	"Odin What Happened",
	"Odin Why It Matters",
	"Odin Recommended Next Actions",
	"Odin Raw Response",
	"Odin Uploaded Files",
	"Odin Prompt",
	"Odin Response Updated At",
]
INCIDENTS_SEARCH_LOCATORS: tuple[Locator, ...] = (
	(By.CSS_SELECTOR, "input#SearchBox1612.ms-SearchBox-field.field-533"),
	(By.CSS_SELECTOR, "input[id^='SearchBox'][class*='ms-SearchBox-field']"),
	(By.CSS_SELECTOR, "input.ms-SearchBox-field"),
	(By.CSS_SELECTOR, "input[aria-label*='Search']"),
)
INCIDENT_ID_LOCATORS: tuple[Locator, ...] = (
	(By.CSS_SELECTOR, "div.ms-DetailsRow-cell.cell-745.cellUnpadded-743"),
)
OUTLOOK_SEARCH_LOCATORS: tuple[Locator, ...] = (
	(By.CSS_SELECTOR, "input#topSearchInput.uz227.GgHzM"),
	(By.CSS_SELECTOR, "input#topSearchInput.uz227.EmdGf"),
	(By.ID, "topSearchInput"),
	(By.CSS_SELECTOR, "input[aria-label*='Search']"),
)
ODIN_PROMPT_INPUT_SELECTORS: tuple[str, ...] = (
	".form-control.m-0.w-100.border-0.bg-transparent.chat-input.chat-input-normal",
	"input.form-control.m-0.w-100.border-0.bg-transparent.chat-input.chat-input-normal",
	"textarea",
	"textarea[placeholder*='Ask']",
	"input[placeholder*='Ask']",
	"[role='textbox'][contenteditable='true']",
	"div[contenteditable='true']",
)
ODIN_FILE_INPUT_SELECTORS: tuple[str, ...] = (
	"input#nputFilesOdin",
	"input#inputFilesOdin",
	"input[type='file'][id*='filesodin']",
	"input[type='file'][name*='filesodin']",
	"input[type='file']#nputFilesOdin",
	"input[type='file']#inputFilesOdin",
	"input[type='file'][multiple]",
	"input[type='file']",
)
ODIN_SEND_BUTTON_SELECTORS: tuple[str, ...] = (
	"button.btn.btn-outline-secondary.fw-bold.ms-1.me-1",
	"button[aria-label*='Send']",
	"button[title*='Send']",
	"button[type='submit']",
	"[role='button'][aria-label*='Send']",
)
ODIN_RESPONSE_BLOCK_SELECTORS: tuple[str, ...] = (
	".flex.flex-grow.flex-col.w-100",
	"[data-message-author-role='assistant']",
	"[data-testid*='assistant']",
	"article",
	"[role='article']",
	"[class*='markdown']",
	"[class*='prose']",
	"[class*='message']",
)
ODIN_CAPTURE_OUTPUT_FILE_NAMES = {
	"odin-prompt.txt",
	"odin-response.txt",
	"odin-response.json",
}
ODIN_PROMPT_TEMPLATE = """Output plain text only.
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
TEXT_TABLE_SHEET_NAME = "Rows"
WORD_DOCUMENT_SHEET_NAME = "Paragraphs"
EMAIL_MESSAGE_SHEET_NAME = "Email"
EMAIL_ATTACHMENT_SHEET_PREFIX = "Email Attachment"
PDF_DOCUMENT_SHEET_NAME = "PDF"
TEXT_TABLE_DELIMITERS = ",\t;|"
WORD_DOCUMENT_EXTRACTION_TIMEOUT_SECONDS = 90


class PurviewHtmlTableParser(HTMLParser):
	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self.header_keys: list[str] = []
		self.rows: list[dict[str, str]] = []
		self._stack: list[str] = []
		self._current_row: dict[str, str] | None = None
		self._row_depth = 0
		self._current_cell_key: str | None = None
		self._cell_depth = 0
		self._cell_text_parts: list[str] = []

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		attr_map = dict(attrs)
		self._stack.append(tag)

		if tag == "div" and attr_map.get("data-automationid") == "ColumnsHeaderColumn":
			key = (attr_map.get("data-item-key") or "").strip()
			if key and key != "ListPage-Expand" and key not in self.header_keys:
				self.header_keys.append(key)

		if tag == "div" and attr_map.get("data-automationid") == "DetailsRow":
			self._current_row = {}
			self._row_depth = len(self._stack)

		if self._current_row is not None and tag == "div" and attr_map.get("data-automationid") == "DetailsRowCell":
			key = (attr_map.get("data-automation-key") or "").strip()
			if key and key != "ListPage-Expand":
				self._current_cell_key = key
				self._cell_depth = len(self._stack)
				self._cell_text_parts = []

	def handle_data(self, data: str) -> None:
		if self._current_cell_key and data:
			text = data.strip()
			if text:
				self._cell_text_parts.append(text)

	def handle_endtag(self, tag: str) -> None:
		if not self._stack:
			return

		closing_current_cell = (
			self._current_cell_key is not None
			and tag == "div"
			and len(self._stack) == self._cell_depth
		)
		closing_current_row = (
			self._current_row is not None
			and tag == "div"
			and len(self._stack) == self._row_depth
		)

		self._stack.pop()

		if closing_current_cell:
			cell_value = " ".join(" ".join(self._cell_text_parts).split())
			self._current_row[self._current_cell_key] = cell_value
			self._current_cell_key = None
			self._cell_depth = 0
			self._cell_text_parts = []

		if closing_current_row:
			self.rows.append(self._current_row or {})
			self._current_row = None
			self._row_depth = 0


def get_openpyxl_module():
	global openpyxl, OPENPYXL_IMPORT_ATTEMPTED
	if openpyxl is not None:
		return openpyxl
	if OPENPYXL_IMPORT_ATTEMPTED:
		return None

	OPENPYXL_IMPORT_ATTEMPTED = True
	try:
		import openpyxl as imported_openpyxl
	except ImportError:
		return None

	openpyxl = imported_openpyxl
	return openpyxl


def get_pypdf_module():
	global pypdf, PYPDF_IMPORT_ATTEMPTED
	if pypdf is not None:
		return pypdf
	if PYPDF_IMPORT_ATTEMPTED:
		return None

	PYPDF_IMPORT_ATTEMPTED = True
	try:
		import pypdf as imported_pypdf
	except ImportError:
		return None

	pypdf = imported_pypdf
	return pypdf


def extract_purview_rows_from_html(html_text: str) -> tuple[list[str], list[list[str]]]:
	parser = PurviewHtmlTableParser()
	parser.feed(html_text)

	available_keys = [key for key in parser.header_keys if key in PURVIEW_COLUMN_LABELS]
	if not available_keys and parser.rows:
		for key in PURVIEW_COLUMN_ORDER:
			if any(key in row for row in parser.rows):
				available_keys.append(key)
		for row in parser.rows:
			for key in row:
				if key not in available_keys and key in PURVIEW_COLUMN_LABELS:
					available_keys.append(key)

	ordered_keys = [key for key in PURVIEW_COLUMN_ORDER if key in available_keys]
	for key in available_keys:
		if key not in ordered_keys:
			ordered_keys.append(key)

	headers = [PURVIEW_COLUMN_LABELS.get(key, key) for key in ordered_keys]
	rows = []
	for row in parser.rows:
		values = [row.get(key, "") for key in ordered_keys]
		if any(value.strip() for value in values):
			rows.append(values)

	if not headers:
		headers = ["Content"]
	if not rows:
		rows = [["No Purview rows were extracted from the downloaded HTML."]]

	return headers, rows


def _html_class_tokens(class_attr: str | None) -> set[str]:
	return {token for token in str(class_attr or "").split() if token}


def _extract_element_text(element: ET.Element | None) -> str:
	if element is None:
		return ""
	return " ".join(" ".join(text for text in element.itertext()).split())


def _humanize_automation_key(key: str) -> str:
	known_labels = {
		"SITName": "Sensitive information type",
		"MatchedSensitiveContent": "Matched sensitive content",
		"Location": "Location",
		"SurroundingCharacters": "Surrounding characters",
	}
	if key in known_labels:
		return known_labels[key]

	humanized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key or ""))
	humanized = humanized.replace("_", " ").replace("-", " ")
	humanized = " ".join(humanized.split()).strip()
	if not humanized:
		return "Value"
	return humanized[:1].upper() + humanized[1:]


def _render_simple_html_table(headers: list[str], rows: list[list[str]]) -> str:
	cell_style = (
		"border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; "
		"vertical-align: top; white-space: pre-wrap;"
	)
	thead_html = ""
	if headers:
		thead_cells = "".join(f'<th style="{cell_style}">{xml_escape(header)}</th>' for header in headers)
		thead_html = f"<thead><tr>{thead_cells}</tr></thead>"

	body_rows = []
	for row in rows:
		cells = [str(cell or "") for cell in row]
		if not any(cell.strip() for cell in cells):
			continue
		row_html = "".join(f'<td style="{cell_style}">{xml_escape(cell)}</td>' for cell in cells)
		body_rows.append(f"<tr>{row_html}</tr>")

	if not body_rows:
		return ""

	return (
		'<table class="captured-details-table" '
		'style="border-collapse: collapse; width: 100%; table-layout: fixed;">'
		f"{thead_html}<tbody>{''.join(body_rows)}</tbody></table>"
	)


def _normalize_table_header_text(value: str | None) -> str:
	return " ".join(str(value or "").split()).strip().lower()


def _normalize_workbook_lookup_value(value: str | None) -> str:
	return " ".join(str(value or "").split()).strip().lower()


def _normalize_digit_only_lookup_value(value: str | None) -> str:
	digits_only = re.sub(r"\D+", "", str(value or ""))
	return digits_only if len(digits_only) >= 4 else ""


def _stringify_workbook_cell_value(value: object) -> str:
	if value is None:
		return ""
	return str(value).strip()


def _trim_trailing_empty_values(values: list[str]) -> list[str]:
	last_non_empty_index = 0
	for index, value in enumerate(values, start=1):
		if str(value).strip():
			last_non_empty_index = index
	return values[:last_non_empty_index]


def _excel_column_label(column_index: int) -> str:
	if column_index < 1:
		return "Column"

	label = ""
	remaining = column_index
	while remaining > 0:
		remaining, remainder = divmod(remaining - 1, 26)
		label = chr(65 + remainder) + label
	return label


def _excel_column_index(column_label: str) -> int:
	normalized_label = str(column_label or "").strip().upper()
	if not normalized_label or not normalized_label.isalpha():
		return 0

	column_index = 0
	for character in normalized_label:
		column_index = (column_index * 26) + (ord(character) - 64)
	return column_index


def _is_excel_workbook_path(file_path: str) -> bool:
	return str(file_path or "").lower().endswith(ALERT_WORKBOOK_SUFFIXES)


def _is_delimited_text_source_path(file_path: str) -> bool:
	return str(file_path or "").lower().endswith(ALERT_TEXT_SOURCE_SUFFIXES)


def _is_word_document_source_path(file_path: str) -> bool:
	return str(file_path or "").lower().endswith(ALERT_WORD_DOCUMENT_SUFFIXES)


def _is_email_message_source_path(file_path: str) -> bool:
	return str(file_path or "").lower().endswith(ALERT_EMAIL_MESSAGE_SUFFIXES)


def _is_pdf_document_source_path(file_path: str) -> bool:
	return str(file_path or "").lower().endswith(ALERT_PDF_DOCUMENT_SUFFIXES)


def _split_document_text_rows(text: str) -> list[str]:
	normalized_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
	return [
		line
		for line in (
			" ".join(raw_line.split()).strip()
			for raw_line in normalized_text.split("\n")
		)
		if line
	]


def _normalize_virtual_sheet_name(value: str | None, fallback: str) -> str:
	normalized_value = " ".join(str(value or "").replace("!", " ").split()).strip()
	return normalized_value or fallback


def _is_email_attachment_sheet_name(sheet_name: str | None) -> bool:
	return str(sheet_name or "").startswith(f"{EMAIL_ATTACHMENT_SHEET_PREFIX}:")


def _build_email_attachment_sheet_name(file_name: str, nested_name: str = "") -> str:
	attachment_name = _normalize_virtual_sheet_name(file_name, "Attachment")
	base_sheet_name = f"{EMAIL_ATTACHMENT_SHEET_PREFIX}: {attachment_name}"
	if not nested_name:
		return base_sheet_name
	return f"{base_sheet_name} / {_normalize_virtual_sheet_name(nested_name, 'Content')}"


def _get_unique_virtual_sheet_name(existing_sheet_names: set[str], desired_name: str) -> str:
	if desired_name not in existing_sheet_names:
		existing_sheet_names.add(desired_name)
		return desired_name

	for duplicate_index in range(2, 1000):
		candidate_name = f"{desired_name} ({duplicate_index})"
		if candidate_name in existing_sheet_names:
			continue
		existing_sheet_names.add(candidate_name)
		return candidate_name

	existing_sheet_names.add(desired_name)
	return desired_name


def _convert_html_fragment_to_plain_text(html_fragment: str) -> str:
	normalized_html = str(html_fragment or "")
	if not normalized_html.strip():
		return ""

	normalized_html = re.sub(r"(?is)<(script|style)\b.*?>.*?</\\1>", " ", normalized_html)
	for pattern in (
		r"(?i)<br\s*/?>",
		r"(?i)</p\s*>",
		r"(?i)</div\s*>",
		r"(?i)</li\s*>",
		r"(?i)</tr\s*>",
		r"(?i)</h[1-6]\s*>",
	):
		normalized_html = re.sub(pattern, "\n", normalized_html)

	normalized_html = re.sub(r"(?s)<[^>]+>", " ", normalized_html)
	normalized_html = html_unescape(normalized_html)
	return "\n".join(_split_document_text_rows(normalized_html))


def _extract_email_message_part_text(message_part) -> str:
	try:
		part_content = message_part.get_content()
	except Exception:
		part_content = message_part.get_payload(decode=True)

	if isinstance(part_content, bytes):
		charset = message_part.get_content_charset() or "utf-8"
		try:
			return part_content.decode(charset, errors="ignore")
		except LookupError:
			return part_content.decode("utf-8", errors="ignore")

	return str(part_content or "")


def _decode_email_attachment_text(payload_bytes: bytes) -> str:
	for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
		try:
			return payload_bytes.decode(encoding)
		except UnicodeDecodeError:
			continue
	return payload_bytes.decode("utf-8", errors="ignore")


def _extract_email_attachment_text_lines(
	file_name: str,
	payload_bytes: bytes,
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	decoded_text = _decode_email_attachment_text(payload_bytes)
	row_entries = [
		(line_index, [f"Line {line_index}", line_text])
		for line_index, line_text in enumerate(_split_document_text_rows(decoded_text), start=1)
	]
	if not row_entries:
		return []
	return [(_build_email_attachment_sheet_name(file_name), ["Location", "Content"], row_entries)]


def _extract_email_attachment_delimited_rows(
	file_name: str,
	payload_bytes: bytes,
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	decoded_text = _decode_email_attachment_text(payload_bytes)
	if not decoded_text.strip():
		return []

	source_handle = io.StringIO(decoded_text, newline="")
	sample = source_handle.read(8192)
	source_handle.seek(0)
	sniffer = csv.Sniffer()
	has_header = False
	try:
		dialect = sniffer.sniff(sample, delimiters=TEXT_TABLE_DELIMITERS)
		source_reader = csv.reader(source_handle, dialect)
	except csv.Error:
		delimiter_scores = {
			delimiter: sample.count(delimiter)
			for delimiter in TEXT_TABLE_DELIMITERS
		}
		delimiter = max(delimiter_scores, key=delimiter_scores.get) if sample else ","
		if not delimiter_scores.get(delimiter):
			delimiter = "," if str(file_name or "").lower().endswith(".csv") else "\t"
		source_reader = csv.reader(source_handle, delimiter=delimiter)

	if sample:
		try:
			has_header = sniffer.has_header(sample)
		except csv.Error:
			has_header = False

	headers: list[str] = []
	row_entries: list[tuple[int, list[str]]] = []
	for row_index, row in enumerate(source_reader, start=1):
		row_text_values = _trim_trailing_empty_values([
			_stringify_workbook_cell_value(cell)
			for cell in row
		])
		if not row_text_values:
			continue
		if row_index == 1 and has_header:
			headers = [
				value or f"Column {_excel_column_label(index)}"
				for index, value in enumerate(row_text_values, start=1)
			]
			continue
		if not headers:
			headers = [
				f"Column {_excel_column_label(index)}"
				for index in range(1, len(row_text_values) + 1)
			]
		row_entries.append((row_index, row_text_values))

	if not row_entries:
		return []

	return [(_build_email_attachment_sheet_name(file_name), headers, row_entries)]


def _extract_email_attachment_workbook_rows(
	file_name: str,
	payload_bytes: bytes,
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	openpyxl_module = get_openpyxl_module()
	if openpyxl_module is None:
		return []

	try:
		workbook = openpyxl_module.load_workbook(io.BytesIO(payload_bytes), data_only=True, read_only=True)
	except Exception:
		return []

	attachment_sheets: list[tuple[str, list[str], list[tuple[int, list[str]]]]] = []
	try:
		for sheet in workbook.worksheets:
			headers: list[str] = []
			row_entries: list[tuple[int, list[str]]] = []
			for row in sheet.iter_rows():
				if not row:
					continue
				row_number = _get_workbook_row_index(row)
				if row_number < 1:
					continue

				row_text_values = _trim_trailing_empty_values([
					_stringify_workbook_cell_value(cell.value)
					for cell in row
				])
				if not row_text_values:
					continue

				if not headers:
					headers = [
						value or f"Column {_excel_column_label(index)}"
						for index, value in enumerate(row_text_values, start=1)
					]

				row_entries.append((row_number, row_text_values))

			if not row_entries:
				continue
			if not headers:
				headers = [f"Column {_excel_column_label(index)}" for index in range(1, len(row_entries[0][1]) + 1)]

			attachment_sheets.append((
				_build_email_attachment_sheet_name(file_name, sheet.title),
				headers,
				row_entries,
			))
	finally:
		workbook.close()

	return attachment_sheets


def _extract_email_attachment_from_temp_file(
	file_name: str,
	payload_bytes: bytes,
	suffix: str,
	extractor: Callable[[str], list],
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	temp_path = ""
	try:
		with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as temp_file:
			temp_file.write(payload_bytes)
			temp_path = temp_file.name

		extracted_rows = extractor(temp_path)
	finally:
		if temp_path:
			try:
				os.remove(temp_path)
			except OSError:
				pass

	row_entries: list[tuple[int, list[str]]] = []
	for row_index, row in enumerate(extracted_rows, start=1):
		row_values = [str(value or "") for value in (row if isinstance(row, list) else [row])]
		if not any(value.strip() for value in row_values):
			continue
		if len(row_values) == 1:
			row_entries.append((row_index, [f"Entry {row_index}", row_values[0]]))
			continue
		row_entries.append((row_index, [row_values[0], " | ".join(value for value in row_values[1:] if value)]))

	if not row_entries:
		return []

	return [(_build_email_attachment_sheet_name(file_name), ["Location", "Content"], row_entries)]


def _extract_email_attachment_sheet_data(
	message_part,
	attachment_index: int,
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	payload_bytes = message_part.get_payload(decode=True) or b""
	if not payload_bytes:
		return []

	file_name = _normalize_virtual_sheet_name(
		message_part.get_filename(),
		f"Attachment {attachment_index}",
	)
	lower_file_name = file_name.lower()
	if lower_file_name.endswith((".htm", ".html")):
		return []
	if lower_file_name.endswith(ALERT_WORKBOOK_SUFFIXES):
		return _extract_email_attachment_workbook_rows(file_name, payload_bytes)
	if lower_file_name.endswith(ALERT_TEXT_SOURCE_SUFFIXES):
		return _extract_email_attachment_delimited_rows(file_name, payload_bytes)
	if lower_file_name.endswith(ALERT_WORD_DOCUMENT_SUFFIXES):
		return _extract_email_attachment_from_temp_file(
			file_name,
			payload_bytes,
			os.path.splitext(file_name)[1],
			lambda temp_path: [[paragraph] for paragraph in _extract_word_document_paragraphs(temp_path)],
		)
	if lower_file_name.endswith(ALERT_PDF_DOCUMENT_SUFFIXES):
		return _extract_email_attachment_from_temp_file(
			file_name,
			payload_bytes,
			os.path.splitext(file_name)[1],
			_extract_pdf_document_rows,
		)

	content_type = str(message_part.get_content_type() or "").lower()
	if content_type == "text/plain":
		return _extract_email_attachment_text_lines(file_name, payload_bytes)

	return []


def _extract_email_message_sheet_data(
	file_path: str,
) -> list[tuple[str, list[str], list[tuple[int, list[str]]]]]:
	if not _is_email_message_source_path(file_path):
		return []

	try:
		with open(file_path, "rb") as email_file:
			message = BytesParser(policy=policy.default).parse(email_file)
	except (OSError, ValueError):
		return []

	body_row_entries: list[tuple[int, list[str]]] = []
	seen_body_rows: set[tuple[str, str]] = set()
	body_row_index = 0

	def append_body_rows(section_name: str, text_value: str) -> None:
		nonlocal body_row_index
		for line_text in _split_document_text_rows(text_value):
			row_key = (section_name, line_text)
			if row_key in seen_body_rows:
				continue
			seen_body_rows.add(row_key)
			body_row_index += 1
			body_row_entries.append((body_row_index, [section_name, line_text]))

	for header_name in ("Subject", "From", "To", "Cc"):
		header_value = " ".join(str(message.get(header_name) or "").split()).strip()
		if header_value:
			append_body_rows(header_name, header_value)

	plain_body_count = 0
	html_body_count = 0
	sheet_data: list[tuple[str, list[str], list[tuple[int, list[str]]]]] = []
	used_sheet_names = {EMAIL_MESSAGE_SHEET_NAME}
	message_parts = list(message.walk()) if message.is_multipart() else [message]
	attachment_index = 0
	for message_part in message_parts:
		if message_part.is_multipart():
			continue

		if str(message_part.get_content_disposition() or "").lower() == "attachment":
			attachment_index += 1
			for sheet_name, headers, row_entries in _extract_email_attachment_sheet_data(message_part, attachment_index):
				if not row_entries:
					continue
				sheet_data.append((
					_get_unique_virtual_sheet_name(used_sheet_names, sheet_name),
					headers,
					row_entries,
				))
			continue

		content_type = str(message_part.get_content_type() or "").lower()
		if content_type not in {"text/plain", "text/html"}:
			continue

		part_text = _extract_email_message_part_text(message_part)
		if content_type == "text/html":
			part_text = _convert_html_fragment_to_plain_text(part_text)
			html_body_count += 1
			section_name = "Body HTML" if html_body_count > 1 else "Body"
		else:
			plain_body_count += 1
			section_name = "Body" if plain_body_count == 1 else f"Body {plain_body_count}"

		append_body_rows(section_name, part_text)

	sheet_data.insert(0, (EMAIL_MESSAGE_SHEET_NAME, ["Section", "Content"], body_row_entries))
	return sheet_data


def _extract_email_message_rows(file_path: str) -> list[list[str]]:
	for sheet_name, _, row_entries in _extract_email_message_sheet_data(file_path):
		if sheet_name != EMAIL_MESSAGE_SHEET_NAME:
			continue
		return [row_values for _, row_values in row_entries]
	return []


def _extract_pdf_document_rows(file_path: str) -> list[list[str]]:
	if not _is_pdf_document_source_path(file_path):
		return []

	pypdf_module = get_pypdf_module()
	if pypdf_module is None:
		return []

	try:
		pdf_reader = pypdf_module.PdfReader(file_path)
	except Exception:
		return []

	rows: list[list[str]] = []
	for page_number, pdf_page in enumerate(pdf_reader.pages, start=1):
		try:
			page_text = pdf_page.extract_text() or ""
		except Exception:
			continue

		for line_text in _split_document_text_rows(page_text):
			rows.append([f"Page {page_number}", line_text])

	return rows


def _extract_word_document_paragraphs(file_path: str) -> list[str]:
	if os.name != "nt" or not _is_word_document_source_path(file_path):
		return []

	powershell_script = r"""
	param([string]$DocumentPath)
	$ErrorActionPreference = 'Stop'
	[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
	$OutputEncoding = [System.Text.Encoding]::UTF8
	if (-not $DocumentPath) {
		throw 'Missing document path.'
	}

	$word = $null
	$document = $null
	try {
		$word = New-Object -ComObject Word.Application
		$word.Visible = $false
		$word.DisplayAlerts = 0
		$document = $word.Documents.Open($DocumentPath, $false, $true)

		$paragraphs = New-Object System.Collections.Generic.List[string]
		foreach ($paragraph in @($document.Paragraphs)) {
			$text = [string]$paragraph.Range.Text
			$text = $text -replace '[\u0000-\u001f]+', ' '
			$text = $text -replace '\s+', ' '
			$text = $text.Trim()
			if ($text) {
				[void]$paragraphs.Add($text)
			}
		}

		$paragraphs | ConvertTo-Json -Compress -Depth 2
	}
	finally {
		if ($document -ne $null) {
			$document.Close([ref]$false)
		}
		if ($word -ne $null) {
			$word.Quit()
		}
		[System.GC]::Collect()
		[System.GC]::WaitForPendingFinalizers()
	}
	"""

	script_path = ""
	try:
		with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as script_file:
			script_file.write(powershell_script)
			script_path = script_file.name

		result = subprocess.run(
			[
				"powershell.exe",
				"-NoProfile",
				"-NonInteractive",
				"-ExecutionPolicy",
				"Bypass",
				"-File",
				script_path,
				"-DocumentPath",
				file_path,
			],
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="ignore",
			timeout=WORD_DOCUMENT_EXTRACTION_TIMEOUT_SECONDS,
			check=False,
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
		)
	except (OSError, subprocess.SubprocessError):
		return []
	finally:
		if script_path:
			try:
				os.remove(script_path)
			except OSError:
				pass

	if result.returncode != 0:
		return []

	output_text = result.stdout.strip()
	if not output_text:
		return []

	try:
		parsed_output = json.loads(output_text)
	except json.JSONDecodeError:
		return []

	if isinstance(parsed_output, str):
		paragraphs = [parsed_output]
	elif isinstance(parsed_output, list):
		paragraphs = [paragraph for paragraph in parsed_output if isinstance(paragraph, str)]
	else:
		return []

	return [paragraph for paragraph in (" ".join(paragraph.split()).strip() for paragraph in paragraphs) if paragraph]


def _cache_alert_word_document_paragraphs(
	workbook_cache: AlertWorkbookLookupCacheEntry,
) -> AlertWorkbookLookupCacheEntry:
	if WORD_DOCUMENT_SHEET_NAME in workbook_cache.sheet_headers:
		return workbook_cache

	workbook_cache.sheet_headers[WORD_DOCUMENT_SHEET_NAME] = ["Paragraph"]
	paragraphs = _extract_word_document_paragraphs(workbook_cache.workbook_path)
	for paragraph_index, paragraph_text in enumerate(paragraphs, start=1):
		workbook_cache.row_values[(WORD_DOCUMENT_SHEET_NAME, paragraph_index)] = [paragraph_text]

	return workbook_cache


def _cache_alert_email_message_rows(
	workbook_cache: AlertWorkbookLookupCacheEntry,
) -> AlertWorkbookLookupCacheEntry:
	if EMAIL_MESSAGE_SHEET_NAME in workbook_cache.sheet_headers:
		return workbook_cache

	for sheet_name, headers, row_entries in _extract_email_message_sheet_data(workbook_cache.workbook_path):
		workbook_cache.sheet_headers[sheet_name] = list(headers)
		for row_index, row_values in row_entries:
			workbook_cache.row_values[(sheet_name, row_index)] = row_values

	if EMAIL_MESSAGE_SHEET_NAME not in workbook_cache.sheet_headers:
		workbook_cache.sheet_headers[EMAIL_MESSAGE_SHEET_NAME] = ["Section", "Content"]

	return workbook_cache


def _cache_alert_pdf_document_rows(
	workbook_cache: AlertWorkbookLookupCacheEntry,
) -> AlertWorkbookLookupCacheEntry:
	if PDF_DOCUMENT_SHEET_NAME in workbook_cache.sheet_headers:
		return workbook_cache

	workbook_cache.sheet_headers[PDF_DOCUMENT_SHEET_NAME] = ["Page", "Content"]
	for row_index, row_values in enumerate(_extract_pdf_document_rows(workbook_cache.workbook_path), start=1):
		workbook_cache.row_values[(PDF_DOCUMENT_SHEET_NAME, row_index)] = row_values

	return workbook_cache


def _iter_cached_sheet_rows(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
) -> list[tuple[int, list[str]]]:
	return sorted(
		(
			row_index,
			row_values,
		)
		for (cached_sheet_name, row_index), row_values in workbook_cache.row_values.items()
		if cached_sheet_name == sheet_name and row_values
	)


def _get_email_message_sheet_names(workbook_cache: AlertWorkbookLookupCacheEntry) -> list[str]:
	return [
		sheet_name
		for sheet_name in workbook_cache.sheet_headers
		if sheet_name == EMAIL_MESSAGE_SHEET_NAME or _is_email_attachment_sheet_name(sheet_name)
	]


def _populate_cached_document_lookup_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
	sheet_name: str,
	cache_loader: Callable[[AlertWorkbookLookupCacheEntry], AlertWorkbookLookupCacheEntry],
) -> AlertWorkbookLookupCacheEntry:
	pending_values = {
		normalized_value
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_value in [_normalize_workbook_lookup_value(matched_sensitive_content)]
		if normalized_value and normalized_value not in workbook_cache.searched_values
	}
	pending_digits = {
		normalized_digits
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_digits in [_normalize_digit_only_lookup_value(matched_sensitive_content)]
		if normalized_digits and normalized_digits not in workbook_cache.searched_digits
	}
	if not pending_values and not pending_digits:
		return workbook_cache

	workbook_cache = cache_loader(workbook_cache)
	for row_index, row_text_values in _iter_cached_sheet_rows(workbook_cache, sheet_name):
		for column_index, cell_text in enumerate(row_text_values, start=1):
			location = (
				f"{workbook_cache.workbook_file_name} - "
				f"{sheet_name}!{_excel_column_label(column_index)}{row_index}"
			)

			normalized_value = _normalize_workbook_lookup_value(cell_text)
			if normalized_value:
				for pending_value in pending_values:
					if pending_value not in normalized_value:
						continue
					workbook_cache.value_lookup.setdefault(pending_value, [])
					if location not in workbook_cache.value_lookup[pending_value]:
						workbook_cache.value_lookup[pending_value].append(location)

			normalized_digits = _normalize_digit_only_lookup_value(cell_text)
			if normalized_digits:
				for pending_digit in pending_digits:
					if pending_digit not in normalized_digits:
						continue
					workbook_cache.digit_lookup.setdefault(pending_digit, [])
					if location not in workbook_cache.digit_lookup[pending_digit]:
						workbook_cache.digit_lookup[pending_digit].append(location)

	workbook_cache.searched_values.update(pending_values)
	workbook_cache.searched_digits.update(pending_digits)
	return workbook_cache


def _get_alert_lookup_source_priority(file_name: str) -> int:
	lower_file_name = str(file_name or "").lower()
	if lower_file_name.endswith(ALERT_WORKBOOK_SUFFIXES):
		return 0
	if lower_file_name.endswith(ALERT_TEXT_SOURCE_SUFFIXES):
		return 1
	if lower_file_name.endswith(ALERT_WORD_DOCUMENT_SUFFIXES):
		return 2
	if lower_file_name.endswith(ALERT_EMAIL_MESSAGE_SUFFIXES):
		return 3
	if lower_file_name.endswith(ALERT_PDF_DOCUMENT_SUFFIXES):
		return 4
	return 5


def _find_alert_lookup_source_paths(alert_name: str) -> list[str]:
	artifact_folder = get_alert_artifact_folder_path(alert_name)
	if not os.path.isdir(artifact_folder):
		return []

	candidate_paths: list[tuple[int, float, str, str]] = []
	with os.scandir(artifact_folder) as entries:
		for entry in entries:
			if not entry.is_file():
				continue
			if not entry.name.lower().endswith(ALERT_LOOKUP_SOURCE_SUFFIXES):
				continue
			try:
				entry_stats = entry.stat()
			except OSError:
				continue
			candidate_paths.append((
				_get_alert_lookup_source_priority(entry.name),
				-entry_stats.st_mtime,
				entry.name.lower(),
				entry.path,
			))

	candidate_paths.sort()
	return [path for _, _, _, path in candidate_paths]


def _find_latest_alert_workbook_path(alert_name: str) -> str | None:
	lookup_source_paths = _find_alert_lookup_source_paths(alert_name)
	return lookup_source_paths[0] if lookup_source_paths else None


def _cache_workbook_sheet_headers(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	row_text_values: list[str],
) -> None:
	if sheet_name in workbook_cache.sheet_headers or not row_text_values:
		return

	workbook_cache.sheet_headers[sheet_name] = [
		value or f"Column {_excel_column_label(index)}"
		for index, value in enumerate(row_text_values, start=1)
	]


def _cache_generic_sheet_headers(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	column_count: int,
) -> None:
	if sheet_name in workbook_cache.sheet_headers or column_count < 1:
		return

	workbook_cache.sheet_headers[sheet_name] = [
		f"Column {_excel_column_label(index)}"
		for index in range(1, column_count + 1)
	]


def _get_workbook_row_index(row: tuple[object, ...]) -> int:
	for cell in row:
		row_index = getattr(cell, "row", 0)
		if isinstance(row_index, int) and row_index > 0:
			return row_index
	return 0


def _open_delimited_text_source(file_path: str):
	last_error: Exception | None = None
	default_delimiter = "," if str(file_path or "").lower().endswith(".csv") else "\t"

	for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
		source_handle = None
		try:
			source_handle = open(file_path, "r", encoding=encoding, newline="")
			sample = source_handle.read(8192)
			source_handle.seek(0)

			sniffer = csv.Sniffer()
			has_header = False
			try:
				dialect = sniffer.sniff(sample, delimiters=TEXT_TABLE_DELIMITERS)
				source_reader = csv.reader(source_handle, dialect)
			except csv.Error:
				delimiter_scores = {
					delimiter: sample.count(delimiter)
					for delimiter in TEXT_TABLE_DELIMITERS
				}
				delimiter = max(delimiter_scores, key=delimiter_scores.get) if sample else default_delimiter
				if not delimiter_scores.get(delimiter):
					delimiter = default_delimiter
				source_reader = csv.reader(source_handle, delimiter=delimiter)

			if sample:
				try:
					has_header = sniffer.has_header(sample)
				except csv.Error:
					has_header = False

			return source_handle, source_reader, has_header
		except UnicodeDecodeError as exc:
			last_error = exc
			if source_handle is not None:
				source_handle.close()
			continue
		except OSError:
			if source_handle is not None:
				source_handle.close()
			raise

	if last_error is not None:
		raise last_error
	raise OSError(f"Unable to open delimited text source: {file_path}")


def _get_alert_workbook_cache_for_path(workbook_path: str) -> AlertWorkbookLookupCacheEntry | None:
	if not workbook_path:
		return None
	if _is_excel_workbook_path(workbook_path) and get_openpyxl_module() is None:
		return None

	try:
		workbook_mtime = os.path.getmtime(workbook_path)
	except OSError:
		return None

	cache_key = (workbook_path, workbook_mtime)
	if cache_key in ALERT_WORKBOOK_LOOKUP_CACHE:
		return ALERT_WORKBOOK_LOOKUP_CACHE[cache_key]

	workbook_file_name = os.path.basename(workbook_path)

	cache_entry = AlertWorkbookLookupCacheEntry(
		workbook_path=workbook_path,
		workbook_file_name=workbook_file_name,
		value_lookup={},
		digit_lookup={},
		sheet_headers={},
		row_values={},
		searched_values=set(),
		searched_digits=set(),
	)
	ALERT_WORKBOOK_LOOKUP_CACHE[cache_key] = cache_entry
	return cache_entry


def _get_alert_workbook_caches(alert_name: str) -> list[AlertWorkbookLookupCacheEntry]:
	workbook_caches: list[AlertWorkbookLookupCacheEntry] = []
	for workbook_path in _find_alert_lookup_source_paths(alert_name):
		workbook_cache = _get_alert_workbook_cache_for_path(workbook_path)
		if workbook_cache is not None:
			workbook_caches.append(workbook_cache)
	return workbook_caches


def _get_alert_workbook_cache(alert_name: str) -> AlertWorkbookLookupCacheEntry | None:
	workbook_caches = _get_alert_workbook_caches(alert_name)
	return workbook_caches[0] if workbook_caches else None


def _get_alert_workbook_cache_by_name(
	alert_name: str,
	workbook_name: str,
) -> AlertWorkbookLookupCacheEntry | None:
	if not workbook_name:
		return None
	for workbook_cache in _get_alert_workbook_caches(alert_name):
		if workbook_cache.workbook_file_name == workbook_name:
			return workbook_cache
	return None


def _populate_alert_delimited_text_lookup_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
) -> AlertWorkbookLookupCacheEntry:
	pending_values = {
		normalized_value
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_value in [_normalize_workbook_lookup_value(matched_sensitive_content)]
		if normalized_value and normalized_value not in workbook_cache.searched_values
	}
	pending_digits = {
		normalized_digits
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_digits in [_normalize_digit_only_lookup_value(matched_sensitive_content)]
		if normalized_digits and normalized_digits not in workbook_cache.searched_digits
	}
	if not pending_values and not pending_digits:
		return workbook_cache

	try:
		source_handle, source_reader, has_header = _open_delimited_text_source(workbook_cache.workbook_path)
	except Exception:
		return workbook_cache

	try:
		for row_index, row in enumerate(source_reader, start=1):
			row_text_values = _trim_trailing_empty_values([
				_stringify_workbook_cell_value(cell)
				for cell in row
			])
			if not row_text_values:
				continue

			if row_index == 1 and has_header:
				_cache_workbook_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, row_text_values)
				continue

			if TEXT_TABLE_SHEET_NAME not in workbook_cache.sheet_headers:
				_cache_generic_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, len(row_text_values))

			matched_row = False
			for column_index, cell_text in enumerate(row_text_values, start=1):
				location = (
					f"{workbook_cache.workbook_file_name} - "
					f"{TEXT_TABLE_SHEET_NAME}!{_excel_column_label(column_index)}{row_index}"
				)

				normalized_value = _normalize_workbook_lookup_value(cell_text)
				if normalized_value and normalized_value in pending_values:
					workbook_cache.value_lookup.setdefault(normalized_value, [])
					if location not in workbook_cache.value_lookup[normalized_value]:
						workbook_cache.value_lookup[normalized_value].append(location)
					matched_row = True

				normalized_digits = _normalize_digit_only_lookup_value(cell_text)
				if normalized_digits and normalized_digits in pending_digits:
					workbook_cache.digit_lookup.setdefault(normalized_digits, [])
					if location not in workbook_cache.digit_lookup[normalized_digits]:
						workbook_cache.digit_lookup[normalized_digits].append(location)
					matched_row = True

			if matched_row:
				workbook_cache.row_values[(TEXT_TABLE_SHEET_NAME, row_index)] = row_text_values
	finally:
		source_handle.close()

	workbook_cache.searched_values.update(pending_values)
	workbook_cache.searched_digits.update(pending_digits)
	return workbook_cache


def _populate_alert_word_document_lookup_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
) -> AlertWorkbookLookupCacheEntry:
	return _populate_cached_document_lookup_for_values(
		workbook_cache,
		matched_sensitive_contents,
		WORD_DOCUMENT_SHEET_NAME,
		_cache_alert_word_document_paragraphs,
	)


def _populate_alert_email_message_lookup_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
) -> AlertWorkbookLookupCacheEntry:
	pending_values = {
		normalized_value
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_value in [_normalize_workbook_lookup_value(matched_sensitive_content)]
		if normalized_value and normalized_value not in workbook_cache.searched_values
	}
	pending_digits = {
		normalized_digits
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_digits in [_normalize_digit_only_lookup_value(matched_sensitive_content)]
		if normalized_digits and normalized_digits not in workbook_cache.searched_digits
	}
	if not pending_values and not pending_digits:
		return workbook_cache

	workbook_cache = _cache_alert_email_message_rows(workbook_cache)
	for sheet_name in _get_email_message_sheet_names(workbook_cache):
		for row_index, row_text_values in _iter_cached_sheet_rows(workbook_cache, sheet_name):
			for column_index, cell_text in enumerate(row_text_values, start=1):
				location = (
					f"{workbook_cache.workbook_file_name} - "
					f"{sheet_name}!{_excel_column_label(column_index)}{row_index}"
				)

				normalized_value = _normalize_workbook_lookup_value(cell_text)
				if normalized_value:
					for pending_value in pending_values:
						if pending_value not in normalized_value:
							continue
						workbook_cache.value_lookup.setdefault(pending_value, [])
						if location not in workbook_cache.value_lookup[pending_value]:
							workbook_cache.value_lookup[pending_value].append(location)

				normalized_digits = _normalize_digit_only_lookup_value(cell_text)
				if normalized_digits:
					for pending_digit in pending_digits:
						if pending_digit not in normalized_digits:
							continue
						workbook_cache.digit_lookup.setdefault(pending_digit, [])
						if location not in workbook_cache.digit_lookup[pending_digit]:
							workbook_cache.digit_lookup[pending_digit].append(location)

	workbook_cache.searched_values.update(pending_values)
	workbook_cache.searched_digits.update(pending_digits)
	return workbook_cache


def _populate_alert_pdf_document_lookup_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
) -> AlertWorkbookLookupCacheEntry:
	return _populate_cached_document_lookup_for_values(
		workbook_cache,
		matched_sensitive_contents,
		PDF_DOCUMENT_SHEET_NAME,
		_cache_alert_pdf_document_rows,
	)


def _populate_workbook_cache_for_values(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_contents: list[str],
) -> AlertWorkbookLookupCacheEntry:
	if _is_delimited_text_source_path(workbook_cache.workbook_path):
		return _populate_alert_delimited_text_lookup_for_values(workbook_cache, matched_sensitive_contents)
	if _is_word_document_source_path(workbook_cache.workbook_path):
		return _populate_alert_word_document_lookup_for_values(workbook_cache, matched_sensitive_contents)
	if _is_email_message_source_path(workbook_cache.workbook_path):
		return _populate_alert_email_message_lookup_for_values(workbook_cache, matched_sensitive_contents)
	if _is_pdf_document_source_path(workbook_cache.workbook_path):
		return _populate_alert_pdf_document_lookup_for_values(workbook_cache, matched_sensitive_contents)

	openpyxl_module = get_openpyxl_module()
	if openpyxl_module is None:
		return workbook_cache

	pending_values = {
		normalized_value
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_value in [_normalize_workbook_lookup_value(matched_sensitive_content)]
		if normalized_value and normalized_value not in workbook_cache.searched_values
	}
	pending_digits = {
		normalized_digits
		for matched_sensitive_content in matched_sensitive_contents
		for normalized_digits in [_normalize_digit_only_lookup_value(matched_sensitive_content)]
		if normalized_digits and normalized_digits not in workbook_cache.searched_digits
	}
	if not pending_values and not pending_digits:
		return workbook_cache

	try:
		workbook = openpyxl_module.load_workbook(
			workbook_cache.workbook_path,
			data_only=True,
			read_only=True,
		)
	except Exception:
		return workbook_cache

	try:
		for sheet in workbook.worksheets:
			headers_cached = sheet.title in workbook_cache.sheet_headers
			for row in sheet.iter_rows():
				if not row:
					continue

				row_text_values: list[str] | None = None
				cell_text_values: list[str] | None = None
				matched_row = False

				if not headers_cached:
					cell_text_values = [
						_stringify_workbook_cell_value(cell.value)
						for cell in row
					]
					row_text_values = _trim_trailing_empty_values(cell_text_values)
					if row_text_values:
						_cache_workbook_sheet_headers(workbook_cache, sheet.title, row_text_values)
						headers_cached = True

				for index, cell in enumerate(row):
					if cell.value is None:
						continue

					cell_text = (
						cell_text_values[index]
						if cell_text_values is not None and index < len(cell_text_values)
						else _stringify_workbook_cell_value(cell.value)
					)
					location = f"{workbook_cache.workbook_file_name} - {sheet.title}!{cell.coordinate}"

					normalized_value = _normalize_workbook_lookup_value(cell_text)
					if normalized_value and normalized_value in pending_values:
						workbook_cache.value_lookup.setdefault(normalized_value, [])
						if location not in workbook_cache.value_lookup[normalized_value]:
							workbook_cache.value_lookup[normalized_value].append(location)
						matched_row = True

					normalized_digits = _normalize_digit_only_lookup_value(cell_text)
					if normalized_digits and normalized_digits in pending_digits:
						workbook_cache.digit_lookup.setdefault(normalized_digits, [])
						if location not in workbook_cache.digit_lookup[normalized_digits]:
							workbook_cache.digit_lookup[normalized_digits].append(location)
						matched_row = True

				if not matched_row:
					continue

				if row_text_values is None:
					row_text_values = _trim_trailing_empty_values([
						_stringify_workbook_cell_value(cell.value)
						for cell in row
					])
				row_index = _get_workbook_row_index(row)
				if row_text_values and row_index > 0:
					workbook_cache.row_values[(sheet.title, row_index)] = row_text_values
	finally:
		workbook.close()

	workbook_cache.searched_values.update(pending_values)
	workbook_cache.searched_digits.update(pending_digits)
	return workbook_cache


def _populate_alert_workbook_lookup_for_values(
	alert_name: str,
	matched_sensitive_contents: list[str],
) -> list[AlertWorkbookLookupCacheEntry]:
	workbook_caches = _get_alert_workbook_caches(alert_name)
	if not workbook_caches:
		return []
	return [
		_populate_workbook_cache_for_values(workbook_cache, matched_sensitive_contents)
		for workbook_cache in workbook_caches
	]


def _ensure_alert_delimited_text_row_cached(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	row_index: int,
) -> AlertWorkbookLookupCacheEntry:
	if sheet_name != TEXT_TABLE_SHEET_NAME or row_index < 1:
		return workbook_cache

	try:
		source_handle, source_reader, has_header = _open_delimited_text_source(workbook_cache.workbook_path)
	except Exception:
		return workbook_cache

	try:
		for current_row_index, row in enumerate(source_reader, start=1):
			row_text_values = _trim_trailing_empty_values([
				_stringify_workbook_cell_value(cell)
				for cell in row
			])
			if not row_text_values:
				continue

			if current_row_index == 1 and has_header:
				_cache_workbook_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, row_text_values)
				continue

			if TEXT_TABLE_SHEET_NAME not in workbook_cache.sheet_headers:
				_cache_generic_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, len(row_text_values))

			if current_row_index == row_index:
				workbook_cache.row_values[(TEXT_TABLE_SHEET_NAME, row_index)] = row_text_values
				break
	finally:
		source_handle.close()

	return workbook_cache


def _ensure_alert_word_document_row_cached(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	row_index: int,
) -> AlertWorkbookLookupCacheEntry:
	if sheet_name != WORD_DOCUMENT_SHEET_NAME or row_index < 1:
		return workbook_cache
	if (sheet_name, row_index) in workbook_cache.row_values:
		return workbook_cache
	return _cache_alert_word_document_paragraphs(workbook_cache)


def _ensure_alert_email_message_row_cached(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	row_index: int,
) -> AlertWorkbookLookupCacheEntry:
	if row_index < 1:
		return workbook_cache
	if sheet_name != EMAIL_MESSAGE_SHEET_NAME and not _is_email_attachment_sheet_name(sheet_name):
		return workbook_cache
	if (sheet_name, row_index) in workbook_cache.row_values:
		return workbook_cache
	return _cache_alert_email_message_rows(workbook_cache)


def _ensure_alert_pdf_document_row_cached(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	sheet_name: str,
	row_index: int,
) -> AlertWorkbookLookupCacheEntry:
	if sheet_name != PDF_DOCUMENT_SHEET_NAME or row_index < 1:
		return workbook_cache
	if (sheet_name, row_index) in workbook_cache.row_values:
		return workbook_cache
	return _cache_alert_pdf_document_rows(workbook_cache)


def _ensure_alert_workbook_row_cached(
	alert_name: str,
	workbook_name: str,
	sheet_name: str,
	row_index: int,
) -> AlertWorkbookLookupCacheEntry | None:
	workbook_cache = _get_alert_workbook_cache_by_name(alert_name, workbook_name)
	if workbook_cache is None:
		return None
	if row_index < 1:
		return workbook_cache
	if (sheet_name, row_index) in workbook_cache.row_values:
		return workbook_cache
	if _is_delimited_text_source_path(workbook_cache.workbook_path):
		return _ensure_alert_delimited_text_row_cached(workbook_cache, sheet_name, row_index)
	if _is_word_document_source_path(workbook_cache.workbook_path):
		return _ensure_alert_word_document_row_cached(workbook_cache, sheet_name, row_index)
	if _is_email_message_source_path(workbook_cache.workbook_path):
		return _ensure_alert_email_message_row_cached(workbook_cache, sheet_name, row_index)
	if _is_pdf_document_source_path(workbook_cache.workbook_path):
		return _ensure_alert_pdf_document_row_cached(workbook_cache, sheet_name, row_index)

	openpyxl_module = get_openpyxl_module()
	if openpyxl_module is None:
		return workbook_cache

	try:
		workbook = openpyxl_module.load_workbook(
			workbook_cache.workbook_path,
			data_only=True,
			read_only=True,
		)
	except Exception:
		return workbook_cache

	try:
		if sheet_name not in workbook.sheetnames:
			return workbook_cache

		sheet = workbook[sheet_name]
		if sheet_name not in workbook_cache.sheet_headers:
			for row in sheet.iter_rows():
				if not row:
					continue
				row_text_values = _trim_trailing_empty_values([
					_stringify_workbook_cell_value(cell.value)
					for cell in row
				])
				if row_text_values:
					_cache_workbook_sheet_headers(workbook_cache, sheet_name, row_text_values)
					break

		for row in sheet.iter_rows(min_row=row_index, max_row=row_index):
			if not row:
				continue
			row_text_values = _trim_trailing_empty_values([
				_stringify_workbook_cell_value(cell.value)
				for cell in row
			])
			if row_text_values:
				workbook_cache.row_values[(sheet_name, row_index)] = row_text_values
				break
	finally:
		workbook.close()

	return workbook_cache


def _get_classifier_matching_locations(
	workbook_caches: list[AlertWorkbookLookupCacheEntry],
	matched_sensitive_content: str,
) -> list[str]:
		matching_locations: list[str] = []
		normalized_value = _normalize_workbook_lookup_value(matched_sensitive_content)
		normalized_digits = _normalize_digit_only_lookup_value(matched_sensitive_content)

		for workbook_cache in workbook_caches:
			candidate_locations: list[str] = []
			if normalized_value:
				candidate_locations = list(workbook_cache.value_lookup.get(normalized_value, []))
			if not candidate_locations and normalized_digits:
				candidate_locations = list(workbook_cache.digit_lookup.get(normalized_digits, []))

			for location in candidate_locations:
				if location in matching_locations:
					continue
				matching_locations.append(location)
				if len(matching_locations) >= 3:
					return matching_locations

		return matching_locations


def _find_classifier_values_in_alert_workbook(
	alert_name: str,
	matched_sensitive_contents: list[str],
) -> dict[str, str]:
	workbook_caches = _populate_alert_workbook_lookup_for_values(
		alert_name,
		matched_sensitive_contents,
	)
	if not workbook_caches:
		return {}

	lookup_results: dict[str, str] = {}
	for matched_sensitive_content in matched_sensitive_contents:
		if matched_sensitive_content in lookup_results:
			continue
		lookup_results[matched_sensitive_content] = "; ".join(
			_get_classifier_matching_locations(workbook_caches, matched_sensitive_content)
		)

	return lookup_results


def _find_classifier_value_in_alert_workbook(alert_name: str, matched_sensitive_content: str) -> str:
	return _find_classifier_values_in_alert_workbook(
		alert_name,
		[matched_sensitive_content],
	).get(matched_sensitive_content, "")


def _find_matching_classifier_row_column_index(
	row_text_values: list[str],
	matched_sensitive_content: str,
	surrounding_characters: str,
) -> int:
	normalized_value = _normalize_workbook_lookup_value(matched_sensitive_content)
	normalized_digits = _normalize_digit_only_lookup_value(matched_sensitive_content)
	normalized_excerpt = _normalize_workbook_lookup_value(surrounding_characters)
	best_index = 0

	for index, cell_text in enumerate(row_text_values, start=1):
		normalized_cell_text = _normalize_workbook_lookup_value(cell_text)
		normalized_cell_digits = re.sub(r"\D+", "", str(cell_text or ""))

		if normalized_value and normalized_cell_text == normalized_value:
			return index
		if normalized_digits and normalized_cell_digits == normalized_digits:
			return index

		if not best_index and normalized_digits and normalized_cell_digits and normalized_digits in normalized_cell_digits:
			best_index = index
		if not best_index and normalized_value and normalized_cell_text and normalized_value in normalized_cell_text:
			best_index = index
		if not best_index and normalized_excerpt and normalized_cell_text and normalized_excerpt in normalized_cell_text:
			best_index = index

	row_text = _normalize_workbook_lookup_value(" ".join(row_text_values))
	row_digits = re.sub(r"\D+", "", " ".join(str(value or "") for value in row_text_values))
	if normalized_excerpt and normalized_excerpt in row_text:
		return best_index or 1
	if normalized_value and normalized_value in row_text:
		return best_index or 1
	if normalized_digits and normalized_digits in row_digits:
		return best_index or 1

	return 0


def _find_classifier_row_location_from_workbook_cache(
	workbook_cache: AlertWorkbookLookupCacheEntry,
	matched_sensitive_content: str,
	surrounding_characters: str,
) -> str:
	if _is_delimited_text_source_path(workbook_cache.workbook_path):
		try:
			source_handle, source_reader, has_header = _open_delimited_text_source(workbook_cache.workbook_path)
		except Exception:
			return ""

		try:
			for row_index, row in enumerate(source_reader, start=1):
				row_text_values = _trim_trailing_empty_values([
					_stringify_workbook_cell_value(cell)
					for cell in row
				])
				if not row_text_values:
					continue

				if row_index == 1 and has_header:
					_cache_workbook_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, row_text_values)
					continue

				if TEXT_TABLE_SHEET_NAME not in workbook_cache.sheet_headers:
					_cache_generic_sheet_headers(workbook_cache, TEXT_TABLE_SHEET_NAME, len(row_text_values))

				column_index = _find_matching_classifier_row_column_index(
					row_text_values,
					matched_sensitive_content,
					surrounding_characters,
				)
				if column_index < 1:
					continue

				workbook_cache.row_values[(TEXT_TABLE_SHEET_NAME, row_index)] = row_text_values
				return (
					f"{workbook_cache.workbook_file_name} - "
					f"{TEXT_TABLE_SHEET_NAME}!{_excel_column_label(column_index)}{row_index}"
				)
		finally:
			source_handle.close()

	if _is_word_document_source_path(workbook_cache.workbook_path):
		workbook_cache = _cache_alert_word_document_paragraphs(workbook_cache)
		for row_index, row_text_values in _iter_cached_sheet_rows(workbook_cache, WORD_DOCUMENT_SHEET_NAME):
			column_index = _find_matching_classifier_row_column_index(
				row_text_values,
				matched_sensitive_content,
				surrounding_characters,
			)
			if column_index > 0:
				return (
					f"{workbook_cache.workbook_file_name} - "
					f"{WORD_DOCUMENT_SHEET_NAME}!{_excel_column_label(column_index)}{row_index}"
				)

	if _is_email_message_source_path(workbook_cache.workbook_path):
		workbook_cache = _cache_alert_email_message_rows(workbook_cache)
		for sheet_name in _get_email_message_sheet_names(workbook_cache):
			for row_index, row_text_values in _iter_cached_sheet_rows(workbook_cache, sheet_name):
				column_index = _find_matching_classifier_row_column_index(
					row_text_values,
					matched_sensitive_content,
					surrounding_characters,
				)
				if column_index > 0:
					return (
						f"{workbook_cache.workbook_file_name} - "
						f"{sheet_name}!{_excel_column_label(column_index)}{row_index}"
					)

	if _is_pdf_document_source_path(workbook_cache.workbook_path):
		workbook_cache = _cache_alert_pdf_document_rows(workbook_cache)
		for row_index, row_text_values in _iter_cached_sheet_rows(workbook_cache, PDF_DOCUMENT_SHEET_NAME):
			column_index = _find_matching_classifier_row_column_index(
				row_text_values,
				matched_sensitive_content,
				surrounding_characters,
			)
			if column_index > 0:
				return (
					f"{workbook_cache.workbook_file_name} - "
					f"{PDF_DOCUMENT_SHEET_NAME}!{_excel_column_label(column_index)}{row_index}"
				)

	openpyxl_module = get_openpyxl_module()
	if openpyxl_module is None:
		return ""

	try:
		workbook = openpyxl_module.load_workbook(
			workbook_cache.workbook_path,
			data_only=True,
			read_only=True,
		)
	except Exception:
		return ""

	try:
		for sheet in workbook.worksheets:
			headers_cached = sheet.title in workbook_cache.sheet_headers
			for row in sheet.iter_rows():
				if not row:
					continue

				row_number = _get_workbook_row_index(row)
				if row_number < 1:
					continue

				row_text_values = _trim_trailing_empty_values([
					_stringify_workbook_cell_value(cell.value)
					for cell in row
				])
				if not row_text_values:
					continue

				if not headers_cached:
					_cache_workbook_sheet_headers(workbook_cache, sheet.title, row_text_values)
					headers_cached = True

				column_index = _find_matching_classifier_row_column_index(
					row_text_values,
					matched_sensitive_content,
					surrounding_characters,
				)
				if column_index < 1:
					continue

				workbook_cache.row_values[(sheet.title, row_number)] = row_text_values
				return (
					f"{workbook_cache.workbook_file_name} - "
					f"{sheet.title}!{_excel_column_label(column_index)}{row_number}"
				)
	finally:
		workbook.close()

	return ""


def _find_classifier_row_location_from_source_context(
	alert_name: str,
	matched_sensitive_content: str,
	surrounding_characters: str,
) -> str:
	for workbook_cache in _get_alert_workbook_caches(alert_name):
		data_found_value = _find_classifier_row_location_from_workbook_cache(
			workbook_cache,
			matched_sensitive_content,
			surrounding_characters,
		)
		if data_found_value:
			return data_found_value

	return ""


def _split_workbook_location_reference(location_reference: str) -> tuple[str, str]:
	workbook_name, separator, workbook_location = str(location_reference or "").strip().rpartition(" - ")
	if not separator:
		return "", ""
	workbook_name = workbook_name.strip()
	workbook_location = workbook_location.strip()
	if not workbook_name or "!" not in workbook_location:
		return "", ""
	return workbook_name, workbook_location


def _parse_workbook_location_coordinate(workbook_location: str) -> tuple[str, str, int]:
	sheet_name, separator, coordinate = str(workbook_location or "").strip().rpartition("!")
	if not separator:
		return "", "", 0

	coordinate_match = re.fullmatch(r"(?P<column>[A-Za-z]+)(?P<row>\d+)", coordinate.strip())
	if coordinate_match is None:
		return "", "", 0

	return (
		sheet_name.strip(),
		coordinate_match.group("column").strip().upper(),
		int(coordinate_match.group("row")),
	)


def _parse_workbook_location_reference(location_reference: str) -> tuple[str, str, int, int]:
	workbook_name, workbook_location = _split_workbook_location_reference(location_reference)
	if not workbook_name or not workbook_location:
		return "", "", 0, 0

	sheet_name, column_label, row_index = _parse_workbook_location_coordinate(workbook_location)
	if not sheet_name or not column_label or row_index < 1:
		return "", "", 0, 0

	return workbook_name, sheet_name, _excel_column_index(column_label), row_index


def _format_workbook_location_label(workbook_location: str) -> str:
	sheet_name, _, row_index = _parse_workbook_location_coordinate(workbook_location)
	if not sheet_name or row_index < 1:
		return workbook_location
	if sheet_name.strip() == TEXT_TABLE_SHEET_NAME:
		return f"Row {row_index}"
	if sheet_name.strip() == WORD_DOCUMENT_SHEET_NAME:
		return f"Paragraph {row_index}"
	if _is_email_attachment_sheet_name(sheet_name.strip()):
		return f"Attachment row {row_index}"
	if sheet_name.strip() == EMAIL_MESSAGE_SHEET_NAME:
		return f"Email excerpt {row_index}"
	if sheet_name.strip() == PDF_DOCUMENT_SHEET_NAME:
		return f"PDF excerpt {row_index}"
	return workbook_location


def _format_workbook_summary_chip_label(workbook_location: str) -> str:
	sheet_name, separator, _ = str(workbook_location or "").strip().rpartition("!")
	if not separator:
		return _format_workbook_location_label(workbook_location)

	normalized_sheet_name = sheet_name.strip()
	if normalized_sheet_name == TEXT_TABLE_SHEET_NAME:
		return TEXT_TABLE_SHEET_NAME
	if normalized_sheet_name == WORD_DOCUMENT_SHEET_NAME:
		return WORD_DOCUMENT_SHEET_NAME
	if _is_email_attachment_sheet_name(normalized_sheet_name):
		return normalized_sheet_name
	if normalized_sheet_name == EMAIL_MESSAGE_SHEET_NAME:
		return EMAIL_MESSAGE_SHEET_NAME
	if normalized_sheet_name == PDF_DOCUMENT_SHEET_NAME:
		return PDF_DOCUMENT_SHEET_NAME
	if not normalized_sheet_name:
		return _format_workbook_location_label(workbook_location)
	return f"{normalized_sheet_name}!"


def _format_workbook_preview_display_value(value: str) -> str:
	formatted_value = " ".join(str(value or "").split()).strip()
	if re.fullmatch(r"\d{4}-\d{2}-\d{2} 00:00:00", formatted_value):
		return formatted_value[:10]
	return formatted_value


def _classify_workbook_preview_section(header_label: str) -> str:
	normalized_label = _normalize_table_header_text(header_label)
	if not normalized_label:
		return "Other"
	if "mailing" in normalized_label:
		return "Mailing"
	if any(token in normalized_label for token in ("date", "start", "dropped", "notice")):
		return "Timeline"
	if normalized_label in {"service address", "city", "state", "zip", "congest zone"} or "service" in normalized_label:
		return "Service"
	if normalized_label in {"customer name", "validation name"} or (
		"name" in normalized_label and normalized_label != "name of aggregation"
	):
		return "Customer"
	if any(token in normalized_label for token in ("utility", "status", "rate", "billing", "tax", "aggregation", "account", "number")):
		return "Account"
	return "Other"


def _format_workbook_matched_column_label(headers: list[str], column_index: int) -> str:
	if column_index < 1:
		return ""

	column_letter = _excel_column_label(column_index)
	default_label = f"Column {column_letter}"
	header_label = headers[column_index - 1].strip() if column_index - 1 < len(headers) else ""
	if not header_label:
		return default_label
	if _normalize_table_header_text(header_label) == _normalize_table_header_text(default_label):
		return header_label
	return f"{header_label} ({column_letter})"


def _build_workbook_row_preview_html(alert_name: str, location_reference: str) -> str:
	workbook_name, sheet_name, matched_column_index, row_index = _parse_workbook_location_reference(location_reference)
	if not workbook_name or not sheet_name or row_index < 1:
		return ""
	_, workbook_location = _split_workbook_location_reference(location_reference)

	workbook_cache = _get_alert_workbook_cache_by_name(alert_name, workbook_name)
	if workbook_cache is None:
		return ""

	row_values = workbook_cache.row_values.get((sheet_name, row_index))
	if not row_values:
		workbook_cache = _ensure_alert_workbook_row_cached(
			alert_name,
			workbook_name,
			sheet_name,
			row_index,
		)
		if workbook_cache is None:
			return ""
		row_values = workbook_cache.row_values.get((sheet_name, row_index))
	if not row_values:
		return ""

	headers = list(workbook_cache.sheet_headers.get(sheet_name, []))
	matched_column_label = _format_workbook_matched_column_label(headers, matched_column_index)
	preview_rows: list[list[str]] = []
	preview_values_by_label: dict[str, str] = {}
	for index, value in enumerate(row_values, start=1):
		display_value = _format_workbook_preview_display_value(value)
		if not display_value:
			continue
		header_label = headers[index - 1].strip() if index - 1 < len(headers) and headers[index - 1].strip() else f"Column {_excel_column_label(index)}"
		preview_rows.append([header_label, display_value])
		preview_values_by_label.setdefault(_normalize_table_header_text(header_label), display_value)

	if not preview_rows:
		return ""

	highlight_specs = [
		("Customer", ("customer name", "validation name")),
		("Account", ("utility acnt number", "utility account number", "account number")),
		("Status", ("enrollment status", "status")),
		("Service", ("service address", "mailing address", "city")),
	]
	highlight_cards: list[str] = []
	if matched_column_label:
		highlight_cards.append(
			'<div class="artifact-row-preview-highlight">'
			'<div class="artifact-row-preview-highlight-kicker">Matched Column</div>'
			f'<div class="artifact-row-preview-highlight-value">{xml_escape(matched_column_label)}</div>'
			"</div>"
		)
	if workbook_location:
		highlight_cards.append(
			'<div class="artifact-row-preview-highlight">'
			'<div class="artifact-row-preview-highlight-kicker">Location</div>'
			f'<div class="artifact-row-preview-highlight-value">{xml_escape(workbook_location)}</div>'
			"</div>"
		)
	for label, candidate_keys in highlight_specs:
		for candidate_key in candidate_keys:
			candidate_value = preview_values_by_label.get(candidate_key)
			if not candidate_value:
				continue
			highlight_cards.append(
				'<div class="artifact-row-preview-highlight">'
				f'<div class="artifact-row-preview-highlight-kicker">{xml_escape(label)}</div>'
				f'<div class="artifact-row-preview-highlight-value">{xml_escape(candidate_value)}</div>'
				"</div>"
			)
			break

	section_order = ["Account", "Customer", "Service", "Mailing", "Timeline", "Other"]
	section_rows: dict[str, list[tuple[str, str]]] = {section_name: [] for section_name in section_order}
	for header_label, value in preview_rows:
		section_rows.setdefault(_classify_workbook_preview_section(header_label), []).append((header_label, value))

	section_html_parts: list[str] = []
	for section_name in section_order:
		entries = section_rows.get(section_name) or []
		if not entries:
			continue

		entry_cards: list[str] = []
		for header_label, value in entries:
			normalized_label = _normalize_table_header_text(header_label)
			item_class = "artifact-data-found-at-preview-item"
			if "address" in normalized_label or len(value) > 38:
				item_class += " artifact-data-found-at-preview-item-wide"

			entry_cards.append(
				f'<div class="{item_class}">'
				f'<div class="artifact-data-found-at-preview-label">{xml_escape(header_label)}</div>'
				f'<div class="artifact-data-found-at-preview-value">{xml_escape(value)}</div>'
				"</div>"
			)

		section_html_parts.append(
			'<section class="artifact-row-preview-section">'
			f'<div class="artifact-row-preview-section-heading">{xml_escape(section_name)}</div>'
			'<div class="artifact-data-found-at-preview-grid">'
			+ "".join(entry_cards)
			+ "</div></section>"
		)

	preview_html_parts = ['<div class="artifact-data-found-at-preview-layout">']
	if highlight_cards:
		preview_html_parts.append('<div class="artifact-row-preview-highlights">' + "".join(highlight_cards) + "</div>")
	preview_html_parts.append('<div class="artifact-row-preview-sections">' + "".join(section_html_parts) + "</div>")
	preview_html_parts.append("</div>")
	return "".join(preview_html_parts)


def _describe_data_found_at_preview(entry: str) -> str:
	_, workbook_location = _split_workbook_location_reference(entry)
	sheet_name, separator, _ = str(workbook_location or "").strip().rpartition("!")
	if not separator:
		return "Matched source excerpt"
	if sheet_name.strip() == TEXT_TABLE_SHEET_NAME:
		return "Matched source row"
	if sheet_name.strip() == WORD_DOCUMENT_SHEET_NAME:
		return "Matched source paragraph"
	if _is_email_attachment_sheet_name(sheet_name.strip()):
		return "Matched email attachment row"
	if sheet_name.strip() == EMAIL_MESSAGE_SHEET_NAME:
		return "Matched email excerpt"
	if sheet_name.strip() == PDF_DOCUMENT_SHEET_NAME:
		return "Matched PDF excerpt"
	return "Matched workbook row"



def _build_data_found_at_fallback_preview_html(alert_name: str, entry: str) -> str:
	workbook_name, workbook_location = _split_workbook_location_reference(entry)
	display_location = _format_workbook_location_label(workbook_location)
	sheet_name, column_label, _ = _parse_workbook_location_coordinate(workbook_location)
	matched_column_label = ""
	if column_label:
		column_index = _excel_column_index(column_label)
		workbook_cache = _get_alert_workbook_cache_by_name(alert_name, workbook_name) if workbook_name else None
		headers = list(workbook_cache.sheet_headers.get(sheet_name, [])) if workbook_cache and sheet_name else []
		matched_column_label = _format_workbook_matched_column_label(headers, column_index)
	preview_items: list[str] = []

	if matched_column_label:
		preview_items.append(
			'<div class="artifact-data-found-at-preview-item">'
			'<div class="artifact-data-found-at-preview-label">Matched Column</div>'
			f'<div class="artifact-data-found-at-preview-value">{xml_escape(matched_column_label)}</div>'
			"</div>"
		)

	if display_location:
		preview_items.append(
			'<div class="artifact-data-found-at-preview-item">'
			'<div class="artifact-data-found-at-preview-label">Location</div>'
			f'<div class="artifact-data-found-at-preview-value">{xml_escape(display_location)}</div>'
			"</div>"
		)

	reference_value = workbook_location or entry
	if reference_value and reference_value != display_location:
		item_class = "artifact-data-found-at-preview-item"
		if "!" in reference_value or len(reference_value) > 38:
			item_class += " artifact-data-found-at-preview-item-wide"
		preview_items.append(
			f'<div class="{item_class}">'
			'<div class="artifact-data-found-at-preview-label">Reference</div>'
			f'<div class="artifact-data-found-at-preview-value">{xml_escape(reference_value)}</div>'
			"</div>"
		)

	if workbook_name and not preview_items:
		preview_items.append(
			'<div class="artifact-data-found-at-preview-item artifact-data-found-at-preview-item-wide">'
			'<div class="artifact-data-found-at-preview-label">Source</div>'
			f'<div class="artifact-data-found-at-preview-value">{xml_escape(workbook_name)}</div>'
			"</div>"
		)

	if not preview_items:
		return ""

	return (
		'<div class="artifact-data-found-at-preview-layout"><div class="artifact-row-preview-sections">'
		'<section class="artifact-row-preview-section"><div class="artifact-row-preview-section-heading">Reference</div>'
		'<div class="artifact-data-found-at-preview-grid">'
		+ "".join(preview_items)
		+ "</div></section></div></div>"
	)


def _append_data_found_at_preview_card(entry_container: ET.Element, entry: str, preview_html: str) -> None:
	workbook_name, workbook_location = _split_workbook_location_reference(entry)
	display_location = _format_workbook_location_label(workbook_location)
	summary_chip_label = _format_workbook_summary_chip_label(workbook_location)

	preview_card = ET.SubElement(entry_container, "details")
	preview_card.set("class", "artifact-row-preview-card")
	preview_card.set("open", "open")

	preview_summary = ET.SubElement(preview_card, "summary")
	preview_summary.set("class", "artifact-row-preview-summary")

	summary_copy = ET.SubElement(preview_summary, "span")
	summary_copy.set("class", "artifact-row-preview-summary-copy")
	summary_copy.text = _describe_data_found_at_preview(entry)

	summary_chip = ET.SubElement(preview_summary, "span")
	summary_chip.set("class", "artifact-row-preview-chip")
	summary_chip.set("title", entry)
	summary_chip.text = summary_chip_label or display_location or workbook_name or "Preview"

	preview_panel = ET.SubElement(preview_card, "div")
	preview_panel.set("class", "artifact-data-found-at-preview")

	preview_meta = ET.SubElement(preview_panel, "div")
	preview_meta.set("class", "artifact-row-preview-meta")

	if workbook_name:
		workbook_badge = ET.SubElement(preview_meta, "span")
		workbook_badge.set("class", "artifact-row-preview-badge artifact-row-preview-badge-primary")
		workbook_badge.text = workbook_name

	if workbook_location:
		location_badge = ET.SubElement(preview_meta, "span")
		location_badge.set("class", "artifact-row-preview-badge")
		location_badge.text = workbook_location

	preview_content = ET.fromstring(preview_html)
	preview_panel.append(preview_content)

def _set_data_found_at_cell_content(target_cell: ET.Element, alert_name: str, data_found_value: str) -> None:
	for child in list(target_cell):
		target_cell.remove(child)
	target_cell.text = None

	entries = [entry.strip() for entry in str(data_found_value or "").split(";") if entry.strip()]
	if not entries:
		target_cell.text = ""
		return

	for entry in entries:
		entry_container = ET.SubElement(target_cell, "div")
		entry_container.set("class", "artifact-data-found-at-entry")

		preview_html = _build_workbook_row_preview_html(alert_name, entry)
		if not preview_html:
			preview_html = _build_data_found_at_fallback_preview_html(alert_name, entry)
		if preview_html:
			_append_data_found_at_preview_card(entry_container, entry, preview_html)
			continue

		location_text = ET.SubElement(entry_container, "div")
		location_text.set("class", "artifact-data-found-at-location artifact-data-found-at-location-plain")
		location_text.text = entry


def _add_classifier_data_found_at_column(alert_name: str, html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html or "<table" not in stripped_html.lower():
		return stripped_html

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return stripped_html

	table_element = next((element for element in root.iter() if element.tag.lower() == "table"), None)
	if table_element is None:
		return stripped_html

	thead_element = next((child for child in list(table_element) if child.tag.lower() == "thead"), None)
	if thead_element is None:
		return stripped_html

	header_row = next((child for child in list(thead_element) if child.tag.lower() == "tr"), None)
	if header_row is None:
		return stripped_html

	header_cells = [cell for cell in list(header_row) if cell.tag.lower() in {"th", "td"}]
	if not header_cells:
		return stripped_html

	header_labels = [_normalize_table_header_text(_extract_element_text(cell)) for cell in header_cells]
	sensitive_info_type_index = next((index for index, label in enumerate(header_labels) if label == "sensitive information type"), None)
	matched_sensitive_content_index = next((index for index, label in enumerate(header_labels) if label == "matched sensitive content"), None)

	location_index = next((index for index, label in enumerate(header_labels) if label == "location"), None)
	surrounding_characters_index = next((index for index, label in enumerate(header_labels) if label == "surrounding characters"), None)
	fallback_source_index = location_index if location_index is not None else surrounding_characters_index
	data_found_at_index = next((index for index, label in enumerate(header_labels) if label == "data found at"), None)
	if data_found_at_index is None:
		insert_index = fallback_source_index + 1 if fallback_source_index is not None else len(header_cells)
		header_template = header_cells[fallback_source_index] if fallback_source_index is not None else header_cells[-1]

		new_header = ET.Element("th")
		for attr_name, attr_value in header_template.attrib.items():
			new_header.set(attr_name, attr_value)
		new_header.text = "Data Found At"
		header_row.insert(insert_index, new_header)
		data_found_at_index = insert_index
	else:
		insert_index = data_found_at_index

	tbody_element = next((child for child in list(table_element) if child.tag.lower() == "tbody"), None)
	if tbody_element is None:
		return stripped_html

	row_elements = [child for child in list(tbody_element) if child.tag.lower() == "tr"]
	matched_content_lookup: dict[str, str] = {}
	if matched_sensitive_content_index is not None:
		lookup_candidates: list[str] = []
		for row_element in row_elements:
			row_cells = [cell for cell in list(row_element) if cell.tag.lower() in {"th", "td"}]
			if not row_cells:
				continue

			sensitive_info_type = ""
			if sensitive_info_type_index is not None and sensitive_info_type_index < len(row_cells):
				sensitive_info_type = _normalize_table_header_text(_extract_element_text(row_cells[sensitive_info_type_index]))
			if sensitive_info_type == "all full names" or matched_sensitive_content_index >= len(row_cells):
				continue

			matched_sensitive_content = _extract_element_text(row_cells[matched_sensitive_content_index]).strip()
			if matched_sensitive_content:
				lookup_candidates.append(matched_sensitive_content)

		if lookup_candidates:
			matched_content_lookup = _find_classifier_values_in_alert_workbook(alert_name, lookup_candidates)

	for row_element in row_elements:
		row_cells = [cell for cell in list(row_element) if cell.tag.lower() in {"th", "td"}]
		if not row_cells:
			continue

		data_found_value = ""
		matched_sensitive_content = ""
		surrounding_characters = ""
		sensitive_info_type = ""
		if sensitive_info_type_index is not None and sensitive_info_type_index < len(row_cells):
			sensitive_info_type = _normalize_table_header_text(_extract_element_text(row_cells[sensitive_info_type_index]))

		if sensitive_info_type != "all full names" and matched_sensitive_content_index is not None and matched_sensitive_content_index < len(row_cells):
			matched_sensitive_content = _extract_element_text(row_cells[matched_sensitive_content_index]).strip()
			data_found_value = matched_content_lookup.get(matched_sensitive_content, "")
		elif matched_sensitive_content_index is not None and matched_sensitive_content_index < len(row_cells):
			matched_sensitive_content = _extract_element_text(row_cells[matched_sensitive_content_index]).strip()

		if surrounding_characters_index is not None and surrounding_characters_index < len(row_cells):
			surrounding_characters = _extract_element_text(row_cells[surrounding_characters_index]).strip()

		if not data_found_value and (matched_sensitive_content or surrounding_characters):
			data_found_value = _find_classifier_row_location_from_source_context(
				alert_name,
				matched_sensitive_content,
				surrounding_characters,
			)

		if data_found_at_index < len(row_cells):
			target_cell = row_cells[data_found_at_index]
			_set_data_found_at_cell_content(target_cell, alert_name, data_found_value)
			continue

		template_index = fallback_source_index if fallback_source_index is not None and fallback_source_index < len(row_cells) else len(row_cells) - 1
		cell_template = row_cells[template_index]
		new_cell = ET.Element("td")
		for attr_name, attr_value in cell_template.attrib.items():
			new_cell.set(attr_name, attr_value)
		_set_data_found_at_cell_content(new_cell, alert_name, data_found_value)
		row_element.insert(insert_index, new_cell)

	return "".join(ET.tostring(child, encoding="unicode") for child in list(root))


def _filter_classifier_rows_for_sit_analysis(html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html or "<table" not in stripped_html.lower():
		return ""

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return ""

	table_element = next((element for element in root.iter() if element.tag.lower() == "table"), None)
	if table_element is None:
		return ""

	tbody_element = next((child for child in list(table_element) if child.tag.lower() == "tbody"), None)
	if tbody_element is None:
		return stripped_html

	for row_element in list(tbody_element):
		if row_element.tag.lower() != "tr":
			continue

		row_cells = [cell for cell in list(row_element) if cell.tag.lower() in {"th", "td"}]
		row_texts = [
			_normalize_table_header_text(_extract_element_text(cell))
			for cell in row_cells
		]
		if any("all full names" in row_text for row_text in row_texts):
			tbody_element.remove(row_element)

	remaining_rows = [child for child in list(tbody_element) if child.tag.lower() == "tr"]
	if not remaining_rows:
		return ""

	return "".join(ET.tostring(child, encoding="unicode") for child in list(root))


def write_sit_analysis_html(alert_name: str, classifier_html_fragment: str) -> str | None:
	sit_analysis_fragment = _filter_classifier_rows_for_sit_analysis(classifier_html_fragment)
	if not sit_analysis_fragment:
		sit_analysis_fragment = (
			'<div class="alert alert-warning mb-0">'
			"No classifier rows remained after filtering out All Full names."
			"</div>"
		)

	artifact_folder = get_alert_artifact_folder(alert_name)
	output_path = os.path.join(artifact_folder, "SIT_analysis.html")
	html_document = build_html_artifact_document(
		f"{alert_name} - SIT Analysis",
		sit_analysis_fragment,
	)
	with open(output_path, "w", encoding="utf-8") as sit_analysis_file:
		sit_analysis_file.write(html_document)
	return output_path


def refresh_saved_classifier_artifacts(
	alert_name: str,
) -> tuple[str | None, str | None]:
	artifact_folder = get_alert_artifact_folder(alert_name)
	classifiers_output_path = os.path.join(artifact_folder, "classifiers.html")
	if not os.path.isfile(classifiers_output_path):
		return None, None

	try:
		with open(classifiers_output_path, "r", encoding="utf-8") as classifiers_file:
			classifiers_document = classifiers_file.read()
	except OSError:
		return None, None

	classifier_table_match = re.search(
		r"<table\b[^>]*>.*?</table>",
		classifiers_document,
		flags=re.IGNORECASE | re.DOTALL,
	)
	if classifier_table_match is None:
		return None, None

	classifier_html_fragment = classifier_table_match.group(0)
	enriched_html_fragment = _add_classifier_data_found_at_column(
		alert_name,
		classifier_html_fragment,
	)
	if not _html_fragment_has_meaningful_content(enriched_html_fragment):
		return None, None

	classifiers_document = build_html_artifact_document(
		f"{alert_name} - Classifiers",
		enriched_html_fragment,
	)
	with open(classifiers_output_path, "w", encoding="utf-8") as classifiers_file:
		classifiers_file.write(classifiers_document)

	sit_output_path = write_sit_analysis_html(alert_name, enriched_html_fragment)
	return classifiers_output_path, sit_output_path


def _normalize_metadata_html_to_table(html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html or "<table" in stripped_html.lower():
		return stripped_html
	if "itemContiner-1541" not in stripped_html and "itemContainer-1541" not in stripped_html:
		return stripped_html

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return stripped_html

	rows: list[list[str]] = []
	seen_rows: set[tuple[str, str]] = set()
	for element in root.iter():
		classes = _html_class_tokens(element.get("class"))
		if "itemContiner-1541" not in classes and "itemContainer-1541" not in classes:
			continue

		label_element = next(
			(
				child
				for child in element.iter()
				if child.tag.lower() in {"h1", "h2", "h3", "h4", "h5", "h6", "th"}
				or any(token.startswith("header-") for token in _html_class_tokens(child.get("class")))
			),
			None,
		)
		value_element = next(
			(
				child
				for child in element.iter()
				if "body-1537" in _html_class_tokens(child.get("class"))
				or any(token.startswith("body-") for token in _html_class_tokens(child.get("class")))
			),
			None,
		)

		label = _extract_element_text(label_element)
		value = _extract_element_text(value_element) if value_element is not None else _extract_element_text(element)
		row_key = (label, value)
		if row_key in seen_rows or not any(part.strip() for part in row_key):
			continue
		seen_rows.add(row_key)
		rows.append([label or "Field", value])

	if not rows:
		return stripped_html

	return _render_simple_html_table(["Field", "Value"], rows)


def _extract_ms_list_table_data_from_html(html_fragment: str) -> tuple[list[str], list[list[str]]]:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html:
		return [], []
	if all(token not in stripped_html for token in ("ms-List-cell", "ms-DetailsRow", "DetailsRowFields", "DetailsRowCell")):
		return [], []

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return [], []

	row_elements = [
		element
		for element in root.iter()
		if "ms-List-cell" in _html_class_tokens(element.get("class"))
	]
	if not row_elements:
		row_elements = [
			element
			for element in root.iter()
			if element.get("data-automationid") == "DetailsRow"
			or "ms-DetailsRow" in _html_class_tokens(element.get("class"))
		]

	column_keys: list[str] = []
	row_maps: list[dict[str, str]] = []
	for row_element in row_elements:
		row_map: dict[str, str] = {}
		candidate_cells: list[ET.Element] = []
		for cell in row_element.iter():
			classes = _html_class_tokens(cell.get("class"))
			is_row_cell = (
				cell.get("data-automationid") == "DetailsRowCell"
				or "ms-DetailsRow-cell" in classes
				or cell.get("role") == "gridcell"
			)
			if not is_row_cell:
				continue
			candidate_cells.append(cell)

		keyed_cells = [cell for cell in candidate_cells if str(cell.get("data-automation-key") or "").strip()]
		for cell in keyed_cells or candidate_cells:

			key = str(cell.get("data-automation-key") or "").strip() or f"Column{len(row_map) + 1}"
			value = _extract_element_text(cell)
			if key not in row_map:
				row_map[key] = value
			if key not in column_keys:
				column_keys.append(key)

		if row_map:
			row_maps.append(row_map)

	active_keys = [key for key in column_keys if any(str(row.get(key, "")).strip() for row in row_maps)]
	if not active_keys:
		return [], []

	header_labels = {key: _humanize_automation_key(key) for key in active_keys}
	if any(label == "Sensitive information type" for label in header_labels.values()):
		classifier_metadata_labels = {
			"Event Name",
			"Event Actor",
			"Event Time",
			"Workload",
		}
		active_keys = [
			key
			for key in active_keys
			if header_labels.get(key) not in classifier_metadata_labels
		]

	rows: list[list[str]] = []
	seen_rows: set[tuple[str, ...]] = set()
	for row_map in row_maps:
		row_values = [str(row_map.get(key, "")) for key in active_keys]
		row_key = tuple(value.strip() for value in row_values)
		if not any(row_key) or row_key in seen_rows:
			continue
		seen_rows.add(row_key)
		rows.append(row_values)

	headers = [header_labels.get(key, _humanize_automation_key(key)) for key in active_keys]
	return headers, rows


def _normalize_ms_list_html_to_table(html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html or "<table" in stripped_html.lower():
		return stripped_html

	headers, rows = _extract_ms_list_table_data_from_html(stripped_html)
	if not headers or not rows:
		return stripped_html

	return _render_simple_html_table(headers, rows)


def normalize_extracted_html_fragment(html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html:
		return ""
	return _normalize_metadata_html_to_table(_normalize_ms_list_html_to_table(stripped_html))


def _extract_ms_list_cells_from_html(html_fragment: str) -> list[tuple[int, str]]:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html or "ms-List-cell" not in stripped_html:
		return []

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return []

	results: list[tuple[int, str]] = []
	seen_indexes: set[int] = set()
	fallback_index = 0
	for element in root.iter():
		classes = _html_class_tokens(element.get("class"))
		if "ms-List-cell" not in classes:
			continue

		raw_index = str(element.get("data-list-index") or "").strip()
		if raw_index and raw_index.lstrip("-").isdigit():
			index = int(raw_index)
		else:
			while fallback_index in seen_indexes:
				fallback_index += 1
			index = fallback_index

		if index in seen_indexes:
			continue

		cell_html = ET.tostring(element, encoding="unicode")
		if not cell_html:
			continue

		seen_indexes.add(index)
		results.append((index, cell_html))

	return sorted(results, key=lambda item: item[0])


def _count_ms_list_cells_in_html(html_fragment: str) -> int:
	return len(_extract_ms_list_cells_from_html(html_fragment))


def _merge_ms_list_cell_html_fragments(*html_fragments: str) -> str:
	cells_by_index: dict[int, str] = {}
	for html_fragment in html_fragments:
		for index, cell_html in _extract_ms_list_cells_from_html(html_fragment):
			cells_by_index.setdefault(index, cell_html)

	if not cells_by_index:
		return ""

	merged_cells = "".join(cells_by_index[index] for index in sorted(cells_by_index))
	return f'<div class="captured-ms-list-cells">{merged_cells}</div>'


def _add_bootstrap_table_classes(html_fragment: str) -> str:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html:
		return ""

	bootstrap_classes = [
		"table",
		"table-striped",
		"table-hover",
		"table-bordered",
		"align-middle",
		"mb-0",
	]

	def replace_table(match: re.Match[str]) -> str:
		attrs = match.group("attrs") or ""
		class_match = re.search(
			r'\bclass\s*=\s*(["\'])(.*?)\1',
			attrs,
			flags=re.IGNORECASE | re.DOTALL,
		)
		if class_match:
			existing_classes = [token for token in class_match.group(2).split() if token]
			merged_classes: list[str] = []
			for token in existing_classes + bootstrap_classes:
				if token not in merged_classes:
					merged_classes.append(token)
			updated_attrs = re.sub(
				r'\bclass\s*=\s*(["\']).*?\1',
				f'class="{' '.join(merged_classes)}"',
				attrs,
				count=1,
				flags=re.IGNORECASE | re.DOTALL,
			)
		else:
			updated_attrs = f'{attrs} class="{' '.join(bootstrap_classes)}"'
		return f"<table{updated_attrs}>"

	return re.sub(
		r"<table(?P<attrs>[^>]*)>",
		replace_table,
		stripped_html,
		flags=re.IGNORECASE,
	)


def build_html_artifact_document(title: str, html_fragment: str) -> str:
	styled_fragment = _add_bootstrap_table_classes(html_fragment)
	escaped_title = xml_escape(title)

	if not styled_fragment:
		content_html = '<div class="alert alert-secondary mb-0">No HTML content was extracted.</div>'
	elif "<table" in styled_fragment.lower():
		content_html = (
			'<div class="table-responsive rounded-4 border border-secondary-subtle shadow-sm bg-white">'
			f"{styled_fragment}"
			"</div>"
		)
	else:
		content_html = (
			'<div class="rounded-4 border border-secondary-subtle bg-white shadow-sm p-3 artifact-html-body">'
			f"{styled_fragment}"
			"</div>"
		)

	return f"""<!doctype html>
<html lang=\"en\">
<head>
	<meta charset=\"utf-8\">
	<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
	<title>{escaped_title}</title>
	<link href=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css\" rel=\"stylesheet\" integrity=\"sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH\" crossorigin=\"anonymous\">
	<style>
		body {{
			min-height: 100vh;
			background:
				radial-gradient(circle at top right, rgba(13, 110, 253, 0.18), transparent 28%),
				linear-gradient(180deg, #f8fbff 0%, #eef3f8 100%);
		}}
		.artifact-shell {{ max-width: 1180px; }}
		.artifact-card {{
			border: 0;
			border-radius: 1.5rem;
			box-shadow: 0 1.5rem 3rem rgba(15, 23, 42, 0.08);
			background: rgba(255, 255, 255, 0.92);
			backdrop-filter: blur(10px);
		}}
		.artifact-fragment th,
		.artifact-fragment td {{ white-space: pre-wrap; }}
		.artifact-fragment table {{ margin-bottom: 0; }}
		.artifact-fragment thead th {{
			background: linear-gradient(180deg, #f8f9fa 0%, #e9ecef 100%);
			font-size: 0.82rem;
			text-transform: uppercase;
			letter-spacing: 0.04em;
		}}
		.artifact-data-found-at-entry + .artifact-data-found-at-entry {{
			margin-top: 0.75rem;
			padding-top: 0.75rem;
			border-top: 1px solid rgba(13, 110, 253, 0.12);
		}}
		.artifact-row-preview-card {{
			border: 1px solid rgba(14, 116, 144, 0.18);
			border-radius: 1rem;
			background: linear-gradient(145deg, #fffdf8 0%, #ffffff 42%, #eef7ff 100%);
			box-shadow: 0 0.9rem 1.8rem rgba(15, 23, 42, 0.06);
			overflow: hidden;
		}}
		.artifact-row-preview-card[open] {{
			border-color: rgba(8, 145, 178, 0.34);
			box-shadow: 0 1.2rem 2.2rem rgba(14, 116, 144, 0.12);
		}}
		.artifact-row-preview-summary {{
			list-style: none;
			cursor: pointer;
			position: relative;
			display: flex;
			flex-wrap: wrap;
			align-items: flex-start;
			gap: 0.55rem 0.75rem;
			padding: 0.9rem 3.25rem 0.9rem 1rem;
			font-weight: 600;
			color: #102a43;
		}}
		.artifact-row-preview-summary::-webkit-details-marker {{ display: none; }}
		.artifact-row-preview-summary::after {{
			content: '+';
			position: absolute;
			right: 1rem;
			top: 0.88rem;
			display: inline-flex;
			align-items: center;
			justify-content: center;
			width: 1.7rem;
			height: 1.7rem;
			border-radius: 999px;
			background: #102a43;
			color: #ffffff;
			font-size: 1rem;
			line-height: 1;
		}}
		.artifact-row-preview-card[open] .artifact-row-preview-summary::after {{ content: '-'; }}
		.artifact-row-preview-summary-copy {{
			flex: 1 1 10rem;
			min-width: 0;
			font-size: 0.84rem;
			letter-spacing: 0.01em;
		}}
		.artifact-row-preview-chip {{
			display: inline-flex;
			align-items: center;
			flex: 0 0 auto;
			max-width: 100%;
			padding: 0.28rem 0.65rem;
			border-radius: 999px;
			background: rgba(14, 116, 144, 0.12);
			color: #0f4c5c;
			font-size: 0.72rem;
			font-weight: 700;
			line-height: 1.25;
			white-space: normal;
			overflow: visible;
			text-overflow: clip;
			overflow-wrap: anywhere;
			word-break: break-word;
		}}
		.artifact-data-found-at-preview {{
			padding: 0.9rem 1rem 1rem;
			border-top: 1px solid rgba(148, 163, 184, 0.22);
			background: linear-gradient(180deg, rgba(247, 250, 252, 0.78) 0%, rgba(255, 255, 255, 0.96) 100%);
		}}
		.artifact-data-found-at-preview-layout {{
			display: grid;
			gap: 1rem;
		}}
		.artifact-row-preview-highlights {{
			display: grid;
			grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
			gap: 0.65rem;
		}}
		.artifact-row-preview-highlight {{
			padding: 0.85rem 0.9rem;
			border-radius: 1rem;
			background: linear-gradient(180deg, rgba(255, 255, 255, 0.96) 0%, rgba(240, 249, 255, 0.94) 100%);
			border: 1px solid rgba(14, 116, 144, 0.14);
			box-shadow: 0 0.55rem 1.1rem rgba(15, 23, 42, 0.05);
		}}
		.artifact-row-preview-highlight-kicker {{
			font-size: 0.68rem;
			font-weight: 800;
			letter-spacing: 0.08em;
			text-transform: uppercase;
			color: #64748b;
			margin-bottom: 0.35rem;
		}}
		.artifact-row-preview-highlight-value {{
			font-size: 0.9rem;
			font-weight: 700;
			line-height: 1.35;
			color: #102a43;
			word-break: break-word;
		}}
		.artifact-row-preview-meta {{
			display: grid;
			gap: 0.45rem;
			justify-items: start;
			margin-bottom: 0.85rem;
		}}
		.artifact-row-preview-badge {{
			display: inline-flex;
			align-items: center;
			padding: 0.3rem 0.65rem;
			border-radius: 999px;
			background: rgba(15, 23, 42, 0.06);
			color: #44556b;
			font-size: 0.72rem;
			font-weight: 700;
		}}
		.artifact-row-preview-badge-primary {{
			max-width: min(100%, 28rem);
			padding: 0.45rem 0.8rem;
			background: rgba(2, 132, 199, 0.12);
			color: #0f4c81;
			border: 1px solid rgba(2, 132, 199, 0.1);
			box-shadow: 0 0.35rem 0.75rem rgba(14, 116, 144, 0.08);
			white-space: normal;
			overflow-wrap: anywhere;
		}}
		.artifact-row-preview-sections {{
			display: grid;
			gap: 1rem;
			max-height: 22rem;
			overflow: auto;
			padding-right: 0.2rem;
		}}
		.artifact-row-preview-section + .artifact-row-preview-section {{
			padding-top: 1rem;
			border-top: 1px solid rgba(148, 163, 184, 0.22);
		}}
		.artifact-row-preview-section-heading {{
			margin-bottom: 0.65rem;
			font-size: 0.76rem;
			font-weight: 800;
			letter-spacing: 0.08em;
			text-transform: uppercase;
			color: #475569;
		}}
		.artifact-data-found-at-preview-grid {{
			display: grid;
			grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
			gap: 0.55rem;
		}}
		.artifact-data-found-at-preview-item {{
			display: grid;
			gap: 0.45rem;
			padding: 0.7rem 0.85rem;
			border: 1px solid rgba(15, 23, 42, 0.08);
			border-radius: 0.9rem;
			background: rgba(255, 255, 255, 0.92);
			box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
		}}
		.artifact-data-found-at-preview-item-wide {{ grid-column: 1 / -1; }}
		.artifact-data-found-at-preview-label {{
			font-size: 0.7rem;
			font-weight: 800;
			letter-spacing: 0.08em;
			text-transform: uppercase;
			color: #6b7280;
			white-space: normal;
		}}
		.artifact-data-found-at-preview-value {{
			font-size: 0.84rem;
			font-weight: 600;
			line-height: 1.45;
			color: #102a43;
			word-break: break-word;
			white-space: normal;
		}}
		.artifact-data-found-at-location {{
			margin-top: 0.4rem;
			font-size: 0.78rem;
			line-height: 1.35;
			color: #5c6773;
			word-break: break-word;
		}}
		.artifact-data-found-at-location-plain {{
			display: inline-flex;
			align-items: center;
			padding: 0.34rem 0.7rem;
			border-radius: 999px;
			background: rgba(15, 23, 42, 0.05);
			color: #44556b;
			font-weight: 600;
		}}
		@media (max-width: 768px) {{
			.artifact-row-preview-summary {{
				align-items: flex-start;
				gap: 0.45rem;
			}}
			.artifact-row-preview-summary::after {{
				top: 0.88rem;
			}}
			.artifact-row-preview-chip {{
				max-width: 100%;
			}}
			.artifact-row-preview-sections {{
				max-height: none;
			}}
		}}
		.artifact-row-preview-sections::-webkit-scrollbar {{ width: 0.55rem; }}
		.artifact-row-preview-sections::-webkit-scrollbar-thumb {{
			background: rgba(148, 163, 184, 0.65);
			border-radius: 999px;
		}}
		.artifact-html-body > *:last-child {{ margin-bottom: 0; }}
	</style>
</head>
<body class=\"py-4 py-lg-5\">
	<main class=\"container artifact-shell\">
		<section class=\"artifact-card p-4 p-lg-5\">
			<div class=\"d-flex flex-column flex-lg-row justify-content-between align-items-lg-start gap-3 mb-4\">
				<div>
					<p class=\"text-uppercase text-secondary fw-semibold small mb-2\">Purview Extract</p>
					<h1 class=\"h3 mb-0\">{escaped_title}</h1>
				</div>
				<span class=\"badge text-bg-primary rounded-pill px-3 py-2\">Bootstrap Styled</span>
			</div>
			<div class=\"artifact-fragment\">{content_html}</div>
		</section>
	</main>
</body>
</html>
"""


def _html_fragment_has_meaningful_content(html_fragment: str) -> bool:
	stripped_html = str(html_fragment or "").strip()
	if not stripped_html:
		return False

	lowered = stripped_html.lower()
	if any(token in lowered for token in ("<table", "<img", "<canvas", "<svg", "<iframe", "<embed", "<object", "data:image/")):
		return True

	try:
		root = ET.fromstring(f"<root>{stripped_html}</root>")
	except ET.ParseError:
		return bool(re.sub(r"<[^>]+>", " ", stripped_html).strip())

	return bool(" ".join(" ".join(text for text in root.itertext()).split()))


def _should_replace_missing_artifact(path: str) -> bool:
	if not os.path.isfile(path):
		return True

	try:
		with open(path, "r", encoding="utf-8") as existing_file:
			existing_html = existing_file.read()
	except OSError:
		return True

	if "No HTML content was extracted." in existing_html:
		return True

	lowered_html = existing_html.lower()
	if "could not capture a usable " in lowered_html and "capture unavailable" in lowered_html:
		return True

	if re.search(
		r'<div[^>]*role="tabpanel"[^>]*>\s*<div>\s*</div>\s*</div>',
		existing_html,
		flags=re.IGNORECASE | re.DOTALL,
	):
		return True

	return False


def build_capture_failure_html_fragment(
	target_panel_text: str,
	target_selector: str,
	reason: str,
	debug_panel_html: str = "",
	tab_debug_result: dict[str, object] | None = None,
) -> str:
	status_rows = [
		["Status", "Capture unavailable"],
		["Requested panel", str(target_panel_text or "")],
		["Target selector", str(target_selector or "")],
		["Reason", str(reason or "")],
	]

	if isinstance(tab_debug_result, dict):
		page_title = str(tab_debug_result.get("page_title") or "").strip()
		page_url = str(tab_debug_result.get("page_url") or "").strip()
		if page_title:
			status_rows.append(["Page title", page_title])
		if page_url:
			status_rows.append(["Page URL", page_url])

	fragment_parts = [
		(
			'<div class="alert alert-warning mb-4">'
			f'Could not capture a usable {xml_escape(target_panel_text)} panel during this run.'
			'</div>'
		),
		_render_simple_html_table(["Field", "Value"], status_rows),
	]

	visible_tabs_rows: list[list[str]] = []
	if isinstance(tab_debug_result, dict):
		for item in tab_debug_result.get("visible_tabs") or []:
			if not isinstance(item, dict):
				continue
			visible_tabs_rows.append([
				str(item.get("text") or ""),
				str(item.get("automation_id") or item.get("automationid") or ""),
				str(item.get("role") or ""),
				str(item.get("aria_selected") or ""),
			])

	if visible_tabs_rows:
		fragment_parts.append('<h2 class="h5 mt-4 mb-3">Visible Tabs</h2>')
		fragment_parts.append(
			_render_simple_html_table(
				["Text", "Automation ID", "Role", "Selected"],
				visible_tabs_rows,
			)
		)

	if _html_fragment_has_meaningful_content(debug_panel_html):
		fragment_parts.append(
			'<details class="mt-4">'
			'<summary class="fw-semibold">Captured panel HTML sample</summary>'
			f'<pre class="mt-3 mb-0 small">{xml_escape(str(debug_panel_html)[:20000])}</pre>'
			'</details>'
		)

	return "".join(fragment_parts)


def excel_column_name(index: int) -> str:
	name = ""
	while index > 0:
		index, remainder = divmod(index - 1, 26)
		name = chr(65 + remainder) + name
	return name


def excel_inline_string_cell(cell_ref: str, value: str) -> str:
	text = str(value or "")
	space_attr = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
	return f'<c r="{cell_ref}" t="inlineStr"><is><t{space_attr}>{xml_escape(text)}</t></is></c>'


def write_dashboard_db_info_cache(output_path: str, payload: dict[str, object]) -> None:
	"""Mirror db_info.txt into a local JS file that dashboard.html can load directly."""
	repo_root = os.path.dirname(os.path.dirname(output_path))
	cache_path = os.path.join(repo_root, "js", "dbInfoData.js")
	cache_content = (
		"window.__REDFLAGGED_DB_INFO__ = "
		+ json.dumps(payload, indent=2, ensure_ascii=False)
		+ ";\n"
	)
	with open(cache_path, "w", encoding="utf-8") as cache_file:
		cache_file.write(cache_content)


def write_dashboard_artifacts_cache(workspace_root: str, payload: dict[str, object]) -> None:
	"""Mirror artifacts inventory into a local JS file that dashboard.html can load directly."""
	cache_path = os.path.join(workspace_root, "js", "artifactData.js")
	cache_content = (
		"window.__REDFLAGGED_ARTIFACTS__ = "
		+ json.dumps(payload, indent=2, ensure_ascii=False)
		+ ";\n"
	)
	with open(cache_path, "w", encoding="utf-8") as cache_file:
		cache_file.write(cache_content)


def build_artifacts_inventory_payload(workspace_root: str) -> dict[str, object]:
	"""Return a recursive inventory of files inside artifacts/."""
	artifacts_dir = os.path.join(workspace_root, "artifacts")
	payload: dict[str, object] = {
		"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
		"files": [],
	}
	if not os.path.isdir(artifacts_dir):
		return payload

	metadata_cache: dict[str, dict[str, object]] = {}
	artifact_files: list[dict[str, object]] = []

	def read_folder_metadata(folder_path: str) -> dict[str, object]:
		cached_metadata = metadata_cache.get(folder_path)
		if cached_metadata is not None:
			return cached_metadata

		metadata_path = os.path.join(folder_path, "alert-record.json")
		metadata: dict[str, object] = {}
		if os.path.isfile(metadata_path):
			try:
				with open(metadata_path, "r", encoding="utf-8") as metadata_file:
					loaded_metadata = json.load(metadata_file)
				if isinstance(loaded_metadata, dict):
					metadata = loaded_metadata
			except Exception:
				metadata = {}

		metadata_cache[folder_path] = metadata
		return metadata

	for root, dir_names, file_names in os.walk(artifacts_dir):
		dir_names.sort()
		file_names.sort()
		folder_metadata = read_folder_metadata(root)
		fallback_alert_name = os.path.basename(root) if os.path.abspath(root) != os.path.abspath(artifacts_dir) else ""
		alert_name = str(
			folder_metadata.get("Alert Name")
			or folder_metadata.get("alert_name")
			or fallback_alert_name
		).strip()
		alert_status = str(
			folder_metadata.get("Status")
			or folder_metadata.get("status")
			or "Unknown"
		).strip() or "Unknown"
		incident_id = str(
			folder_metadata.get("Incident ID")
			or folder_metadata.get("Incident Id")
			or folder_metadata.get("incident_id")
			or ""
		).strip()
		ticket = str(
			folder_metadata.get("ServiceNow Ticket")
			or folder_metadata.get("SNOW Ticket")
			or folder_metadata.get("Ticket")
			or folder_metadata.get("Case Number")
			or ""
		).strip()

		for file_name in file_names:
			absolute_path = os.path.join(root, file_name)
			if not os.path.isfile(absolute_path):
				continue

			try:
				file_stat = os.stat(absolute_path)
			except OSError:
				continue

			relative_path = os.path.relpath(absolute_path, artifacts_dir).replace("\\", "/")
			relative_folder = os.path.dirname(relative_path).replace("\\", "/")
			artifact_files.append(
				{
					"name": file_name,
					"relative_path": relative_path,
					"folder_name": os.path.basename(root),
					"folder_path": "" if relative_folder in {"", "."} else relative_folder,
					"modified_iso": datetime.fromtimestamp(
						file_stat.st_mtime,
						timezone.utc,
					).astimezone().isoformat(timespec="seconds"),
					"size": file_stat.st_size,
					"alert_name": alert_name,
					"alert_status": alert_status,
					"incident_id": incident_id,
					"ticket": ticket,
					"deletable": bool(re.search(r"resolved|closed", alert_status, re.IGNORECASE)),
				}
			)

	payload["files"] = artifact_files
	return payload


def refresh_dashboard_artifacts_cache(workspace_root: str | None = None) -> dict[str, object]:
	"""Refresh the dashboard artifact inventory cache from artifacts/."""
	resolved_workspace_root = workspace_root or _get_workspace_root()
	payload = build_artifacts_inventory_payload(resolved_workspace_root)
	automation_dir = os.path.join(resolved_workspace_root, "automation")
	os.makedirs(automation_dir, exist_ok=True)
	inventory_path = os.path.join(automation_dir, "artifacts_inventory.json")
	with open(inventory_path, "w", encoding="utf-8") as inventory_file:
		json.dump(payload, inventory_file, indent=2, ensure_ascii=False)
	write_dashboard_artifacts_cache(resolved_workspace_root, payload)
	return payload


def _build_purview_payload_headers(
	existing_headers: list[str],
	records: list[dict[str, object]],
) -> list[str]:
	headers: list[str] = []
	seen_headers: set[str] = set()

	for header in existing_headers:
		normalized_header = str(header or "").strip()
		header_key = normalized_header.lower()
		if not normalized_header or header_key in seen_headers:
			continue
		seen_headers.add(header_key)
		headers.append(normalized_header)

	for record in records:
		if not isinstance(record, dict):
			continue
		for key in record.keys():
			normalized_header = str(key or "").strip()
			header_key = normalized_header.lower()
			if not normalized_header or header_key in seen_headers:
				continue
			seen_headers.add(header_key)
			headers.append(normalized_header)

	return headers


def read_purview_payload(text_path: str) -> dict[str, object]:
	"""Read the raw db_info payload and normalize its header/record structure."""
	try:
		with open(text_path, "r", encoding="utf-8") as f:
			raw_text = f.read()
		if not raw_text.strip():
			return {"headers": [], "purview": []}

		loaded_payload = json.loads(raw_text)
		if not isinstance(loaded_payload, dict):
			return {"headers": [], "purview": []}

		records = loaded_payload.get("purview")
		if records is None:
			records = loaded_payload.get("records", [])
		if not isinstance(records, list):
			records = []

		normalized_records = [record for record in records if isinstance(record, dict)]
		headers = _build_purview_payload_headers(
			list(loaded_payload.get("headers", [])) if isinstance(loaded_payload.get("headers"), list) else [],
			normalized_records,
		)
		return {"headers": headers, "purview": normalized_records}
	except Exception as exc:
		print(f"ERROR: Failed to read db_info.txt: {exc}", file=sys.stderr)
		return {"headers": [], "purview": []}


def write_purview_payload(output_path: str, payload: dict[str, object]) -> None:
	"""Write a normalized db_info payload and refresh the dashboard cache mirror."""
	records = payload.get("purview")
	if records is None:
		records = payload.get("records", [])
	if not isinstance(records, list):
		records = []

	normalized_records = [record for record in records if isinstance(record, dict)]
	headers = _build_purview_payload_headers(
		list(payload.get("headers", [])) if isinstance(payload.get("headers"), list) else [],
		normalized_records,
	)
	normalized_payload = {
		"headers": headers,
		"purview": normalized_records,
	}
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(normalized_payload, f, indent=2, ensure_ascii=False)
	write_dashboard_db_info_cache(output_path, normalized_payload)


def write_purview_text_file(output_path: str, headers: list[str], rows: list[list[str]]) -> None:
	"""Write Purview data to a plain JSON array text file (db_info.txt).

	Format: {"headers": [...], "purview": [{...}, ...]}
	"""
	records = []
	for row in rows:
		record = {header: (row[idx] if idx < len(row) else "") for idx, header in enumerate(headers)}
		records.append(record)
	write_purview_payload(output_path, {"headers": headers, "purview": records})


def read_purview_text_file(text_path: str) -> tuple[list[str], list[list[str]]]:
	"""Read headers and rows from a plain JSON array text file (db_info.txt)."""
	payload = read_purview_payload(text_path)
	headers = list(payload.get("headers", []))
	records = payload.get("purview", [])
	rows = [
		[str(record.get(h, "")) for h in headers]
		for record in records
		if isinstance(record, dict)
	]
	return headers, rows


def _align_rows_to_headers(
	target_headers: list[str],
	source_headers: list[str],
	rows: list[list[str]],
) -> list[list[str]]:
	"""Return rows reordered to match the merged header order."""
	source_indexes = {header: index for index, header in enumerate(source_headers)}
	aligned_rows: list[list[str]] = []
	for row in rows:
		aligned_rows.append([
			str(row[source_indexes[header]])
			if header in source_indexes and source_indexes[header] < len(row)
			else ""
			for header in target_headers
		])
	return aligned_rows


def get_alert_names_from_data(
	text_path: str,
	startswith: str | None = None,
) -> list[str]:
	"""Return unique alert names from db_info.txt, optionally filtered by prefix."""
	alert_records = get_alert_records_from_data(text_path, startswith=startswith)
	return [record["alert_name"] for record in alert_records]


def get_alert_detail_records_from_data(
	text_path: str,
	startswith: str | None = None,
) -> list[dict[str, str]]:
	"""Return unique alert records from db_info.txt preserving all columns."""
	headers, rows = read_purview_text_file(text_path)
	if not headers or not rows:
		return []

	alert_name_index = None
	for idx, header in enumerate(headers):
		if str(header or "").strip().lower() == "alert name":
			alert_name_index = idx
			break

	if alert_name_index is None:
		return []

	normalized_headers = [str(header or "").strip() for header in headers]
	normalized_prefix = startswith.upper() if startswith else None
	alert_records: list[dict[str, str]] = []
	seen_names: set[str] = set()

	for row in rows:
		if not row or alert_name_index >= len(row):
			continue

		alert_name = str(row[alert_name_index]).strip()
		if not alert_name or alert_name.lower() in ("new alert", "test alert"):
			continue
		if normalized_prefix and not alert_name.upper().startswith(normalized_prefix):
			continue
		if alert_name in seen_names:
			continue

		seen_names.add(alert_name)
		alert_records.append({
			header: str(row[idx]).strip() if idx < len(row) else ""
			for idx, header in enumerate(normalized_headers)
		})

	return alert_records


def get_alert_records_from_data(
	text_path: str,
	startswith: str | None = None,
) -> list[dict[str, str]]:
	"""Return unique alert records from db_info.txt with Alert Name and Location."""
	alert_detail_records = get_alert_detail_records_from_data(text_path, startswith=startswith)
	alert_records: list[dict[str, str]] = []
	for record in alert_detail_records:
		alert_name = str(record.get("Alert Name") or "").strip()
		location_value = str(record.get("Location") or "").strip()
		alert_records.append({
			"alert_name": alert_name,
			"location": location_value,
		})

	return alert_records


def _find_column_index(headers: list[str], column_name: str) -> int:
	normalized_column_name = str(column_name or "").strip().lower()
	for index, header in enumerate(headers):
		if str(header or "").strip().lower() == normalized_column_name:
			return index
	return -1


def _get_first_visible_locator_text(
	driver: WebDriver,
	locators: tuple[Locator, ...],
) -> str:
	for locator in locators:
		try:
			elements = driver.find_elements(*locator)
		except WebDriverException:
			continue

		for element in elements:
			try:
				if not element.is_displayed():
					continue
				text_value = " ".join(str(element.text or "").split()).strip()
			except WebDriverException:
				continue
			if text_value:
				return text_value

	return ""


def _is_plausible_incident_id_value(value: str) -> bool:
	normalized_value = " ".join(str(value or "").split()).strip()
	return bool(normalized_value and re.search(r"[A-Za-z0-9]", normalized_value))


def _extract_incident_id_from_incidents_page(driver: WebDriver) -> str:
	exact_match = _get_first_visible_locator_text(driver, INCIDENT_ID_LOCATORS)
	if _is_plausible_incident_id_value(exact_match):
		return exact_match

	try:
		return " ".join(
			str(
				driver.execute_script(
					r"""
					function normalize(value) {
					  return String(value || '').replace(/\s+/g, ' ').trim();
					}

					function visible(node) {
					  if (!(node instanceof Element)) {
					    return false;
					  }
					  const style = window.getComputedStyle(node);
					  if (!style || style.display === 'none' || style.visibility === 'hidden') {
					    return false;
					  }
					  const rect = node.getBoundingClientRect();
					  return !!(rect.width || rect.height);
					}

					for (const root of Array.from(document.querySelectorAll('.ms-DetailsList, .ms-List-surface, [role="grid"]'))) {
					  if (!(root instanceof Element) || !visible(root)) {
					    continue;
					  }

					  const headers = Array.from(root.querySelectorAll('[role="columnheader"], .ms-DetailsHeader-cell, th'))
					    .map((header) => normalize(header.innerText || header.textContent || ''));
					  const incidentIndex = headers.findIndex((header) => header.toLowerCase().includes('incident id'));

					  const rows = Array.from(root.querySelectorAll('[data-automationid="DetailsRow"], .ms-DetailsRow, [role="row"]'));
					  for (const row of rows) {
					    if (!(row instanceof Element) || !visible(row)) {
					      continue;
					    }

					    const cells = Array.from(row.querySelectorAll('[role="gridcell"], .ms-DetailsRow-cell, td'))
					      .filter((cell) => cell instanceof Element && visible(cell));
					    if (!cells.length) {
					      continue;
					    }

					    for (const cell of cells) {
					      const automationKey = normalize(cell.getAttribute('data-automation-key') || '').toLowerCase();
					      if (!(automationKey === 'id' || automationKey === 'incidentid' || automationKey === 'incident id')) {
					        continue;
					      }
					      const incidentId = normalize(cell.innerText || cell.textContent || '');
					      if (/[a-z0-9]/i.test(incidentId)) {
					        return incidentId;
					      }
					    }

					    if (incidentIndex >= 0 && incidentIndex < cells.length) {
					      const incidentId = normalize(cells[incidentIndex].innerText || cells[incidentIndex].textContent || '');
					      if (/[a-z0-9]/i.test(incidentId) && !incidentId.toLowerCase().includes('incident id')) {
					        return incidentId;
					      }
					    }

					    for (const cell of cells) {
					      const automationKey = normalize(cell.getAttribute('data-automation-key') || '').toLowerCase();
					      const ariaLabel = normalize(cell.getAttribute('aria-label') || '').toLowerCase();
					      if (!automationKey.includes('incident') && !ariaLabel.includes('incident')) {
					        continue;
					      }
					      const incidentId = normalize(cell.innerText || cell.textContent || '');
					      if (/[a-z0-9]/i.test(incidentId)) {
					        return incidentId;
					      }
					    }
					  }
					}

					return '';
					"""
				)
			).split()
		).strip()
	except WebDriverException:
		return ""


def _extract_first_incidents_row_text(driver: WebDriver) -> str:
	try:
		return " ".join(
			str(
				driver.execute_script(
					r"""
					function normalize(value) {
					  return String(value || '').replace(/\s+/g, ' ').trim();
					}

					function visible(node) {
					  if (!(node instanceof Element)) {
					    return false;
					  }
					  const style = window.getComputedStyle(node);
					  if (!style || style.display === 'none' || style.visibility === 'hidden') {
					    return false;
					  }
					  const rect = node.getBoundingClientRect();
					  return !!(rect.width || rect.height);
					}

					const rows = Array.from(document.querySelectorAll('[data-automationid="DetailsRow"], .ms-DetailsRow'));
					for (const row of rows) {
					  if (!(row instanceof Element) || !visible(row)) {
					    continue;
					  }
					  const rowText = normalize(row.innerText || row.textContent || '');
					  if (rowText) {
					    return rowText;
					  }
					}

					return '';
					"""
				)
			).split()
		).strip()
	except WebDriverException:
		return ""


def _lookup_incident_id_for_alert_df(
	driver: WebDriver,
	incidents_handle: str,
	alert_DF: str,
	timeout: int,
) -> str:
	if not incidents_handle or not alert_DF:
		return ""

	original_handle = driver.current_window_handle
	try:
		driver.switch_to.window(incidents_handle)
		wait_for_page_load(driver, max(5, min(timeout, 15)))
		search_input = find_first_available_search_input(
			driver,
			max(6, min(timeout, 20)),
			list(INCIDENTS_SEARCH_LOCATORS),
		)
		previous_incident_id = _extract_incident_id_from_incidents_page(driver)
		type_into_search_box(driver, search_input, alert_DF)
		WebDriverWait(driver, max(4, min(timeout, 10))).until(
			lambda current_driver: (
				str(
					current_driver.execute_script(
						"return String(arguments[0]?.value || '');",
						search_input,
					)
				)
				== alert_DF
			)
		)

		if not submit_search_input_with_enter(driver, search_input):
			print(
				f"Incident search trigger fallback was not confirmed for alert DF: {alert_DF}",
				file=sys.stderr,
			)

		def wait_for_incident_search_result(current_driver: WebDriver) -> str | bool:
			incident_id = _extract_incident_id_from_incidents_page(current_driver)
			first_row_text = _extract_first_incidents_row_text(current_driver)
			if incident_id and (
				not previous_incident_id
				or incident_id != previous_incident_id
				or str(alert_DF or "").strip().lower() in first_row_text.lower()
			):
				return incident_id
			body_text = str(
				current_driver.execute_script("return String(document.body?.innerText || '');")
			).lower()
			if "no results" in body_text or "0 results" in body_text or "no incidents" in body_text:
				return ""
			return False

		incident_id = WebDriverWait(driver, max(8, min(timeout, 20))).until(wait_for_incident_search_result)
		return str(incident_id or "")
	except TimeoutException:
		print(
			f"Timed out waiting for Incident ID result for alert DF: {alert_DF}",
			file=sys.stderr,
		)
		return ""
	except Exception as exc:  # noqa: BLE001
		print(
			f"Failed Incident ID lookup for alert DF {alert_DF}: {exc}",
			file=sys.stderr,
		)
		return ""
	finally:
		try:
			driver.switch_to.window(original_handle)
		except WebDriverException:
			pass


def enrich_purview_text_file_with_incident_ids(
	driver: WebDriver,
	text_path: str,
	timeout: int,
	startswith: str | None = None,
) -> tuple[int, int]:
	headers, rows = read_purview_text_file(text_path)
	if not headers or not rows:
		return 0, 0

	alert_name_index = _find_column_index(headers, "Alert Name")
	if alert_name_index < 0:
		return 0, 0

	incident_id_index = _find_column_index(headers, "Incident ID")
	header_added = False
	if incident_id_index < 0:
		headers.append("Incident ID")
		incident_id_index = len(headers) - 1
		header_added = True

	for row in rows:
		if len(row) < len(headers):
			row.extend([""] * (len(headers) - len(row)))

	normalized_prefix = startswith.upper() if startswith else None
	processed_rows = 0
	updated_rows = 0
	original_handle = driver.current_window_handle if driver.window_handles else ""
	incidents_handle = find_existing_tab_for_url(driver, INCIDENTS_URL)
	if original_handle:
		try:
			driver.switch_to.window(original_handle)
		except WebDriverException:
			pass

	if not incidents_handle:
		if header_added:
			write_purview_text_file(text_path, headers, rows)
		print(
			"Incident ID enrichment skipped: existing incidents tab was not available.",
			file=sys.stderr,
		)
		return 0, 0

	for row in rows:
		if alert_name_index >= len(row):
			continue

		alert_name = str(row[alert_name_index] or "").strip()
		if not alert_name:
			continue
		if normalized_prefix and not alert_name.upper().startswith(normalized_prefix):
			continue

		alert_DF = extract_alert_df_from_alert_name(alert_name)
		if not alert_DF:
			continue

		processed_rows += 1
		incident_id = _lookup_incident_id_for_alert_df(
			driver,
			incidents_handle,
			alert_DF,
			timeout,
		)
		if not incident_id:
			continue
		if row[incident_id_index] == incident_id:
			continue

		row[incident_id_index] = incident_id
		updated_rows += 1

	if updated_rows or header_added:
		write_purview_text_file(text_path, headers, rows)

	return updated_rows, processed_rows


WINDOWS_INVALID_FILENAME_CHARS = '<>:"/\\|?*'
DOWNLOAD_INCOMPLETE_SUFFIXES = ('.crdownload', '.download', '.partial', '.tmp')
ALERT_WORKBOOK_SUFFIXES = ('.xlsx', '.xlsm', '.xltx', '.xltm')
ALERT_TEXT_SOURCE_SUFFIXES = ('.csv', '.txt', '.tsv')
ALERT_WORD_DOCUMENT_SUFFIXES = ('.doc', '.docx')
ALERT_EMAIL_MESSAGE_SUFFIXES = ('.eml',)
ALERT_PDF_DOCUMENT_SUFFIXES = ('.pdf',)
ALERT_TABULAR_SOURCE_SUFFIXES = ALERT_WORKBOOK_SUFFIXES + ALERT_TEXT_SOURCE_SUFFIXES
ALERT_LOOKUP_SOURCE_SUFFIXES = (
	ALERT_TABULAR_SOURCE_SUFFIXES
	+ ALERT_WORD_DOCUMENT_SUFFIXES
	+ ALERT_EMAIL_MESSAGE_SUFFIXES
	+ ALERT_PDF_DOCUMENT_SUFFIXES
)
ALERT_WORKBOOK_LOOKUP_CACHE: dict[tuple[str, float], AlertWorkbookLookupCacheEntry] = {}


def sanitize_alert_name_for_path(alert_name: str) -> str:
	"""Sanitize only Windows-invalid characters to preserve alert identity."""
	sanitized_name = str(alert_name)
	for char in WINDOWS_INVALID_FILENAME_CHARS:
		sanitized_name = sanitized_name.replace(char, "_")
	return sanitized_name


def _find_existing_alert_folder_name(artifacts_dir: str, folder_name: str) -> str | None:
	if not os.path.isdir(artifacts_dir):
		return None

	target_name = str(folder_name or "").casefold()
	with os.scandir(artifacts_dir) as entries:
		for entry in entries:
			if entry.is_dir() and entry.name.casefold() == target_name:
				return entry.name

	return None


def _build_collision_safe_alert_folder_name(alert_name: str) -> str:
	sanitized_name = sanitize_alert_name_for_path(alert_name)
	hash_suffix = hashlib.sha1(str(alert_name).encode("utf-8")).hexdigest()[:8]
	return f"{sanitized_name} [{hash_suffix}]"


def get_default_downloads_folder() -> str:
	"""Return the current user's default Downloads folder."""
	return os.path.join(os.path.expanduser("~"), "Downloads")


def snapshot_completed_downloads(downloads_dir: str | None = None) -> dict[str, tuple[float, int]]:
	"""Snapshot completed files in the Downloads folder before triggering a new download."""
	downloads_path = downloads_dir or get_default_downloads_folder()
	if not os.path.isdir(downloads_path):
		return {}

	snapshot: dict[str, tuple[float, int]] = {}
	with os.scandir(downloads_path) as entries:
		for entry in entries:
			if not entry.is_file():
				continue
			if entry.name.lower().endswith(DOWNLOAD_INCOMPLETE_SUFFIXES):
				continue
			try:
				stats = entry.stat()
			except OSError:
				continue
			snapshot[entry.path] = (stats.st_mtime, stats.st_size)
	return snapshot


def get_unique_destination_path(directory: str, file_name: str) -> str:
	"""Return a collision-safe output path inside the target directory."""
	base_name, extension = os.path.splitext(file_name)
	candidate_path = os.path.join(directory, file_name)
	suffix = 1
	while os.path.exists(candidate_path):
		candidate_path = os.path.join(directory, f"{base_name} ({suffix}){extension}")
		suffix += 1
	return candidate_path


def wait_for_new_completed_download(
	download_snapshot: dict[str, tuple[float, int]],
	download_started_at: float,
	timeout: int,
	downloads_dir: str | None = None,
) -> str | None:
	"""Wait for a newly created or updated completed file to appear in Downloads."""
	downloads_path = downloads_dir or get_default_downloads_folder()
	if not os.path.isdir(downloads_path):
		return None

	deadline = time.time() + timeout
	last_seen_sizes: dict[str, int] = {}
	stable_polls: dict[str, int] = {}
	baseline_mtime = download_started_at - 1.0

	while time.time() < deadline:
		candidates: list[tuple[float, str, int]] = []
		with os.scandir(downloads_path) as entries:
			for entry in entries:
				if not entry.is_file():
					continue
				if entry.name.lower().endswith(DOWNLOAD_INCOMPLETE_SUFFIXES):
					continue
				try:
					stats = entry.stat()
				except OSError:
					continue

				previous_stats = download_snapshot.get(entry.path)
				if previous_stats is None:
					if stats.st_mtime < baseline_mtime:
						continue
				else:
					previous_mtime, previous_size = previous_stats
					if stats.st_mtime <= max(previous_mtime, baseline_mtime) and stats.st_size == previous_size:
						continue

				candidates.append((stats.st_mtime, entry.path, stats.st_size))

		candidates.sort(reverse=True)
		for _, candidate_path, candidate_size in candidates:
			if last_seen_sizes.get(candidate_path) == candidate_size:
				stable_polls[candidate_path] = stable_polls.get(candidate_path, 0) + 1
			else:
				last_seen_sizes[candidate_path] = candidate_size
				stable_polls[candidate_path] = 1

			if stable_polls[candidate_path] >= 2:
				return candidate_path

		time.sleep(0.5)

	return None


def move_download_to_alert_artifact_folder(
	alert_name: str,
	download_snapshot: dict[str, tuple[float, int]],
	download_started_at: float,
	timeout: int,
) -> str | None:
	"""Move the newly downloaded Source file into the alert's artifact folder."""
	downloaded_file_path = wait_for_new_completed_download(
		download_snapshot,
		download_started_at,
		timeout,
	)
	if not downloaded_file_path:
		return None

	artifact_folder = get_alert_artifact_folder(alert_name)
	destination_path = get_unique_destination_path(
		artifact_folder,
		os.path.basename(downloaded_file_path),
	)
	move_deadline = time.time() + max(15, min(timeout, 60))

	while time.time() < move_deadline:
		try:
			shutil.move(downloaded_file_path, destination_path)
			return destination_path
		except (OSError, PermissionError):
			time.sleep(0.5)

	return None


def get_alert_artifact_folder_path(alert_name: str) -> str:
	"""Return the artifact folder path for a given alert name without creating it."""
	workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	artifacts_dir = os.path.join(workspace_root, "artifacts")
	desired_folder_name = sanitize_alert_name_for_path(alert_name)
	existing_folder_name = _find_existing_alert_folder_name(artifacts_dir, desired_folder_name)
	if existing_folder_name == desired_folder_name:
		return os.path.join(artifacts_dir, existing_folder_name)
	if existing_folder_name:
		return os.path.join(
			artifacts_dir,
			_build_collision_safe_alert_folder_name(alert_name),
		)
	return os.path.join(artifacts_dir, desired_folder_name)


def get_alert_artifact_folder(alert_name: str) -> str:
	"""Return the artifact folder path for a given alert name, creating it if needed."""
	folder_path = get_alert_artifact_folder_path(alert_name)
	os.makedirs(folder_path, exist_ok=True)
	return folder_path


def should_capture_source_panel(alert_location: str | None) -> bool:
	"""Only capture Source for OneDrive and SharePoint locations from db_info.txt."""
	normalized_alert_location = str(alert_location or "").lower()
	return "onedrive" in normalized_alert_location or "sharepoint" in normalized_alert_location


def extract_email_subject_from_alert_name(alert_name: str) -> str:
	match = EMAIL_SUBJECT_ALERT_PATTERN.search(str(alert_name or ""))
	if not match:
		return ""
	return " ".join(match.group(1).split()).strip()


def extract_alert_df_from_alert_name(alert_name: str) -> str:
	match = ALERT_SINGLE_QUOTED_TEXT_PATTERN.search(str(alert_name or ""))
	if not match:
		return ""
	return " ".join(match.group(1).split()).strip()


def get_record_value(record: dict[str, object], *aliases: str) -> str:
	if not isinstance(record, dict):
		return ""

	normalized_record = {
		str(key or "").strip().lower(): value
		for key, value in record.items()
	}
	for alias in aliases:
		value = normalized_record.get(str(alias or "").strip().lower())
		if value is None:
			continue
		normalized_value = str(value).strip()
		if normalized_value:
			return normalized_value
	return ""


def list_alert_artifact_files(alert_name: str, limit: int = 8) -> list[str]:
	artifact_paths = list_alert_artifact_paths(alert_name, limit=limit)
	return [os.path.basename(path) for path in artifact_paths]


def list_alert_artifact_paths(alert_name: str, limit: int | None = None) -> list[str]:
	artifact_folder = get_alert_artifact_folder_path(alert_name)
	if not os.path.isdir(artifact_folder):
		return []

	artifact_paths: list[str] = []
	with os.scandir(artifact_folder) as entries:
		for entry in sorted(entries, key=lambda item: item.name.lower()):
			if not entry.is_file():
				continue
			if entry.name.lower() in ODIN_CAPTURE_OUTPUT_FILE_NAMES:
				continue
			artifact_paths.append(entry.path)
			if limit is not None and len(artifact_paths) >= max(1, limit):
				break
	return artifact_paths


def build_odin_summary_prompt(alert_record: dict[str, object]) -> str:
	return ODIN_PROMPT_TEMPLATE


def _find_first_visible_dom_element(driver: WebDriver, selectors: tuple[str, ...]) -> WebElement | None:
	lookup_script = r"""
	const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];
	const requireVisible = Boolean(arguments[1]);
	function isVisible(element) {
	  if (!element) return false;
	  const style = window.getComputedStyle(element);
	  if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
	  const rect = element.getBoundingClientRect();
	  return rect.width > 0 && rect.height > 0;
	}
	for (const selector of selectors) {
	  const matches = document.querySelectorAll(selector);
	  for (const element of matches) {
	    if (!requireVisible || isVisible(element)) {
	      return element;
	    }
	  }
	}
	return null;
	"""
	try:
		candidate = driver.execute_script(lookup_script, list(selectors), True)
		return candidate if isinstance(candidate, WebElement) else None
	except WebDriverException:
		return None


def _find_first_dom_element(driver: WebDriver, selectors: tuple[str, ...]) -> WebElement | None:
	lookup_script = r"""
	const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];
	const requireVisible = Boolean(arguments[1]);
	function isVisible(element) {
	  if (!element) return false;
	  const style = window.getComputedStyle(element);
	  if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
	  const rect = element.getBoundingClientRect();
	  return rect.width > 0 && rect.height > 0;
	}
	for (const selector of selectors) {
	  const matches = document.querySelectorAll(selector);
	  for (const element of matches) {
	    if (!requireVisible || isVisible(element)) {
	      return element;
	    }
	  }
	}
	return null;
	"""
	try:
		candidate = driver.execute_script(lookup_script, list(selectors), False)
		return candidate if isinstance(candidate, WebElement) else None
	except WebDriverException:
		return None


def _collect_dom_elements(driver: WebDriver, selectors: tuple[str, ...], require_visible: bool = False) -> list[WebElement]:
	lookup_script = r"""
	const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];
	const requireVisible = Boolean(arguments[1]);
	function isVisible(element) {
	  if (!element) return false;
	  const style = window.getComputedStyle(element);
	  if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
	  const rect = element.getBoundingClientRect();
	  return rect.width > 0 && rect.height > 0;
	}
	const seen = new WeakSet();
	const elements = [];
	for (const selector of selectors) {
	  const matches = document.querySelectorAll(selector);
	  for (const element of matches) {
	    if (seen.has(element)) continue;
	    seen.add(element);
	    if (!requireVisible || isVisible(element)) {
	      elements.push(element);
	    }
	  }
	}
	return elements;
	"""
	try:
		result = driver.execute_script(lookup_script, list(selectors), require_visible) or []
		return [element for element in result if isinstance(element, WebElement)]
	except WebDriverException:
		return []


def _upload_odin_artifact_files(driver: WebDriver, alert_name: str, timeout: int) -> list[str]:
	artifact_paths = list_alert_artifact_paths(alert_name)
	if not artifact_paths:
		return []

	absolute_artifact_paths = [os.path.abspath(path) for path in artifact_paths if os.path.isfile(path)]
	if not absolute_artifact_paths:
		return []

	file_inputs = _collect_dom_elements(driver, ODIN_FILE_INPUT_SELECTORS, require_visible=False)
	if not file_inputs:
		raise TimeoutException(f"ODIN file input was not found for alert: {alert_name}")

	print(f"[ODIN] Uploading {len(absolute_artifact_paths)} files for alert: {alert_name}")

	def _input_score(candidate: WebElement) -> int:
		try:
			candidate_id = str(candidate.get_attribute("id") or "").lower()
		except WebDriverException:
			candidate_id = ""
		try:
			candidate_name = str(candidate.get_attribute("name") or "").lower()
		except WebDriverException:
			candidate_name = ""
		try:
			candidate_class = str(candidate.get_attribute("class") or "").lower()
		except WebDriverException:
			candidate_class = ""
		try:
			candidate_multiple = str(candidate.get_attribute("multiple") or "").lower()
		except WebDriverException:
			candidate_multiple = ""
		try:
			candidate_accept = str(candidate.get_attribute("accept") or "").lower()
		except WebDriverException:
			candidate_accept = ""

		score = 0
		if "filesodin" in candidate_id or "filesodin" in candidate_name:
			score += 100
		if "odin" in candidate_id or "odin" in candidate_name or "odin" in candidate_class:
			score += 40
		if candidate_multiple in {"true", "multiple"}:
			score += 25
		if any(token in candidate_accept for token in (".xlsx", ".pdf", ".eml", ".doc", ".docx", "text/", "application/")):
			score += 15
		return score

	scored_candidates: list[tuple[int, WebElement]] = [(_input_score(file_input), file_input) for file_input in file_inputs]
	scored_candidates.sort(key=lambda item: item[0], reverse=True)

	uploaded_count = 0
	for index, (candidate_score, file_input) in enumerate(scored_candidates, start=1):
		try:
			candidate_id = str(file_input.get_attribute("id") or "")
		except WebDriverException:
			candidate_id = ""
		try:
			candidate_name = str(file_input.get_attribute("name") or "")
		except WebDriverException:
			candidate_name = ""
		try:
			candidate_class = str(file_input.get_attribute("class") or "")
		except WebDriverException:
			candidate_class = ""
		print(
			f"[ODIN] File input candidate {index}/{len(scored_candidates)} score={candidate_score} id={candidate_id!r} name={candidate_name!r} class={candidate_class!r}"
		)

		try:
			driver.execute_script(
				"arguments[0].removeAttribute('hidden'); arguments[0].style.display = 'block'; arguments[0].style.visibility = 'visible'; arguments[0].style.opacity = '1'; arguments[0].value = '';",
				file_input,
			)
		except WebDriverException:
			pass

		for path in absolute_artifact_paths:
			try:
				file_input.send_keys(path)
			except Exception:
				continue
			time.sleep(0.2)

		try:
			uploaded_count = int(
				driver.execute_script(
					"return arguments[0] && arguments[0].files ? arguments[0].files.length : 0;",
					file_input,
				)
				or 0
			)
		except Exception:
			uploaded_count = 0

		print(f"[ODIN] File input candidate {index}/{len(scored_candidates)} captured {uploaded_count} file(s).")
		if uploaded_count > 0:
			break

	if uploaded_count <= 0:
		raise TimeoutException(f"ODIN file input did not accept files for alert: {alert_name}")

	try:
		WebDriverWait(driver, max(8, min(timeout, 25))).until(lambda _: uploaded_count >= 1)
	except Exception:
		pass

	time.sleep(1)
	return absolute_artifact_paths


def _populate_odin_prompt_input(driver: WebDriver, prompt_input: WebElement, prompt_text: str) -> None:
	try:
		driver.execute_script("arguments[0].focus();", prompt_input)
	except WebDriverException:
		pass

	tag_name = str(prompt_input.tag_name or "").lower()
	is_content_editable = False
	try:
		is_content_editable = str(prompt_input.get_attribute("contenteditable") or "").lower() == "true"
	except WebDriverException:
		is_content_editable = False

	if tag_name in {"textarea", "input"}:
		# Avoid multiline key presses in <input> fields because Enter may submit/reload the page.
		sanitized_prompt_text = str(prompt_text or "")
		if tag_name == "input":
			sanitized_prompt_text = " ".join(sanitized_prompt_text.splitlines()).strip()
		try:
			driver.execute_script(
				"arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
				prompt_input,
				sanitized_prompt_text,
			)
		except WebDriverException:
			prompt_input.send_keys(Keys.CONTROL, "a")
			prompt_input.send_keys(sanitized_prompt_text)
		print(f"[ODIN] Prompt populated into {tag_name} with {len(sanitized_prompt_text)} chars.")
		return

	driver.execute_script(
		r"""
		const element = arguments[0];
		if (!element) return;
		element.focus();
		if (element.isContentEditable) {
		  element.textContent = '';
		} else if ('value' in element) {
		  element.value = '';
		}
		element.dispatchEvent(new Event('input', { bubbles: true }));
		""",
		prompt_input,
	)
	if is_content_editable:
		prompt_input.send_keys(prompt_text)
	else:
		driver.execute_script(
			"arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
			prompt_input,
			prompt_text,
		)
	print(f"[ODIN] Prompt populated into contenteditable={is_content_editable} with {len(str(prompt_text or ''))} chars.")


def _click_odin_send_button(driver: WebDriver, prompt_input: WebElement | None = None) -> None:
	if prompt_input is not None:
		try:
			nearby_button = driver.execute_script(
				r"""
				const input = arguments[0];
				if (!input) return null;
				const selectors = [
				  'button.btn.btn-outline-secondary.fw-bold.ms-1.me-1',
				  "button[aria-label*='Send']",
				  "button[title*='Send']",
				  "button[type='submit']"
				];
				let container = input.closest('form') || input.closest('.input-group') || input.parentElement;
				for (let depth = 0; container && depth < 4; depth += 1, container = container.parentElement) {
				  for (const selector of selectors) {
				    const button = container.querySelector(selector);
				    if (button) return button;
				  }
				}
				return null;
				""",
				prompt_input,
			)
			if isinstance(nearby_button, WebElement):
				try:
					ActionChains(driver).move_to_element(nearby_button).click().perform()
					print("[ODIN] Sent prompt using nearby send button.")
					return
				except Exception:
					driver.execute_script("arguments[0].click();", nearby_button)
					print("[ODIN] Sent prompt using nearby send button (JS click).")
					return
		except WebDriverException:
			pass

	send_button = _find_first_visible_dom_element(driver, ODIN_SEND_BUTTON_SELECTORS)
	if send_button is not None:
		try:
			ActionChains(driver).move_to_element(send_button).click().perform()
			print("[ODIN] Sent prompt using global send button selector.")
			return
		except Exception:
			try:
				driver.execute_script("arguments[0].click();", send_button)
				print("[ODIN] Sent prompt using global send button selector (JS click).")
				return
			except WebDriverException:
				pass

	if prompt_input is not None:
		try:
			prompt_input.send_keys(Keys.ENTER)
			print("[ODIN] Sent prompt using Enter key on input.")
			return
		except WebDriverException:
			pass

	ActionChains(driver).send_keys(Keys.ENTER).perform()
	print("[ODIN] Sent prompt using fallback Enter key.")


def _looks_like_complete_odin_response(text: str) -> bool:
	normalized = " ".join(str(text or "").split()).strip().lower()
	if not normalized:
		return False
	if "analyzing uploaded documents" in normalized:
		return False
	if "few seconds ago" in normalized:
		return False
	if "classifiers.html" in normalized or "itemscontainer" in normalized:
		return False
	required_markers = (
		"summary of assessment",
		"what happened",
		"why it matters",
		"recommended next actions",
	)
	marker_hits = sum(1 for marker in required_markers if marker in normalized)
	if marker_hits >= 3:
		return True
	return "confidence" in normalized and marker_hits >= 1


def _collect_odin_response_blocks(driver: WebDriver) -> list[str]:
	collect_script = r"""
	const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];
	function normalize(value) {
	  return String(value || '').replace(/\s+/g, ' ').trim();
	}
	function isVisible(element) {
	  if (!element) return false;
	  const style = window.getComputedStyle(element);
	  if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
	  const rect = element.getBoundingClientRect();
	  return rect.width > 0 && rect.height > 0;
	}
	const seenElements = new WeakSet();
	const blocks = [];
	for (const selector of selectors) {
	  const matches = document.querySelectorAll(selector);
	  for (const element of matches) {
	    if (seenElements.has(element)) continue;
	    seenElements.add(element);
	    if (!isVisible(element)) continue;
	    const text = normalize(element.innerText || element.textContent || '');
	    if (!text || text.length < 40) continue;
	    if (/ask dlp incident consultant anything/i.test(text)) continue;
	    if (/generative ai can make mistakes/i.test(text)) continue;
	    if (/^output plain text only\.?/i.test(text)) continue;
	    const rect = element.getBoundingClientRect();
	    const classText = String(element.className || '').toLowerCase();
	    const idText = String(element.id || '').toLowerCase();
	    const roleText = String(element.getAttribute('data-message-author-role') || '').toLowerCase();
	    const ariaLabel = String(element.getAttribute('aria-label') || '').toLowerCase();
	    const score =
	      (roleText.includes('assistant') ? 100 : 0) +
	      (classText.includes('assistant') ? 50 : 0) +
	      (classText.includes('response') ? 20 : 0) +
	      (classText.includes('message') ? 10 : 0) +
	      (idText.includes('assistant') ? 30 : 0) +
	      (ariaLabel.includes('assistant') ? 30 : 0);
	    blocks.push({ text, top: rect.top, left: rect.left, score });
	  }
	}
	blocks.sort((left, right) => left.top - right.top || left.left - right.left);
	const preferred = blocks.filter((entry) => entry.score > 0);
	const selected = preferred.length ? preferred : blocks;
	return selected.map((entry) => entry.text);
	"""
	try:
		result = driver.execute_script(collect_script, list(ODIN_RESPONSE_BLOCK_SELECTORS)) or []
		return [str(item).strip() for item in result if str(item).strip()]
	except WebDriverException:
		return []


def _wait_for_odin_response_block(driver: WebDriver, baseline_blocks: list[str], prompt_text: str, timeout: int) -> str:
	baseline_keys = {" ".join(str(block).split()).strip().lower() for block in baseline_blocks}
	prompt_key = " ".join(str(prompt_text or "").split()).strip().lower()
	deadline = time.time() + max(30, timeout)
	last_candidate = ""
	stable_hits = 0
	empty_cycles = 0

	while time.time() < deadline:
		response_blocks = _collect_odin_response_blocks(driver)
		if not response_blocks:
			empty_cycles += 1
			if empty_cycles >= 3:
				stable_hits = 0
			time.sleep(2)
			continue
		empty_cycles = 0

		candidate = ""
		for block in reversed(response_blocks):
			block_key = " ".join(str(block).split()).strip().lower()
			if not block_key or block_key in baseline_keys:
				continue
			if prompt_key and block_key == prompt_key:
				continue
			if prompt_key and block_key in prompt_key:
				continue
			if "analyzing uploaded documents" in block_key or "few seconds ago" in block_key:
				continue
			if "classifiers.html" in block_key and "summary of assessment" not in block_key:
				continue
			if "confidence" not in block_key and "summary of assessment" not in block_key and len(block_key) < 80:
				continue
			candidate = block.strip()
			break

		if candidate:
			looks_complete = _looks_like_complete_odin_response(candidate)
			print(f"[ODIN] Candidate response length={len(candidate)} complete={looks_complete}")
			if candidate != last_candidate:
				last_candidate = candidate
				stable_hits = 1
			else:
				stable_hits += 1
				if stable_hits >= 2 and looks_complete:
					return candidate
				if stable_hits >= 3:
					return candidate

		time.sleep(2)

	return last_candidate


def _extract_section_lines(raw_response: str) -> tuple[str, str, str, str, str, str]:
	lines = [line.strip() for line in str(raw_response or "").replace("\r", "\n").splitlines() if line.strip()]
	confidence = ""
	confidence_label = ""
	current_section = ""
	sections: dict[str, list[str]] = {
		"summary": [],
		"what": [],
		"why": [],
		"next": [],
	}

	for index, line in enumerate(lines):
		normalized_line = line.casefold()
		if normalized_line == "confidence":
			if index + 1 < len(lines):
				confidence = lines[index + 1]
			if index + 2 < len(lines):
				confidence_label = lines[index + 2]
			continue
		if normalized_line == "summary of assessment":
			current_section = "summary"
			continue
		if normalized_line == "what happened":
			current_section = "what"
			continue
		if normalized_line == "why it matters":
			current_section = "why"
			continue
		if normalized_line == "recommended next actions":
			current_section = "next"
			continue
		if normalized_line in {"odin • ai findings", "odin - ai findings"}:
			continue
		if current_section:
			sections[current_section].append(line)

	return (
		confidence,
		confidence_label,
		" ".join(sections["summary"]).strip(),
		"\n".join(sections["what"]).strip(),
		"\n".join(sections["why"]).strip(),
		"\n".join(sections["next"]).strip(),
	)


def _normalize_odin_list_block(text_block: str) -> str:
	if not str(text_block or "").strip():
		return ""

	items: list[str] = []
	for line in str(text_block or "").splitlines():
		normalized_line = re.sub(r"^(?:[-*•]\s*|\d+[.)]?\s*)", "", str(line).strip())
		if normalized_line:
			items.append(normalized_line)
	return "\n".join(items[:3])


def parse_odin_response(raw_response: str) -> dict[str, str]:
	confidence, confidence_label, summary, what_block, why_block, next_block = _extract_section_lines(raw_response)
	if not summary:
		summary = str(raw_response or "").strip().split("\n", 1)[0].strip()
	return {
		"Odin Summary": summary,
		"Odin Confidence": confidence,
		"Odin Confidence Label": confidence_label,
		"Odin What Happened": _normalize_odin_list_block(what_block),
		"Odin Why It Matters": _normalize_odin_list_block(why_block),
		"Odin Recommended Next Actions": _normalize_odin_list_block(next_block),
		"Odin Raw Response": str(raw_response or "").strip(),
	}


def capture_odin_response_for_alert(
	driver: WebDriver,
	odin_handle: str,
	alert_record: dict[str, object],
	timeout: int,
) -> dict[str, str]:
	alert_name = get_record_value(alert_record, "Alert Name", "AlertName") or "Purview incident"
	prompt_text = build_odin_summary_prompt(alert_record)
	driver.switch_to.window(odin_handle)
	load_url_in_current_tab(driver, ODIN_URL)
	wait_for_servicenow_with_browser_refresh(driver, max(20, timeout), portal_label="ODIN")
	time.sleep(2)

	baseline_blocks = _collect_odin_response_blocks(driver)
	print(f"[ODIN] Baseline response blocks before send: {len(baseline_blocks)}")
	uploaded_artifact_paths = _upload_odin_artifact_files(driver, alert_name, timeout)
	print(f"[ODIN] Uploaded file count after selection: {len(uploaded_artifact_paths)}")
	prompt_input = _find_first_visible_dom_element(driver, ODIN_PROMPT_INPUT_SELECTORS)
	if prompt_input is not None:
		_populate_odin_prompt_input(driver, prompt_input, prompt_text)
	else:
		print(
			f"ODIN prompt input was not found for alert {alert_name!r}; continuing with uploaded files only.",
			file=sys.stderr,
		)
	time.sleep(0.5)
	_click_odin_send_button(driver, prompt_input)
	response_text = _wait_for_odin_response_block(driver, baseline_blocks, prompt_text if prompt_input is not None else "", max(timeout * 2, 45))
	print(f"[ODIN] Final captured response length: {len(str(response_text or ''))}")
	if not response_text:
		raise TimeoutException(f"ODIN response was not captured for alert: {alert_name}")

	parsed_response = parse_odin_response(response_text)
	parsed_response["Odin Uploaded Files"] = "\n".join(os.path.basename(path) for path in uploaded_artifact_paths)
	parsed_response["Odin Prompt"] = prompt_text
	parsed_response["Odin Response Updated At"] = time.strftime("%Y-%m-%d %H:%M:%S")
	return parsed_response


def write_odin_capture_artifacts(alert_name: str, prompt_text: str, response_payload: dict[str, str]) -> None:
	artifact_folder = get_alert_artifact_folder(alert_name)
	prompt_path = os.path.join(artifact_folder, "odin-prompt.txt")
	response_path = os.path.join(artifact_folder, "odin-response.txt")
	response_json_path = os.path.join(artifact_folder, "odin-response.json")

	with open(prompt_path, "w", encoding="utf-8") as prompt_file:
		prompt_file.write(str(prompt_text or "").strip() + "\n")
	with open(response_path, "w", encoding="utf-8") as response_file:
		response_file.write(str(response_payload.get("Odin Raw Response") or "").strip() + "\n")
	with open(response_json_path, "w", encoding="utf-8") as response_json_file:
		json.dump(response_payload, response_json_file, indent=2, ensure_ascii=False)


def capture_odin_responses_from_data(
	driver: WebDriver,
	text_path: str,
	timeout: int,
	odin_handle: str | None = None,
	startswith: str | None = None,
) -> tuple[int, int]:
	payload = read_purview_payload(text_path)
	records = payload.get("purview", [])
	if not isinstance(records, list) or not records:
		return 0, 0

	resolved_odin_handle = odin_handle or find_existing_tab_for_url(driver, ODIN_URL)
	if not resolved_odin_handle:
		print("ODIN tab was not available; skipping ODIN response capture.", file=sys.stderr)
		return 0, 0

	headers = payload.get("headers", [])
	if not isinstance(headers, list):
		headers = []
		payload["headers"] = headers
	for field_name in ODIN_DB_INFO_FIELD_ORDER:
		if field_name not in headers:
			headers.append(field_name)

	normalized_prefix = str(startswith or "").strip().upper()
	processed_count = 0
	captured_count = 0

	for record in records:
		if not isinstance(record, dict):
			continue
		alert_name = get_record_value(record, "Alert Name", "AlertName")
		if not alert_name:
			continue
		if normalized_prefix and not alert_name.upper().startswith(normalized_prefix):
			continue

		processed_count += 1
		try:
			response_payload = capture_odin_response_for_alert(
				driver,
				resolved_odin_handle,
				record,
				timeout,
			)
		except Exception as exc:  # noqa: BLE001
			print(f"Failed ODIN capture for alert {alert_name!r}: {exc}", file=sys.stderr)
			continue

		for field_name, field_value in response_payload.items():
			record[field_name] = str(field_value or "").strip()

		try:
			write_odin_capture_artifacts(alert_name, response_payload.get("Odin Prompt", ""), response_payload)
			write_alert_record_metadata(get_alert_artifact_folder(alert_name), {key: str(value) for key, value in record.items()})
		except Exception as exc:  # noqa: BLE001
			print(f"Failed to persist ODIN artifacts for alert {alert_name!r}: {exc}", file=sys.stderr)

		captured_count += 1

	if captured_count:
		write_purview_payload(text_path, payload)
		refresh_dashboard_artifacts_cache(_get_workspace_root())

	return captured_count, processed_count


def search_outlook_for_alert_subject(
	driver: WebDriver,
	alert_name: str,
	timeout: int,
	outlook_handle: str | None = None,
) -> bool:
	email_subject = extract_email_subject_from_alert_name(alert_name)
	if not email_subject:
		return False

	original_handle = driver.current_window_handle
	resolved_outlook_handle = ""
	window_handles = list(driver.window_handles)

	if outlook_handle and outlook_handle in window_handles:
		resolved_outlook_handle = outlook_handle
	else:
		for handle in window_handles:
			try:
				driver.switch_to.window(handle)
				current_url = str(driver.current_url or "").lower()
			except WebDriverException:
				continue
			if "outlook.office.com" in current_url:
				resolved_outlook_handle = handle
				break

	if not resolved_outlook_handle:
		print(
			f"Outlook tab was not available for email-subject alert: {alert_name}",
			file=sys.stderr,
		)
		try:
			driver.switch_to.window(original_handle)
		except WebDriverException:
			pass
		return False

	search_query = f'from:postmaster hasattachments:yes "{email_subject}"'
	try:
		driver.switch_to.window(resolved_outlook_handle)
		wait_for_page_load(driver, max(5, min(timeout, 15)))
		search_input = find_first_available_search_input(
			driver,
			max(6, min(timeout, 20)),
			list(OUTLOOK_SEARCH_LOCATORS),
		)
		type_into_search_box(driver, search_input, search_query)
		WebDriverWait(driver, max(4, min(timeout, 10))).until(
			lambda current_driver: (
				str(
					current_driver.execute_script(
						"return String(arguments[0]?.value || '');",
						search_input,
					)
				)
				== search_query
			)
		)
		print(
			f"Typed Outlook search query for alert {alert_name}; attempting search trigger before Outlook click sequence."
		)

		# The Outlook result-row selectors are rendered only after the search is actually submitted.
		search_triggered = submit_search_input_with_enter(driver, search_input)
		if not search_triggered:
			print(
				f"Outlook search trigger fallback was not confirmed for alert: {alert_name}",
				file=sys.stderr,
			)
		WebDriverWait(driver, max(6, min(timeout, 20))).until(
			lambda current_driver: bool(
				current_driver.execute_script(
					"""
					const bodyText = String(document.body?.innerText || '');
					const headingTexts = Array.from(document.querySelectorAll('h1, h2, [role="heading"]'))
					  .map(el => String(el.innerText || el.textContent || '').trim())
					  .filter(Boolean);
					const buttonTexts = Array.from(document.querySelectorAll('button, [role="button"]'))
					  .map(el => [
					    String(el.innerText || el.textContent || '').trim(),
					    String(el.getAttribute('aria-label') || '').trim(),
					  ].join(' '))
					  .filter(Boolean);
					return [
					  /\bresults\b/i.test(bodyText),
					  headingTexts.some(text => /\bresults\b|\btop results\b/i.test(text)),
					  buttonTexts.some(text => /exit search|close search|search filters/i.test(text)),
					].some(Boolean);
					"""
				)
			)
		)
		print(f"Searched Outlook for alert subject: {search_query}")

		artifact_folder = get_alert_artifact_folder(alert_name)

		def configure_download_directory(download_dir: str) -> bool:
			os.makedirs(download_dir, exist_ok=True)
			commands = [
				(
					'Browser.setDownloadBehavior',
					{
						'behavior': 'allow',
						'downloadPath': download_dir,
						'eventsEnabled': False,
					},
				),
				(
					'Page.setDownloadBehavior',
					{
						'behavior': 'allow',
						'downloadPath': download_dir,
					},
				),
			]

			for command_name, command_params in commands:
				try:
					driver.execute_cdp_cmd(command_name, command_params)
					return True
				except Exception:
					continue

			return False

		def click_outlook_target(
			css_selectors: list[str] | None = None,
			xpath_selectors: list[str] | None = None,
			target_text: str = "",
			timeout_seconds: int = 10,
		) -> bool:
			selectors = css_selectors or []
			xpaths = xpath_selectors or []
			normalized_target_text = " ".join(str(target_text or "").split()).strip().lower()
			compact_target_text = re.sub(r"[^a-z0-9]+", "", normalized_target_text)
			deadline = time.time() + timeout_seconds

			def try_click(element: WebElement) -> bool:
				try:
					if not element.is_displayed():
						return False
				except WebDriverException:
					return False

				try:
					driver.execute_script(
						"arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
						element,
					)
				except WebDriverException:
					pass

				try:
					ActionChains(driver).move_to_element(element).click().perform()
					return True
				except WebDriverException:
					pass

				try:
					element.click()
					return True
				except WebDriverException:
					pass

				try:
					driver.execute_script("arguments[0].click();", element)
					return True
				except WebDriverException:
					return False

			def matches_target_text(element: WebElement) -> bool:
				if not normalized_target_text:
					return True
				try:
					text_candidates = [
						str(element.text or "").strip().lower(),
						str(element.get_attribute("aria-label") or "").strip().lower(),
						str(element.get_attribute("title") or "").strip().lower(),
					]
				except WebDriverException:
					return False
				for candidate in text_candidates:
					if not candidate:
						continue
					if candidate == normalized_target_text or normalized_target_text in candidate:
						return True
					candidate_compact = re.sub(r"[^a-z0-9]+", "", candidate)
					if compact_target_text and (
						candidate_compact == compact_target_text or compact_target_text in candidate_compact
					):
						return True
				return False

			while time.time() < deadline:
				for selector in selectors:
					try:
						elements = driver.find_elements(By.CSS_SELECTOR, selector)
					except WebDriverException:
						continue
					for element in elements:
						if not matches_target_text(element):
							continue
						if try_click(element):
							return True

				for xpath in xpaths:
					try:
						elements = driver.find_elements(By.XPATH, xpath)
					except WebDriverException:
						continue
					for element in elements:
						if not matches_target_text(element):
							continue
						if try_click(element):
							return True

				time.sleep(0.25)

			return False

		def wait_for_outlook_search_results(timeout_seconds: int) -> bool:
			try:
				WebDriverWait(driver, timeout_seconds).until(
					lambda current_driver: bool(
						current_driver.execute_script(
							"""
							function normalize(value) {
							  return String(value || '').replace(/\\s+/g, ' ').trim();
							}

							function visible(node) {
							  return !!(node && node instanceof Element && (node.offsetWidth || node.offsetHeight || node.getClientRects().length));
							}

							const region = document.querySelector("[id='Skip to message list-region']")
							  || document.querySelector("[role='complementary'][aria-label*='Message list']");
							if (!region || !visible(region)) {
							  return false;
							}

							const regionText = normalize(region.innerText || region.textContent || '');
							if (/results searching/i.test(regionText)) {
							  return false;
							}

							return Array.from(region.querySelectorAll('div, li, article, a, button, span')).some(node => {
							  if (!visible(node)) {
							    return false;
							  }

							  const text = normalize(node.innerText || node.textContent || '');
							  if (!text || text.length < 20) {
							    return false;
							  }

							  if (/^results$/i.test(text) || /^top results$/i.test(text) || /^results searching/i.test(text)) {
							    return false;
							  }

							  const rect = node.getBoundingClientRect();
							  return rect.width >= 220 && rect.height >= 36 && rect.height <= 260;
							});
							"""
						)
					)
				)
				return True
			except TimeoutException:
				return False

		def click_first_outlook_search_result(timeout_seconds: int) -> bool:
			deadline = time.time() + timeout_seconds

			while time.time() < deadline:
				try:
					click_result = driver.execute_script(
						"""
						function normalize(value) {
						  return String(value || '').replace(/\\s+/g, ' ').trim();
						}

						function visible(node) {
						  return !!(node && node instanceof Element && (node.offsetWidth || node.offsetHeight || node.getClientRects().length));
						}

						const region = document.querySelector("[id='Skip to message list-region']")
						  || document.querySelector("[role='complementary'][aria-label*='Message list']");
						if (!region || !visible(region)) {
						  return { status: 'region-missing' };
						}

						const regionText = normalize(region.innerText || region.textContent || '');
						if (/results searching/i.test(regionText)) {
						  return { status: 'searching' };
						}

						let baseline = 0;
						for (const node of Array.from(region.querySelectorAll('div, span, h1, h2, h3'))) {
						  if (!visible(node)) {
						    continue;
						  }

						  const text = normalize(node.innerText || node.textContent || '');
						  if (/^results$/i.test(text) || /^top results$/i.test(text)) {
						    baseline = Math.max(baseline, node.getBoundingClientRect().bottom);
						  }
						}

						const candidates = [];
						const seen = new Set();
						for (const node of Array.from(region.querySelectorAll('div, li, article, a, button, span'))) {
						  if (!visible(node)) {
						    continue;
						  }

						  const text = normalize(node.innerText || node.textContent || '');
						  if (!text || text.length < 20) {
						    continue;
						  }

						  if (/^results$/i.test(text) || /^top results$/i.test(text) || /^results searching/i.test(text)) {
						    continue;
						  }

						  const rect = node.getBoundingClientRect();
						  if (baseline && rect.top < baseline - 2) {
						    continue;
						  }

						  if (rect.width < 220 || rect.height < 36 || rect.height > 260) {
						    continue;
						  }

						  let container = node;
						  while (container && container !== region) {
						    const containerRect = container.getBoundingClientRect();
						    const containerText = normalize(container.innerText || container.textContent || '');
						    if (
						      containerText
						      && !/^results$/i.test(containerText)
						      && !/^top results$/i.test(containerText)
						      && !/^results searching/i.test(containerText)
						      && containerRect.width >= 220
						      && containerRect.height >= 36
						      && containerRect.height <= 260
						    ) {
						      break;
						    }
						    container = container.parentElement;
						  }

						  const target = container && container !== region ? container : node;
						  if (seen.has(target)) {
						    continue;
						  }
						  seen.add(target);

						  const targetRect = target.getBoundingClientRect();
						  const targetText = normalize(target.innerText || target.textContent || '');
						  const score = [
						    /microsoft outlook/i.test(targetText) ? 100 : 0,
						    /rule detected/i.test(targetText) ? 80 : 0,
						    /inbox/i.test(targetText) ? 30 : 0,
						    /attachment/i.test(targetText) ? 15 : 0,
						    Math.max(0, 1000 - targetRect.top),
						  ].reduce((total, value) => total + value, 0);

						  candidates.push({
						    target,
						    score,
						    top: targetRect.top,
						    left: targetRect.left,
						    area: targetRect.width * targetRect.height,
						    text: targetText,
						  });
						}

						candidates.sort((left, right) => (
						  right.score - left.score
						  || left.top - right.top
						  || left.left - right.left
						  || left.area - right.area
						));

						if (!candidates.length) {
						  return {
						    status: 'no-candidate',
						    regionText: regionText.slice(0, 500),
						  };
						}

						const candidate = candidates[0].target;
						candidate.scrollIntoView({ block: 'center', inline: 'center' });
						candidate.click();
						return {
						  status: 'clicked',
						  text: normalize(candidate.innerText || candidate.textContent || '').slice(0, 300),
						};
						"""
					)
				except WebDriverException:
					click_result = None

				if isinstance(click_result, dict) and click_result.get("status") == "clicked":
					return True

				time.sleep(0.25)

			return False

		if not wait_for_outlook_search_results(max(12, min(timeout, 30))):
			print(
				f"Outlook search results did not finish loading for alert: {alert_name}",
				file=sys.stderr,
			)
			return False

		attachment_first_click_css_selectors = [
			"div[id='33'].cbdbfb4-c021-0b05-8d42-9d895b1436de.jGG6V.epBmH.UWKUc",
			"div[id='33'].jGG6V.epBmH.UWKUc",
			"div.cbdbfb4-c021-0b05-8d42-9d895b1436de.jGG6V.epBmH.UWKUc",
		]
		fallback_first_click_css_selectors = [
			"[id='Skip to message list-region'] [role='option']",
			"[id='Skip to message list-region'] [role='listitem']",
			"[id='Skip to message list-region'] [aria-selected='false'][tabindex]",
			"[id='Skip to message list-region'] [aria-selected='true'][tabindex]",
			"div.jGG6V.epBmH.UWKUc",
			"div.jGG6V.UWKUc",
			"[role='option']",
			"[role='listitem']",
			"[aria-selected='false'][tabindex]",
			"[aria-selected='true'][tabindex]",
		]
		fallback_first_click_xpath_selectors = [
			"//div[@id='33' and contains(@class,'cbdbfb4-c021-0b05-8d42-9d895b1436de') and contains(@class,'jGG6V') and contains(@class,'epBmH') and contains(@class,'UWKUc')]",
			"//div[@id='33' and contains(@class,'jGG6V') and contains(@class,'epBmH') and contains(@class,'UWKUc')]",
			"//div[contains(@class,'cbdbfb4-c021-0b05-8d42-9d895b1436de') and contains(@class,'jGG6V') and contains(@class,'epBmH') and contains(@class,'UWKUc')]",
			"//div[contains(@class,'jGG6V') and contains(@class,'epBmH') and contains(@class,'UWKUc')]",
			"//div[contains(@class,'jGG6V') and contains(@class,'UWKUc')]",
			"//*[@role='option']",
			"//*[@role='listitem']",
			"//*[@aria-selected='false' and @tabindex]",
			"//*[@aria-selected='true' and @tabindex]",
		]
		second_click_selectors = [
			"button.ms-Button.ms-Button--icon.WQ1jB.H_URL.sF_gJ.dUIui.root-191",
			".ms-Button.ms-Button--icon.WQ1jB.H_URL.sF_gJ.dUIui.root-191",
			"[class~='ms-Button'][class~='ms-Button--icon'][class~='WQ1jB'][class~='H_URL'][class~='sF_gJ'][class~='dUIui'][class~='root-191']",
			"button[title='More actions']",
			"button[aria-label*='More actions']",
			"[role='button'][title='More actions']",
			"[role='button'][aria-label*='More actions']",
			"button.ms-Button.ms-Button--icon.root-191",
			".ms-Button.ms-Button--icon.root-191",
			"[class~='ms-Button'][class~='ms-Button--icon'][class~='root-191']",
		]
		second_click_xpath_selectors = [
			"//*[self::button or @role='button'][contains(translate(normalize-space(@aria-label), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more actions')]",
			"//*[self::button or @role='button'][contains(translate(normalize-space(@title), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more actions')]",
			"//*[self::button or @role='button'][contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'more actions')]",
		]
		third_click_selectors = [
			"button.ms-ContextualMenu-link.root-374",
			"a.ms-ContextualMenu-link.root-374",
			".ms-ContextualMenu-link.root-374",
			"[class~='ms-ContextualMenu-link'][class~='root-374']",
		]
		exact_third_click_xpath_selectors = [
			"//*[self::button or self::a][contains(@class,'ms-ContextualMenu-link') and contains(@class,'root-374')]",
		]
		third_click_xpath_selectors = [
			"//*[self::button or self::a][contains(@class,'ms-ContextualMenu-link') and contains(@class,'root-374')]",
			"//*[self::button or self::a][contains(@class,'ms-ContextualMenu-link') and contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')]",
		]

		download_snapshot = snapshot_completed_downloads()
		configured_download_directory = configure_download_directory(artifact_folder)
		artifact_download_snapshot = snapshot_completed_downloads(artifact_folder) if configured_download_directory else {}
		download_started_at = time.time()

		first_click_completed = click_outlook_target(
			css_selectors=attachment_first_click_css_selectors,
			timeout_seconds=max(6, min(timeout, 12)),
		)
		if not first_click_completed:
			first_click_completed = click_first_outlook_search_result(
				max(10, min(timeout, 20)),
			)
		if not first_click_completed:
			first_click_completed = click_outlook_target(
				css_selectors=fallback_first_click_css_selectors,
				xpath_selectors=fallback_first_click_xpath_selectors,
				timeout_seconds=max(10, min(timeout, 20)),
			)

		if not first_click_completed:
			print(
				f"Outlook download step 1 target was not found for alert: {alert_name}",
				file=sys.stderr,
			)
			return False

		time.sleep(0.75)

		if not click_outlook_target(
			css_selectors=second_click_selectors,
			xpath_selectors=second_click_xpath_selectors,
			target_text="More actions",
			timeout_seconds=max(10, min(timeout, 20)),
		):
			print(
				f"Outlook download step 2 target was not found for alert: {alert_name}",
				file=sys.stderr,
			)
			return False

		time.sleep(0.5)

		third_click_completed = click_outlook_target(
			css_selectors=third_click_selectors,
			xpath_selectors=exact_third_click_xpath_selectors,
			timeout_seconds=max(6, min(timeout, 12)),
		)
		if not third_click_completed:
			third_click_completed = click_outlook_target(
				css_selectors=third_click_selectors,
				xpath_selectors=third_click_xpath_selectors,
				target_text="Download",
				timeout_seconds=max(10, min(timeout, 20)),
			)

		if not third_click_completed:
			print(
				f"Outlook download step 3 target was not found for alert: {alert_name}",
				file=sys.stderr,
			)
			return False

		stored_download_path = None
		wait_timeout = max(timeout, 120)
		if configured_download_directory:
			stored_download_path = wait_for_new_completed_download(
				artifact_download_snapshot,
				download_started_at,
				wait_timeout,
				downloads_dir=artifact_folder,
			)
		if not stored_download_path:
			stored_download_path = move_download_to_alert_artifact_folder(
				alert_name,
				download_snapshot,
				download_started_at,
				wait_timeout,
			)
		if not stored_download_path:
			print(
				f"Outlook download was triggered for {alert_name}, but no new completed download was stored in the alert folder.",
				file=sys.stderr,
			)
			return False

		print(f"Stored downloaded Outlook file for {alert_name} to: {stored_download_path}")
		print(f"Executed Outlook click sequence for alert subject: {search_query}")
		return True
	except Exception as exc:  # noqa: BLE001
		print(
			f"Failed Outlook subject search for alert {alert_name}: {exc}",
			file=sys.stderr,
		)
		return False
	finally:
		try:
			driver.switch_to.window(original_handle)
		except WebDriverException:
			pass


def click_purview_alert_buttons_from_data(
	driver: WebDriver,
	text_path: str,
	timeout: int,
	startswith: str | None = None,
	outlook_handle: str | None = None,
	odin_handle: str | None = None,
) -> tuple[list[str], list[str]]:
	"""Click live Purview entries whose text matches alert names from db_info.txt."""
	alert_records = get_alert_records_from_data(text_path, startswith=startswith)
	if not alert_records:
		if startswith:
			print(f"No alert names starting with {startswith!r} found in db_info.txt.")
		else:
			print("No alert names found in db_info.txt.")
		return [], []

	if startswith:
		print(
			f"Processing {len(alert_records)} matching alert(s) from db_info.txt with prefix {startswith!r}."
		)
	else:
		print(f"Processing {len(alert_records)} alert(s) from db_info.txt.")

	def upload_alert_folder_files_to_odin(alert_name: str) -> None:
		if not odin_handle:
			return

		artifact_paths = list_alert_artifact_paths(alert_name)
		if not artifact_paths:
			print(f"[ODIN] No artifact files available to upload for alert: {alert_name}")
			return

		original_handle = driver.current_window_handle
		try:
			driver.switch_to.window(odin_handle)
			wait_for_servicenow_with_browser_refresh(
				driver,
				max(20, min(timeout, 45)),
				portal_label="ODIN",
			)

			file_input = _find_first_dom_element(
				driver,
				(
					"input#nputFilesOdin",
					"input[type='file']#nputFilesOdin",
					"input#inputFilesOdin",
					"input[type='file']#inputFilesOdin",
				),
			)
			if file_input is None:
				print(
					f"[ODIN] input#nputFilesOdin was not found for alert: {alert_name}",
					file=sys.stderr,
				)
				return

			try:
				driver.execute_script(
					"arguments[0].removeAttribute('hidden'); arguments[0].removeAttribute('disabled'); arguments[0].setAttribute('multiple','multiple'); arguments[0].style.display = 'block'; arguments[0].style.visibility = 'visible'; arguments[0].style.opacity = '1'; arguments[0].value = '';",
					file_input,
				)
			except WebDriverException:
				pass

			target_paths = [os.path.abspath(path) for path in artifact_paths if os.path.isfile(path)]
			if not target_paths:
				print(f"[ODIN] No existing files resolved for upload: {alert_name}")
				return

			file_input.send_keys("\n".join(target_paths))

			uploaded_count = 0
			try:
				uploaded_count = int(
					driver.execute_script(
						"return arguments[0] && arguments[0].files ? arguments[0].files.length : 0;",
						file_input,
					)
					or 0
				)
			except Exception:
				uploaded_count = 0

			print(
				f"[ODIN] Uploaded files to input#nputFilesOdin for alert {alert_name}: {uploaded_count}/{len(target_paths)}"
			)
		except Exception as exc:  # noqa: BLE001
			print(f"[ODIN] Failed immediate upload for alert {alert_name}: {exc}", file=sys.stderr)
		finally:
			try:
				driver.switch_to.window(original_handle)
			except WebDriverException:
				pass

	capture_targets = [
		{
			"panel_text": "Details",
			"panel_selector": '[data-automation-id="scc-workbenchtab-mip-details"]',
			"target_selector": ".itemsContainer-1540",
			"artifact_name": "itemsContainer-1540",
		},
		{
			"panel_text": "Classifiers",
			"panel_selector": '[data-automation-id="scc-workbenchtab-mip-classifiers"]',
			"target_selector": '[data-automationid="DetailsList"], .ms-DetailsList, .ms-List-surface, .ms-List, .ms-List-cell, .ms-DetailsRow-cell, .ms-DetailsRow-fields, [role="grid"], .itemContainer-1371, .itemContiner-1371, [class*="itemContainer-1371"], [class*="itemContiner-1371"], .itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]',
			"artifact_name": "classifiers",
		},
		{
			"panel_text": "Source",
			"panel_selector": '[data-automation-id="scc-workbenchtab-mip-source"], [data-automation-id="scc-workbenchtab-mip-native"]',
			"target_selector": 'canvas.ewr-sheettable, .ewr-sheettable, .ewr-grdblk, .ewr-grdblkflow, .ewr-grdblkflowclip',
			"artifact_name": "source",
		},
	]

	def normalize_visible_text(value: str | None) -> str:
		return " ".join(str(value or "").split()).strip().lower()

	def click_text_across_frames(button_text: str, preferred_selector: str | None = None) -> bool:
		click_script = r"""
		const targetText = String(arguments[0] || '').replace(/\s+/g, ' ').trim().toLowerCase();
		const preferredSelector = String(arguments[1] || '').trim();
		const seen = new Set();
		const docs = [];

		function collectDocs(root) {
			if (!root || seen.has(root)) {
				return;
			}
			seen.add(root);
			docs.push(root);
			const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node && node.shadowRoot) {
					collectDocs(node.shadowRoot);
				}
			}
		}

		function normalize(value) {
			return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
		}

		function isVisible(node) {
			if (!(node instanceof Element)) {
				return false;
			}
			const style = window.getComputedStyle(node);
			if (!style || style.display === 'none' || style.visibility === 'hidden') {
				return false;
			}
			const rect = node.getBoundingClientRect();
			return rect.width > 0 && rect.height > 0;
		}

		function resolvePreferredClickTarget(node) {
			if (!(node instanceof Element)) {
				return null;
			}
			const selfClickable = node.closest?.('button, a, [role="tab"], [role="button"], [role="link"]');
			if (selfClickable && isVisible(selfClickable)) {
				return selfClickable;
			}
			const descendantClickable = node.querySelector?.('button, a, [role="tab"], [role="button"], [role="link"]');
			if (descendantClickable && isVisible(descendantClickable)) {
				return descendantClickable;
			}
			if (isVisible(node)) {
				return node;
			}
			return null;
		}

		function clickPreferred(root) {
			if (!preferredSelector) {
				return false;
			}
			for (const node of root.querySelectorAll(preferredSelector)) {
				const clickTarget = resolvePreferredClickTarget(node);
				if (!clickTarget) {
					continue;
				}
				clickTarget.click();
				return true;
			}
			return false;
		}

		function clickTextFromTablist(root) {
			const tablists = root.querySelectorAll('[role="tablist"], .ms-Pivot, .scc-pivot');
			for (const tablist of tablists) {
				for (const selector of ['button', 'a', '[role="tab"]', '[role="button"]', 'span', 'div']) {
					for (const node of tablist.querySelectorAll(selector)) {
						const text = normalize(node.innerText || node.textContent || '');
						if (!text || (text !== targetText && !text.includes(targetText)) || !isVisible(node)) {
							continue;
						}
						node.click();
						return true;
					}
				}
			}
			return false;
		}

		collectDocs(document);
		for (const root of docs) {
			if (clickPreferred(root)) {
				return true;
			}
			if (clickTextFromTablist(root)) {
				return true;
			}
		}
		return false;
		"""

		def search_current_context() -> bool:
			try:
				if driver.execute_script(click_script, button_text, preferred_selector or ""):
					return True
			except WebDriverException:
				pass

			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					if search_current_context():
						return True
				except WebDriverException:
					pass
				finally:
					try:
						driver.switch_to.parent_frame()
					except WebDriverException:
						pass

			return False

		driver.switch_to.default_content()
		return search_current_context()

	def click_visible_selector_or_text_across_frames(
		preferred_selector: str = "",
		target_text: str = "",
	) -> bool:
		click_script = r"""
		const preferredSelector = String(arguments[0] || '').trim();
		const targetText = String(arguments[1] || '').replace(/\s+/g, ' ').trim().toLowerCase();
		const seen = new Set();
		const docs = [];

		function collectDocs(root) {
			if (!root || seen.has(root)) {
				return;
			}
			seen.add(root);
			docs.push(root);
			const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node && node.shadowRoot) {
					collectDocs(node.shadowRoot);
				}
			}
		}

		function queryAllDeep(root, selector) {
			const out = [];
			const seenRoots = new Set();
			const stack = [root];

			while (stack.length) {
				const node = stack.pop();
				if (!node || seenRoots.has(node)) {
					continue;
				}
				seenRoots.add(node);

				try {
					if (node.querySelectorAll) {
						out.push(...Array.from(node.querySelectorAll(selector)));
					}
				} catch (_) {}

				let elements = [];
				try {
					elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
				} catch (_) {}

				for (const element of elements) {
					if (element.shadowRoot) {
						stack.push(element.shadowRoot);
					}
				}
			}

			return out;
		}

		function normalize(value) {
			return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
		}

		function isVisible(node) {
			if (!(node instanceof Element)) {
				return false;
			}
			const style = window.getComputedStyle(node);
			if (!style || style.display === 'none' || style.visibility === 'hidden') {
				return false;
			}
			const rect = node.getBoundingClientRect();
			return rect.width > 0 && rect.height > 0;
		}

		function resolveClickTarget(node, requireVisible = true) {
			if (!(node instanceof Element)) {
				return null;
			}
			const selfClickable = node.closest?.('button, a, [role="menuitem"], [role="button"], [role="link"], [tabindex]');
			if (selfClickable && (!requireVisible || isVisible(selfClickable))) {
				return selfClickable;
			}
			const descendantClickable = node.querySelector?.('button, a, [role="menuitem"], [role="button"], [role="link"], [tabindex]');
			if (descendantClickable && (!requireVisible || isVisible(descendantClickable))) {
				return descendantClickable;
			}
			if (!requireVisible || isVisible(node)) {
				return node;
			}
			return null;
		}

		function clickNode(node, requireVisible = true) {
			const clickTarget = resolveClickTarget(node, requireVisible);
			if (!(clickTarget instanceof Element)) {
				return false;
			}

			if (requireVisible && !isVisible(clickTarget)) {
				return false;
			}

			try {
				clickTarget.scrollIntoView({ block: 'center', inline: 'center' });
			} catch (_) {}

			try {
				clickTarget.focus?.();
			} catch (_) {}

			try {
				clickTarget.click();
				return true;
			} catch (_) {}

			try {
				for (const eventName of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
					clickTarget.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
				}
				return true;
			} catch (_) {}

			return false;
		}

		collectDocs(document);

		if (preferredSelector) {
			for (const root of docs) {
				for (const node of queryAllDeep(root, preferredSelector)) {
					if (!clickNode(node, false)) {
						continue;
					}
					return true;
				}
			}
		}

		if (targetText) {
			const selectors = ['button', 'a', '[role="menuitem"]', '[role="button"]', '[role="link"]', 'span', 'div'];
			for (const root of docs) {
				for (const selector of selectors) {
					for (const node of queryAllDeep(root, selector)) {
						const text = normalize(node.innerText || node.textContent || '');
						if (!text || (text !== targetText && !text.includes(targetText))) {
							continue;
						}
						if (!clickNode(node)) {
							continue;
						}
						return true;
					}
				}
			}
		}

		return false;
		"""

		def search_current_context() -> bool:
			try:
				if driver.execute_script(click_script, preferred_selector or "", target_text or ""):
					return True
			except WebDriverException:
				pass

			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					if search_current_context():
						return True
				except WebDriverException:
					pass
				finally:
					try:
						driver.switch_to.parent_frame()
					except WebDriverException:
						pass

			return False

		driver.switch_to.default_content()
		return search_current_context()

	def click_source_download_action_across_frames(panel_timeout: int) -> bool:
		deadline = time.time() + panel_timeout
		menu_button_selectors = [
			'button[aria-label*="View more event actions"]',
			'[role="menuitem"][aria-label*="View more event actions"]',
			'.ms-Button.ms-Button--hasMenu[aria-label*="View more event actions"]',
			'button.ms-Button.ms-Button--icon.ms-Button--hasMenu',
			'button.ms-Button.root-1550',
			'.ms-Button.root-1550',
			'[class~="ms-Button"][class~="root-1550"]',
			'button[class~="root-1550"]',
			'[role="button"][class~="root-1550"]',
			'[role="menuitem"][class~="root-1550"]',
			'[class*="root-1550"][class*="ms-Button"]',
			'[class*="root-1550"] .ms-Button-flexContainer',
			'button.ms-Button.root-1611',
			'.ms-Button.root-1611',
			'[class~="ms-Button"][class~="root-1611"]',
			'button[class~="root-1611"]',
			'[role="button"][class~="root-1611"]',
			'[class*="root-1611"][class*="ms-Button"]',
			'[class*="root-1611"] .ms-Button-flexContainer',
			'button.ms-Button.root-1555',
			'.ms-Button.root-1555',
			'[class~="ms-Button"][class~="root-1555"]',
			'button[class~="root-1555"]',
			'[role="button"][class~="root-1555"]',
			'[class*="root-1555"][class*="ms-Button"]',
			'[class*="root-1555"] .ms-Button-flexContainer',
		]
		menu_item_selectors = [
			'span.ms-ContextualMenu-itemText.label',
			'span.ms-ContextualMenu-itemText',
		]
		menu_item_xpath_selectors = [
			(
				"//*[self::span or self::button or self::a or @role='button' or @role='menuitem' or @role='link']"
				"[contains(translate(normalize-space(string(.)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')"
				" or contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')"
				" or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download')]"
			)
		]

		def click_visible_native_across_frames(
			css_selectors: list[str] | None = None,
			xpath_selectors: list[str] | None = None,
			target_text: str = "",
		) -> bool:
			selectors = css_selectors or []
			xpaths = xpath_selectors or []
			normalized_target_text = str(target_text or "").strip().lower()

			def try_click(element: WebElement) -> bool:
				try:
					if not element.is_displayed():
						return False
				except WebDriverException:
					return False

				try:
					driver.execute_script(
						"arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
						element,
					)
				except WebDriverException:
					pass

				try:
					ActionChains(driver).move_to_element(element).click().perform()
					return True
				except WebDriverException:
					pass

				try:
					element.click()
					return True
				except WebDriverException:
					return False

			def find_deep_click_target() -> WebElement | None:
				lookup_script = r"""
				const selectors = arguments[0] || [];
				const targetText = String(arguments[1] || '').replace(/\s+/g, ' ').trim().toLowerCase();
				const seen = new Set();
				const docs = [];

				function collectDocs(root) {
				  if (!root || seen.has(root)) {
				    return;
				  }
				  seen.add(root);
				  docs.push(root);
				  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
				  while (walker.nextNode()) {
				    const node = walker.currentNode;
				    if (node && node.shadowRoot) {
				      collectDocs(node.shadowRoot);
				    }
				  }
				}

				function queryAllDeep(root, selector) {
				  const out = [];
				  const seenRoots = new Set();
				  const stack = [root];

				  while (stack.length) {
				    const node = stack.pop();
				    if (!node || seenRoots.has(node)) {
				      continue;
				    }
				    seenRoots.add(node);

				    try {
				      if (node.querySelectorAll) {
				        out.push(...Array.from(node.querySelectorAll(selector)));
				      }
				    } catch (_) {}

				    let elements = [];
				    try {
				      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
				    } catch (_) {}

				    for (const element of elements) {
				      if (element.shadowRoot) {
				        stack.push(element.shadowRoot);
				      }
				    }
				  }

				  return out;
				}

				function normalize(value) {
				  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
				}

				function isVisible(node) {
				  if (!(node instanceof Element)) {
				    return false;
				  }
				  const style = window.getComputedStyle(node);
				  if (!style || style.display === 'none' || style.visibility === 'hidden') {
				    return false;
				  }
				  const rect = node.getBoundingClientRect();
				  return rect.width > 0 && rect.height > 0;
				}

				function resolveClickTarget(node) {
				  if (!(node instanceof Element)) {
				    return null;
				  }
				  const selfClickable = node.closest?.('button, a, [role="menuitem"], [role="button"], [role="link"], [tabindex]');
				  if (selfClickable && isVisible(selfClickable)) {
				    return selfClickable;
				  }
				  const descendantClickable = node.querySelector?.('button, a, [role="menuitem"], [role="button"], [role="link"], [tabindex]');
				  if (descendantClickable && isVisible(descendantClickable)) {
				    return descendantClickable;
				  }
				  if (isVisible(node)) {
				    return node;
				  }
				  return null;
				}

				function matchesTargetText(node, clickTarget) {
				  if (!targetText) {
				    return true;
				  }
				  const candidates = [
				    normalize(node?.innerText || node?.textContent || ''),
				    normalize(clickTarget?.innerText || clickTarget?.textContent || ''),
				    normalize(clickTarget?.getAttribute?.('aria-label') || ''),
				    normalize(clickTarget?.getAttribute?.('title') || ''),
				  ];
				  return candidates.some((candidate) => candidate && (candidate === targetText || candidate.includes(targetText)));
				}

				collectDocs(document);

				for (const root of docs) {
				  for (const selector of selectors) {
				    for (const node of queryAllDeep(root, selector)) {
				      const clickTarget = resolveClickTarget(node);
				      if (clickTarget && matchesTargetText(node, clickTarget)) {
				        return clickTarget;
				      }
				    }
				  }

				  if (targetText) {
				    for (const selector of ['button', 'a', '[role="menuitem"]', '[role="button"]', '[role="link"]', 'span', 'div']) {
				      for (const node of queryAllDeep(root, selector)) {
				        const text = normalize(node.innerText || node.textContent || '');
				        if (!text || (text !== targetText && !text.includes(targetText))) {
				          continue;
				        }
				        const clickTarget = resolveClickTarget(node);
				        if (clickTarget) {
				          return clickTarget;
				        }
				      }
				    }
				  }
				}

				return null;
				"""

				try:
					result = driver.execute_script(lookup_script, selectors, normalized_target_text)
				except WebDriverException:
					return None

				return result if isinstance(result, WebElement) else None

			def search_current_context() -> bool:
				deep_target = find_deep_click_target()
				if deep_target and try_click(deep_target):
					return True

				for selector in selectors:
					try:
						elements = driver.find_elements(By.CSS_SELECTOR, selector)
					except WebDriverException:
						continue
					for element in elements:
						if normalized_target_text:
							text_candidates = [
								str(element.text or "").strip().lower(),
								str(element.get_attribute("aria-label") or "").strip().lower(),
								str(element.get_attribute("title") or "").strip().lower(),
							]
							if not any(
								candidate and (candidate == normalized_target_text or normalized_target_text in candidate)
								for candidate in text_candidates
							):
								continue
						if try_click(element):
							return True

				for xpath in xpaths:
					try:
						elements = driver.find_elements(By.XPATH, xpath)
					except WebDriverException:
						continue
					for element in elements:
						if normalized_target_text:
							text_candidates = [
								str(element.text or "").strip().lower(),
								str(element.get_attribute("aria-label") or "").strip().lower(),
								str(element.get_attribute("title") or "").strip().lower(),
							]
							if not any(
								candidate and (candidate == normalized_target_text or normalized_target_text in candidate)
								for candidate in text_candidates
							):
								continue
						if try_click(element):
							return True

				frames = driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')
				for frame_index in range(len(frames)):
					try:
						frames = driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')
						if frame_index >= len(frames):
							continue
						driver.switch_to.frame(frames[frame_index])
						if search_current_context():
							return True
					except WebDriverException:
						pass
					finally:
						try:
							driver.switch_to.parent_frame()
						except WebDriverException:
							pass

				return False

			driver.switch_to.default_content()
			return search_current_context()

		menu_opened = False
		while time.time() < deadline and not menu_opened:
			menu_opened = click_visible_native_across_frames(
				css_selectors=menu_button_selectors,
				target_text='View more event actions',
			)
			if not menu_opened:
				menu_opened = click_visible_native_across_frames(css_selectors=menu_button_selectors)
			if not menu_opened:
				time.sleep(0.25)

		if not menu_opened:
			return False

		while time.time() < deadline:
			if click_visible_native_across_frames(
				css_selectors=menu_item_selectors,
				xpath_selectors=menu_item_xpath_selectors,
				target_text='Download',
			):
				return True
			click_visible_native_across_frames(
				css_selectors=menu_button_selectors,
				target_text='View more event actions',
			)
			time.sleep(0.25)

		return False

	def attempt_source_download_storage(alert_name: str, panel_timeout: int) -> None:
		artifact_folder = get_alert_artifact_folder(alert_name)

		def configure_download_directory(download_dir: str) -> bool:
			os.makedirs(download_dir, exist_ok=True)
			commands = [
				(
					'Browser.setDownloadBehavior',
					{
						'behavior': 'allow',
						'downloadPath': download_dir,
						'eventsEnabled': False,
					},
				),
				(
					'Page.setDownloadBehavior',
					{
						'behavior': 'allow',
						'downloadPath': download_dir,
					},
				),
			]

			for command_name, command_params in commands:
				try:
					driver.execute_cdp_cmd(command_name, command_params)
					return True
				except Exception:
					continue

			return False

		def save_source_download_control_debug(debug_output_path: str) -> None:
			capture_script = r"""
			const seen = new Set();
			const docs = [];

			function collectDocs(root) {
			  if (!root || seen.has(root)) {
			    return;
			  }
			  seen.add(root);
			  docs.push(root);
			  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
			  while (walker.nextNode()) {
			    const node = walker.currentNode;
			    if (node && node.shadowRoot) {
			      collectDocs(node.shadowRoot);
			    }
			  }
			}

			function queryAllDeep(root, selector) {
			  const out = [];
			  const seenRoots = new Set();
			  const stack = [root];

			  while (stack.length) {
			    const node = stack.pop();
			    if (!node || seenRoots.has(node)) {
			      continue;
			    }
			    seenRoots.add(node);

			    try {
			      if (node.querySelectorAll) {
			        out.push(...Array.from(node.querySelectorAll(selector)));
			      }
			    } catch (_) {}

			    let elements = [];
			    try {
			      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
			    } catch (_) {}

			    for (const element of elements) {
			      if (element.shadowRoot) {
			        stack.push(element.shadowRoot);
			      }
			    }
			  }

			  return out;
			}

			function normalize(value) {
			  return String(value || '').replace(/\s+/g, ' ').trim();
			}

			function isVisible(node) {
			  if (!(node instanceof Element)) {
			    return false;
			  }
			  const style = window.getComputedStyle(node);
			  if (!style || style.display === 'none' || style.visibility === 'hidden') {
			    return false;
			  }
			  const rect = node.getBoundingClientRect();
			  return rect.width > 0 && rect.height > 0;
			}

			function isInteresting(node) {
			  const className = normalize(node.className || '').toLowerCase();
			  const text = normalize(node.innerText || node.textContent || '').toLowerCase();
			  const ariaLabel = normalize(node.getAttribute?.('aria-label') || '').toLowerCase();
			  const title = normalize(node.getAttribute?.('title') || '').toLowerCase();
			  return (
			    className.includes('ms-button') ||
			    className.includes('contextualmenu') ||
			    className.includes('root-') ||
			    text.includes('download') ||
			    ariaLabel.includes('download') ||
			    title.includes('download')
			  );
			}

			collectDocs(document);
			const selectors = ['button', 'a', '[role="button"]', '[role="menuitem"]', '[role="link"]', '.ms-Button', 'span.ms-ContextualMenu-itemText.label'];
			const results = [];
			const emitted = new Set();

			for (const root of docs) {
			  for (const selector of selectors) {
			    for (const node of queryAllDeep(root, selector)) {
			      if (!(node instanceof Element) || !isVisible(node) || !isInteresting(node)) {
			        continue;
			      }
			      const key = `${selector}::${node.outerHTML?.slice(0, 120) || ''}`;
			      if (emitted.has(key)) {
			        continue;
			      }
			      emitted.add(key);
			      results.push({
			        selector,
			        tagName: normalize(node.tagName || ''),
			        text: normalize(node.innerText || node.textContent || ''),
			        ariaLabel: normalize(node.getAttribute?.('aria-label') || ''),
			        title: normalize(node.getAttribute?.('title') || ''),
			        role: normalize(node.getAttribute?.('role') || ''),
			        className: normalize(node.className || ''),
			        outerHTML: normalize(node.outerHTML || '').slice(0, 400),
			      });
			      if (results.length >= 120) {
			        return results;
			      }
			    }
			  }
			}

			return results;
			"""

			debug_payload: dict[str, object] = {
				'alert_name': alert_name,
				'controls': [],
			}

			try:
				debug_payload['title'] = driver.title
			except Exception:
				debug_payload['title'] = ''

			try:
				debug_payload['url'] = driver.current_url
			except Exception:
				debug_payload['url'] = ''

			collected_controls: list[dict[str, object]] = []

			def search_current_context(frame_path: list[int]) -> None:
				if len(collected_controls) >= 200:
					return

				try:
					results = driver.execute_script(capture_script)
				except WebDriverException:
					results = []

				if isinstance(results, list):
					for entry in results:
						if not isinstance(entry, dict):
							continue
						entry['frame_path'] = list(frame_path)
						collected_controls.append(entry)
						if len(collected_controls) >= 200:
							return

				try:
					frames = driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')
				except WebDriverException:
					frames = []

				for frame_index in range(len(frames)):
					try:
						frames = driver.find_elements(By.CSS_SELECTOR, 'iframe,frame')
						if frame_index >= len(frames):
							continue
						driver.switch_to.frame(frames[frame_index])
						search_current_context(frame_path + [frame_index])
					except WebDriverException:
						pass
					finally:
						try:
							driver.switch_to.parent_frame()
						except WebDriverException:
							pass

			driver.switch_to.default_content()
			search_current_context([])
			debug_payload['controls'] = collected_controls

			with open(debug_output_path, 'w', encoding='utf-8') as debug_file:
				json.dump(debug_payload, debug_file, indent=2)

		download_snapshot = snapshot_completed_downloads()
		configured_download_directory = configure_download_directory(artifact_folder)
		artifact_download_snapshot = snapshot_completed_downloads(artifact_folder) if configured_download_directory else {}
		download_started_at = time.time()
		if not click_source_download_action_across_frames(panel_timeout):
			debug_output_path = os.path.join(artifact_folder, 'source-download-controls-debug.json')
			try:
				save_source_download_control_debug(debug_output_path)
				print(f"Saved Source download control debug for {alert_name} to: {debug_output_path}", file=sys.stderr)
			except Exception as exc:
				print(f"Failed to save Source download control debug for {alert_name}: {exc}", file=sys.stderr)
			print(f"Source download controls were not found for: {alert_name}", file=sys.stderr)
			return

		print(f"Clicked Source download controls for: {alert_name}")
		stored_download_path = None
		wait_timeout = max(panel_timeout, 120)
		if configured_download_directory:
			stored_download_path = wait_for_new_completed_download(
				artifact_download_snapshot,
				download_started_at,
				wait_timeout,
				downloads_dir=artifact_folder,
			)
		if not stored_download_path:
			stored_download_path = move_download_to_alert_artifact_folder(
				alert_name,
				download_snapshot,
				download_started_at,
				wait_timeout,
			)
		if stored_download_path:
			print(f"Stored downloaded source file for {alert_name} to: {stored_download_path}")
			try:
				refreshed_classifiers_path, refreshed_sit_path = refresh_saved_classifier_artifacts(alert_name)
				if refreshed_classifiers_path:
					print(f"Refreshed classifiers HTML for {alert_name} after Source download: {refreshed_classifiers_path}")
				if refreshed_sit_path:
					print(f"Refreshed SIT analysis HTML for {alert_name} after Source download: {refreshed_sit_path}")
			except Exception as exc:  # noqa: BLE001
				print(
					f"Failed to refresh classifiers/SIT artifacts after Source download for {alert_name}: {exc}",
					file=sys.stderr,
				)
		else:
			debug_output_path = os.path.join(artifact_folder, 'source-download-controls-debug.json')
			try:
				save_source_download_control_debug(debug_output_path)
				print(f"Saved Source download control debug for {alert_name} to: {debug_output_path}", file=sys.stderr)
			except Exception as exc:
				print(f"Failed to save Source download control debug for {alert_name}: {exc}", file=sys.stderr)
			print(
				f"Source file was triggered for {alert_name}, but no new completed download was stored in the alert folder.",
				file=sys.stderr,
			)

	def capture_class_html_across_frames(class_name: str, panel_timeout: int) -> str:
		capture_script = r"""
		const selector = String(arguments[0] || '').trim();
		const seen = new Set();
		const docs = [];

		function collectDocs(root) {
			if (!root || seen.has(root)) {
				return;
			}
			seen.add(root);
			docs.push(root);
			const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node && node.shadowRoot) {
					collectDocs(node.shadowRoot);
				}
			}
		}

		function isVisible(node) {
			if (!(node instanceof Element)) {
				return false;
			}
			const style = window.getComputedStyle(node);
			if (!style || style.display === 'none' || style.visibility === 'hidden') {
				return false;
			}
			const rect = node.getBoundingClientRect();
			return rect.width > 0 && rect.height > 0;
		}

		function escapeHtml(value) {
			return String(value || '')
				.replace(/&/g, '&amp;')
				.replace(/</g, '&lt;')
				.replace(/>/g, '&gt;')
				.replace(/\"/g, '&quot;')
				.replace(/'/g, '&#39;');
		}

		function getNodeText(node) {
			return String(node?.innerText || node?.textContent || '')
				.replace(/\u00a0/g, ' ')
				.trim();
		}

		function buildRowsTableHtml(headers, rows) {
			const cellStyle = 'border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; white-space: pre-wrap;';
			const normalizedRows = rows
				.map((cells) => Array.isArray(cells) ? cells.map((cell) => String(cell || '')) : [])
				.filter((cells) => cells.some((cell) => cell.trim()));
			if (!normalizedRows.length) {
				return '';
			}

			const theadHtml = headers.length
				? `<thead><tr>${headers.map((header) => `<th style="${cellStyle}">${escapeHtml(header)}</th>`).join('')}</tr></thead>`
				: '';
			const bodyRows = normalizedRows
				.map((cells) => `<tr>${cells.map((cell) => `<td style="${cellStyle}">${escapeHtml(cell)}</td>`).join('')}</tr>`)
				.join('');
			return `<table class="captured-details-table" style="border-collapse: collapse; width: 100%; table-layout: fixed;">${theadHtml}<tbody>${bodyRows}</tbody></table>`;
		}

		function getMetadataRows(source) {
			if (!(source instanceof Element)) {
				return [];
			}

			const directRows = Array.from(source.children || [])
				.filter((child) => child.matches?.('.itemContiner-1541, .itemContainer-1541'));
			const rawRows = directRows.length
				? directRows
				: Array.from(source.querySelectorAll('.itemContiner-1541, .itemContainer-1541'));

			return rawRows
				.map((row) => {
					const label = getNodeText(row.querySelector('h3, [role="heading"], [class*="header-"]'));
					const bodyNode = row.querySelector('.body-1537, [class*="body-"], [data-automation-id="visibleContent"]')
						|| row.querySelector('.ms-TooltipHost, a, span, div');
					const value = getNodeText(bodyNode);
					return [label, value];
				})
				.filter((cells) => cells.some((cell) => String(cell || '').trim()));
		}

		function buildMetadataTableHtml(node) {
			if (!(node instanceof Element)) {
				return '';
			}

			const source = node.matches?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]')
				? node
				: node.querySelector?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]')
					|| node.closest?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]');
			if (!(source instanceof Element)) {
				return '';
			}

			return buildRowsTableHtml(['Field', 'Value'], getMetadataRows(source));
		}

		function prettifyAutomationKey(key) {
			const knownLabels = {
				SITName: 'Sensitive information type',
				MatchedSensitiveContent: 'Matched sensitive content',
				Location: 'Location',
				SurroundingCharacters: 'Surrounding characters',
			};
			if (knownLabels[key]) {
				return knownLabels[key];
			}
			return String(key || '')
				.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
				.replace(/[_-]+/g, ' ')
				.replace(/\s+/g, ' ')
				.trim() || 'Value';
		}

		function buildDetailsRowFieldsTableHtmlFromRoots(rowRoots) {
			const rowsByKey = new Map();
			const columnOrder = [];
			const columnLabels = new Map();

			for (const rowRoot of rowRoots) {
				if (!(rowRoot instanceof Element)) {
					continue;
				}

				const rowValues = new Map();
				for (const cell of rowRoot.querySelectorAll('[data-automationid="DetailsRowCell"], .ms-DetailsRow-cell, [role="gridcell"]')) {
					if (!(cell instanceof Element)) {
						continue;
					}
					const key = String(cell.getAttribute('data-automation-key') || '').trim()
						|| `Column${columnOrder.length + rowValues.size + 1}`;
					const value = getNodeText(cell);
					if (!rowValues.has(key)) {
						rowValues.set(key, value);
					}
					if (!columnLabels.has(key)) {
						columnLabels.set(key, prettifyAutomationKey(key));
					}
					if (!columnOrder.includes(key)) {
						columnOrder.push(key);
					}
				}

				if (!rowValues.size) {
					continue;
				}

				const rowKey = columnOrder.map((key) => rowValues.get(key) || '').join('::');
				if (!rowKey || rowsByKey.has(rowKey)) {
					continue;
				}
				rowsByKey.set(rowKey, rowValues);
			}

			if (!rowsByKey.size || !columnOrder.length) {
				return '';
			}

			const activeColumns = columnOrder.filter((key) => {
				for (const rowValues of rowsByKey.values()) {
					if (String(rowValues.get(key) || '').trim()) {
						return true;
					}
				}
				return false;
			});

			if (!activeColumns.length) {
				return '';
			}

			const headers = activeColumns.map((key) => columnLabels.get(key) || prettifyAutomationKey(key));
			const rows = Array.from(rowsByKey.values()).map((rowValues) =>
				activeColumns.map((key) => rowValues.get(key) || '')
			);
			return buildRowsTableHtml(headers, rows);
		}

		function buildMergedDetailsRowFieldsHtml(node) {
			const wantsRowFields = selector.includes('ms-DetailsRow-fields') || selector.includes('DetailsRowFields');
			if (!wantsRowFields || !(node instanceof Element)) {
				return '';
			}

			const rowFieldSelectors = '[data-automationid="DetailsRowFields"], .ms-DetailsRow-fields';
			const containerSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"], [role="tabpanel"], .itemContainer-1371, .itemContiner-1371, [class*="itemContainer-1371"], [class*="itemContiner-1371"]';
			const containers = [];
			const seenContainers = new Set();

			function addContainer(candidate) {
				if (!(candidate instanceof Element) || seenContainers.has(candidate)) {
					return;
				}
				seenContainers.add(candidate);
				containers.push(candidate);
			}

			addContainer(node.closest?.(containerSelectors));
			if (node.matches?.(containerSelectors)) {
				addContainer(node);
			}
			for (const candidate of node.querySelectorAll?.(containerSelectors) || []) {
				addContainer(candidate);
			}
			if (!containers.length) {
				addContainer(node);
			}

			const htmlParts = [];
			const rowRoots = [];
			const seenRows = new Set();
			for (const container of containers) {
				const rowFieldNodes = [];
				if (container.matches?.(rowFieldSelectors)) {
					rowFieldNodes.push(container);
				}
				for (const rowFieldNode of container.querySelectorAll?.(rowFieldSelectors) || []) {
					rowFieldNodes.push(rowFieldNode);
				}

				for (const rowFieldNode of rowFieldNodes) {
					const rowRoot = rowFieldNode.closest?.('.ms-List-cell')
						|| rowFieldNode.closest?.('[data-automationid="DetailsRow"], .ms-DetailsRow')
						|| rowFieldNode;
					if (!(rowRoot instanceof Element)) {
						continue;
					}
					const html = String(rowRoot.outerHTML || '').trim();
					if (!html || seenRows.has(html)) {
						continue;
					}
					seenRows.add(html);
					rowRoots.push(rowRoot);
					htmlParts.push(html);
				}
			}

			if (!htmlParts.length) {
				return '';
			}

			const tableHtml = buildDetailsRowFieldsTableHtmlFromRoots(rowRoots);

			if (tableHtml) {
				return `<div class="captured-ms-details-row-fields">${tableHtml}</div>`;
			}

			return `<div class="captured-ms-details-row-fields">${htmlParts.join('')}</div>`;
		}

		function buildMergedListCellHtml(node) {
			const wantsListCells = selector.includes('ms-List-cell') || selector.includes('ms-List-surface') || selector.includes('ms-List');
			if (!wantsListCells || !(node instanceof Element)) {
				return '';
			}

			const rootSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"]';
			const roots = [];
			const seenRoots = new Set();

			function addRoot(candidate) {
				if (!(candidate instanceof Element) || seenRoots.has(candidate)) {
					return;
				}
				seenRoots.add(candidate);
				roots.push(candidate);
			}

			addRoot(node.closest?.(rootSelectors));
			if (node.matches?.(rootSelectors)) {
				addRoot(node);
			}
			for (const candidate of node.querySelectorAll?.(rootSelectors) || []) {
				addRoot(candidate);
			}

			const htmlParts = [];
			const seenCells = new Set();
			for (const root of roots) {
				for (const cell of root.querySelectorAll('.ms-List-cell')) {
					if (!(cell instanceof Element)) {
						continue;
					}
					const html = String(cell.outerHTML || '').trim();
					if (!html || seenCells.has(html)) {
						continue;
					}
					seenCells.add(html);
					htmlParts.push(html);
				}
			}

			if (!htmlParts.length) {
				return '';
			}

			return `<div class="captured-ms-list-cells">${htmlParts.join('')}</div>`;
		}

		function buildTableHtml(node) {
			if (!(node instanceof Element)) {
				return '';
			}

			const source = node.matches?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]')
				? node
				: node.querySelector?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]')
					|| node.closest?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]');
			if (!(source instanceof Element)) {
				return '';
			}

			const headers = Array.from(source.querySelectorAll('[role="columnheader"]'))
				.map((header) => getNodeText(header))
				.filter(Boolean);
			const cellSelector = '[role="gridcell"], .ms-DetailsRow-cell, .ms-DetailsRow-fields, td';
			const rows = Array.from(source.querySelectorAll('[data-automationid="DetailsRow"], .ms-DetailsRow, [role="row"]'))
				.map((row) => Array.from(row.querySelectorAll(cellSelector)).map((cell) => getNodeText(cell)).filter(Boolean))
				.filter((cells) => cells.length > 0);

			const gridTableHtml = buildRowsTableHtml(headers, rows);
			if (gridTableHtml) {
				return gridTableHtml;
			}

			return buildMetadataTableHtml(node);
		}

		function buildCanvasSnapshotHtml(node) {
			const wantsSourceCanvas = selector.includes('ewr-sheettable');
			if (!wantsSourceCanvas || !(node instanceof Element)) {
				return '';
			}

			function canvasLooksRendered(canvas) {
				if (!(canvas instanceof HTMLCanvasElement)) {
					return false;
				}

				const width = Number(canvas.width || canvas.clientWidth || 0);
				const height = Number(canvas.height || canvas.clientHeight || 0);
				if (width <= 1 || height <= 1) {
					return false;
				}

				try {
					const context = canvas.getContext('2d', { willReadFrequently: true });
					if (!context) {
						return false;
					}

					const sampleSize = Math.max(2, Math.min(12, Math.min(width, height)));
					const positions = [
						[0, 0],
						[Math.max(0, Math.floor((width - sampleSize) / 2)), Math.max(0, Math.floor((height - sampleSize) / 2))],
						[Math.max(0, width - sampleSize), Math.max(0, height - sampleSize)],
					];

					let baseline = '';
					for (const [x, y] of positions) {
						const imageData = context.getImageData(x, y, sampleSize, sampleSize).data;
						if (!imageData.length) {
							continue;
						}

						for (let index = 0; index < imageData.length; index += 4) {
							const pixel = `${imageData[index]},${imageData[index + 1]},${imageData[index + 2]},${imageData[index + 3]}`;
							if (!baseline) {
								baseline = pixel;
								continue;
							}
							if (pixel !== baseline) {
								return true;
							}
						}
					}
				} catch (_) {
					return isVisible(canvas) && width > 32 && height > 32;
				}

				return false;
			}

			const canvasSelector = 'canvas.ewr-sheettable';
			const containerSelector = '.ewr-grdblk, .ewr-grdblkflow, .ewr-grdblkflowclip';
			const canvases = [];
			if (node.matches?.(canvasSelector)) {
				canvases.push(node);
			}
			for (const canvas of node.querySelectorAll?.(canvasSelector) || []) {
				canvases.push(canvas);
			}

			if (!canvases.length && node.matches?.(containerSelector)) {
				return '';
			}

			let bestCanvas = null;
			let bestScore = -1;
			for (const canvas of canvases) {
				if (!(canvas instanceof HTMLCanvasElement)) {
					continue;
				}
				if (!canvasLooksRendered(canvas)) {
					continue;
				}
				const width = Number(canvas.width || canvas.clientWidth || 0);
				const height = Number(canvas.height || canvas.clientHeight || 0);
				const score = (isVisible(canvas) ? 1_000_000_000 : 0) + (width * height);
				if (score > bestScore) {
					bestCanvas = canvas;
					bestScore = score;
				}
			}

			if (!(bestCanvas instanceof HTMLCanvasElement)) {
				return '';
			}

			const width = Number(bestCanvas.width || bestCanvas.clientWidth || 0);
			const height = Number(bestCanvas.height || bestCanvas.clientHeight || 0);
			const altText = escapeHtml(`Source sheet snapshot ${width}x${height}`.trim());
			let dataUrl = '';
			try {
				dataUrl = String(bestCanvas.toDataURL('image/png') || '');
			} catch (_) {}

			if (dataUrl) {
				return `<figure class="captured-ewr-sheettable mb-0"><img src="${dataUrl}" alt="${altText}" style="max-width: 100%; height: auto; display: block; border: 1px solid #d0d7de; border-radius: 0.75rem; background: #fff;" /></figure>`;
			}

			const canvasHtml = String(bestCanvas.outerHTML || '').trim();
			if (!canvasHtml) {
				return '';
			}
			return `<div class="captured-ewr-sheettable">${canvasHtml}</div>`;
		}

		function serializeNode(node) {
			if (!(node instanceof Element)) {
				return '';
			}
			const canvasHtml = buildCanvasSnapshotHtml(node);
			const canvasScopeSelector = 'canvas.ewr-sheettable, .ewr-sheettable, .ewr-grdblk, .ewr-grdblkflow, .ewr-grdblkflowclip';
			if (selector.includes('ewr-sheettable') && (node.matches?.(canvasScopeSelector) || node.querySelector?.('canvas.ewr-sheettable'))) {
				return canvasHtml || '';
			}
			return canvasHtml || buildMergedDetailsRowFieldsHtml(node) || buildMergedListCellHtml(node) || buildTableHtml(node) || node.outerHTML || '';
		}

		function findScopedMatches(root) {
			const results = [];
			const activePanels = Array.from(root.querySelectorAll('[role="tabpanel"][aria-hidden="false"], [role="tabpanel"]:not([aria-hidden="true"])'));
			for (const panel of activePanels) {
				if (panel.matches?.(selector)) {
					results.push(panel);
				}
				results.push(...panel.querySelectorAll(selector));
			}
			return results;
		}

		collectDocs(document);
		let fallback = '';
		for (const root of docs) {
			const scopedNodes = findScopedMatches(root);
			const nodes = scopedNodes.length ? scopedNodes : Array.from(root.querySelectorAll(selector));
			for (const node of nodes) {
				const formattedHtml = serializeNode(node);
				if (formattedHtml && !fallback) {
					fallback = formattedHtml;
				}
				if (formattedHtml && isVisible(node)) {
					return formattedHtml;
				}
			}
		}
		return fallback;
		"""
		fallback_html = ""
		deadline = time.time() + panel_timeout

		def search_current_context() -> str:
			nonlocal fallback_html
			try:
				outer_html = (driver.execute_script(capture_script, class_name) or "").strip()
				if outer_html and not fallback_html:
					fallback_html = outer_html
				if outer_html:
					return outer_html
			except WebDriverException:
				pass

			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					html_fragment = search_current_context()
					if html_fragment:
						return html_fragment
				except WebDriverException:
					pass
				finally:
					try:
						driver.switch_to.parent_frame()
					except WebDriverException:
						pass

			return ""

		while time.time() < deadline:
			driver.switch_to.default_content()
			html_fragment = search_current_context()
			if html_fragment:
				return html_fragment
			time.sleep(0.25)

		return fallback_html

	def click_open_alerts_page_from_panel(
		alert_name: str,
		alert_location: str,
		panel_timeout: int,
	) -> bool:
		panel_click_script = r"""
		const buttonText = arguments[0];

		function normalize(text) {
		  return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
		}

		function visible(el) {
		  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
		}

		function collectDocs(rootDoc) {
		  const docs = [rootDoc];
		  for (const frame of Array.from(rootDoc.querySelectorAll('iframe'))) {
		    try {
		      const fdoc = frame.contentDocument || frame.contentWindow?.document;
		      if (fdoc) docs.push(fdoc);
		    } catch (_) {
		      // Ignore cross-origin frames.
		    }
		  }
		  return docs;
		}

		function queryAllDeep(root, selector) {
		  const out = [];
		  const seen = new Set();
		  const stack = [root];

		  while (stack.length) {
		    const node = stack.pop();
		    if (!node || seen.has(node)) continue;
		    seen.add(node);

		    try {
		      if (node.querySelectorAll) {
		        out.push(...Array.from(node.querySelectorAll(selector)));
		      }
		    } catch (_) {}

		    let elements = [];
		    try {
		      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
		    } catch (_) {}

		    for (const el of elements) {
		      if (el.shadowRoot) {
		        stack.push(el.shadowRoot);
		      }
		    }
		  }

		  return out;
		}

		function asClickable(el) {
		  if (!el) return null;
		  return el.closest?.('button, a, [role="button"], [role="link"]') || el;
		}

		function clickNode(node) {
		  const clickable = asClickable(node);
		  if (!clickable || !visible(clickable)) return false;
		  try {
		    clickable.scrollIntoView({ block: 'center', inline: 'nearest' });
		  } catch (_) {}
		  try {
		    clickable.click();
		  } catch (_) {
		    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		  }
		  return true;
		}

		const targetText = normalize(buttonText);
		const selectors = [
		  'button',
		  'a',
		  '[role="button"]',
		  '[role="link"]',
		  'span',
		  'div',
		];

		for (const doc of collectDocs(document)) {
		  for (const selector of selectors) {
		    const nodes = queryAllDeep(doc, selector).filter(visible);
		    for (const node of nodes) {
		      const text = normalize(node.innerText || node.textContent || '');
		      if (!text) continue;
		      if (text === targetText || text.includes(targetText)) {
		        return {
		          clicked: clickNode(node),
		          matched_text: String(node.innerText || node.textContent || '').trim(),
		        };
		      }
		    }
		  }
		}

		return { clicked: false };
		"""

		original_handle = driver.current_window_handle
		existing_handles = set(driver.window_handles)
		result: dict[str, object] | None = None
		panel_deadline = time.time() + panel_timeout

		while time.time() < panel_deadline:
			result = driver.execute_script(panel_click_script, "Open alerts page")
			if isinstance(result, dict) and result.get("clicked"):
				break
			time.sleep(0.25)

		if not isinstance(result, dict) or not result.get("clicked"):
			print(
				f"Open alerts page button not found for alert: {alert_name}",
				file=sys.stderr,
			)
			return False

		print(f"Clicked Open alerts page on alert panel: {alert_name}")

		new_tab_opened = False
		details_handle = original_handle
		try:
			WebDriverWait(driver, panel_timeout).until(
				lambda d: len(d.window_handles) > len(existing_handles)
			)
			new_tab_opened = True
			new_handle = next(
				handle for handle in driver.window_handles if handle not in existing_handles
			)
			details_handle = new_handle
			print(f"Opened alerts page tab for: {alert_name}")
		except Exception as exc:  # noqa: BLE001
			print(
				f"New alerts tab did not open for alert {alert_name}: {exc}",
				file=sys.stderr,
			)

		details_script = r"""
		const buttonText = arguments[0];
		const targetSelector = String(arguments[1] || '').trim();
		const targetPanelSelector = String(arguments[2] || '').trim();

		function normalize(text) {
		  return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
		}

		function visible(el) {
		  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
		}

		function collectDocs(rootDoc) {
		  const docs = [rootDoc];
		  for (const frame of Array.from(rootDoc.querySelectorAll('iframe'))) {
		    try {
		      const fdoc = frame.contentDocument || frame.contentWindow?.document;
		      if (fdoc) docs.push(fdoc);
		    } catch (_) {
		      // Ignore cross-origin frames.
		    }
		  }
		  return docs;
		}

		function queryAllDeep(root, selector) {
		  const out = [];
		  const seen = new Set();
		  const stack = [root];

		  while (stack.length) {
		    const node = stack.pop();
		    if (!node || seen.has(node)) continue;
		    seen.add(node);

		    try {
		      if (node.querySelectorAll) {
		        out.push(...Array.from(node.querySelectorAll(selector)));
		      }
		    } catch (_) {}

		    let elements = [];
		    try {
		      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
		    } catch (_) {}

		    for (const el of elements) {
		      if (el.shadowRoot) {
		        stack.push(el.shadowRoot);
		      }
		    }
		  }

		  return out;
		}

		function asClickable(el) {
		  if (!el) return null;
		  return el.closest?.('button, a, [role="tab"], [role="button"], [role="link"]') || el;
		}

		function resolvePreferredClickTarget(el) {
		  if (!(el instanceof Element)) {
		    return null;
		  }
		  const clickable = asClickable(el);
		  if (clickable && visible(clickable)) {
		    return clickable;
		  }
		  const descendantClickable = el.querySelector?.('button, a, [role="tab"], [role="button"], [role="link"]');
		  if (descendantClickable && visible(descendantClickable)) {
		    return descendantClickable;
		  }
		  if (visible(el)) {
		    return el;
		  }
		  return null;
		}

		function clickNode(node) {
		  const clickable = asClickable(node);
		  if (!clickable || !visible(clickable)) return false;
		  try {
		    clickable.scrollIntoView({ block: 'center', inline: 'nearest' });
		  } catch (_) {}
		  try {
		    clickable.click();
		  } catch (_) {
		    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		  }
		  return true;
		}

		function escapeHtml(value) {
		  return String(value || '')
		    .replace(/&/g, '&amp;')
		    .replace(/</g, '&lt;')
		    .replace(/>/g, '&gt;')
		    .replace(/\"/g, '&quot;')
		    .replace(/'/g, '&#39;');
		}

		function getNodeText(node) {
		  return String(node?.innerText || node?.textContent || '')
		    .replace(/\u00a0/g, ' ')
		    .trim();
		}

		function buildRowsTableHtml(headers, rows) {
		  const cellStyle = 'border: 1px solid #d0d7de; padding: 6px 8px; text-align: left; vertical-align: top; white-space: pre-wrap;';
		  const normalizedRows = rows
		    .map((cells) => Array.isArray(cells) ? cells.map((cell) => String(cell || '')) : [])
		    .filter((cells) => cells.some((cell) => cell.trim()));
		  if (!normalizedRows.length) {
		    return '';
		  }

		  const theadHtml = headers.length
		    ? `<thead><tr>${headers.map((header) => `<th style="${cellStyle}">${escapeHtml(header)}</th>`).join('')}</tr></thead>`
		    : '';
		  const bodyRows = normalizedRows
		    .map((cells) => `<tr>${cells.map((cell) => `<td style="${cellStyle}">${escapeHtml(cell)}</td>`).join('')}</tr>`)
		    .join('');
		  return `<table class="captured-details-table" style="border-collapse: collapse; width: 100%; table-layout: fixed;">${theadHtml}<tbody>${bodyRows}</tbody></table>`;
		}

		function getMetadataRows(source) {
		  if (!(source instanceof Element)) {
		    return [];
		  }

		  const rowSelector = '.itemContiner-1541, .itemContainer-1541, [class*="itemContiner-"], [class*="itemContainer-"]';
		  const directRows = Array.from(source.children || [])
		    .filter((child) => child.matches?.(rowSelector));
		  const rawRows = directRows.length
		    ? directRows
		    : Array.from(source.querySelectorAll(rowSelector));
		  return rawRows
		    .map((row) => {
		      const label = getNodeText(row.querySelector('h3, [role="heading"], [class*="header-"]'));
		      const bodyNode = row.querySelector('.body-1537, [class*="body-"], [data-automation-id="visibleContent"]')
		        || row.querySelector('.ms-TooltipHost, a, span, div');
		      const value = getNodeText(bodyNode);
		      return [label, value];
		    })
		    .filter((cells) => cells.some((cell) => String(cell || '').trim()));
		}

		function buildMetadataTableHtml(node) {
		  if (!(node instanceof Element)) {
		    return '';
		  }

		  const source = node.matches?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]')
		    ? node
		    : node.querySelector?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]')
		      || node.closest?.('.itemsContainer-1540, .ms-MetaDataList, [class*="itemsContainer-"], [class*="ms-MetaDataList"]');
		  if (!(source instanceof Element)) {
		    return '';
		  }

		  return buildRowsTableHtml(['Field', 'Value'], getMetadataRows(source));
		}

		function getReactItemFromNode(node) {
		  let current = node;
		  while (current) {
		    try {
		      for (const key of Object.keys(current)) {
		        if (key.startsWith('__reactProps$')) {
		          const props = current[key];
		          if (props && typeof props.item === 'object' && props.item) {
		            return props.item;
		          }
		        }
		        if (key.startsWith('__reactFiber$')) {
		          const seenFibers = new Set();
		          let fiber = current[key];
		          while (fiber && !seenFibers.has(fiber)) {
		            seenFibers.add(fiber);
		            const props = fiber.memoizedProps;
		            if (props && typeof props.item === 'object' && props.item) {
		              return props.item;
		            }
		            fiber = fiber.return;
		          }
		        }
		      }
		    } catch (_) {}
		    current = current.parentElement || null;
		  }
		  return null;
		}

		function stringifyReactValue(value) {
		  if (value == null) {
		    return '';
		  }
		  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
		    return String(value);
		  }
		  if (Array.isArray(value)) {
		    return value.map((item) => stringifyReactValue(item)).filter(Boolean).join(', ');
		  }
		  if (typeof value === 'object') {
		    for (const key of ['text', 'value', 'label', 'displayName', 'name']) {
		      const nested = stringifyReactValue(value[key]);
		      if (nested) {
		        return nested;
		      }
		    }
		  }
		  return '';
		}

		function buildReactItemsTableHtml(root) {
		  if (!root || typeof root.querySelectorAll !== 'function') {
		    return '';
		  }

		  const columnMap = [
		    ['SITName', 'Sensitive info types'],
		    ['MatchedSensitiveContent', 'Matched sensitive content'],
		    ['Location', 'Location'],
		    ['SurroundingCharacters', 'Surrounding characters'],
		  ];
		  const rowsByKey = new Map();

		  for (const node of queryAllDeep(root, '.ms-List-cell, [data-automationid="DetailsRow"], .ms-DetailsRow')) {
		    const item = getReactItemFromNode(node);
		    if (!item || typeof item !== 'object') {
		      continue;
		    }

		    const rowValues = columnMap.map(([key]) => stringifyReactValue(item[key]));
		    if (!rowValues.some((value) => value.trim())) {
		      continue;
		    }

		    const rowKey = rowValues.join('::');
		    if (!rowKey || rowsByKey.has(rowKey)) {
		      continue;
		    }
		    rowsByKey.set(rowKey, item);
		  }

		  if (!rowsByKey.size) {
		    return '';
		  }

		  const activeColumns = columnMap.filter(([key]) => {
		    for (const item of rowsByKey.values()) {
		      if (stringifyReactValue(item[key]).trim()) {
		        return true;
		      }
		    }
		    return false;
		  });

		  if (!activeColumns.length) {
		    return '';
		  }

		  const headers = activeColumns.map(([, label]) => label);
		  const rows = Array.from(rowsByKey.values()).map((item) =>
		    activeColumns.map(([key]) => stringifyReactValue(item[key]))
		  );
		  return buildRowsTableHtml(headers, rows);
		}

		function prettifyAutomationKey(key) {
		  const knownLabels = {
		    SITName: 'Sensitive information type',
		    MatchedSensitiveContent: 'Matched sensitive content',
		    Location: 'Location',
		    SurroundingCharacters: 'Surrounding characters',
		  };
		  if (knownLabels[key]) {
		    return knownLabels[key];
		  }
		  return String(key || '')
		    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		    .replace(/[_-]+/g, ' ')
		    .replace(/\s+/g, ' ')
		    .trim() || 'Value';
		}

		function buildDetailsRowFieldsTableHtmlFromRoots(rowRoots) {
		  const rowsByKey = new Map();
		  const columnOrder = [];
		  const columnLabels = new Map();

		  for (const rowRoot of rowRoots) {
		    if (!(rowRoot instanceof Element)) {
		      continue;
		    }

		    const rowValues = new Map();
		    for (const cell of rowRoot.querySelectorAll('[data-automationid="DetailsRowCell"], .ms-DetailsRow-cell, [role="gridcell"]')) {
		      if (!(cell instanceof Element)) {
		        continue;
		      }
		      const key = String(cell.getAttribute('data-automation-key') || '').trim()
		        || `Column${columnOrder.length + rowValues.size + 1}`;
		      const value = getNodeText(cell);
		      if (!rowValues.has(key)) {
		        rowValues.set(key, value);
		      }
		      if (!columnLabels.has(key)) {
		        columnLabels.set(key, prettifyAutomationKey(key));
		      }
		      if (!columnOrder.includes(key)) {
		        columnOrder.push(key);
		      }
		    }

		    if (!rowValues.size) {
		      continue;
		    }

		    const rowKey = columnOrder.map((key) => rowValues.get(key) || '').join('::');
		    if (!rowKey || rowsByKey.has(rowKey)) {
		      continue;
		    }
		    rowsByKey.set(rowKey, rowValues);
		  }

		  if (!rowsByKey.size || !columnOrder.length) {
		    return '';
		  }

		  const activeColumns = columnOrder.filter((key) => {
		    for (const rowValues of rowsByKey.values()) {
		      if (String(rowValues.get(key) || '').trim()) {
		        return true;
		      }
		    }
		    return false;
		  });

		  if (!activeColumns.length) {
		    return '';
		  }

		  const headers = activeColumns.map((key) => columnLabels.get(key) || prettifyAutomationKey(key));
		  const rows = Array.from(rowsByKey.values()).map((rowValues) =>
		    activeColumns.map((key) => rowValues.get(key) || '')
		  );
		  return buildRowsTableHtml(headers, rows);
		}

		function buildMergedDetailsRowFieldsHtml(node) {
		  const wantsRowFields = targetSelector.includes('ms-DetailsRow-fields') || targetSelector.includes('DetailsRowFields');
		  if (!wantsRowFields || !node || typeof node.querySelectorAll !== 'function') {
		    return '';
		  }

		  const rowFieldSelectors = '[data-automationid="DetailsRowFields"], .ms-DetailsRow-fields';
		  const containerSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"], [role="tabpanel"], .itemContainer-1371, .itemContiner-1371, [class*="itemContainer-1371"], [class*="itemContiner-1371"]';
		  const containers = [];
		  const seenContainers = new Set();

		  function addContainer(candidate) {
		    if (!(candidate instanceof Element) || seenContainers.has(candidate)) {
		      return;
		    }
		    seenContainers.add(candidate);
		    containers.push(candidate);
		  }

		  addContainer(node.closest?.(containerSelectors));
		  if (node.matches?.(containerSelectors)) {
		    addContainer(node);
		  }
		  for (const candidate of node.querySelectorAll?.(containerSelectors) || []) {
		    addContainer(candidate);
		  }
		  if (!containers.length) {
		    addContainer(node);
		  }

		  const htmlParts = [];
		  const rowRoots = [];
		  const seenRows = new Set();
		  for (const container of containers) {
		    const rowFieldNodes = [];
		    if (container.matches?.(rowFieldSelectors)) {
		      rowFieldNodes.push(container);
		    }
		    for (const rowFieldNode of container.querySelectorAll?.(rowFieldSelectors) || []) {
		      rowFieldNodes.push(rowFieldNode);
		    }

		    for (const rowFieldNode of rowFieldNodes) {
		      const rowRoot = rowFieldNode.closest?.('.ms-List-cell')
		        || rowFieldNode.closest?.('[data-automationid="DetailsRow"], .ms-DetailsRow')
		        || rowFieldNode;
		      if (!(rowRoot instanceof Element)) {
		        continue;
		      }
		      const html = String(rowRoot.outerHTML || '').trim();
		      if (!html || seenRows.has(html)) {
		        continue;
		      }
		      seenRows.add(html);
		      rowRoots.push(rowRoot);
		      htmlParts.push(html);
		    }
		  }

		  if (!htmlParts.length) {
		    return '';
		  }

		  const tableHtml = buildDetailsRowFieldsTableHtmlFromRoots(rowRoots);
		  if (tableHtml) {
		    return `<div class="captured-ms-details-row-fields">${tableHtml}</div>`;
		  }

		  return `<div class="captured-ms-details-row-fields">${htmlParts.join('')}</div>`;
		}

		function buildMergedListCellHtml(node) {
		  const wantsListCells = targetSelector.includes('ms-List-cell') || targetSelector.includes('ms-List-surface') || targetSelector.includes('ms-List');
		  if (!wantsListCells || !node || typeof node.querySelectorAll !== 'function') {
		    return '';
		  }

		  const rootSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"]';
		  const roots = [];
		  const seenRoots = new Set();

		  function addRoot(candidate) {
		    if (!(candidate instanceof Element) || seenRoots.has(candidate)) {
		      return;
		    }
		    seenRoots.add(candidate);
		    roots.push(candidate);
		  }

		  addRoot(node.closest?.(rootSelectors));
		  if (node.matches?.(rootSelectors)) {
		    addRoot(node);
		  }
		  for (const candidate of node.querySelectorAll?.(rootSelectors) || []) {
		    addRoot(candidate);
		  }

		  const htmlParts = [];
		  const seenCells = new Set();
		  for (const root of roots) {
		    for (const cell of root.querySelectorAll('.ms-List-cell')) {
		      if (!(cell instanceof Element)) {
		        continue;
		      }
		      const html = String(cell.outerHTML || '').trim();
		      if (!html || seenCells.has(html)) {
		        continue;
		      }
		      seenCells.add(html);
		      htmlParts.push(html);
		    }
		  }

		  if (!htmlParts.length) {
		    return '';
		  }

		  return `<div class="captured-ms-list-cells">${htmlParts.join('')}</div>`;
		}

		function buildRawMergedListCellHtml(node) {
		  const wantsListCells = targetSelector.includes('ms-List-cell') || targetSelector.includes('ms-List-surface') || targetSelector.includes('ms-List');
		  if (!wantsListCells || !node || typeof node.querySelectorAll !== 'function') {
		    return '';
		  }

		  const rootSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"]';
		  const roots = [];
		  const seenRoots = new Set();

		  function addRoot(candidate) {
		    if (!(candidate instanceof Element) || seenRoots.has(candidate)) {
		      return;
		    }
		    seenRoots.add(candidate);
		    roots.push(candidate);
		  }

		  addRoot(node.closest?.(rootSelectors));
		  if (node.matches?.(rootSelectors)) {
		    addRoot(node);
		  }
		  for (const candidate of node.querySelectorAll?.(rootSelectors) || []) {
		    addRoot(candidate);
		  }

		  const htmlParts = [];
		  const seenCells = new Set();
		  for (const root of roots) {
		    for (const cell of root.querySelectorAll('.ms-List-cell')) {
		      if (!(cell instanceof Element)) {
		        continue;
		      }
		      const html = String(cell.outerHTML || '').trim();
		      if (!html || seenCells.has(html)) {
		        continue;
		      }
		      seenCells.add(html);
		      htmlParts.push(html);
		    }
		  }

		  if (!htmlParts.length) {
		    return '';
		  }

		  return `<div class="captured-ms-list-cells">${htmlParts.join('')}</div>`;
		}

		function buildTableHtml(node) {
		  if (!(node instanceof Element)) {
		    return '';
		  }

		  const source = node.matches?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]')
		    ? node
		    : node.querySelector?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]')
		      || node.closest?.('.ms-DetailsList, .ms-List-surface, .ms-List, [data-automationid="DetailsList"], [role="grid"]');
		  if (!(source instanceof Element)) {
		    return '';
		  }

		  const headers = Array.from(source.querySelectorAll('[role="columnheader"]'))
		    .map((header) => getNodeText(header))
		    .filter(Boolean);
		  const cellSelector = '[role="gridcell"], .ms-DetailsRow-cell, .ms-DetailsRow-fields, td';
		  const rows = Array.from(source.querySelectorAll('[data-automationid="DetailsRow"], .ms-DetailsRow, [role="row"]'))
		    .map((row) => Array.from(row.querySelectorAll(cellSelector)).map((cell) => getNodeText(cell)).filter(Boolean))
		    .filter((cells) => cells.length > 0);

		  const gridTableHtml = buildRowsTableHtml(headers, rows);
		  if (gridTableHtml) {
		    return gridTableHtml;
		  }

		  return buildMetadataTableHtml(node);
		}

		function buildCanvasSnapshotHtml(node) {
		  const wantsSourceCanvas = targetSelector.includes('ewr-sheettable');
		  if (!wantsSourceCanvas || !(node instanceof Element)) {
		    return '';
		  }

		  function canvasLooksRendered(canvas) {
		    if (!(canvas instanceof HTMLCanvasElement)) {
		      return false;
		    }

		    const width = Number(canvas.width || canvas.clientWidth || 0);
		    const height = Number(canvas.height || canvas.clientHeight || 0);
		    if (width <= 1 || height <= 1) {
		      return false;
		    }

		    try {
		      const context = canvas.getContext('2d', { willReadFrequently: true });
		      if (!context) {
		        return false;
		      }

		      const sampleSize = Math.max(2, Math.min(12, Math.min(width, height)));
		      const positions = [
		        [0, 0],
		        [Math.max(0, Math.floor((width - sampleSize) / 2)), Math.max(0, Math.floor((height - sampleSize) / 2))],
		        [Math.max(0, width - sampleSize), Math.max(0, height - sampleSize)],
		      ];

		      let baseline = '';
		      for (const [x, y] of positions) {
		        const imageData = context.getImageData(x, y, sampleSize, sampleSize).data;
		        if (!imageData.length) {
		          continue;
		        }

		        for (let index = 0; index < imageData.length; index += 4) {
		          const pixel = `${imageData[index]},${imageData[index + 1]},${imageData[index + 2]},${imageData[index + 3]}`;
		          if (!baseline) {
		            baseline = pixel;
		            continue;
		          }
		          if (pixel !== baseline) {
		            return true;
		          }
		        }
		      }
		    } catch (_) {
		      return visible(canvas) && width > 32 && height > 32;
		    }

		    return false;
		  }

		  const canvasSelector = 'canvas.ewr-sheettable';
		  const containerSelector = '.ewr-grdblk, .ewr-grdblkflow, .ewr-grdblkflowclip';
		  const canvases = [];
		  if (node.matches?.(canvasSelector)) {
		    canvases.push(node);
		  }
		  for (const canvas of node.querySelectorAll?.(canvasSelector) || []) {
		    canvases.push(canvas);
		  }

		  if (!canvases.length && node.matches?.(containerSelector)) {
		    return '';
		  }

		  let bestCanvas = null;
		  let bestScore = -1;
		  for (const canvas of canvases) {
		    if (!(canvas instanceof HTMLCanvasElement)) {
		      continue;
		    }
		    if (!canvasLooksRendered(canvas)) {
		      continue;
		    }
		    const width = Number(canvas.width || canvas.clientWidth || 0);
		    const height = Number(canvas.height || canvas.clientHeight || 0);
		    const score = (visible(canvas) ? 1000000000 : 0) + (width * height);
		    if (score > bestScore) {
		      bestCanvas = canvas;
		      bestScore = score;
		    }
		  }

		  if (!(bestCanvas instanceof HTMLCanvasElement)) {
		    return '';
		  }

		  const width = Number(bestCanvas.width || bestCanvas.clientWidth || 0);
		  const height = Number(bestCanvas.height || bestCanvas.clientHeight || 0);
		  const altText = escapeHtml(`Source sheet snapshot ${width}x${height}`.trim());
		  let dataUrl = '';
		  try {
		    dataUrl = String(bestCanvas.toDataURL('image/png') || '');
		  } catch (_) {}

		  if (dataUrl) {
		    return `<figure class="captured-ewr-sheettable mb-0"><img src="${dataUrl}" alt="${altText}" style="max-width: 100%; height: auto; display: block; border: 1px solid #d0d7de; border-radius: 0.75rem; background: #fff;" /></figure>`;
		  }

		  const canvasHtml = String(bestCanvas.outerHTML || '').trim();
		  if (!canvasHtml) {
		    return '';
		  }
		  return `<div class="captured-ewr-sheettable">${canvasHtml}</div>`;
		}

		function serializeNode(node) {
		  if (!node || typeof node.querySelectorAll !== 'function') {
		    return '';
		  }
		  const canvasHtml = buildCanvasSnapshotHtml(node);
		  const canvasScopeSelector = 'canvas.ewr-sheettable, .ewr-sheettable, .ewr-grdblk, .ewr-grdblkflow, .ewr-grdblkflowclip';
		  if (targetSelector.includes('ewr-sheettable') && (node.matches?.(canvasScopeSelector) || node.querySelector?.('canvas.ewr-sheettable'))) {
		    return canvasHtml || '';
		  }
		  return canvasHtml || buildMergedDetailsRowFieldsHtml(node) || buildMergedListCellHtml(node) || buildTableHtml(node) || node.outerHTML || '';
		}

		function getPanelRoots(doc) {
		  if (!targetTabId) {
		    return [];
		  }
		  return queryAllDeep(doc, '[aria-labelledby]')
		    .filter((node) => String(node.getAttribute?.('aria-labelledby') || '') === targetTabId);
		}

		function getVisibleTabPanels(doc) {
		  return queryAllDeep(doc, '[role="tabpanel"][aria-hidden="false"], [role="tabpanel"]:not([aria-hidden="true"])')
		    .filter(visible);
		}

		function getSearchRoots(doc) {
		  const panelRoots = getPanelRoots(doc);
		  if (panelRoots.length) {
		    return panelRoots;
		  }
		  const visiblePanels = getVisibleTabPanels(doc);
		  if (visiblePanels.length) {
		    return visiblePanels;
		  }
		  return [doc];
		}

		function getTargetNodes(doc) {
		  const nodes = [];
		  const seenNodes = new Set();
		  const searchRoots = [...getSearchRoots(doc), doc];
		  for (const searchRoot of searchRoots) {
		    if (searchRoot instanceof Element && searchRoot.matches?.(targetSelector) && !seenNodes.has(searchRoot)) {
		      seenNodes.add(searchRoot);
		      nodes.push(searchRoot);
		    }
		    for (const node of queryAllDeep(searchRoot, targetSelector)) {
		      if (seenNodes.has(node)) {
		        continue;
		      }
		      seenNodes.add(node);
		      nodes.push(node);
		    }
		  }
		  return nodes;
		}

		function getPanelHtml(doc) {
		  for (const searchRoot of getSearchRoots(doc)) {
		    const html = serializeNode(searchRoot);
		    if (html) {
		      return html;
		    }
		  }
		  return '';
		}

		function getNodeSize(node) {
		  if (!(node instanceof Element)) {
		    return 0;
		  }
		  try {
		    return node.querySelectorAll('*').length;
		  } catch (_) {
		    return 0;
		  }
		}

		function describeElement(node) {
		  if (!(node instanceof Element)) {
		    return null;
		  }
		  return {
		    tag: String(node.tagName || '').toLowerCase(),
		    id: String(node.id || ''),
		    class_name: String(node.className || '').trim(),
		    role: String(node.getAttribute?.('role') || ''),
		    scroll_top: Number(node.scrollTop || 0),
		    client_height: Number(node.clientHeight || 0),
		    scroll_height: Number(node.scrollHeight || 0),
		  };
		}

		function advanceListScroll(doc) {
		  const wantsListCells = targetSelector.includes('ms-List-cell') || targetSelector.includes('ms-List-surface') || targetSelector.includes('ms-List');
		  if (!wantsListCells) {
		    return { advanced: false, complete: true, position: 0, max: 0, container: null, candidates: [] };
		  }

		  const rootSelectors = '.ms-List-surface, .ms-List, .ms-DetailsList, [role="grid"]';
		  let bestContainer = null;
		  let bestMaxScrollTop = 0;
		  const candidateSummaries = [];
		  const seenCandidates = new Set();

		  function considerContainer(candidate) {
		    if (!(candidate instanceof Element) || seenCandidates.has(candidate)) {
		      return;
		    }
		    seenCandidates.add(candidate);
		    const maxScrollTop = Math.max(0, (candidate.scrollHeight || 0) - (candidate.clientHeight || 0));
		    const description = describeElement(candidate);
		    if (description) {
		      description.max_scroll_top = maxScrollTop;
		      candidateSummaries.push(description);
		    }
		    if (maxScrollTop <= 0) {
		      return;
		    }
		    if (!bestContainer || maxScrollTop > bestMaxScrollTop) {
		      bestContainer = candidate;
		      bestMaxScrollTop = maxScrollTop;
		    }
		  }

		  function considerScrollableAncestors(node) {
		    let current = node instanceof Element ? node : null;
		    while (current) {
		      considerContainer(current);
		      current = current.parentElement || null;
		    }
		  }

		  for (const searchRoot of [...getSearchRoots(doc), doc]) {
		    considerContainer(searchRoot.closest?.(rootSelectors));
		    if (searchRoot instanceof Element && searchRoot.matches?.(rootSelectors)) {
		      considerContainer(searchRoot);
		    }
		    if (searchRoot instanceof Element) {
		      considerScrollableAncestors(searchRoot);
		    }
		    for (const candidate of queryAllDeep(searchRoot, rootSelectors)) {
		      considerContainer(candidate);
		    }
		    for (const listCell of queryAllDeep(searchRoot, '.ms-List-cell')) {
		      considerScrollableAncestors(listCell);
		    }
		  }
		  if (doc.scrollingElement instanceof Element) {
		    considerContainer(doc.scrollingElement);
		  }

		  if (!(bestContainer instanceof Element)) {
		    return {
		      advanced: false,
		      complete: true,
		      position: 0,
		      max: 0,
		      container: null,
		      candidates: candidateSummaries.slice(0, 10),
		    };
		  }

		  const currentTop = Number(bestContainer.scrollTop || 0);
		  if (currentTop >= bestMaxScrollTop - 1) {
		    return {
		      advanced: false,
		      complete: true,
		      position: currentTop,
		      max: bestMaxScrollTop,
		      container: describeElement(bestContainer),
		      candidates: candidateSummaries.slice(0, 10),
		    };
		  }

		  const step = Math.max(200, Math.floor((bestContainer.clientHeight || 0) * 0.85));
		  const nextTop = Math.min(bestMaxScrollTop, currentTop + step);
		  try {
		    bestContainer.scrollTop = nextTop;
		    bestContainer.dispatchEvent(new Event('scroll', { bubbles: true }));
		  } catch (_) {}

		  return {
		    advanced: nextTop > currentTop + 1,
		    complete: nextTop >= bestMaxScrollTop - 1,
		    position: nextTop,
		    max: bestMaxScrollTop,
		    container: describeElement(bestContainer),
		    candidates: candidateSummaries.slice(0, 10),
		  };
		}

		function buildMergedItemsContainer(root) {
		  if (!(targetSelector.includes('itemsContainer-') || targetSelector.includes('ms-MetaDataList'))) {
		    return { html: '', count: 0 };
		  }

		  const rowsByKey = new Map();
		  const containerSelectors = [
		    targetSelector,
		    '.itemsContainer-1540',
		    '.ms-MetaDataList',
		    '[class*="itemsContainer-"]',
		    '[class*="ms-MetaDataList"]',
		  ];
		  const seenContainers = new Set();
		  for (const containerSelector of containerSelectors) {
		    for (const container of queryAllDeep(root, containerSelector)) {
		      if (!(container instanceof Element) || seenContainers.has(container)) {
		        continue;
		      }
		      seenContainers.add(container);
		    for (const row of getMetadataRows(container)) {
		      const key = `${row[0]}::${row[1]}`.trim() || JSON.stringify(row);
		      if (!rowsByKey.has(key)) {
		        rowsByKey.set(key, row);
		      }
		    }
		    }
		  }

		  if (!rowsByKey.size) {
		    return { html: '', count: 0 };
		  }

		  return {
		    html: buildRowsTableHtml(['Field', 'Value'], Array.from(rowsByKey.values())),
		    count: rowsByKey.size,
		  };
		}

		const targetText = normalize(buttonText);

		function findTargetTab(doc) {
		  if (targetPanelSelector) {
		    for (const node of queryAllDeep(doc, targetPanelSelector)) {
		      const preferredTarget = resolvePreferredClickTarget(node);
		      if (preferredTarget) {
		        return preferredTarget;
		      }
		    }
		  }

		  const tablists = queryAllDeep(doc, '[role="tablist"], .ms-Pivot, .scc-pivot');
		  for (const tablist of tablists) {
		    for (const selector of ['button', 'a', '[role="tab"]', '[role="button"]', 'span', 'div']) {
		      const nodes = Array.from(tablist.querySelectorAll(selector)).filter(visible);
		      for (const node of nodes) {
		        const text = normalize(node.innerText || node.textContent || '');
		        if (text === targetText || text.includes(targetText)) {
		          return node;
		        }
		      }
		    }
		  }

		  return null;
		}

		let detailsClicked = false;
		let targetTabId = '';
		for (const doc of collectDocs(document)) {
		  const targetTab = findTargetTab(doc);
		  if (targetTab) {
		    const clickable = asClickable(targetTab) || targetTab;
		    targetTabId = String(clickable.id || targetTab.id || '');
		    const alreadySelected = String(clickable.getAttribute('aria-selected') || targetTab.getAttribute('aria-selected') || '').toLowerCase() === 'true';
		    detailsClicked = alreadySelected || clickNode(targetTab);
		  }
		  if (detailsClicked) break;
		}

		let classHtml = '';
		let classSize = 0;
		let mergedListCellHtml = '';
		let mergedListCellSize = 0;
		let mergedListCellRawHtml = '';
		let mergedListCellRawSize = 0;
		let mergedClassHtml = '';
		let mergedClassSize = 0;
		let fallbackHtml = '';
		let panelHtml = '';
		let panelSize = 0;
		let sawScrollableList = false;
		let listScrollAdvanced = false;
		let listScrollComplete = true;
		let listScrollPosition = 0;
		let listScrollMax = 0;
		let listScrollContainer = null;
		let listScrollCandidates = [];
		for (const doc of collectDocs(document)) {
		  const mergedListCells = buildMergedListCellHtml(doc);
		  if (mergedListCells) {
		    const listCellCount = (mergedListCells.match(/class=\"ms-List-cell\"/g) || []).length;
		    if (listCellCount > mergedListCellSize || (listCellCount === mergedListCellSize && mergedListCells.length > mergedListCellHtml.length)) {
		      mergedListCellHtml = mergedListCells;
		      mergedListCellSize = listCellCount;
		    }
		  }
		  const mergedListCellsRaw = buildRawMergedListCellHtml(doc);
		  if (mergedListCellsRaw) {
		    const listCellCount = (mergedListCellsRaw.match(/class=\"ms-List-cell\"/g) || []).length;
		    if (listCellCount > mergedListCellRawSize || (listCellCount === mergedListCellRawSize && mergedListCellsRaw.length > mergedListCellRawHtml.length)) {
		      mergedListCellRawHtml = mergedListCellsRaw;
		      mergedListCellRawSize = listCellCount;
		    }
		  }
		  const mergedContainer = buildMergedItemsContainer(doc);
		  if (mergedContainer.count > mergedClassSize) {
		    mergedClassHtml = mergedContainer.html;
		    mergedClassSize = mergedContainer.count;
		  }
		  const nodes = getTargetNodes(doc);
		  for (const node of nodes) {
		    const nodeHtml = serializeNode(node);
		    if (nodeHtml && nodeHtml.length > fallbackHtml.length) {
		      fallbackHtml = nodeHtml;
		    }
		  }
		  if (!panelHtml) {
		    panelHtml = getPanelHtml(doc);
		    panelSize = getSearchRoots(doc)
		      .map((node) => getNodeSize(node))
		      .reduce((maxSize, size) => Math.max(maxSize, size), 0);
		  }
		  const visibleNodes = nodes.filter(visible);
		  for (const node of visibleNodes) {
		    const nodeSize = getNodeSize(node);
		    if (nodeSize > classSize) {
		      classHtml = serializeNode(node);
		      classSize = nodeSize;
		    }
		  }

		  const scrollResult = advanceListScroll(doc);
		  if ((scrollResult.max || 0) > 0) {
		    sawScrollableList = true;
		    listScrollAdvanced = listScrollAdvanced || !!scrollResult.advanced;
		    listScrollComplete = listScrollComplete && !!scrollResult.complete;
		    listScrollPosition = Math.max(listScrollPosition, Number(scrollResult.position || 0));
		    listScrollMax = Math.max(listScrollMax, Number(scrollResult.max || 0));
		    if (scrollResult.container) {
		      listScrollContainer = scrollResult.container;
		    }
		    if (Array.isArray(scrollResult.candidates) && scrollResult.candidates.length) {
		      listScrollCandidates = scrollResult.candidates;
		    }
		  }
		}

		return {
		  details_clicked: detailsClicked,
		  class_html: mergedListCellHtml || mergedClassHtml || classHtml || fallbackHtml,
		  raw_list_cells_html: mergedListCellRawHtml,
		  class_size: Math.max(classSize, mergedClassSize, mergedListCellSize),
		  panel_html: panelHtml,
		  panel_size: panelSize,
		  list_scroll_advanced: sawScrollableList ? listScrollAdvanced : false,
		  list_scroll_complete: sawScrollableList ? listScrollComplete : true,
		  list_scroll_position: listScrollPosition,
		  list_scroll_max: listScrollMax,
		  list_scroll_container: listScrollContainer,
		  list_scroll_candidates: listScrollCandidates,
		};
		"""

		tab_debug_script = r"""
		const buttonText = arguments[0];
		const targetPanelSelector = String(arguments[1] || '').trim();

		function normalize(text) {
		  return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
		}

		function visible(el) {
		  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
		}

		function collectDocs(rootDoc) {
		  const docs = [rootDoc];
		  for (const frame of Array.from(rootDoc.querySelectorAll('iframe'))) {
		    try {
		      const fdoc = frame.contentDocument || frame.contentWindow?.document;
		      if (fdoc) docs.push(fdoc);
		    } catch (_) {
		      // Ignore cross-origin frames.
		    }
		  }
		  return docs;
		}

		function queryAllDeep(root, selector) {
		  const out = [];
		  const seen = new Set();
		  const stack = [root];

		  while (stack.length) {
		    const node = stack.pop();
		    if (!node || seen.has(node)) continue;
		    seen.add(node);

		    try {
		      if (node.querySelectorAll) {
		        out.push(...Array.from(node.querySelectorAll(selector)));
		      }
		    } catch (_) {}

		    let elements = [];
		    try {
		      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
		    } catch (_) {}

		    for (const el of elements) {
		      if (el.shadowRoot) {
		        stack.push(el.shadowRoot);
		      }
		    }
		  }

		  return out;
		}

		function summarizeNode(node) {
		  if (!(node instanceof Element)) {
		    return null;
		  }
		  return {
		    text: String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim(),
		    role: String(node.getAttribute('role') || ''),
		    automation_id: String(node.getAttribute('data-automation-id') || ''),
		    automationid: String(node.getAttribute('data-automationid') || ''),
		    aria_selected: String(node.getAttribute('aria-selected') || ''),
		    class_name: String(node.className || ''),
		    visible: visible(node),
		  };
		}

		const targetText = normalize(buttonText);
		const tabSummaries = [];
		const preferredMatches = [];
		const panelHtmlSamples = [];
		const seenKeys = new Set();

		for (const doc of collectDocs(document)) {
		  if (targetPanelSelector) {
		    for (const node of queryAllDeep(doc, targetPanelSelector)) {
		      const summary = summarizeNode(node);
		      if (!summary) continue;
		      preferredMatches.push(summary);
		      if (preferredMatches.length >= 20) break;
		    }
		  }

		  for (const node of queryAllDeep(doc, '[role="tab"], button, a, span, div')) {
		    const summary = summarizeNode(node);
		    if (!summary) continue;
		    const automationText = `${summary.automation_id} ${summary.automationid}`.toLowerCase();
		    const normalizedText = normalize(summary.text);
		    if (!(summary.visible || automationText.includes('classifier') || normalizedText.includes(targetText))) {
		      continue;
		    }
		    if (!(summary.role === 'tab' || automationText.includes('workbenchtab') || automationText.includes('classifier') || normalizedText.includes(targetText) || normalizedText.includes('details'))) {
		      continue;
		    }
		    const key = JSON.stringify([summary.text, summary.automation_id, summary.automationid, summary.class_name]);
		    if (seenKeys.has(key)) {
		      continue;
		    }
		    seenKeys.add(key);
		    tabSummaries.push(summary);
		    if (tabSummaries.length >= 50) {
		      break;
		    }
		  }

		  for (const selector of ['[role="tabpanel"]', '.itemContainer-1371', '.itemContiner-1371', '.ms-DetailsList', '[role="grid"]']) {
		    for (const node of queryAllDeep(doc, selector)) {
		      if (!(node instanceof Element)) continue;
		      const html = String(node.outerHTML || '').trim();
		      if (!html) continue;
		      panelHtmlSamples.push({
		        selector,
		        class_name: String(node.className || ''),
		        role: String(node.getAttribute('role') || ''),
		        automation_id: String(node.getAttribute('data-automation-id') || ''),
		        visible: visible(node),
		        html: html.slice(0, 12000),
		      });
		      if (panelHtmlSamples.length >= 15) {
		        break;
		      }
		    }
		    if (panelHtmlSamples.length >= 15) {
		      break;
		    }
		  }
		}

		return {
		  page_title: String(document.title || ''),
		  page_url: String(document.location?.href || ''),
		  target_text: buttonText,
		  target_panel_selector: targetPanelSelector,
		  preferred_matches: preferredMatches,
		  visible_tabs: tabSummaries,
		  panel_html_samples: panelHtmlSamples,
		};
		"""

		capture_handles = [details_handle] if new_tab_opened else [original_handle]

		def restore_alert_context(result: bool) -> bool:
			if new_tab_opened and details_handle != original_handle:
				try:
					driver.switch_to.window(details_handle)
					driver.close()
				except Exception:
					pass
				finally:
					try:
						driver.switch_to.window(original_handle)
					except Exception:
						pass
			return result

		captured_any_target = False
		for capture_target in capture_targets:
			target_panel_text = str(capture_target["panel_text"])
			target_panel_selector = str(capture_target["panel_selector"])
			target_selector = str(capture_target["target_selector"])
			target_artifact_name = str(capture_target["artifact_name"])

			if target_panel_text == "Source" and not should_capture_source_panel(alert_location):
				artifact_folder = get_alert_artifact_folder(alert_name)
				html_output_path = os.path.join(artifact_folder, f"{target_artifact_name}.html")
				if _should_replace_missing_artifact(html_output_path):
					failure_fragment = build_capture_failure_html_fragment(
						target_panel_text,
						target_selector,
						(
							"Source capture is only attempted for OneDrive or SharePoint alerts. "
							f"Current location: {alert_location or 'Unknown'}."
						),
					)
					failure_document = build_html_artifact_document(
						f"{alert_name} - {target_panel_text}",
						failure_fragment,
					)
					with open(html_output_path, "w", encoding="utf-8") as html_file:
						html_file.write(failure_document)
					print(f"Saved fallback {target_artifact_name} HTML for {alert_name} to: {html_output_path}")
				print(
					f"Skipping Source capture for alert without OneDrive/SharePoint location: {alert_name} (Location: {alert_location})"
				)
				continue

			saw_target_panel = False
			debug_panel_html = ""
			tab_debug_result: dict[str, object] | None = None
			best_html_fragment = ""
			best_html_score = -1
			stable_html_polls = 0
			capture_poll_interval_seconds = 0.1 if target_artifact_name == "classifiers" else 0.25
			capture_timeout_seconds = max(panel_timeout, 240) if target_artifact_name == "classifiers" else panel_timeout
			accumulated_list_html_fragment = ""
			accumulated_list_cell_count = 0
			list_scroll_complete_polls = 0
			last_list_scroll_position = 0
			last_list_scroll_max = 0
			last_list_scroll_container: dict[str, object] | None = None
			last_list_scroll_candidates: list[object] = []
			source_download_attempted = False
			target_captured = False

			for capture_handle in capture_handles:
				driver.switch_to.window(capture_handle)
				if capture_handle != original_handle:
					try:
						wait_for_page_load(driver, max(3, min(panel_timeout, 10)))
					except Exception:
						pass

				details_result: dict[str, object] | None = None
				details_deadline = time.time() + capture_timeout_seconds
				while time.time() < details_deadline:
					details_result = driver.execute_script(details_script, target_panel_text, target_selector, target_panel_selector)
					if isinstance(details_result, dict) and details_result.get("details_clicked"):
						saw_target_panel = True
					if isinstance(details_result, dict):
						current_panel_html = str(details_result.get("panel_html") or "").strip()
						current_panel_size = int(details_result.get("panel_size") or 0)
						if current_panel_html and (
							not debug_panel_html
							or current_panel_size > debug_panel_html.count("<")
							or len(current_panel_html) > len(debug_panel_html)
						):
							debug_panel_html = current_panel_html

						current_html_fragment = str(details_result.get("class_html") or "").strip()
						current_raw_list_fragment = str(details_result.get("raw_list_cells_html") or current_html_fragment).strip()
						current_html_score = int(details_result.get("class_size") or 0)
						if current_html_fragment:
							current_list_cell_count = _count_ms_list_cells_in_html(current_raw_list_fragment)
							if current_list_cell_count:
								merged_list_html_fragment = _merge_ms_list_cell_html_fragments(
									accumulated_list_html_fragment,
									current_raw_list_fragment,
								)
								merged_list_cell_count = _count_ms_list_cells_in_html(merged_list_html_fragment)
								if merged_list_html_fragment:
									if (
										merged_list_cell_count > accumulated_list_cell_count
										or merged_list_html_fragment != accumulated_list_html_fragment
									):
										accumulated_list_html_fragment = merged_list_html_fragment
										accumulated_list_cell_count = merged_list_cell_count
										best_html_fragment = merged_list_html_fragment
										best_html_score = merged_list_cell_count
										stable_html_polls = 0
									else:
										stable_html_polls += 1

								if bool(details_result.get("list_scroll_complete")) and not bool(details_result.get("list_scroll_advanced")):
									list_scroll_complete_polls += 1
								else:
									list_scroll_complete_polls = 0
								last_list_scroll_position = int(details_result.get("list_scroll_position") or 0)
								last_list_scroll_max = int(details_result.get("list_scroll_max") or 0)
								if isinstance(details_result.get("list_scroll_container"), dict):
									last_list_scroll_container = details_result.get("list_scroll_container")
								if isinstance(details_result.get("list_scroll_candidates"), list):
									last_list_scroll_candidates = details_result.get("list_scroll_candidates")
							else:
								if current_html_score > best_html_score or (
									current_html_score == best_html_score and len(current_html_fragment) > len(best_html_fragment)
								):
									best_html_fragment = current_html_fragment
									best_html_score = current_html_score
									stable_html_polls = 0
								elif current_html_fragment == best_html_fragment:
									stable_html_polls += 1
					if (
						isinstance(details_result, dict)
						and details_result.get("details_clicked")
						and best_html_fragment
						and (
							(accumulated_list_html_fragment and list_scroll_complete_polls >= 2 and stable_html_polls >= 2)
							or (not accumulated_list_html_fragment and stable_html_polls >= 3)
						)
					):
						break
					time.sleep(capture_poll_interval_seconds)

				if not isinstance(details_result, dict) or not details_result.get("details_clicked"):
					continue

				html_fragment = accumulated_list_html_fragment or best_html_fragment or str(details_result.get("class_html") or "").strip()
				if not html_fragment and debug_panel_html and target_selector == ".itemContainer-1354":
					html_fragment = debug_panel_html
				if (
					not html_fragment
					and debug_panel_html
					and target_selector.startswith(".")
					and target_selector[1:] in debug_panel_html
				):
					html_fragment = debug_panel_html
				if not html_fragment:
					continue
				html_fragment = normalize_extracted_html_fragment(html_fragment)
				if target_artifact_name == "classifiers":
					html_fragment = _add_classifier_data_found_at_column(alert_name, html_fragment)
				if not _html_fragment_has_meaningful_content(html_fragment):
					continue
				html_document = build_html_artifact_document(
					f"{alert_name} - {target_panel_text}",
					html_fragment,
				)

				artifact_folder = get_alert_artifact_folder(alert_name)
				html_output_path = os.path.join(artifact_folder, f"{target_artifact_name}.html")
				sit_analysis_output_path = ""
				with open(html_output_path, "w", encoding="utf-8") as html_file:
					html_file.write(html_document)
				if target_artifact_name == "source" and not source_download_attempted:
					source_download_attempted = True
					attempt_source_download_storage(alert_name, panel_timeout)
				if target_artifact_name == "classifiers":
					scroll_debug_output_path = os.path.join(artifact_folder, "classifiers-scroll-debug.json")
					with open(scroll_debug_output_path, "w", encoding="utf-8") as debug_file:
						json.dump(
							{
								"alert_name": alert_name,
								"target_panel_text": target_panel_text,
								"capture_timeout_seconds": capture_timeout_seconds,
								"capture_poll_interval_seconds": capture_poll_interval_seconds,
								"accumulated_list_cell_count": accumulated_list_cell_count,
								"list_scroll_complete_polls": list_scroll_complete_polls,
								"last_list_scroll_position": last_list_scroll_position,
								"last_list_scroll_max": last_list_scroll_max,
								"last_list_scroll_container": last_list_scroll_container,
								"last_list_scroll_candidates": last_list_scroll_candidates,
							},
							debug_file,
							indent=2,
						)
					try:
						sit_analysis_output_path = write_sit_analysis_html(alert_name, html_fragment) or ""
					except Exception as exc:  # noqa: BLE001
						print(
							f"Failed SIT analysis generation for {alert_name}: {exc}",
							file=sys.stderr,
						)

				print(f"Clicked {target_panel_text} panel for: {alert_name}")
				print(f"Saved {target_artifact_name} HTML for {alert_name} to: {html_output_path}")
				if sit_analysis_output_path:
					print(f"Saved SIT analysis HTML for {alert_name} to: {sit_analysis_output_path}")
					upload_alert_folder_files_to_odin(alert_name)
				target_captured = True
				captured_any_target = True
				break

			if target_captured:
				continue

			for capture_handle in capture_handles:
				driver.switch_to.window(capture_handle)
				if capture_handle != original_handle:
					try:
						wait_for_page_load(driver, max(3, min(panel_timeout, 10)))
					except Exception:
						pass

				if not click_text_across_frames(target_panel_text, target_panel_selector):
					continue

				saw_target_panel = True
				html_fragment = capture_class_html_across_frames(target_selector, panel_timeout).strip()
				if not html_fragment:
					continue
				html_fragment = normalize_extracted_html_fragment(html_fragment)
				if target_artifact_name == "classifiers":
					html_fragment = _add_classifier_data_found_at_column(alert_name, html_fragment)
				if not _html_fragment_has_meaningful_content(html_fragment):
					continue
				html_document = build_html_artifact_document(
					f"{alert_name} - {target_panel_text}",
					html_fragment,
				)

				artifact_folder = get_alert_artifact_folder(alert_name)
				html_output_path = os.path.join(artifact_folder, f"{target_artifact_name}.html")
				sit_analysis_output_path = ""
				with open(html_output_path, "w", encoding="utf-8") as html_file:
					html_file.write(html_document)
				if target_artifact_name == "source" and not source_download_attempted:
					source_download_attempted = True
					attempt_source_download_storage(alert_name, panel_timeout)
				if target_artifact_name == "classifiers":
					try:
						sit_analysis_output_path = write_sit_analysis_html(alert_name, html_fragment) or ""
					except Exception as exc:  # noqa: BLE001
						print(
							f"Failed SIT analysis generation for {alert_name}: {exc}",
							file=sys.stderr,
						)

				print(f"Clicked {target_panel_text} panel for: {alert_name}")
				print(f"Saved {target_artifact_name} HTML for {alert_name} to: {html_output_path}")
				if sit_analysis_output_path:
					print(f"Saved SIT analysis HTML for {alert_name} to: {sit_analysis_output_path}")
					upload_alert_folder_files_to_odin(alert_name)
				target_captured = True
				captured_any_target = True
				break

			if target_captured:
				continue

			if not saw_target_panel:
				artifact_folder = get_alert_artifact_folder(alert_name)
				html_output_path = os.path.join(artifact_folder, f"{target_artifact_name}.html")
				if _html_fragment_has_meaningful_content(debug_panel_html):
					debug_output_path = os.path.join(artifact_folder, f"{target_panel_text.lower()}-panel-debug.html")
					with open(debug_output_path, "w", encoding="utf-8") as debug_file:
						debug_file.write(debug_panel_html)
					print(f"Saved {target_panel_text} panel debug HTML for {alert_name} to: {debug_output_path}", file=sys.stderr)

				for capture_handle in capture_handles:
					try:
						driver.switch_to.window(capture_handle)
						candidate_tab_debug = driver.execute_script(tab_debug_script, target_panel_text, target_panel_selector)
						if not isinstance(candidate_tab_debug, dict):
							continue
						tab_debug_result = candidate_tab_debug
						tab_debug_path = os.path.join(artifact_folder, f"{target_panel_text.lower()}-tab-debug.json")
						with open(tab_debug_path, "w", encoding="utf-8") as debug_file:
							json.dump(tab_debug_result, debug_file, indent=2, ensure_ascii=False)
						print(f"Saved {target_panel_text} tab debug JSON for {alert_name} to: {tab_debug_path}", file=sys.stderr)
						break
					except Exception:
						continue
				if _should_replace_missing_artifact(html_output_path):
					failure_fragment = build_capture_failure_html_fragment(
						target_panel_text,
						target_selector,
						f"The {target_panel_text} panel was not found on the page during capture.",
						debug_panel_html=debug_panel_html,
						tab_debug_result=tab_debug_result,
					)
					failure_document = build_html_artifact_document(
						f"{alert_name} - {target_panel_text}",
						failure_fragment,
					)
					with open(html_output_path, "w", encoding="utf-8") as html_file:
						html_file.write(failure_document)
					print(f"Saved fallback {target_artifact_name} HTML for {alert_name} to: {html_output_path}", file=sys.stderr)
				print(f"{target_panel_text} panel was not found for alert: {alert_name}", file=sys.stderr)
			else:
				artifact_folder = get_alert_artifact_folder(alert_name)
				html_output_path = os.path.join(artifact_folder, f"{target_artifact_name}.html")
				if _html_fragment_has_meaningful_content(debug_panel_html):
					artifact_folder = get_alert_artifact_folder(alert_name)
					debug_output_path = os.path.join(artifact_folder, f"{target_panel_text.lower()}-panel-debug.html")
					with open(debug_output_path, "w", encoding="utf-8") as debug_file:
						debug_file.write(debug_panel_html)
					print(f"Saved {target_panel_text} panel debug HTML for {alert_name} to: {debug_output_path}", file=sys.stderr)
				for capture_handle in capture_handles:
					try:
						driver.switch_to.window(capture_handle)
						candidate_tab_debug = driver.execute_script(tab_debug_script, target_panel_text, target_panel_selector)
						if not isinstance(candidate_tab_debug, dict):
							continue
						tab_debug_result = candidate_tab_debug
						tab_debug_path = os.path.join(artifact_folder, f"{target_panel_text.lower()}-tab-debug.json")
						with open(tab_debug_path, "w", encoding="utf-8") as debug_file:
							json.dump(tab_debug_result, debug_file, indent=2, ensure_ascii=False)
						print(f"Saved {target_panel_text} tab debug JSON for {alert_name} to: {tab_debug_path}", file=sys.stderr)
						break
					except Exception:
						continue
				if _should_replace_missing_artifact(html_output_path):
					failure_fragment = build_capture_failure_html_fragment(
						target_panel_text,
						target_selector,
						f"The {target_panel_text} panel was found, but it did not expose usable content for the requested selector.",
						debug_panel_html=debug_panel_html,
						tab_debug_result=tab_debug_result,
					)
					failure_document = build_html_artifact_document(
						f"{alert_name} - {target_panel_text}",
						failure_fragment,
					)
					with open(html_output_path, "w", encoding="utf-8") as html_file:
						html_file.write(failure_document)
					print(f"Saved fallback {target_artifact_name} HTML for {alert_name} to: {html_output_path}", file=sys.stderr)
				print(f"No visible {target_selector} element found for alert: {alert_name}", file=sys.stderr)

		return restore_alert_context(captured_any_target or new_tab_opened)

	click_script = """
	const alertName = arguments[0];

	function normalize(text) {
	  return String(text || '').replace(/\\s+/g, ' ').trim();
	}

	function visible(el) {
	  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
	}

	function collectDocs(rootDoc) {
	  const docs = [rootDoc];
	  for (const frame of Array.from(rootDoc.querySelectorAll('iframe'))) {
	    try {
	      const fdoc = frame.contentDocument || frame.contentWindow?.document;
	      if (fdoc) docs.push(fdoc);
	    } catch (_) {
	      // Ignore cross-origin frames.
	    }
	  }
	  return docs;
	}

	function queryAllDeep(root, selector) {
	  const out = [];
	  const seen = new Set();
	  const stack = [root];

	  while (stack.length) {
	    const node = stack.pop();
	    if (!node || seen.has(node)) continue;
	    seen.add(node);

	    try {
	      if (node.querySelectorAll) {
	        out.push(...Array.from(node.querySelectorAll(selector)));
	      }
	    } catch (_) {}

	    let elements = [];
	    try {
	      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
	    } catch (_) {}

	    for (const el of elements) {
	      if (el.shadowRoot) {
	        stack.push(el.shadowRoot);
	      }
	    }
	  }

	  return out;
	}

	function asClickable(el) {
	  if (!el) return null;
	  return el.closest?.(
	    'button, a, [role="button"], [role="link"], [role="row"], '
	    + '[data-automationid="DetailsRow"], .ms-DetailsRow'
	  ) || el;
	}

	function clickNode(node) {
	  const clickable = asClickable(node);
	  if (!clickable || !visible(clickable)) return false;
	  try {
	    clickable.scrollIntoView({ block: 'center', inline: 'nearest' });
	  } catch (_) {}
	  try {
	    clickable.click();
	  } catch (_) {
	    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
	  }
	  return true;
	}

	const targetText = normalize(alertName);
	const selectors = [
	  'button',
	  'a',
	  '[role="button"]',
	  '[role="link"]',
	  '[data-automation-key="AlertName"]',
	  '[data-automationid="DetailsRowCell"]',
	  '.ms-DetailsRow-cell',
	  '[role="gridcell"]',
	  '[data-automationid="DetailsRow"]',
	  '.ms-DetailsRow',
	  '[role="row"]',
	];

	let exactMatch = null;
	let containingMatch = null;

	for (const doc of collectDocs(document)) {
	  for (const selector of selectors) {
	    const nodes = queryAllDeep(doc, selector).filter(visible);
	    for (const node of nodes) {
	      const text = normalize(node.innerText || node.textContent || '');
	      if (!text) continue;
	      if (text === targetText) {
	        exactMatch = node;
	        break;
	      }
	      if (!containingMatch && text.includes(targetText)) {
	        containingMatch = node;
	      }
	    }
	    if (exactMatch) break;
	  }
	  if (exactMatch) break;
	}

	const target = exactMatch || containingMatch;
	if (!target) {
	  return { clicked: false, alert_name: alertName };
	}

	return {
	  clicked: clickNode(target),
	  alert_name: alertName,
	  matched_text: normalize(target.innerText || target.textContent || ''),
	};
	"""

	clicked_alerts: list[str] = []
	missing_alerts: list[str] = []
	per_click_timeout = max(20, min(timeout, 30))

	for alert_record in alert_records:
		alert_name = str(alert_record.get("alert_name") or "").strip()
		alert_location = str(alert_record.get("location") or "").strip()
		normalized_location = alert_location.lower()
		is_exchange_alert = "exchange" in normalized_location

		if not alert_name:
			continue

		result = driver.execute_script(click_script, alert_name)
		if not isinstance(result, dict) or not result.get("clicked"):
			missing_alerts.append(alert_name)
			continue

		clicked_alerts.append(alert_name)
		print(f"Clicked Purview alert entry: {alert_name} (Location: {alert_location or 'Unknown'})")

		click_open_alerts_page_from_panel(alert_name, alert_location, per_click_timeout)

		if is_exchange_alert:
			outlook_download_completed = search_outlook_for_alert_subject(
				driver,
				alert_name,
				per_click_timeout,
				outlook_handle,
			)
			if outlook_download_completed:
				try:
					refreshed_classifiers_path, refreshed_sit_path = refresh_saved_classifier_artifacts(alert_name)
					if refreshed_classifiers_path:
						print(f"Refreshed classifiers HTML for {alert_name} after Outlook download: {refreshed_classifiers_path}")
					if refreshed_sit_path:
						print(f"Refreshed SIT analysis HTML for {alert_name} after Outlook download: {refreshed_sit_path}")
						upload_alert_folder_files_to_odin(alert_name)
				except Exception as exc:  # noqa: BLE001
					print(
						f"Failed to refresh classifiers/SIT artifacts after Outlook download for {alert_name}: {exc}",
						file=sys.stderr,
					)

		try:
			wait_for_root_1202_loaded(driver, per_click_timeout)
			wait_for_purview_grid_settled(driver, per_click_timeout)
		except Exception:
			try:
				driver.back()
				wait_for_page_load(driver, per_click_timeout)
				wait_for_root_1202_loaded(driver, per_click_timeout)
				wait_for_purview_grid_settled(driver, per_click_timeout)
			except Exception as recovery_exc:  # noqa: BLE001
				print(
					"Failed to recover Purview grid after clicking alert entry "
					f"{alert_name}: {recovery_exc}",
					file=sys.stderr,
				)
				break

	if missing_alerts:
		print(
			"Purview alert entries not found in live grid: "
			+ ", ".join(missing_alerts),
			file=sys.stderr,
		)

	refresh_dashboard_artifacts_cache(_get_workspace_root())

	return clicked_alerts, missing_alerts


def merge_with_existing_text_rows(
	text_path: str,
	new_headers: list[str],
	new_rows: list[list[str]],
) -> tuple[list[str], list[list[str]], int, int]:
	"""Merge extracted rows with existing text file and append only new rows.

	Returns:
		(merged_headers, merged_rows, added_row_count, existing_row_count)
	"""
	if not os.path.exists(text_path):
		return new_headers, new_rows, len(new_rows), 0

	existing_headers: list[str] = []
	existing_rows: list[list[str]] = []
	for attempt in range(3):
		existing_headers, existing_rows = read_purview_text_file(text_path)
		if existing_headers:
			break
		if attempt < 2:
			time.sleep(1)
	if not existing_headers:
		return new_headers, new_rows, len(new_rows), 0

	merged_headers = list(existing_headers)
	for header in new_headers:
		if header not in merged_headers:
			merged_headers.append(header)

	aligned_existing_rows = _align_rows_to_headers(merged_headers, existing_headers, existing_rows)
	aligned_new_rows = _align_rows_to_headers(merged_headers, new_headers, new_rows)

	def _key(row: list[str]) -> tuple[str, ...]:
		return tuple(str(cell).strip() for cell in row)

	existing_keys = {_key(row) for row in aligned_existing_rows}
	rows_to_add: list[list[str]] = []
	for row in aligned_new_rows:
		if not any(str(cell).strip() for cell in row):
			continue
		row_key = _key(row)
		if row_key in existing_keys:
			continue
		existing_keys.add(row_key)
		rows_to_add.append(row)

	merged_rows = aligned_existing_rows + rows_to_add
	return merged_headers, merged_rows, len(rows_to_add), len(aligned_existing_rows)


def write_alert_record_metadata(folder_path: str, alert_record: dict[str, str]) -> str:
	metadata_path = os.path.join(folder_path, "alert-record.json")
	with open(metadata_path, "w", encoding="utf-8") as metadata_file:
		json.dump(alert_record, metadata_file, indent=2, ensure_ascii=False)
	return metadata_path


def create_alert_folders_from_data(
	text_path: str,
	startswith: str | None = None,
) -> None:
	r"""Create folders in artifacts/ for each alert row in the Purview text file."""
	alert_records = get_alert_detail_records_from_data(text_path, startswith=startswith)
	if not alert_records:
		if startswith:
			print(
				"No matching rows found in Purview text file for folder creation. "
				f"Condition is Alert Name starts with {startswith!r}."
			)
		else:
			print("No alert names found in Purview text file for folder creation.")
		refresh_dashboard_artifacts_cache(_get_workspace_root())
		return

	created_count = 0
	already_exists_count = 0
	metadata_written_count = 0

	for alert_record in alert_records:
		alert_name = str(alert_record.get("Alert Name") or "").strip()
		if not alert_name:
			continue

		folder_path = get_alert_artifact_folder_path(alert_name)
		folder_already_exists = os.path.isdir(folder_path)
		try:
			os.makedirs(folder_path, exist_ok=True)
			if folder_already_exists:
				already_exists_count += 1
				print(f"Alert folder already exists: {folder_path}")
			else:
				created_count += 1
				print(f"Created alert folder: {folder_path}")

			write_alert_record_metadata(folder_path, alert_record)
			metadata_written_count += 1
		except Exception as exc:
			print(f"Error creating alert folder {folder_path}: {exc}", file=sys.stderr)

	if startswith:
		print(
			"Alert folder creation complete. "
			f"prefix={startswith!r}, matched_rows={len(alert_records)}, created={created_count}, "
			f"existing={already_exists_count}, metadata_written={metadata_written_count}."
		)
	else:
		print(
			"Alert folder creation complete. "
			f"matched_rows={len(alert_records)}, created={created_count}, "
			f"existing={already_exists_count}, metadata_written={metadata_written_count}."
		)

	refresh_dashboard_artifacts_cache(_get_workspace_root())


def create_dlp_folders_from_data(text_path: str) -> None:
	r"""Create folders in artifacts/ for each DLP alert in the Purview text file."""
	create_alert_folders_from_data(text_path, startswith="DLP")


_monitoring_active = False
_monitor_thread = None
_driver_operation_lock = threading.Lock()
_shutdown_requested = False


def shutdown_requested() -> bool:
	return _shutdown_requested


def request_shutdown(reason: str, *, announce: bool = True) -> None:
	global _monitoring_active, _shutdown_requested
	first_request = not _shutdown_requested
	_shutdown_requested = True
	_monitoring_active = False
	if announce and first_request:
		print(reason, file=sys.stderr)


def install_shutdown_signal_handlers() -> None:
	def handle_shutdown_signal(signum: int, _frame) -> None:
		signal_names = {
			getattr(signal, "SIGINT", None): "SIGINT",
			getattr(signal, "SIGTERM", None): "SIGTERM",
			getattr(signal, "SIGBREAK", None): "SIGBREAK",
		}
		signal_name = signal_names.get(signum, f"signal {signum}")
		request_shutdown(f"Shutdown requested via {signal_name}.")
		raise KeyboardInterrupt

	for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
		signal_value = getattr(signal, signal_name, None)
		if signal_value is None:
			continue
		try:
			signal.signal(signal_value, handle_shutdown_signal)
		except (OSError, RuntimeError, ValueError):
			continue


def wait_for_shutdown_or_timeout(seconds: int) -> bool:
	deadline = time.time() + max(0, seconds)
	while time.time() < deadline:
		if shutdown_requested():
			return False
		time.sleep(min(1.0, deadline - time.time()))
	return not shutdown_requested()


def monitor_purview_table_worker(
	text_path: str,
	check_interval: int = 10,
	refresh_callback: Callable[[], None] | None = None,
	alert_name_prefix: str | None = None,
) -> None:
	"""Background worker thread to monitor for new rows in Purview export.
	
	Runs until _monitoring_active is set to False. Every interval it can refresh
	the data from live Purview data (if refresh_callback is provided),
	then checks for newly added rows.
	
	Args:
		text_path: Path to the JSON array text file to monitor
		check_interval: Seconds between checks (default: 10)
		refresh_callback: Optional callback to refresh data before checking rows
	"""
	_, initial_rows = read_purview_text_file(text_path)
	previous_row_count = len(initial_rows)
	print(f"[Purview Monitor] Initial row count: {previous_row_count}")
	
	while _monitoring_active and not shutdown_requested():
		try:
			if refresh_callback is not None:
				refresh_callback()

			headers, rows = read_purview_text_file(text_path)
			current_row_count = len(rows)
			
			if current_row_count > previous_row_count:
				new_count = current_row_count - previous_row_count
				print(
					f"[Purview Monitor] New rows detected: {previous_row_count} → {current_row_count}. "
					f"Detected {new_count} new row(s)."
				)
				
				# Ensure alert folders are created for newly merged data.
				if alert_name_prefix:
					print(
						f"[Purview Monitor] Creating alert folders for updated data with prefix {alert_name_prefix!r}..."
					)
				else:
					print("[Purview Monitor] Creating alert folders for updated data...")
				create_alert_folders_from_data(text_path, startswith=alert_name_prefix)
				
				previous_row_count = current_row_count
		except Exception as exc:
			print(f"[Purview Monitor] Error reading Excel file: {exc}", file=sys.stderr)
		
		# Sleep in small intervals to allow quick stop signal
		for _ in range(check_interval):
			if not _monitoring_active or shutdown_requested():
				break
			time.sleep(1)


def start_purview_table_monitor(
	text_path: str,
	check_interval: int = 10,
	refresh_callback: Callable[[], None] | None = None,
	alert_name_prefix: str | None = None,
) -> threading.Thread:
	"""Start a background thread to monitor the Purview export for new rows.
	
	The monitor runs in a separate thread while other code executes. It can also
	refresh the data every check interval using a callback.
	
	Args:
		text_path: Path to the db_info.txt file
		check_interval: Seconds between checks (default: 10)
		refresh_callback: Optional callback to refresh data before each check
		
	Returns:
		The monitoring thread object
	"""
	global _monitoring_active, _monitor_thread
	
	if _monitoring_active and _monitor_thread and _monitor_thread.is_alive():
		print("[Purview Monitor] Monitor already running.")
		return _monitor_thread
	
	_monitoring_active = True
	_monitor_thread = threading.Thread(
		target=monitor_purview_table_worker,
		args=(text_path, check_interval, refresh_callback, alert_name_prefix),
		daemon=True,
		name="PurviewTableMonitor"
	)
	_monitor_thread.start()
	print(f"[Purview Monitor] Started background monitoring (check interval: {check_interval}s)")
	return _monitor_thread


def stop_purview_table_monitor() -> None:
	"""Stop the background Purview table monitor thread.
	
	Signals the monitor to stop and waits for it to finish.
	"""
	global _monitoring_active, _monitor_thread
	
	if not _monitoring_active or not _monitor_thread:
		return
	
	print("[Purview Monitor] Stopping background monitor...")
	_monitoring_active = False
	
	if _monitor_thread:
		_monitor_thread.join(timeout=5)
		if _monitor_thread.is_alive():
			print("[Purview Monitor] Monitor thread did not stop cleanly (timeout)", file=sys.stderr)
		else:
			print("[Purview Monitor] Monitor thread stopped.")


def refresh_purview_excel_from_driver(
	driver: WebDriver,
	purview_handle: str,
	timeout: int,
	alert_name_prefix: str | None = None,
) -> None:
	"""Refresh Excel export by re-capturing current Purview table from the live tab."""
	with _driver_operation_lock:
		driver.switch_to.window(purview_handle)
		try:
			wait_for_root_1202_loaded(driver, max(8, timeout))
			wait_for_purview_grid_settled(driver, max(8, timeout))
		except Exception as exc:  # noqa: BLE001
			print(f"[Purview Monitor] Pre-refresh wait warning: {exc}", file=sys.stderr)
		download_ms_list_surface_html(driver, timeout, alert_name_prefix=alert_name_prefix)


@dataclass(frozen=True)
class PageAction:
	url: str
	primary_locator: Locator | None = None
	fallback_locators: Tuple[Locator, ...] = ()


def parse_args() -> argparse.Namespace:
	default_edge_user_data = os.path.join(
		os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"
	)

	parser = argparse.ArgumentParser(
		description="Open three M365 URLs in separate Edge tabs and search for text."
	)
	parser.add_argument(
		"--search-text",
		default="test",
		help="Text to type into each page search input (default: test)",
	)
	parser.add_argument(
		"--timeout",
		type=int,
		default=30,
		help="Explicit wait timeout in seconds (default: 30)",
	)
	parser.add_argument(
		"--edge-user-data-dir",
		default=default_edge_user_data,
		help=(
			"Edge user data directory. "
			"Example: C:/Users/<you>/AppData/Local/Microsoft/Edge/User Data"
		),
	)
	parser.add_argument(
		"--edge-profile-directory",
		default="",
		help=(
			"Edge profile directory inside user data dir. "
			"If omitted, the script prefers Profile 1 and then Default."
		),
	)
	parser.add_argument(
		"--attach-debugger-address",
		default="",
		help=(
			"Attach to an already-running Edge with remote debugging enabled, "
			"for example 127.0.0.1:9222"
		),
	)
	parser.add_argument(
		"--no-profile-fallback",
		action="store_true",
		help="Disable fallback mode that starts Edge without profile reuse if profile is locked.",
	)
	parser.add_argument(
		"--monitor",
		action="store_true",
		help="Keep the Purview background monitor running until Ctrl+C.",
	)
	parser.add_argument(
		"--keep-open",
		action="store_true",
		help="Keep browser open until Enter is pressed.",
	)
	parser.add_argument(
		"--alert-name-prefix",
		default="DLP",
		help="Only process alert names from db_info.txt that start with this prefix (default: DLP).",
	)
	return parser.parse_args()


def build_driver(user_data_dir: str, profile_directory: str) -> WebDriver:
	options = EdgeOptions()

	# Reuse local Edge session/profile state.
	options.add_argument(f"--user-data-dir={user_data_dir}")
	options.add_argument(f"--profile-directory={profile_directory}")
	options.add_argument("--start-maximized")

	# Helps reduce automation infobars; harmless if ignored by driver/browser version.
	options.add_experimental_option("excludeSwitches", ["enable-automation"])

	return webdriver.Edge(options=options)


def build_driver_attached(debugger_address: str) -> WebDriver:
	options = EdgeOptions()
	options.debugger_address = debugger_address
	return webdriver.Edge(options=options)


def build_driver_no_profile() -> WebDriver:
	options = EdgeOptions()
	options.add_argument("--start-maximized")
	return webdriver.Edge(options=options)


def is_profile_lock_error(exc: WebDriverException) -> bool:
	message = str(exc).lower()
	return "session not created" in message and (
		"chrome instance exited" in message
		or "user data directory is already in use" in message
	)


def resolve_profile_directory(user_data_dir: str, requested_profile: str) -> str:
	if requested_profile:
		return requested_profile

	# Prefer the profile that has a signed-in user in Edge Local State.
	local_state_path = os.path.join(user_data_dir, "Local State")
	if os.path.isfile(local_state_path):
		try:
			with open(local_state_path, "r", encoding="utf-8") as f:
				local_state = json.load(f)

			info_cache = local_state.get("profile", {}).get("info_cache", {})
			candidates = [
				d for d in ("Default", "Profile 1") if os.path.isdir(os.path.join(user_data_dir, d))
			]

			if candidates:
				def score_profile(profile_dir: str) -> tuple[int, int]:
					entry = info_cache.get(profile_dir, {}) if isinstance(info_cache, dict) else {}
					user_name = str(entry.get("user_name", "")).strip()
					# Higher score for profiles with an account; tie-breaker favors Default.
					return (1 if user_name else 0, 1 if profile_dir == "Default" else 0)

				return max(candidates, key=score_profile)
		except Exception:  # noqa: BLE001
			pass

	for candidate in ("Default", "Profile 1"):
		if os.path.isdir(os.path.join(user_data_dir, candidate)):
			return candidate

	raise FileNotFoundError(
		"Could not find an Edge profile directory. Checked Profile 1 and Default."
	)


def copy_profile_for_automation(user_data_dir: str, profile_directory: str) -> str:
	temp_user_data_dir = tempfile.mkdtemp(prefix="edge-selenium-")
	profile_source = os.path.join(user_data_dir, profile_directory)
	profile_target = os.path.join(temp_user_data_dir, profile_directory)

	if not os.path.isdir(profile_source):
		raise FileNotFoundError(f"Edge profile directory not found: {profile_source}")

	local_state_source = os.path.join(user_data_dir, "Local State")
	if os.path.isfile(local_state_source):
		shutil.copy2(local_state_source, os.path.join(temp_user_data_dir, "Local State"))

	copy_started_at = time.perf_counter()
	ignored_names = shutil.ignore_patterns(
		"lockfile",
		"Singleton*",
		"*.lock",
		"Crashpad*",
		"Archived History",
		"Cache",
		"CacheStorage",
		"Code Cache",
		"Current Session",
		"Current Tabs",
		"GPUCache",
		"DawnGraphiteCache",
		"DawnWebGPUCache",
		"GrShaderCache",
		"GraphiteDawnCache",
		"ShaderCache",
		"History",
		"History-journal",
		"History Provider Cache",
		"History Provider Cache-journal",
		"Favicons",
		"Favicons-journal",
		"Last Session",
		"Last Tabs",
		"Network Action Predictor",
		"Network Action Predictor-journal",
		"Sessions",
		"Session Storage",
		"Shortcuts",
		"Shortcuts-journal",
		"Service Worker",
		"Top Sites",
		"Top Sites-journal",
		"Visited Links",
		"blob_storage",
		"JumpListIcons*",
		"EntityExtraction",
		"EdgeJourneys",
	)
	skipped_locked_files: list[str] = []

	def copy_tree(source_dir: str, target_dir: str) -> None:
		os.makedirs(target_dir, exist_ok=True)
		ignored = set(ignored_names(source_dir, os.listdir(source_dir)))

		for entry in os.scandir(source_dir):
			if entry.name in ignored:
				continue

			source_path = entry.path
			target_path = os.path.join(target_dir, entry.name)

			if entry.is_dir(follow_symlinks=False):
				copy_tree(source_path, target_path)
				continue

			try:
				shutil.copy2(source_path, target_path)
			except PermissionError:
				skipped_locked_files.append(source_path)

	copy_tree(profile_source, profile_target)

	if skipped_locked_files:
		print(
			"Warning: Some locked Edge cache/session files could not be copied. "
			"If you need the logged-in session, close all Edge windows first.",
			file=sys.stderr,
		)

	copy_elapsed_seconds = time.perf_counter() - copy_started_at
	print(
		f"Copied Edge profile into temp automation directory in {copy_elapsed_seconds:.1f}s.")

	return temp_user_data_dir


def ensure_browser_window(driver: WebDriver, timeout_seconds: int = 15) -> None:
	deadline = time.time() + timeout_seconds
	last_error: Exception | None = None

	while time.time() < deadline:
		try:
			handles = driver.window_handles
			if handles:
				driver.switch_to.window(handles[0])
				return
		except WebDriverException as exc:
			last_error = exc

		try:
			driver.get("about:blank")
			handles = driver.window_handles
			if handles:
				driver.switch_to.window(handles[0])
				return
		except WebDriverException as exc:
			last_error = exc

	raise WebDriverException("Browser window not found after session start") from last_error


def collapse_to_single_browser_tab(driver: WebDriver) -> str:
	handles = list(driver.window_handles)
	if not handles:
		raise WebDriverException("No browser tabs were available after startup")

	keep_handle = handles[0]
	closed_count = 0

	for handle in handles[1:]:
		try:
			driver.switch_to.window(handle)
			driver.close()
			closed_count += 1
		except WebDriverException:
			continue

	try:
		driver.switch_to.window(keep_handle)
	except WebDriverException as exc:
		raise WebDriverException("Could not restore the automation startup tab") from exc

	if closed_count:
		print(f"Closed {closed_count} unexpected startup tab(s).")

	return keep_handle


def load_url_in_current_tab(driver: WebDriver, url: str) -> str:
	driver.get(url)
	return driver.current_window_handle


def wait_for_page_load(driver: WebDriver, timeout: int) -> None:
	WebDriverWait(driver, timeout).until(
		lambda d: d.execute_script("return document.readyState") == "complete"
	)


def wait_for_servicenow_with_browser_refresh(
	driver: WebDriver,
	total_timeout: int,
	recovery_after_seconds: int = 10,
	portal_label: str = "ServiceNow",
) -> None:
	start_time = time.time()
	next_recovery_at = recovery_after_seconds

	while time.time() - start_time < total_timeout:
		ready_state = str(driver.execute_script("return document.readyState"))
		current_url = driver.current_url.lower()
		if ready_state == "complete":
			if portal_label == "ServiceNow":
				if (
					"auth_redirect.do" not in current_url
					and "login.microsoftonline.com" not in current_url
				):
					return
			else:
				return

		elapsed = time.time() - start_time
		if elapsed >= next_recovery_at:
			print(
				f"{portal_label} still loading after 10s; force-stopping load then refreshing twice..."
			)
			# Force stop pending network/document loading even when keyboard events are ignored.
			try:
				driver.execute_cdp_cmd("Page.stopLoading", {})
			except Exception:  # noqa: BLE001
				pass
			try:
				driver.execute_script("window.stop();")
			except Exception:  # noqa: BLE001
				pass

			driver.refresh()
			time.sleep(0.4)
			driver.refresh()
			next_recovery_at += recovery_after_seconds

		time.sleep(0.5)

	raise TimeoutException(
		f"ServiceNow page did not finish loading within {total_timeout} seconds"
	)


def find_first_available_search_input(
	driver: WebDriver,
	timeout: int,
	locators: List[Locator],
) -> WebElement:
	last_error: Exception | None = None
	per_locator_timeout = max(4, int(timeout / max(1, len(locators))))

	for index, locator in enumerate(locators, start=1):
		try:
			print(
				f"Checking locator {index}/{len(locators)} with timeout {per_locator_timeout}s: {locator}"
			)
			return WebDriverWait(driver, per_locator_timeout).until(
				EC.element_to_be_clickable(locator)
			)
		except TimeoutException as exc:
			last_error = exc

	raise TimeoutException(
		f"None of the provided locators matched a clickable element: {locators}"
	) from last_error


def _normalize_browser_tab_url(url: str) -> str:
	return str(url or "").strip().lower().rstrip("/")


def find_existing_tab_for_url(driver: WebDriver, url: str) -> str | None:
	target_url = _normalize_browser_tab_url(url)
	if not target_url:
		return None

	original_handle = driver.current_window_handle if driver.window_handles else ""
	matching_handles: list[str] = []

	for handle in list(driver.window_handles):
		try:
			driver.switch_to.window(handle)
			current_url = _normalize_browser_tab_url(driver.current_url)
		except WebDriverException:
			continue

		if not current_url:
			continue

		if (
			current_url == target_url
			or current_url.startswith(target_url + "/")
			or current_url.startswith(target_url + "?")
			or current_url.startswith(target_url + "#")
		):
			matching_handles.append(handle)

	if not matching_handles:
		if original_handle:
			try:
				driver.switch_to.window(original_handle)
			except WebDriverException:
				pass
		return None

	keep_handle = matching_handles[0]
	for duplicate_handle in matching_handles[1:]:
		try:
			driver.switch_to.window(duplicate_handle)
			driver.close()
		except WebDriverException:
			continue

	try:
		driver.switch_to.window(keep_handle)
	except WebDriverException:
		return None

	return keep_handle


def open_url_in_new_tab(driver: WebDriver, url: str) -> str:
	try:
		current_handles = set(driver.window_handles)
		driver.execute_script("window.open(arguments[0], '_blank');", url)
		WebDriverWait(driver, 10).until(
			lambda d: len(d.window_handles) > len(current_handles)
		)
		new_handle = next(h for h in driver.window_handles if h not in current_handles)
		driver.switch_to.window(new_handle)
		return new_handle
	except WebDriverException:
		# Fallback for environments where script-based tab creation is blocked.
		driver.switch_to.new_window("tab")
		driver.execute_script("window.location.replace(arguments[0]);", url)
		return driver.current_window_handle


def type_into_search_box(driver: WebDriver, element: WebElement, text: str) -> None:
	try:
		element.click()
	except ElementClickInterceptedException:
		# Overlay dialogs in M365 portals can block click; force focus then type.
		driver.execute_script("arguments[0].focus();", element)
	element.send_keys(Keys.CONTROL, "a")
	element.send_keys(text)


def submit_search_input_with_enter(driver: WebDriver, search_input: WebElement) -> bool:
	search_triggered = False
	try:
		driver.execute_script(
			"arguments[0].focus(); if (arguments[0].setSelectionRange) { const end = String(arguments[0].value || '').length; arguments[0].setSelectionRange(end, end); }",
			search_input,
		)
		active_element = driver.switch_to.active_element
		if active_element is not None:
			active_element.send_keys(Keys.ENTER)
		else:
			search_input.send_keys(Keys.ENTER)
		ActionChains(driver).send_keys(Keys.ENTER).perform()
		search_triggered = True
	except Exception:
		search_triggered = False

	if search_triggered:
		return True

	try:
		return bool(
			driver.execute_script(
				"""
				const input = arguments[0];
				if (!input) {
				  return false;
				}

				const root = input.getRootNode ? input.getRootNode() : document;
				const buttonSelectors = [
				  'button[aria-label*="Search"]',
				  'button[title*="Search"]',
				  'button[data-icon-name="Search"]',
				  'button i[data-icon-name="Search"]',
				  '[role="button"][aria-label*="Search"]',
				];

				function buildEnterEvent(type) {
				  const event = new KeyboardEvent(type, {
				    key: 'Enter',
				    code: 'Enter',
				    keyCode: 13,
				    which: 13,
				    charCode: type === 'keypress' ? 13 : 0,
				    bubbles: true,
				    cancelable: true,
				    composed: true,
				  });

				  for (const [name, value] of [['keyCode', 13], ['which', 13], ['charCode', type === 'keypress' ? 13 : 0]]) {
				    try {
				      Object.defineProperty(event, name, { get: () => value });
				    } catch (_) {}
				  }

				  return event;
				}

				function imitateEnter(el) {
				  try {
				    el.focus();
				  } catch (_) {}

				  const keydownResult = el.dispatchEvent(buildEnterEvent('keydown'));
				  const keypressResult = el.dispatchEvent(buildEnterEvent('keypress'));
				  const keyupResult = el.dispatchEvent(buildEnterEvent('keyup'));

				  try {
				    el.dispatchEvent(new Event('change', { bubbles: true }));
				    el.dispatchEvent(new Event('search', { bubbles: true }));
				  } catch (_) {}

				  return keydownResult || keypressResult || keyupResult;
				}

				function resolveButton(node) {
				  if (!node) {
				    return null;
				  }
				  if (node.tagName && node.tagName.toLowerCase() === 'button') {
				    return node;
				  }
				  return node.closest ? node.closest('button') : null;
				}

				if (imitateEnter(input)) {
				  return true;
				}

				for (const selector of buttonSelectors) {
				  let candidate = null;
				  try {
				    candidate = root.querySelector(selector) || document.querySelector(selector);
				  } catch (_) {}
				  const button = resolveButton(candidate);
				  if (button && !button.disabled) {
				    try {
				      button.click();
				      return true;
				    } catch (_) {}
				  }
				}

				try {
				  input.dispatchEvent(new Event('change', { bubbles: true }));
				  input.dispatchEvent(new Event('search', { bubbles: true }));
				} catch (_) {}

				try {
				  const form = input.closest ? input.closest('form') : null;
				  if (form) {
				    if (typeof form.requestSubmit === 'function') {
				      form.requestSubmit();
				      return true;
				    }
				    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
				    return true;
				  }
				} catch (_) {}

				try {
				  input.blur();
				} catch (_) {}

				return true;
				""",
				search_input,
			)
		)
	except Exception:
		return False


def click_element_css(driver: WebDriver, selector: str, timeout: int) -> None:
	element = WebDriverWait(driver, timeout).until(
		EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
	)
	try:
		element.click()
	except ElementClickInterceptedException:
		driver.execute_script("arguments[0].click();", element)
	except WebDriverException:
		driver.execute_script("arguments[0].click();", element)


def fill_input_css(driver: WebDriver, selector: str, value: str, timeout: int) -> None:
	element = WebDriverWait(driver, timeout).until(
		EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
	)
	try:
		element.click()
		element.send_keys(Keys.CONTROL, "a")
		element.send_keys(value)
	except WebDriverException:
		driver.execute_script(
			"""
			const el = arguments[0];
			const val = arguments[1];
			el.focus();
			el.value = val;
			el.dispatchEvent(new Event('input', { bubbles: true }));
			el.dispatchEvent(new Event('change', { bubbles: true }));
			el.blur();
			""",
			element,
			value,
		)


def click_first_available(driver: WebDriver, selectors: List[str], timeout: int) -> None:
	last_error: Exception | None = None
	per_selector_timeout = max(2, int(timeout / max(1, len(selectors))))
	for selector in selectors:
		try:
			click_element_css(driver, selector, per_selector_timeout)
			return
		except Exception as exc:  # noqa: BLE001
			last_error = exc
	raise WebDriverException(f"Failed to click selectors: {selectors}") from last_error


def fill_first_available_input(
	driver: WebDriver,
	selectors: List[str],
	value: str,
	timeout: int,
) -> None:
	last_error: Exception | None = None
	per_selector_timeout = max(2, int(timeout / max(1, len(selectors))))
	for selector in selectors:
		try:
			fill_input_css(driver, selector, value, per_selector_timeout)
			return
		except Exception as exc:  # noqa: BLE001
			last_error = exc
	raise WebDriverException(f"Failed to fill selectors: {selectors}") from last_error


def fill_datepicker_by_index_js(driver: WebDriver, index: int, value: str) -> bool:
	return bool(
		driver.execute_script(
			"""
			const idx = arguments[0];
			const val = arguments[1];

			function collectCandidates(doc) {
			  const allInputs = Array.from(doc.querySelectorAll('input'));
			  const visibleInputs = allInputs.filter(el => {
			    const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
			    return visible && !el.disabled;
			  });

			  const preferred = visibleInputs.filter(el => {
			    const id = (el.id || '').toLowerCase();
			    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
			    const ph = (el.getAttribute('placeholder') || '').toLowerCase();
			    const type = (el.getAttribute('type') || '').toLowerCase();
			    return (
			      id.includes('datepicker') ||
			      id.endsWith('-label') ||
			      aria.includes('date') ||
			      ph.includes('date') ||
			      type === 'date'
			    );
			  });

			  if (preferred.length >= 2) {
			    return preferred;
			  }

			  const fallbackTextLike = visibleInputs.filter(el => {
			    const type = (el.getAttribute('type') || 'text').toLowerCase();
			    return type === 'text' || type === 'search' || type === 'date';
			  });
			  return fallbackTextLike;
			}

			let candidates = collectCandidates(document);

			if (candidates.length <= idx) {
			  const frames = Array.from(document.querySelectorAll('iframe'));
			  for (const frame of frames) {
			    try {
			      const fdoc = frame.contentDocument || frame.contentWindow?.document;
			      if (!fdoc) continue;
			      const inFrame = collectCandidates(fdoc);
			      if (inFrame.length > idx) {
			        candidates = inFrame;
			        break;
			      }
			    } catch (_) {
			      // Ignore cross-origin frames.
			    }
			  }
			}

			if (candidates.length <= idx) {
			  return false;
			}
			const el = candidates[idx];
			el.focus();
			el.value = val;
			el.dispatchEvent(new Event('input', { bubbles: true }));
			el.dispatchEvent(new Event('change', { bubbles: true }));
			el.blur();
			return true;
			""",
			index,
			value,
		)
	)


def _click_requested_class_in_current_frame(driver: WebDriver) -> bool:
	selectors = [
		"button.ms-Button.ms-Button--default.ms-Button--hasMenu",
		"button.ms-Button--default.ms-Button--hasMenu",
		"button.ms-Button--hasMenu[aria-haspopup='menu']",
		"button[aria-haspopup='menu']",
		"[role='button'][aria-haspopup='menu']",
		"[class*='ms-Button'][class*='ms-Button--hasMenu']",
	]

	def try_click(element: WebElement) -> bool:
		try:
			if not element.is_displayed():
				return False
		except WebDriverException:
			return False

		try:
			driver.execute_script(
				"arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
				element,
			)
		except WebDriverException:
			pass

		try:
			ActionChains(driver).move_to_element(element).click().perform()
			return True
		except WebDriverException:
			pass

		try:
			element.click()
			return True
		except WebDriverException:
			pass

		try:
			driver.execute_script("arguments[0].click();", element)
			return True
		except WebDriverException:
			return False

	lookup_script = r"""
	const selectors = arguments[0] || [];
	const preferredTerms = ['requested class', 'requested', 'requester', 'requested by', 'classification', 'class'];
	const rejectTerms = ['view more', 'more actions', 'download', 'apply', 'open alerts', 'search'];
	const seenRoots = new Set();
	const docs = [];

	function collectDocs(root) {
	  if (!root || seenRoots.has(root)) {
	    return;
	  }
	  seenRoots.add(root);
	  docs.push(root);
	  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
	  while (walker.nextNode()) {
	    const node = walker.currentNode;
	    if (node && node.shadowRoot) {
	      collectDocs(node.shadowRoot);
	    }
	  }
	}

	function queryAllDeep(root, selector) {
	  const out = [];
	  const seen = new Set();
	  const stack = [root];

	  while (stack.length) {
	    const node = stack.pop();
	    if (!node || seen.has(node)) continue;
	    seen.add(node);

	    try {
	      if (node.querySelectorAll) {
	        out.push(...Array.from(node.querySelectorAll(selector)));
	      }
	    } catch (_) {}

	    let elements = [];
	    try {
	      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
	    } catch (_) {}

	    for (const element of elements) {
	      if (element.shadowRoot) {
	        stack.push(element.shadowRoot);
	      }
	    }
	  }

	  return out;
	}

	function normalize(value) {
	  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
	}

	function isVisible(node) {
	  if (!(node instanceof Element)) {
	    return false;
	  }
	  const style = window.getComputedStyle(node);
	  if (!style || style.display === 'none' || style.visibility === 'hidden') {
	    return false;
	  }
	  const rect = node.getBoundingClientRect();
	  return rect.width > 0 && rect.height > 0;
	}

	function resolveClickTarget(node) {
	  if (!(node instanceof Element)) {
	    return null;
	  }
	  const selfClickable = node.closest?.('button, [role="button"], [role="menuitem"], a, [tabindex]');
	  if (selfClickable && isVisible(selfClickable)) {
	    return selfClickable;
	  }
	  const descendantClickable = node.querySelector?.('button, [role="button"], [role="menuitem"], a, [tabindex]');
	  if (descendantClickable && isVisible(descendantClickable)) {
	    return descendantClickable;
	  }
	  return isVisible(node) ? node : null;
	}

	function summarize(node) {
	  return [
	    normalize(node?.innerText || node?.textContent || ''),
	    normalize(node?.getAttribute?.('aria-label') || ''),
	    normalize(node?.getAttribute?.('title') || ''),
	    normalize(node?.id || ''),
	  ].filter(Boolean).join(' | ');
	}

	function scoreCandidate(node, summary) {
	  const className = normalize(node?.className || '');
	  const role = normalize(node?.getAttribute?.('role') || '');
	  const ariaHasPopup = normalize(node?.getAttribute?.('aria-haspopup') || '');
	  const ariaExpanded = normalize(node?.getAttribute?.('aria-expanded') || '');
	  let score = 0;

	  if (node?.tagName?.toLowerCase() === 'button') score += 8;
	  if (role === 'button' || role === 'menuitem') score += 4;
	  if (ariaHasPopup === 'menu' || ariaHasPopup === 'true') score += 12;
	  if (className.includes('ms-button--hasmenu')) score += 10;
	  if (className.includes('ms-button--default')) score += 6;
	  if (ariaExpanded === 'false') score += 2;

	  for (const term of preferredTerms) {
	    if (summary.includes(term)) {
	      score += term === 'requested class' ? 30 : 10;
	    }
	  }

	  return score;
	}

	collectDocs(document);
	const candidates = [];
	const seenTargets = new Set();

	for (const root of docs) {
	  for (const selector of selectors) {
	    for (const node of queryAllDeep(root, selector)) {
	      const clickTarget = resolveClickTarget(node);
	      if (!(clickTarget instanceof Element) || seenTargets.has(clickTarget) || !isVisible(clickTarget)) {
	        continue;
	      }
	      seenTargets.add(clickTarget);
	      const summary = summarize(clickTarget);
	      if (rejectTerms.some((term) => summary.includes(term))) {
	        continue;
	      }
	      candidates.push({
	        element: clickTarget,
	        summary,
	        score: scoreCandidate(clickTarget, summary),
	      });
	    }
	  }
	}

	if (!candidates.length) {
	  return null;
	}

	candidates.sort((left, right) => right.score - left.score || left.summary.length - right.summary.length);
	return candidates[0].element;
	"""

	try:
		deep_target = driver.execute_script(lookup_script, selectors)
		if isinstance(deep_target, WebElement) and try_click(deep_target):
			return True
	except WebDriverException:
		pass

	buttons = []
	for selector in selectors:
		buttons = driver.find_elements(By.CSS_SELECTOR, selector)
		if buttons:
			break
	for button in buttons:
		if try_click(button):
			return True
	return False

def click_requested_purview_class_button(driver: WebDriver, timeout: int) -> bool:
	def search_current_context() -> bool:
		try:
			if _click_requested_class_in_current_frame(driver):
				return True
		except WebDriverException:
			pass

		frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
		for frame_index in range(len(frames)):
			try:
				frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
				if frame_index >= len(frames):
					continue
				driver.switch_to.frame(frames[frame_index])
				if search_current_context():
					return True
			except WebDriverException:
				continue
			finally:
				try:
					driver.switch_to.parent_frame()
				except WebDriverException:
					pass

		return False

	deadline = time.time() + timeout
	while time.time() < deadline:
		try:
			driver.switch_to.default_content()
			if search_current_context():
				driver.switch_to.default_content()
				return True
		except WebDriverException:
			pass

		time.sleep(0.25)

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	return False


def run_purview_post_load_actions(driver: WebDriver, timeout: int) -> None:
	print("Running Purview post-load actions...")
	if not click_requested_purview_class_button(driver, max(8, timeout)):
		raise WebDriverException(
			"Purview requested class click failed: no matching visible menu button was found"
		)

	result = driver.execute_script(
		"""
		const startValue = 'May 12, 2026';
		const endValue = 'May 12, 2026';

		function visible(el) {
		  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
		}

		function collectDocs(rootDoc) {
		  const docs = [rootDoc];
		  for (const frame of Array.from(rootDoc.querySelectorAll('iframe'))) {
		    try {
		      const fdoc = frame.contentDocument || frame.contentWindow?.document;
		      if (fdoc) docs.push(fdoc);
		    } catch (_) {
		      // Ignore cross-origin frames.
		    }
		  }
		  return docs;
		}

		function queryAllDeep(root, selector) {
		  const out = [];
		  const seen = new Set();
		  const stack = [root];

		  while (stack.length) {
		    const node = stack.pop();
		    if (!node || seen.has(node)) continue;
		    seen.add(node);

		    try {
		      if (node.querySelectorAll) {
		        out.push(...Array.from(node.querySelectorAll(selector)));
		      }
		    } catch (_) {}

		    let elements = [];
		    try {
		      elements = node.querySelectorAll ? Array.from(node.querySelectorAll('*')) : [];
		    } catch (_) {}

		    for (const el of elements) {
		      if (el.shadowRoot) {
		        stack.push(el.shadowRoot);
		      }
		    }
		  }

		  return out;
		}

		function textOf(el) {
		  return String(el?.innerText || el?.textContent || '').trim().toLowerCase();
		}

		function hasApplyLabel(el) {
		  const txt = textOf(el);
		  const aria = String(el?.getAttribute?.('aria-label') || '').toLowerCase();
		  const title = String(el?.getAttribute?.('title') || '').toLowerCase();
		  return txt === 'apply' || aria.includes('apply') || title.includes('apply');
		}

		function asClickable(el) {
		  if (!el) return null;
		  if (el.tagName?.toLowerCase() === 'button') return el;
		  const role = String(el.getAttribute?.('role') || '').toLowerCase();
		  if (role === 'button' || role === 'menuitem') return el;
		  return el.closest?.('button, [role="button"], [role="menuitem"], [tabindex]') || el;
		}

		function summarizeClickable(node) {
		  return [
		    String(node?.innerText || node?.textContent || ''),
		    String(node?.getAttribute?.('aria-label') || ''),
		    String(node?.getAttribute?.('title') || ''),
		    String(node?.className || ''),
		  ].join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
		}

		function toUsDateTerm(value) {
		  const parsed = new Date(String(value || ''));
		  if (Number.isNaN(parsed.getTime())) {
		    return '';
		  }
		  return `${parsed.getMonth() + 1}/${parsed.getDate()}/${parsed.getFullYear()}`;
		}

		function collectRelevantClickableSummaries(docs) {
		  const selectors = ['button', '[role="button"]', '[role="menuitem"]', 'a'];
		  const summaries = [];
		  const seen = new Set();

		  for (const doc of docs) {
		    for (const selector of selectors) {
		      const nodes = queryAllDeep(doc, selector).filter(visible);
		      for (const node of nodes) {
		        const clickable = asClickable(node);
		        if (!clickable || !visible(clickable) || seen.has(clickable)) {
		          continue;
		        }
		        seen.add(clickable);
		        const summary = summarizeClickable(clickable);
		        if (!summary) {
		          continue;
		        }
		        if (!(summary.includes('time') || summary.includes('range') || summary.includes('request') || summary.includes('class') || summary.includes('filter') || summary.includes('apply'))) {
		          continue;
		        }
		        summaries.push(summary);
		        if (summaries.length >= 15) {
		          return summaries;
		        }
		      }
		    }
		  }

		  return summaries;
		}

		function findClickableByText(docs, preferredTerms, rejectTerms = []) {
		  const selectors = [
		    'button',
		    '[role="button"]',
		    '[role="menuitem"]',
		    'a',
		    'span',
		    'div',
		  ];

		  let bestButton = null;
		  let bestScore = -1;

		  for (const doc of docs) {
		    for (const selector of selectors) {
		      const nodes = queryAllDeep(doc, selector).filter(visible);
		      for (const node of nodes) {
		        const clickable = asClickable(node);
		        if (!clickable || !visible(clickable)) {
		          continue;
		        }

		        const summary = summarizeClickable(clickable);
		        if (rejectTerms.some((term) => summary.includes(term))) {
		          continue;
		        }

		        let score = 0;
		        for (const term of preferredTerms) {
		          if (!summary.includes(term)) {
		            continue;
		          }
		          score += term === 'time range' || term === 'requested class' ? 100 : 25;
		        }

		        if (score <= 0) {
		          continue;
		        }

		        if (clickable.tagName?.toLowerCase() === 'button') score += 4;
		        const ariaHasPopup = String(clickable?.getAttribute?.('aria-haspopup') || '').toLowerCase();
		        if (ariaHasPopup === 'menu' || ariaHasPopup === 'true') score += 6;
		        if (score > bestScore) {
		          bestButton = clickable;
		          bestScore = score;
		        }
		      }
		    }
		  }

		  return bestButton;
		}

		function findApplyButton(docs) {
		  const selectors = [
		    'button',
		    '[role="menuitem"]',
		    '[role="button"]',
		    'span',
		    'div',
		  ];

		  for (const doc of docs) {
		    for (const selector of selectors) {
		      const candidates = queryAllDeep(doc, selector).filter(visible);
		      for (const node of candidates) {
		        if (!hasApplyLabel(node)) continue;
		        const clickable = asClickable(node);
		        if (visible(clickable)) {
		          return clickable;
		        }
		      }
		    }
		  }
		  return null;
		}

		function findCommandBarButton(docs) {
		  const selectors = [
		    'button.ms-Button.ms-Button--commandBar.scc-listButton',
		    'button.ms-Button--commandBar.scc-listButton',
		    'button.scc-listButton[aria-haspopup="menu"]',
		    'button.ms-Button--commandBar[aria-haspopup="menu"]',
		    '[role="button"].ms-Button--commandBar',
		    'button.scc-listButton',
		  ];
		  const rejectTerms = ['download', 'apply', 'open alerts', 'search'];
		  const startDateTerm = toUsDateTerm(startValue);
		  const endDateTerm = toUsDateTerm(endValue);
		  const combinedDateTerm = startDateTerm && endDateTerm ? `${startDateTerm}-${endDateTerm}` : '';

		  const timeRangeButton = findClickableByText(
		    docs,
		    ['time range', 'time range:', combinedDateTerm, startDateTerm, endDateTerm].filter(Boolean),
		    rejectTerms,
		  );
		  if (timeRangeButton) {
		    return timeRangeButton;
		  }

		  function scoreButton(node, summary) {
		    const className = String(node?.className || '').toLowerCase();
		    const role = String(node?.getAttribute?.('role') || '').toLowerCase();
		    const ariaHasPopup = String(node?.getAttribute?.('aria-haspopup') || '').toLowerCase();
		    let score = 0;

		    if (node?.tagName?.toLowerCase() === 'button') score += 4;
		    if (role === 'button') score += 2;
		    if (className.includes('ms-button--commandbar')) score += 10;
		    if (className.includes('scc-listbutton')) score += 10;
		    if (ariaHasPopup === 'menu' || ariaHasPopup === 'true') score += 6;
		    if (summary.includes('filter')) score += 4;
		    if (summary.includes('time range')) score += 40;
		    if (combinedDateTerm && summary.includes(combinedDateTerm)) score += 30;
		    if (startDateTerm && summary.includes(startDateTerm)) score += 8;
		    if (endDateTerm && summary.includes(endDateTerm)) score += 8;

		    return score;
		  }

		  let bestButton = null;
		  let bestScore = -1;
		  for (const doc of docs) {
		    for (const selector of selectors) {
		      const buttons = queryAllDeep(doc, selector).filter(visible);
		      for (const btn of buttons) {
		        const summary = summarizeClickable(btn);
		        if (rejectTerms.some((term) => summary.includes(term))) {
		          continue;
		        }
		        const score = scoreButton(btn, summary);
		        if (score > bestScore) {
		          bestButton = btn;
		          bestScore = score;
		        }
		      }
		    }
		  }
		  return bestButton;
		}

		function dateInputs(docs) {
		  let inputs = [];
		  for (const doc of docs) {
		    const all = queryAllDeep(doc, 'input').filter(el => {
		      const id = (el.id || '').toLowerCase();
		      const aria = (el.getAttribute('aria-label') || '').toLowerCase();
		      const ph = (el.getAttribute('placeholder') || '').toLowerCase();
		      const dateLike = id.includes('datepicker') || id.endsWith('-label') || aria.includes('date') || ph.includes('date');
		      return dateLike && visible(el) && !el.disabled;
		    });
		    inputs = inputs.concat(all);
		  }
		  return inputs;
		}

		function setInput(el, val) {
		  el.focus();
		  el.value = val;
		  el.dispatchEvent(new Event('input', { bubbles: true }));
		  el.dispatchEvent(new Event('change', { bubbles: true }));
		  el.blur();
		}

		function typeIntoInput(el, val) {
		  if (!el) return;
		  el.focus();
		  el.value = '';
		  el.dispatchEvent(new Event('input', { bubbles: true }));
		  for (const ch of String(val)) {
		    el.value += ch;
		    el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
		    el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
		    el.dispatchEvent(new Event('input', { bubbles: true }));
		  }
		  el.dispatchEvent(new Event('change', { bubbles: true }));
		  el.blur();
		}

		function queryFirstVisible(docs, selector) {
		  for (const doc of docs) {
		    const nodes = queryAllDeep(doc, selector).filter(visible);
		    if (nodes.length) return nodes[0];
		  }
		  return null;
		}

		function findDatePickerActivationButton(docs) {
		  const selectors = [
		    'button.fui-Button.r1alrhcs.___3odye50.f44lkw9.f1062rbf.f17xqol7',
		    '.fui-Button.r1alrhcs.___3odye50.f44lkw9.f1062rbf.f17xqol7',
		  ];

		  for (const selector of selectors) {
		    const node = queryFirstVisible(docs, selector);
		    if (node) {
		      return asClickable(node);
		    }
		  }

		  return null;
		}

		function clickNode(node) {
		  if (!node) return false;
		  const clickable = asClickable(node);
		  if (!clickable || !visible(clickable)) return false;
		  try {
		    clickable.scrollIntoView({ block: 'center', inline: 'nearest' });
		  } catch (_) {}
		  try {
		    clickable.click();
		  } catch (_) {
		    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		  }
		  return true;
		}

		const initialDocs = collectDocs(document);
		const timeRangeButton = findCommandBarButton(initialDocs);
		if (!timeRangeButton || !clickNode(timeRangeButton)) {
		  return {
		    ok: false,
		    reason: 'time_range_button_not_found',
		    debug_buttons: collectRelevantClickableSummaries(initialDocs),
		  };
		}

		let datePickerActivationClicked = false;
		const activationWaitEnd = Date.now() + 4000;
		while (Date.now() < activationWaitEnd) {
		  const docsForActivation = collectDocs(document);
		  const activationButton = findDatePickerActivationButton(docsForActivation);
		  if (activationButton && clickNode(activationButton)) {
		    datePickerActivationClicked = true;
		    break;
		  }
		}

		const endWait = Date.now() + 6000;
		let inputs = [];
		while (Date.now() < endWait) {
		  inputs = dateInputs(collectDocs(document));
		  if (inputs.length >= 2) {
		    break;
		  }
		}
		if (inputs.length < 2) {
		  return {
		    ok: false,
		    reason: 'date_inputs_not_found',
		    count: inputs.length,
		    datepicker_activation_clicked: datePickerActivationClicked,
		    debug_buttons: collectRelevantClickableSummaries(collectDocs(document)),
		  };
		}

		const docsBeforeDatePopulation = collectDocs(document);
		if (!datePickerActivationClicked) {
		  const datePickerActivationButton = findDatePickerActivationButton(docsBeforeDatePopulation);
		  if (datePickerActivationButton) {
		    datePickerActivationClicked = clickNode(datePickerActivationButton);
		  }
		}

		setInput(inputs[0], startValue);
		setInput(inputs[1], endValue);

		// Requested extra actions immediately after datepicker population.
		const docsAfterDate = collectDocs(document);
		const requestedWarnings = [];
		const debugButtonSummaries = collectRelevantClickableSummaries(docsAfterDate);
		const requestedMenuButton =
		  findClickableByText(docsAfterDate, ['requested class', 'requested by', 'requested', 'classification'], ['download', 'apply', 'open alerts', 'search'])
		  || queryFirstVisible(docsAfterDate, 'button.ms-Button.ms-Button--default.ms-Button--hasMenu');
		if (!requestedMenuButton || !clickNode(requestedMenuButton)) {
		  requestedWarnings.push('requested_menu_button_not_found');
		}

		let requestedComboboxInput = null;
		if (requestedMenuButton) {
		  const comboWaitEnd = Date.now() + 6000;
		  while (Date.now() < comboWaitEnd && !requestedComboboxInput) {
		    const docsLive = collectDocs(document);
		    requestedComboboxInput =
		      queryFirstVisible(docsLive, 'input.ms-BasePicker-input')
		      || queryFirstVisible(docsLive, 'input[role="combobox"]')
		      || queryFirstVisible(docsLive, 'input[aria-autocomplete="list"]');
		    if (!requestedComboboxInput) {
		      requestedMenuButton.click();
		    }
		  }
		}
		if (!requestedComboboxInput) {
		  requestedWarnings.push('requested_combobox_input_not_found');
		} else {
		  typeIntoInput(requestedComboboxInput, 'Grace.Pelingon@vistracorp.com');
		}

		const docsAfterCombo = collectDocs(document);
		const requestedFlexContainer =
		  queryFirstVisible(docsAfterCombo, '.ms-Button-flexContainer')
		  || queryFirstVisible(docsAfterCombo, 'button[class*="flexContainer"]');
		if (!requestedFlexContainer) {
		  requestedWarnings.push('requested_flex_container_not_found');
		} else {
		  clickNode(requestedFlexContainer);
		}

		let applyButton = null;
		const applyWaitEnd = Date.now() + 4000;
		while (Date.now() < applyWaitEnd) {
		  applyButton = findApplyButton(collectDocs(document));
		  if (applyButton) break;
		}
		if (!applyButton) {
		  requestedWarnings.push('apply_button_not_found');
		} else {
		  try {
		    applyButton.click();
		  } catch (_) {
		    applyButton.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		  }
		}
		return {
		  ok: true,
		  count: inputs.length,
		  datepicker_activation_clicked: datePickerActivationClicked,
		  requested_warnings: requestedWarnings,
		  debug_buttons: debugButtonSummaries,
		};
		"""
	)

	if not result or not result.get("ok"):
		raise WebDriverException(f"Purview JS actions failed: {result}")

	warnings = result.get("requested_warnings") if isinstance(result, dict) else None
	if warnings:
		print(f"Purview requested extra action warnings: {warnings}")

	if isinstance(result, dict):
		print(
			"Purview datepicker activation button clicked: "
			f"{bool(result.get('datepicker_activation_clicked'))}"
		)

	print("Purview post-load actions completed.")


def download_ms_list_surface_html(
	driver: WebDriver,
	timeout: int,
	alert_name_prefix: str | None = None,
) -> str:
	deadline = time.time() + timeout
	html_content: str | None = None
	selectors = [
		".root-1202",
		".ms-DetailsList",
		".ms-List-surface",
	]
	best_score = -1
	best_candidate_summary: dict[str, int] | None = None

	score_script = """
	const selectors = arguments[0];
	let best = null;
	const seen = new Set();

	for (const selector of selectors) {
	  for (const matched of Array.from(document.querySelectorAll(selector))) {
	    const element = matched.closest('.root-1202, .ms-DetailsList, .ms-List, [role="grid"]') || matched;
	    if (seen.has(element)) continue;
	    seen.add(element);

	    const html = element.outerHTML || '';
	    if (!html) continue;

	    const rowCount = element.querySelectorAll('tr, [role="row"], .ms-DetailsRow').length;
	    const detailCount = element.querySelectorAll(
	      '.ms-DetailsRow-cell, .ms-DetailsRow-fields, .ms-List-cell, [role="gridcell"], td'
	    ).length;
	    const expandedCount = element.querySelectorAll('[aria-expanded="true"]').length;
	    const expandButtonCount = element.querySelectorAll(
	      '[data-automation-key="ListPage-Expand"] button, '
	      + 'button[title*="Expand or collapse"], '
	      + 'button[aria-label*="Expand or collapse"]'
	    ).length;
	    if (!expandButtonCount) continue;

	    const score = rowCount * 1000000 + expandButtonCount * 500000 + expandedCount * 100000 + detailCount * 1000 + html.length;

	    if (!best || score > best.score) {
	      best = {
	        html,
	        score,
	        row_count: rowCount,
	        detail_count: detailCount,
	        expanded_count: expandedCount,
	        expand_button_count: expandButtonCount,
	      };
	    }
	  }
	}

	return best;
	"""

	while time.time() < deadline and html_content is None:
		try:
			driver.switch_to.default_content()
			best_candidate = driver.execute_script(score_script, selectors)
			if best_candidate and int(best_candidate.get("score", -1)) > best_score:
				best_score = int(best_candidate["score"])
				html_content = str(best_candidate.get("html") or "") or None
				best_candidate_summary = {
					"expanded_count": int(best_candidate.get("expanded_count", 0)),
					"detail_count": int(best_candidate.get("detail_count", 0)),
					"row_count": int(best_candidate.get("row_count", 0)),
					"expand_button_count": int(best_candidate.get("expand_button_count", 0)),
				}

			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					driver.switch_to.default_content()
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					best_candidate = driver.execute_script(score_script, selectors)
					if best_candidate and int(best_candidate.get("score", -1)) > best_score:
						best_score = int(best_candidate["score"])
						html_content = str(best_candidate.get("html") or "") or None
						best_candidate_summary = {
							"expanded_count": int(best_candidate.get("expanded_count", 0)),
							"detail_count": int(best_candidate.get("detail_count", 0)),
							"row_count": int(best_candidate.get("row_count", 0)),
							"expand_button_count": int(best_candidate.get("expand_button_count", 0)),
						}
				except WebDriverException:
					continue
		except WebDriverException:
			pass

		if best_score < 0:
			time.sleep(0.25)

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	if html_content is None:
		html_content = (
			"<!doctype html><html><head><meta charset='utf-8'>"
			"<title>Purview Capture Fallback</title></head><body>"
			"<h1>ms-FocusZone capture fallback</h1>"
			"<p>The requested FocusZone element was not found before timeout."
			" Saved the current page source below for local review.</p><hr>"
			f"<pre>{driver.page_source}</pre></body></html>"
		)

	workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	output_dir = os.path.join(workspace_root, "automation")
	os.makedirs(output_dir, exist_ok=True)
	text_output_path = os.path.join(output_dir, "db_info.txt")
	headers, rows = extract_purview_rows_from_html(html_content)
	merged_headers, merged_rows, added_rows, existing_rows = merge_with_existing_text_rows(
		text_output_path,
		headers,
		rows,
	)
	write_purview_text_file(
		text_output_path,
		merged_headers,
		merged_rows,
	)
	print(
		"Purview data summary: "
		f"existing_rows={existing_rows}, extracted_rows={len(rows)}, "
		f"added_rows={added_rows}, final_rows={len(merged_rows)}"
	)
	if best_candidate_summary:
		print(
			"Selected Purview export container metrics: "
			f"expanded={best_candidate_summary['expanded_count']}, "
			f"expand_buttons={best_candidate_summary['expand_button_count']}, "
			f"details={best_candidate_summary['detail_count']}, "
			f"rows={best_candidate_summary['row_count']}"
		)

	manifest_path = os.path.join(output_dir, "latest_purview_export.json")
	manifest_payload = {
		"data": os.path.basename(text_output_path),
		"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
	}
	with open(manifest_path, "w", encoding="utf-8") as manifest_file:
		json.dump(manifest_payload, manifest_file, indent=2)
	print(f"Saved Purview data to: {text_output_path}")

	incident_rows_updated, incident_rows_processed = enrich_purview_text_file_with_incident_ids(
		driver,
		text_output_path,
		timeout,
		startswith=alert_name_prefix,
	)
	if incident_rows_processed:
		print(
			"Incident ID enrichment summary: "
			f"processed_rows={incident_rows_processed}, updated_rows={incident_rows_updated}"
		)
	
	# Create alert-based folders from the Purview data.
	if alert_name_prefix:
		print(f"Creating alert folders from Purview data with prefix {alert_name_prefix!r}...")
	else:
		print("Creating alert folders from Purview data...")
	create_alert_folders_from_data(text_output_path, startswith=alert_name_prefix)
	
	return text_output_path


def wait_for_root_1202_loaded(driver: WebDriver, timeout: int) -> None:
	deadline = time.time() + timeout
	selectors = [
		".root-1202",
		".ms-List-surface",
		"[data-automationid='DetailsRow']",
		".ms-DetailsList",
	]

	while time.time() < deadline:
		try:
			driver.switch_to.default_content()
			for selector in selectors:
				elements = driver.find_elements(By.CSS_SELECTOR, selector)
				for element in elements:
					try:
						if element.is_displayed() and element.get_attribute("innerHTML"):
							return
					except WebDriverException:
						continue

			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					driver.switch_to.default_content()
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					for selector in selectors:
						elements = driver.find_elements(By.CSS_SELECTOR, selector)
						for element in elements:
							if element.is_displayed() and element.get_attribute("innerHTML"):
								driver.switch_to.default_content()
								return
				except WebDriverException:
					continue
		except WebDriverException:
			pass

		time.sleep(0.25)

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	raise TimeoutException("root-1202 table did not load before download")


def wait_for_purview_grid_settled(driver: WebDriver, timeout: int, stable_cycles: int = 3) -> None:
	"""Wait until Purview details grid stops showing shimmer/loading placeholders."""

	script = """
	const roots = Array.from(document.querySelectorAll('.root-1202, .ms-DetailsList, .ms-List-surface, [role="grid"]'));
	if (!roots.length) {
	  return { found: false, row_count: 0, shimmer_count: 0, busy_count: 0 };
	}

	let rowCount = 0;
	let shimmerCount = 0;
	let busyCount = 0;

	for (const root of roots) {
	  rowCount += root.querySelectorAll('[data-automationid="DetailsRow"], .ms-DetailsRow, [role="row"]').length;
	  shimmerCount += root.querySelectorAll('.ms-Shimmer-container, .ms-Shimmer-shimmerWrapper').length;
	  busyCount += root.querySelectorAll('[aria-busy="true"], [data-is-scrollsyncloading="true"]').length;
	}

	return {
	  found: true,
	  row_count: rowCount,
	  shimmer_count: shimmerCount,
	  busy_count: busyCount,
	};
	"""

	deadline = time.time() + timeout
	stable_hits = 0
	relaxed_stable_hits = 0
	relaxed_required_hits = max(stable_cycles + 3, 6)
	last_relaxed_signature: tuple[int, int, int] | None = None
	last_observed = {"found": False, "row_count": 0, "shimmer_count": 0, "busy_count": 0}

	while time.time() < deadline:
		best = {"found": False, "row_count": 0, "shimmer_count": 999999, "busy_count": 999999}

		try:
			driver.switch_to.default_content()
			result = driver.execute_script(script) or {}
			if result.get("found") and int(result.get("row_count", 0)) >= best["row_count"]:
				best = {
					"found": True,
					"row_count": int(result.get("row_count", 0)),
					"shimmer_count": int(result.get("shimmer_count", 0)),
					"busy_count": int(result.get("busy_count", 0)),
				}
		except WebDriverException:
			pass

		try:
			frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
			for frame_index in range(len(frames)):
				try:
					driver.switch_to.default_content()
					frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
					if frame_index >= len(frames):
						continue
					driver.switch_to.frame(frames[frame_index])
					result = driver.execute_script(script) or {}
					if result.get("found") and int(result.get("row_count", 0)) >= best["row_count"]:
						best = {
							"found": True,
							"row_count": int(result.get("row_count", 0)),
							"shimmer_count": int(result.get("shimmer_count", 0)),
							"busy_count": int(result.get("busy_count", 0)),
						}
				except WebDriverException:
					continue
		except WebDriverException:
			pass

		if best["found"]:
			last_observed = {
				"found": True,
				"row_count": best["row_count"],
				"shimmer_count": best["shimmer_count"],
				"busy_count": best["busy_count"],
			}

		if best["found"] and best["row_count"] > 0:
			current_signature = (
				best["row_count"],
				best["shimmer_count"],
				best["busy_count"],
			)
			if current_signature == last_relaxed_signature:
				relaxed_stable_hits += 1
			else:
				last_relaxed_signature = current_signature
				relaxed_stable_hits = 1
		else:
			last_relaxed_signature = None
			relaxed_stable_hits = 0

		if best["found"] and best["row_count"] > 0 and best["shimmer_count"] == 0 and best["busy_count"] == 0:
			stable_hits += 1
			if stable_hits >= stable_cycles:
				print(f"Purview grid settled: rows={best['row_count']}, shimmer=0, busy=0")
				try:
					driver.switch_to.default_content()
				except WebDriverException:
					pass
				return
		else:
			stable_hits = 0

		if relaxed_stable_hits >= relaxed_required_hits:
			print(
				"Purview grid considered settled with persistent loading markers: "
				f"rows={best['row_count']}, shimmer={best['shimmer_count']}, busy={best['busy_count']}"
			)
			try:
				driver.switch_to.default_content()
			except WebDriverException:
				pass
			return

		time.sleep(0.5)

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	raise TimeoutException(
		"Purview grid did not settle before download "
		f"(found={last_observed['found']}, rows={last_observed['row_count']}, "
		f"shimmer={last_observed['shimmer_count']}, busy={last_observed['busy_count']})"
	)


def get_root_1202_metrics(driver: WebDriver) -> dict[str, int]:
	metrics = {
		"html_length": 0,
		"row_count": 0,
		"expanded_count": 0,
		"detail_count": 0,
	}

	script = """
	const roots = Array.from(document.querySelectorAll('.root-1202, .ms-DetailsList, [role="grid"]'));
	if (!roots.length) {
	  return { html_length: 0, row_count: 0, expanded_count: 0, detail_count: 0 };
	}

	let htmlLength = 0;
	let rowCount = 0;
	let expandedCount = 0;
	let detailCount = 0;

	for (const root of roots) {
	  htmlLength = Math.max(htmlLength, (root.outerHTML || '').length);
	  rowCount += root.querySelectorAll('tr, [role="row"], .ms-DetailsRow').length;
	  expandedCount += root.querySelectorAll('[aria-expanded="true"]').length;
	  detailCount += root.querySelectorAll(
	    '.ms-DetailsRow-cell, .ms-DetailsRow-fields, .ms-List-cell, [role="gridcell"], td'
	  ).length;
	}

	return {
	  html_length: htmlLength,
	  row_count: rowCount,
	  expanded_count: expandedCount,
	  detail_count: detailCount,
	};
	"""

	def merge_metrics(target: dict[str, int], candidate: dict[str, int] | None) -> None:
		if not candidate:
			return
		for key in target:
			target[key] = max(target[key], int(candidate.get(key, 0)))

	try:
		driver.switch_to.default_content()
		merge_metrics(metrics, driver.execute_script(script))
	except WebDriverException:
		pass

	try:
		frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
		for frame_index in range(len(frames)):
			try:
				driver.switch_to.default_content()
				frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
				if frame_index >= len(frames):
					continue
				driver.switch_to.frame(frames[frame_index])
				merge_metrics(metrics, driver.execute_script(script))
			except WebDriverException:
				continue
	except WebDriverException:
		pass

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	return metrics


def expand_root_1202_sub_content(driver: WebDriver, timeout: int) -> int:
	"""Expand visible collapsed rows once so child/sub-content is included in captured HTML."""

	script = """
	function visible(el) {
	  return !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
	}

	function asClickable(el) {
	  if (!el) return null;
	  if (el.tagName && el.tagName.toLowerCase() === 'button') return el;
	  if (el.getAttribute && el.getAttribute('role') === 'button') return el;
	  return el.closest ? (el.closest('button, [role="button"]') || el) : el;
	}

	const roots = Array.from(document.querySelectorAll('.root-1202, .ms-DetailsList, .ms-List-surface, [role="grid"]'));
	if (!roots.length) {
	  return { found: false, clicked: 0 };
	}

	const candidates = [];
	for (const root of roots) {
	  for (const node of Array.from(root.querySelectorAll(
	    '[data-automation-key="ListPage-Expand"] button[aria-expanded="false"], '
	    + 'button[title*="Expand or collapse"][aria-expanded="false"], '
	    + 'button[aria-label*="Expand or collapse"][aria-expanded="false"]'
	  ))) {
	    const clickable = asClickable(node);
	    if (clickable) candidates.push(clickable);
	  }
	}

	const unique = Array.from(new Set(candidates));
	let clicked = 0;

	for (const el of unique) {
	  if (!visible(el)) continue;

	  const expanded = String(el.getAttribute?.('aria-expanded') || '').toLowerCase();
	  const hasChevronRight = !!el.querySelector?.('[data-icon-name="ChevronRightMed"], [data-icon-name="ChevronRight"]');
	  if (!(expanded === 'false' || hasChevronRight)) continue;

	  try {
	    el.click();
	    clicked += 1;
	  } catch (_) {
	    try {
	      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
	      clicked += 1;
	    } catch (_) {}
	  }
	}

	return { found: unique.length > 0, clicked };
	"""

	total_clicked = 0
	found_targets = False

	try:
		driver.switch_to.default_content()
		result = driver.execute_script(script) or {}
		found_targets = found_targets or bool(result.get("found"))
		total_clicked += int(result.get("clicked", 0))
	except WebDriverException:
		pass

	try:
		frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
		for frame_index in range(len(frames)):
			try:
				driver.switch_to.default_content()
				frames = driver.find_elements(By.CSS_SELECTOR, "iframe,frame")
				if frame_index >= len(frames):
					continue
				driver.switch_to.frame(frames[frame_index])
				result = driver.execute_script(script) or {}
				found_targets = found_targets or bool(result.get("found"))
				total_clicked += int(result.get("clicked", 0))
			except WebDriverException:
				continue
	except WebDriverException:
		pass

	try:
		driver.switch_to.default_content()
	except WebDriverException:
		pass

	if total_clicked > 0:
		print(f"Expanded {total_clicked} collapsed row toggle(s) before download.")
	elif found_targets:
		print("No collapsed row toggles found to expand before download.")

	return total_clicked


def wait_for_root_1202_sub_content_loaded(
	driver: WebDriver,
	timeout: int,
	baseline_metrics: dict[str, int],
	expected_expansions: int,
) -> None:
	if expected_expansions <= 0:
		return

	deadline = time.time() + timeout
	stable_hits = 0

	while time.time() < deadline:
		metrics = get_root_1202_metrics(driver)
		has_growth = (
			metrics["html_length"] > baseline_metrics["html_length"]
			or metrics["row_count"] > baseline_metrics["row_count"]
			or metrics["detail_count"] > baseline_metrics["detail_count"]
		)
		has_expanded_state = metrics["expanded_count"] >= max(
			baseline_metrics["expanded_count"],
			expected_expansions,
		)

		if has_growth or has_expanded_state:
			stable_hits += 1
			if stable_hits >= 2:
				print("Expanded root-1202 sub-content finished loading before download.")
				return
		else:
			stable_hits = 0

		time.sleep(0.4)

	raise TimeoutException("Expanded root-1202 sub-content did not finish loading before download")


def main() -> int:
	global _shutdown_requested
	_shutdown_requested = False
	install_shutdown_signal_handlers()
	args = parse_args()

	if not args.attach_debugger_address and not os.path.isdir(args.edge_user_data_dir):
		print(
			f"Edge user data directory not found: {args.edge_user_data_dir}",
			file=sys.stderr,
		)
		return 2

	# Some page class names/IDs can be dynamic; fallback locators target stable attributes.
	page_actions = [
		PageAction(
			url=INCIDENTS_URL,
			primary_locator=(By.CSS_SELECTOR, "input#SearchBox287.ms-SearchBox-field.field-531"),
			fallback_locators=(
				(By.CSS_SELECTOR, "input[id^='SearchBox'][class*='ms-SearchBox-field']"),
				(By.CSS_SELECTOR, "input.ms-SearchBox-field"),
				(By.CSS_SELECTOR, "input[aria-label*='Search']"),
				(By.CSS_SELECTOR, "input[placeholder*='Search']"),
			),
		),
		PageAction(
			url="https://purview.microsoft.com/datalossprevention/alertspage",
			primary_locator=(By.ID, "ms-searchux-input-0"),
			fallback_locators=(
				(By.CSS_SELECTOR, "input[id^='ms-searchux-input-']"),
				(By.CSS_SELECTOR, "input[type='search']"),
			),
		),
		PageAction(
			url="https://outlook.office.com/",
			primary_locator=OUTLOOK_SEARCH_LOCATORS[0],
			fallback_locators=OUTLOOK_SEARCH_LOCATORS[1:],
		),
		PageAction(
			url="https://vistra.service-now.com/nav_to.do?uri=%2Fincident.do%3Fsys_id%3D-1%26sysparm_query%3Dactive%3Dtrue%26sysparm_stack%3Dincident_list.do%3Fsysparm_query%3Dactive%3Dtrue",
		),
		PageAction(
			url=ODIN_URL,
		),
	]

	driver: WebDriver | None = None
	temp_user_data_dir: str | None = None
	monitor_started = False
	purview_export_succeeded = False

	try:
		used_profile_reuse = True
		selected_profile_directory = ""
		if args.attach_debugger_address:
			print(f"Attaching to existing Edge debugger at: {args.attach_debugger_address}")
			driver = build_driver_attached(args.attach_debugger_address)
		else:
			try:
				selected_profile_directory = resolve_profile_directory(
					args.edge_user_data_dir,
					args.edge_profile_directory,
				)
				temp_user_data_dir = copy_profile_for_automation(
					args.edge_user_data_dir,
					selected_profile_directory,
				)
				print(
					"Launching isolated Edge automation window using copied profile: "
					f"{selected_profile_directory}"
				)
				driver = build_driver(temp_user_data_dir, selected_profile_directory)
			except WebDriverException as exc:
				if is_profile_lock_error(exc) and not args.no_profile_fallback:
					print(
						"Profile appears locked by another Edge instance. "
						"Retrying without profile reuse...",
						file=sys.stderr,
					)
					driver = build_driver_no_profile()
					used_profile_reuse = False
				else:
					raise

		ensure_browser_window(driver)

		tab_handles: dict[str, str] = {}
		failures: list[str] = []
		use_clean_startup_tabs = not args.attach_debugger_address

		# First pass: keep the isolated browser window to just the requested tabs.
		if page_actions and use_clean_startup_tabs:
			first_action = page_actions[0]
			remaining_actions = page_actions[1:]

			try:
				collapse_to_single_browser_tab(driver)
				handle = load_url_in_current_tab(driver, first_action.url)
				print(f"Loaded startup tab: {first_action.url}")
				tab_handles[first_action.url] = handle
			except WebDriverException as exc:
				failures.append(first_action.url)
				print(
					f"Could not load startup tab for: {first_action.url}: {exc}",
					file=sys.stderr,
				)

			for action in remaining_actions:
				try:
					handle = open_url_in_new_tab(driver, action.url)
					print(f"Opened tab: {action.url}")
					tab_handles[action.url] = handle
				except TimeoutException:
					failures.append(action.url)
					print(f"Could not open new tab for: {action.url}", file=sys.stderr)
				except WebDriverException as exc:
					failures.append(action.url)
					print(
						f"Could not open new tab for: {action.url}: {exc}",
						file=sys.stderr,
					)
		else:
			for action in page_actions:
				try:
					handle = find_existing_tab_for_url(driver, action.url)
					if handle:
						print(f"Reused tab: {action.url}")
					else:
						handle = open_url_in_new_tab(driver, action.url)
						print(f"Opened tab: {action.url}")
					tab_handles[action.url] = handle
				except TimeoutException:
					failures.append(action.url)
					print(f"Could not open new tab for: {action.url}", file=sys.stderr)

		# Second pass: switch to each tab and perform the page-specific search action.
		for action in page_actions:
			handle = tab_handles.get(action.url)
			if not handle:
				continue

			try:
				driver.switch_to.window(handle)
				print(f"Switched to tab: {action.url}")

				if "vistra.service-now.com" in action.url:
					wait_for_servicenow_with_browser_refresh(
						driver,
						args.timeout,
						portal_label="ServiceNow",
					)
				elif "odin.landpower.net" in action.url:
					wait_for_servicenow_with_browser_refresh(
						driver,
						args.timeout,
						portal_label="ODIN",
					)
				else:
					wait_for_page_load(driver, args.timeout)

				if action.primary_locator is None:
					print(f"Loaded page: {action.url}")
					continue

				target = find_first_available_search_input(
					driver,
					args.timeout,
					[action.primary_locator, *action.fallback_locators],
				)
				print(f"Found search input on: {action.url}")
			except Exception as exc:  # noqa: BLE001
				failures.append(action.url)
				print(
					f"Failed page action on {action.url}: {exc}",
					file=sys.stderr,
				)

		purview_url = "https://purview.microsoft.com/datalossprevention/alertspage"
		outlook_url = "https://outlook.office.com/"
		odin_handle = tab_handles.get(ODIN_URL)
		purview_handle = tab_handles.get(purview_url)
		if purview_handle:
			alert_name_prefix = str(args.alert_name_prefix or "").strip() or "DLP"
			purview_error: Exception | None = None
			try:
				driver.switch_to.window(purview_handle)
				wait_for_page_load(driver, args.timeout)
				run_purview_post_load_actions(driver, args.timeout)
			except Exception as exc:  # noqa: BLE001
				purview_error = exc
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed Purview post-load actions: {exc}",
					file=sys.stderr,
				)

			try:
				wait_for_root_1202_loaded(driver, args.timeout)
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed waiting for root-1202 table: {exc}",
					file=sys.stderr,
				)

			try:
				wait_for_purview_grid_settled(driver, args.timeout)
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed waiting for Purview grid settled state before expansion: {exc}",
					file=sys.stderr,
				)

			baseline_root_1202_metrics = get_root_1202_metrics(driver)
			expanded_root_1202_count = 0

			try:
				expanded_root_1202_count = expand_root_1202_sub_content(driver, args.timeout)
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed expanding root-1202 sub-content: {exc}",
					file=sys.stderr,
				)

			try:
				wait_for_root_1202_sub_content_loaded(
					driver,
					args.timeout,
					baseline_root_1202_metrics,
					expanded_root_1202_count,
				)
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed waiting for expanded root-1202 sub-content: {exc}",
					file=sys.stderr,
				)

			try:
				wait_for_purview_grid_settled(driver, args.timeout)
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed waiting for Purview grid settled state: {exc}",
					file=sys.stderr,
				)

			excel_output_path = ""
			try:
				excel_output_path = download_ms_list_surface_html(
					driver,
					args.timeout,
					alert_name_prefix=alert_name_prefix,
				)
				if excel_output_path:
					purview_export_succeeded = True
			except Exception as exc:  # noqa: BLE001
				if purview_url not in failures:
					failures.append(purview_url)
				print(
					f"Failed Purview HTML download: {exc}",
					file=sys.stderr,
				)

			if excel_output_path:
				try:
					clicked_alerts, missing_alerts = click_purview_alert_buttons_from_data(
						driver,
						excel_output_path,
						args.timeout,
						startswith=alert_name_prefix,
						outlook_handle=tab_handles.get(outlook_url),
						odin_handle=odin_handle,
					)
					if clicked_alerts:
						print(
							"Clicked Purview alert entries from db_info.txt: "
							+ ", ".join(clicked_alerts)
						)
					elif not missing_alerts:
						if alert_name_prefix:
							print(
								f"No alert entries with prefix {alert_name_prefix!r} were available to click from db_info.txt."
							)
						else:
							print("No alert entries were available to click from db_info.txt.")
				except Exception as exc:  # noqa: BLE001
					if purview_url not in failures:
						failures.append(purview_url)
					print(
						f"Failed clicking Purview alert entries from db_info.txt: {exc}",
						file=sys.stderr,
					)

				try:
					captured_odin_count, processed_odin_count = capture_odin_responses_from_data(
						driver,
						excel_output_path,
						args.timeout,
						odin_handle=odin_handle,
						startswith=alert_name_prefix,
					)
					if captured_odin_count:
						print(
							"Captured ODIN responses from automation flow: "
							f"captured={captured_odin_count}, processed={processed_odin_count}"
						)
					elif processed_odin_count:
						print(
							"ODIN response capture did not return any saved summaries for the processed alerts.",
							file=sys.stderr,
						)
				except Exception as exc:  # noqa: BLE001
					print(
						f"Failed ODIN response capture from db_info.txt: {exc}",
						file=sys.stderr,
					)

			if excel_output_path and args.monitor:
				try:
					start_purview_table_monitor(
						excel_output_path,
						check_interval=10,
						refresh_callback=lambda: refresh_purview_excel_from_driver(
							driver,
							purview_handle,
							args.timeout,
							alert_name_prefix=alert_name_prefix,
						),
						alert_name_prefix=alert_name_prefix,
					)
					monitor_started = True
				except Exception as exc:  # noqa: BLE001
					print(
						f"Failed to start Purview monitor: {exc}",
						file=sys.stderr,
					)

		# Leave focus on the first requested page at the end for easier validation.
		first_requested_url = page_actions[0].url
		first_handle = tab_handles.get(first_requested_url)
		if first_handle:
			driver.switch_to.window(first_handle)

		critical_failure_urls = {purview_url}
		critical_failures = [url for url in failures if url in critical_failure_urls]

		if failures:
			print("Completed with page-level failures:", file=sys.stderr)
			for failed_url in failures:
				print(f"- {failed_url}", file=sys.stderr)
			if not critical_failures and purview_export_succeeded:
				print(
					"Continuing with success exit code: only non-critical pages failed "
					"and Purview export succeeded.",
					file=sys.stderr,
				)
		else:
			print("Completed successfully for all pages.")

		if not used_profile_reuse:
			print(
				"Note: Browser ran without your saved profile because profile was locked. "
				"Open tabs will likely require login before search selectors appear.",
				file=sys.stderr,
			)

		if args.keep_open:
			input("All actions completed. Press Enter to close the browser...")

		if critical_failures:
			return 1
		if purview_export_succeeded:
			return 0
		return 0 if not failures else 1

	except TimeoutException as exc:
		print(f"Timeout while loading page or finding element: {exc}", file=sys.stderr)
		return 1
	except KeyboardInterrupt:
		request_shutdown("Automation shutdown requested.", announce=False)
		print("Automation shutdown requested.", file=sys.stderr)
		return 130
	except WebDriverException as exc:
		print(f"WebDriver error: {exc}", file=sys.stderr)
		return 1
	except Exception as exc:  # noqa: BLE001
		print(f"Unexpected error: {exc}", file=sys.stderr)
		return 1
	finally:
		if driver is not None:
			if monitor_started:
				if not shutdown_requested():
					print("Purview monitor running. Press Ctrl+C to stop.")
					try:
						while not shutdown_requested():
							time.sleep(1)
					except KeyboardInterrupt:
						request_shutdown("Automation shutdown requested.", announce=False)
				print("\nStopping monitor...")
				stop_purview_table_monitor()
			else:
				if not args.keep_open and not shutdown_requested():
					print("Waiting 60 seconds before closing browser for validation...")
					wait_for_shutdown_or_timeout(60)
				print("Closing browser.")
			driver.quit()
		if temp_user_data_dir and os.path.isdir(temp_user_data_dir):
			shutil.rmtree(temp_user_data_dir, ignore_errors=True)


if __name__ == "__main__":
	raise SystemExit(main())
