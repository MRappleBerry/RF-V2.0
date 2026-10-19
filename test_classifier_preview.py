#!/usr/bin/env python3
"""Focused regression tests for classifier workbook-row previews."""

import os
import sys
import tempfile
import time
from xml.etree import ElementTree as ET

import openpyxl


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "function"))

import tools


def test_workbook_summary_chip_label() -> None:
	assert tools._format_workbook_summary_chip_label("Sheet1!D524") == "Sheet1!"
	assert tools._format_workbook_summary_chip_label("Rows!A12") == "Rows"
	assert tools._format_workbook_summary_chip_label("Paragraphs!A4") == "Paragraphs"


def test_classifier_preview_defaults_open() -> None:
	original_build_preview = tools._build_workbook_row_preview_html
	try:
		tools._build_workbook_row_preview_html = lambda alert_name, location_reference: (
			'<div class="artifact-data-found-at-preview-layout">'
			'<div class="artifact-row-preview-highlights"></div>'
			'</div>'
		)

		target_cell = ET.Element("td")
		tools._set_data_found_at_cell_content(
			target_cell,
			"Alert Name",
			"Master Account List_Dunstable APR 2026 (2).xlsx - Sheet1!D524",
		)

		details = target_cell.find(".//details")
		assert details is not None
		assert details.get("open") == "open"

		summary_chip = target_cell.find(".//span[@class='artifact-row-preview-chip']")
		assert summary_chip is not None
		assert summary_chip.text == "Sheet1!"

		badge_text = [
			element.text
			for element in target_cell.iter("span")
			if "artifact-row-preview-badge" in str(element.get("class") or "")
		]
		assert "Sheet1!D524" in badge_text
	finally:
		tools._build_workbook_row_preview_html = original_build_preview


def test_classifier_capture_scans_all_alert_folder_sources() -> None:
	alert_name = "DLP policy match for document 'Master Account List_Dunstable APR 2026.xlsx' in OneDrive"
	html_fragment = """
	<table>
	  <thead>
	    <tr>
	      <th>Sensitive information type</th>
	      <th>Matched sensitive content</th>
	      <th>Surrounding characters</th>
	    </tr>
	  </thead>
	  <tbody>
	    <tr>
	      <td>U.S Bank Account Number - JP Morgan Chase</td>
	      <td>3883395027</td>
	      <td>Account Accepted 3883395027 WCMASS 190 THORNDIKE ST</td>
	    </tr>
	  </tbody>
	</table>
	""".strip()

	with tempfile.TemporaryDirectory() as tmpdir:
		artifact_folder = os.path.join(tmpdir, tools.sanitize_alert_name_for_path(alert_name))
		os.makedirs(artifact_folder, exist_ok=True)

		matching_source_path = os.path.join(artifact_folder, "Master Account List_Dunstable APR 2026 (2).xlsx")
		matching_workbook = openpyxl.Workbook()
		matching_sheet = matching_workbook.active
		matching_sheet.title = "Sheet1"
		matching_sheet.append(["Customer Name", "Account Number", "Status", "Service Address"])
		matching_sheet.append(["MARYANN SIMMONS", "3883395027", "Account Accepted", "190 THORNDIKE ST"])
		matching_workbook.save(matching_source_path)

		newer_source_path = os.path.join(artifact_folder, "ZZZ Newer Source.xlsx")
		newer_workbook = openpyxl.Workbook()
		newer_sheet = newer_workbook.active
		newer_sheet.title = "Sheet1"
		newer_sheet.append(["Customer Name", "Account Number", "Status", "Service Address"])
		newer_sheet.append(["UNRELATED CUSTOMER", "0000000000", "Pending", "1 OTHER STREET"])
		newer_workbook.save(newer_source_path)

		current_time = time.time()
		os.utime(matching_source_path, (current_time - 120, current_time - 120))
		os.utime(newer_source_path, (current_time, current_time))

		original_get_folder = tools.get_alert_artifact_folder_path
		try:
			tools.ALERT_WORKBOOK_LOOKUP_CACHE.clear()
			tools.get_alert_artifact_folder_path = lambda current_alert_name: (
				artifact_folder if current_alert_name == alert_name else original_get_folder(current_alert_name)
			)

			transformed_html = tools._add_classifier_data_found_at_column(alert_name, html_fragment)

			assert "artifact-row-preview-card" in transformed_html
			assert "Matched workbook row" in transformed_html
			assert "Master Account List_Dunstable APR 2026 (2).xlsx" in transformed_html
			assert "Sheet1!B2" in transformed_html
			assert "MARYANN SIMMONS" in transformed_html
			assert "3883395027" in transformed_html
		finally:
			tools.get_alert_artifact_folder_path = original_get_folder
			tools.ALERT_WORKBOOK_LOOKUP_CACHE.clear()


if __name__ == "__main__":
	test_workbook_summary_chip_label()
	test_classifier_preview_defaults_open()
	test_classifier_capture_scans_all_alert_folder_sources()
	print("Classifier preview tests passed.")