from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

import tools as t
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


INCIDENTS_URL = "https://security.microsoft.com/incidents?"
INCIDENT_SEARCH_INPUT_SELECTORS = (
	"input#SearchBox1470",
	"input.ms-SearchBox-field.field-557",
	"input[id^='SearchBox'].ms-SearchBox-field[class*='field-']",
	"input.ms-SearchBox-field[class*='field-']",
	"input.ms-SearchBox-field",
)
INCIDENT_SEARCH_LOCATORS = (
	(By.ID, "SearchBox1470"),
	(By.CSS_SELECTOR, "input#SearchBox1470"),
	(By.CSS_SELECTOR, "input.ms-SearchBox-field.field-557"),
	(By.CSS_SELECTOR, "input[id^='SearchBox'].ms-SearchBox-field[class*='field-']"),
	(By.CSS_SELECTOR, "input.ms-SearchBox-field[class*='field-']"),
	*tuple(t.INCIDENTS_SEARCH_LOCATORS),
)
RESULT_ROW_SELECTORS = (
	"[data-automationid='DetailsRow']",
	".ms-DetailsRow",
	"[role='row']",
)
STATUS_DROPDOWN_TITLE_SELECTORS = (
	".ms-Dropdown-title.ms-Dropdown-titleIsPlaceHolder.title-1851",
	".ms-Dropdown-title.title-1851",
)
CLASSIFICATION_DROPDOWN_TITLE_SELECTORS = (
	"div.ms-Dropdown[aria-label='Classification']",
	"[role='combobox'][aria-label='Classification']",
	".ms-Dropdown[aria-label='Classification']",
	".ms-Dropdown-title.title-1902",
	".ms-Dropdown-title.title-1918",
)
COMMENT_FIELD_SELECTORS = (
	"textarea[placeholder='Explain why you resolved this incident']",
	".ms-TextField-field[placeholder='Explain why you resolved this incident']",
	"textarea.ms-TextField-field.field-1924",
	"input.ms-TextField-field.field-1924",
	".ms-TextField-field.field-1924",
	"textarea.ms-TextField-field[class*='field-1924']",
	"input.ms-TextField-field[class*='field-1924']",
)
DROPDOWN_OPTION_SELECTORS = (
	"[role='listbox'] [role='option']",
	"[role='listbox'] [role='menuitem']",
	".ms-Callout [role='option']",
	".ms-Callout [role='menuitem']",
	".ms-Dropdown-item",
	".ms-ContextualMenu-item",
	"span.ms-Dropdown-optionText",
	"span.ms-ContextualMenu-itemText",
	"[role='option']",
	"[role='menuitem']",
	"button",
	"[role='button']",
)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Open the Microsoft Defender incidents page and launch Manage Incident for a specific incident ID."
	)
	parser.add_argument("--incident-id", required=True, help="Incident ID taken from the Resolve Incident modal.")
	parser.add_argument("--profile-directory", default="", help="Optional Edge profile directory name.")
	parser.add_argument("--timeout", type=int, default=45, help="Maximum wait time for each page operation in seconds.")
	parser.add_argument("--status", default="Resolved", help="Incident status to apply in the Manage Incident form.")
	parser.add_argument("--classification", default="", help="Incident classification to apply in the Manage Incident form.")
	parser.add_argument("--comment", default="", help="Comment text to apply in the Manage Incident form.")
	parser.add_argument(
		"--validation-seconds",
		type=int,
		default=60,
		help="Seconds to keep the Manage Incident result visible before closing the browser.",
	)
	return parser.parse_args()


def _normalize_text(value: str) -> str:
	return " ".join(str(value or "").split()).strip()


def _normalize_incident_id(value: str) -> str:
	return _normalize_text(value)


def _classification_option_candidates(value: str) -> tuple[str, ...]:
	normalized_value = _normalize_text(value)
	if not normalized_value:
		return ()
	candidates = [normalized_value]
	if ":" in normalized_value:
		classification_value, suffix = normalized_value.split(":", 1)
		classification_value = _normalize_text(classification_value)
		suffix = _normalize_text(suffix)
		if suffix and suffix not in candidates:
			candidates.append(suffix)
		combined_label = _normalize_text(f"{classification_value} - {suffix}")
		if combined_label and combined_label not in candidates:
			candidates.append(combined_label)
	return tuple(candidates)


def _build_driver(profile_directory: str) -> tuple[t.WebDriver, str]:
	local_app_data = os.environ.get("LOCALAPPDATA", "")
	if not local_app_data:
		raise EnvironmentError("LOCALAPPDATA is not available; cannot locate the Edge profile.")

	edge_user_data_dir = os.path.join(local_app_data, "Microsoft", "Edge", "User Data")
	if not os.path.isdir(edge_user_data_dir):
		raise FileNotFoundError(f"Edge user data directory not found: {edge_user_data_dir}")

	resolved_profile = t.resolve_profile_directory(edge_user_data_dir, profile_directory)
	temp_user_data_dir = t.copy_profile_for_automation(edge_user_data_dir, resolved_profile)
	try:
		driver = t.build_driver(temp_user_data_dir, resolved_profile)
	except Exception:
		shutil.rmtree(temp_user_data_dir, ignore_errors=True)
		raise
	return driver, temp_user_data_dir


def _find_incident_search_input(driver: t.WebDriver, timeout: int) -> WebElement:
	def _find_exact_input(current_driver: t.WebDriver) -> WebElement | bool:
		try:
			element = current_driver.execute_script(
				"""
				const selectors = arguments[0] || [];

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

				for (const selector of selectors) {
				  for (const candidate of Array.from(document.querySelectorAll(selector))) {
				    if (visible(candidate) && !candidate.disabled) {
				      return candidate;
				    }
				  }
				}

				return null;
				""",
				list(INCIDENT_SEARCH_INPUT_SELECTORS),
			)
		except WebDriverException:
			element = None
		if element is not None:
			try:
				if element.is_displayed() and element.is_enabled():
					return element
			except WebDriverException:
				pass
		try:
			element = current_driver.find_element(By.ID, "SearchBox1470")
		except WebDriverException:
			return False
		try:
			if element.is_displayed() and element.is_enabled():
				return element
		except WebDriverException:
			return False
		return False

	try:
		return WebDriverWait(driver, max(5, timeout)).until(_find_exact_input)
	except TimeoutException:
		return t.find_first_available_search_input(
			driver,
			max(8, timeout),
			list(INCIDENT_SEARCH_LOCATORS),
		)


def _set_incident_search_value(driver: t.WebDriver, search_input: WebElement, incident_id: str) -> None:
	driver.execute_script(
		"""
		const input = arguments[0];
		const nextValue = String(arguments[1] || '');
		if (!input) {
		  return '';
		}

		function setNativeValue(element, value) {
		  const prototype = Object.getPrototypeOf(element);
		  const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value')
		    || Object.getOwnPropertyDescriptor(window.HTMLInputElement?.prototype || {}, 'value');
		  if (descriptor && typeof descriptor.set === 'function') {
		    descriptor.set.call(element, value);
		    return;
		  }
		  element.value = value;
		}

		try {
		  input.scrollIntoView({ block: 'center', inline: 'nearest' });
		} catch (_) {}
		try {
		  input.focus();
		} catch (_) {}

		setNativeValue(input, '');
		input.dispatchEvent(new Event('input', { bubbles: true }));
		setNativeValue(input, nextValue);
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new Event('change', { bubbles: true }));
		input.dispatchEvent(new Event('search', { bubbles: true }));

		return String(input.value || '');
		""",
		search_input,
		incident_id,
	)


def _submit_search_without_keyboard(driver: t.WebDriver, search_input: WebElement) -> bool:
	try:
		return bool(
			driver.execute_script(
				"""
				const input = arguments[0];
				if (!input) {
				  return false;
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

				function clickCandidate(node) {
				  if (!(node instanceof Element) || !visible(node)) {
				    return false;
				  }
				  const button = node.tagName.toLowerCase() === 'button'
				    ? node
				    : (node.closest ? node.closest('button, [role="button"]') : null);
				  const target = button || node;
				  if (target.hasAttribute('disabled') || target.getAttribute('aria-disabled') === 'true') {
				    return false;
				  }
				  target.click();
				  return true;
				}

				const selectors = [
				  'button[aria-label*="Search"]',
				  'button[title*="Search"]',
				  'button[data-icon-name="Search"]',
				  '[role="button"][aria-label*="Search"]',
				  '.ms-SearchBox button',
				  '.ms-SearchBox [role="button"]',
				  '.ms-SearchBox-icon',
				  '.ms-SearchBox-searchIconContainer'
				];

				input.dispatchEvent(new Event('input', { bubbles: true }));
				input.dispatchEvent(new Event('change', { bubbles: true }));
				input.dispatchEvent(new Event('search', { bubbles: true }));

				const searchRoots = [
				  input.closest ? input.closest('.ms-SearchBox') : null,
				  input.closest ? input.closest('[role="search"]') : null,
				  input.closest ? input.closest('form') : null,
				  input.parentElement,
				  input.getRootNode ? input.getRootNode() : null,
				  document,
				];

				for (const root of searchRoots) {
				  if (!root || typeof root.querySelector !== 'function') {
				    continue;
				  }
				  for (const selector of selectors) {
				    const candidate = root.querySelector(selector);
				    if (clickCandidate(candidate)) {
				      return true;
				    }
				  }
				}

				const form = input.closest ? input.closest('form') : null;
				if (form) {
				  if (typeof form.requestSubmit === 'function') {
				    form.requestSubmit();
				    return true;
				  }
				  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
				  return true;
				}

				input.blur();
				return true;
				""",
				search_input,
			)
		)
	except WebDriverException:
		return False


def _find_matching_result_row(driver: t.WebDriver, incident_id: str) -> WebElement | None:
	normalized_incident_id = _normalize_incident_id(incident_id).lower()
	for selector in RESULT_ROW_SELECTORS:
		try:
			rows = driver.find_elements(By.CSS_SELECTOR, selector)
		except WebDriverException:
			continue

		for row in rows:
			try:
				if not row.is_displayed():
					continue
				row_text = _normalize_incident_id(row.text).lower()
			except WebDriverException:
				continue
			if not row_text or normalized_incident_id not in row_text:
				continue
			return row
	return None


def _click_element(driver: t.WebDriver, element: WebElement) -> None:
	try:
		driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});", element)
	except WebDriverException:
		pass

	try:
		element.click()
	except (ElementClickInterceptedException, WebDriverException):
		driver.execute_script("arguments[0].click();", element)


def _find_manage_incident_button(driver: t.WebDriver, timeout: int) -> WebElement:
	def _locator(current_driver: t.WebDriver) -> WebElement | bool:
		try:
			candidate = current_driver.execute_script(
				r"""
				function normalize(value) {
				  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
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

				function isEnabled(node) {
				  if (!(node instanceof Element)) {
				    return false;
				  }
				  return !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true';
				}

				function clickable(node) {
				  if (!(node instanceof Element)) {
				    return null;
				  }
				  return (
				    node.closest?.('button, [role="button"], [role="menuitem"], .ms-Button, [data-is-focusable="true"]')
				    || node.querySelector?.('button, [role="button"], [role="menuitem"], .ms-Button, [data-is-focusable="true"]')
				    || node
				  );
				}

				const selectors = [
				  'span.ms-Button-label.label-849',
				  '.ms-Button-label',
				  'button',
				  '[role="button"]',
				  '[role="menuitem"]',
				  '.ms-Button',
				  '[data-is-focusable="true"]',
				];

				for (const selector of selectors) {
				  for (const node of Array.from(document.querySelectorAll(selector))) {
				    if (!visible(node)) {
				      continue;
				    }
				    const nodeText = normalize(node.innerText || node.textContent || node.getAttribute?.('aria-label') || node.getAttribute?.('title') || '');
				    if (!nodeText.includes('manage incident')) {
				      continue;
				    }
				    const target = clickable(node);
				    if (visible(target) && isEnabled(target)) {
				      return target;
				    }
				  }
				}

				return null;
				"""
			)
		except WebDriverException:
			candidate = None
		if candidate is not None:
			try:
				if candidate.is_displayed() and candidate.is_enabled():
					return candidate
			except WebDriverException:
				pass

		candidates: list[WebElement] = []
		for by, value in (
			(By.XPATH, "//button[contains(normalize-space(.), 'Manage Incident')]"),
			(By.XPATH, "//*[@role='button' and contains(normalize-space(.), 'Manage Incident')]"),
			(By.XPATH, "//*[@role='menuitem' and contains(normalize-space(.), 'Manage Incident')]"),
			(By.XPATH, "//span[contains(@class, 'ms-Button-label') and contains(normalize-space(.), 'Manage Incident')]/ancestor::*[self::button or @role='button' or @role='menuitem' or contains(@class, 'ms-Button')][1]"),
			(By.CSS_SELECTOR, "span.ms-Button-label.label-849"),
		):
			try:
				elements = current_driver.find_elements(by, value)
			except WebDriverException:
				continue
			for element in elements:
				try:
					candidate = element
					if by == By.CSS_SELECTOR and element.tag_name.lower() not in {"button", "div"}:
						candidate = element.find_element(
							By.XPATH,
							"./ancestor::*[self::button or @role='button' or @role='menuitem' or contains(@class, 'ms-Button')][1]",
						)
					text_value = " ".join(str(candidate.text or "").split()).lower()
					if candidate.is_displayed() and candidate.is_enabled() and "manage incident" in text_value:
						candidates.append(candidate)
				except WebDriverException:
					continue
		if candidates:
			return candidates[0]
		return False

	return WebDriverWait(driver, timeout).until(_locator)


def _find_dropdown_trigger(
	driver: t.WebDriver,
	field_label: str,
	selectors: tuple[str, ...],
	timeout: int,
) -> WebElement:
	def _locator(current_driver: t.WebDriver) -> WebElement | bool:
		try:
			candidate = current_driver.execute_script(
				r"""
				const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];
				const fieldLabel = String(arguments[1] || '').replace(/\s+/g, ' ').trim().toLowerCase();

				function normalize(value) {
				  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
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

				function enabled(node) {
				  return node instanceof Element && !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true';
				}

				function trigger(node) {
				  if (!(node instanceof Element)) {
				    return null;
				  }
				  return node.closest?.('.ms-Dropdown, [role="combobox"], [role="button"], [data-is-focusable="true"]')
				    || node.querySelector?.('.ms-Dropdown-title, [role="combobox"], [role="button"], [data-is-focusable="true"]')
				    || node;
				}

				for (const selector of selectors) {
				  for (const node of Array.from(document.querySelectorAll(selector))) {
				    const target = trigger(node);
				    if (visible(target) && enabled(target)) {
				      return target;
				    }
				  }
				}

				for (const labelSelector of ['label', '.ms-Label', 'span', 'div']) {
				  for (const labelNode of Array.from(document.querySelectorAll(labelSelector))) {
				    if (normalize(labelNode.innerText || labelNode.textContent || '') !== fieldLabel) {
				      continue;
				    }
				    const roots = [
				      labelNode.closest?.('.ms-Dropdown-container, .ms-TextField, .ms-Stack, .ms-Grid-col, .ms-Dropdown'),
				      labelNode.parentElement,
				      labelNode.parentElement?.parentElement,
				    ].filter(Boolean);
				    for (const root of roots) {
				      for (const selector of ['.ms-Dropdown-title', '[role="combobox"]', '[role="button"]', '.ms-Dropdown']) {
				        const node = root.querySelector?.(selector);
				        const target = trigger(node);
				        if (visible(target) && enabled(target)) {
				          return target;
				        }
				      }
				    }
				  }
				}

				return null;
				""",
				list(selectors),
				field_label,
			)
		except WebDriverException:
			candidate = None
		if candidate is not None:
			try:
				if candidate.is_displayed() and candidate.is_enabled():
					return candidate
			except WebDriverException:
				pass
		return False

	return WebDriverWait(driver, timeout).until(_locator)


def _find_comment_field(driver: t.WebDriver, timeout: int) -> WebElement:
	def _locator(current_driver: t.WebDriver) -> WebElement | bool:
		try:
			candidate = current_driver.execute_script(
				r"""
				const selectors = Array.isArray(arguments[0]) ? arguments[0] : [];

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

				function enabled(node) {
				  return node instanceof Element && !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true';
				}

				for (const selector of selectors) {
				  for (const node of Array.from(document.querySelectorAll(selector))) {
				    if (visible(node) && enabled(node)) {
				      return node;
				    }
				  }
				}

				function normalize(value) {
				  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
				}

				for (const labelNode of Array.from(document.querySelectorAll('label, .ms-Label'))) {
				  if (normalize(labelNode.innerText || labelNode.textContent || '') !== 'explain why you resolved this incident') {
				    continue;
				  }
				  const roots = [
				    labelNode.closest?.('.ms-TextField, .ms-Stack, .ms-Grid-col'),
				    labelNode.parentElement,
				    labelNode.parentElement?.parentElement,
				  ].filter(Boolean);
				  for (const root of roots) {
				    const field = root.querySelector?.('textarea, input, .ms-TextField-field');
				    if (visible(field) && enabled(field)) {
				      return field;
				    }
				  }
				}

				return null;
				""",
				list(COMMENT_FIELD_SELECTORS),
			)
		except WebDriverException:
			candidate = None
		if candidate is not None:
			try:
				if candidate.is_displayed() and candidate.is_enabled():
					return candidate
			except WebDriverException:
				pass
		return False

	return WebDriverWait(driver, timeout).until(_locator)


def _set_comment_field_value(driver: t.WebDriver, comment_field: WebElement, comment: str) -> None:
	driver.execute_script(
		"""
		const field = arguments[0];
		const nextValue = String(arguments[1] || '');
		if (!field) {
		  return '';
		}

		function setNativeValue(element, value) {
		  const tagName = String(element.tagName || '').toLowerCase();
		  const prototype = tagName === 'textarea'
		    ? (window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : null)
		    : (window.HTMLInputElement ? window.HTMLInputElement.prototype : null);
		  const descriptor = prototype ? Object.getOwnPropertyDescriptor(prototype, 'value') : null;
		  if (descriptor && typeof descriptor.set === 'function') {
		    descriptor.set.call(element, value);
		    return;
		  }
		  element.value = value;
		}

		try {
		  field.scrollIntoView({ block: 'center', inline: 'nearest' });
		} catch (_) {}
		try {
		  field.focus();
		} catch (_) {}

		setNativeValue(field, '');
		field.dispatchEvent(new Event('input', { bubbles: true }));
		setNativeValue(field, nextValue);
		field.dispatchEvent(new Event('input', { bubbles: true }));
		field.dispatchEvent(new Event('change', { bubbles: true }));
		try {
		  field.blur();
		} catch (_) {}

		return String(field.value || '');
		""",
		comment_field,
		comment,
	)


def _comment_field_matches(driver: t.WebDriver, comment: str, timeout: int) -> bool:
	try:
		comment_field = _find_comment_field(driver, timeout)
		value = str(
			driver.execute_script(
				"return String(arguments[0] ? arguments[0].value || '' : '');",
				comment_field,
			)
		)
	except Exception:  # noqa: BLE001
		return False
	return _normalize_text(value) == _normalize_text(comment)


def _find_dropdown_option(driver: t.WebDriver, option_labels: tuple[str, ...], timeout: int) -> WebElement:
	normalized_labels = tuple(
		label.lower()
		for label in (_normalize_text(value) for value in option_labels)
		if label
	)
	if not normalized_labels:
		raise ValueError("Dropdown option label is required.")

	def _locator(current_driver: t.WebDriver) -> WebElement | bool:
		try:
			candidate = current_driver.execute_script(
				r"""
				const labels = Array.isArray(arguments[0]) ? arguments[0] : [];
				const selectors = Array.isArray(arguments[1]) ? arguments[1] : [];

				function normalize(value) {
				  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
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

				function enabled(node) {
				  return node instanceof Element && !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true';
				}

				function clickable(node) {
				  if (!(node instanceof Element)) {
				    return null;
				  }
				  return node.closest?.('[role="option"], [role="menuitem"], button, [role="button"], .ms-Dropdown-item, .ms-ContextualMenu-item')
				    || node.querySelector?.('[role="option"], [role="menuitem"], button, [role="button"], .ms-Dropdown-item, .ms-ContextualMenu-item')
				    || node;
				}

				for (const exactMatch of [true, false]) {
				  for (const selector of selectors) {
				    for (const node of Array.from(document.querySelectorAll(selector))) {
				      if (!visible(node)) {
				        continue;
				      }
				      const textValue = normalize(node.innerText || node.textContent || node.getAttribute?.('aria-label') || node.getAttribute?.('title') || '');
				      if (!textValue) {
				        continue;
				      }
				      const matched = labels.some((label) => exactMatch ? textValue === label : textValue.includes(label) || label.includes(textValue));
				      if (!matched) {
				        continue;
				      }
				      const target = clickable(node);
				      if (visible(target) && enabled(target)) {
				        return target;
				      }
				    }
				  }
				}

				return null;
				""",
				list(normalized_labels),
				list(DROPDOWN_OPTION_SELECTORS),
			)
		except WebDriverException:
			candidate = None
		if candidate is not None:
			try:
				if candidate.is_displayed() and candidate.is_enabled():
					return candidate
			except WebDriverException:
				pass
		return False

	return WebDriverWait(driver, timeout).until(_locator)


def _dropdown_text_matches(
	driver: t.WebDriver,
	field_label: str,
	selectors: tuple[str, ...],
	option_labels: tuple[str, ...],
	timeout: int,
) -> bool:
	normalized_labels = tuple(
		label.lower()
		for label in (_normalize_text(value) for value in option_labels)
		if label
	)
	if not normalized_labels:
		return True

	try:
		trigger = _find_dropdown_trigger(driver, field_label, selectors, timeout)
		text_value = _normalize_text(
			str(
				driver.execute_script(
					"return String(arguments[0]?.innerText || arguments[0]?.textContent || arguments[0]?.value || arguments[0]?.getAttribute('aria-label') || arguments[0]?.getAttribute('title') || '');",
					trigger,
				)
			)
		).lower()
	except Exception:  # noqa: BLE001
		return False

	return any(label == text_value or label in text_value or text_value in label for label in normalized_labels)


def _select_dropdown_value(
	driver: t.WebDriver,
	field_label: str,
	selectors: tuple[str, ...],
	option_labels: tuple[str, ...],
	timeout: int,
) -> None:
	normalized_labels = tuple(_normalize_text(label) for label in option_labels if _normalize_text(label))
	if not normalized_labels:
		return
	if _dropdown_text_matches(driver, field_label, selectors, normalized_labels, timeout):
		return

	trigger = _find_dropdown_trigger(driver, field_label, selectors, timeout)
	_click_element(driver, trigger)
	option = _find_dropdown_option(driver, normalized_labels, timeout)
	_click_element(driver, option)
	WebDriverWait(driver, max(5, timeout)).until(
		lambda current_driver: _dropdown_text_matches(current_driver, field_label, selectors, normalized_labels, timeout)
	)


def _populate_manage_incident_fields(
	driver: t.WebDriver,
	status: str,
	classification: str,
	comment: str,
	timeout: int,
) -> None:
	_select_dropdown_value(
		driver,
		"Status",
		STATUS_DROPDOWN_TITLE_SELECTORS,
		(_normalize_text(status) or "Resolved",),
		timeout,
	)

	classification_candidates = _classification_option_candidates(classification)
	if classification_candidates:
		_select_dropdown_value(
			driver,
			"Classification",
			CLASSIFICATION_DROPDOWN_TITLE_SELECTORS,
			classification_candidates,
			timeout,
		)

	normalized_comment = str(comment or "")
	if _normalize_text(normalized_comment):
		comment_field = _find_comment_field(driver, timeout)
		_set_comment_field_value(driver, comment_field, normalized_comment)
		WebDriverWait(driver, max(5, timeout)).until(
			lambda current_driver: _comment_field_matches(current_driver, normalized_comment, timeout)
		)


def resolve_incident(
	incident_id: str,
	profile_directory: str,
	timeout: int,
	validation_seconds: int,
	status: str,
	classification: str,
	comment: str,
) -> None:
	normalized_incident_id = _normalize_incident_id(incident_id)
	if not normalized_incident_id:
		raise ValueError("Incident ID is required.")
	normalized_status = _normalize_text(status) or "Resolved"
	normalized_classification = _normalize_text(classification)
	normalized_comment = str(comment or "").strip()

	driver = None
	temp_user_data_dir = ""
	try:
		driver, temp_user_data_dir = _build_driver(profile_directory)
		t.ensure_browser_window(driver)
		t.collapse_to_single_browser_tab(driver)
		t.load_url_in_current_tab(driver, INCIDENTS_URL)
		t.wait_for_page_load(driver, max(10, timeout))

		search_input = _find_incident_search_input(driver, timeout)
		_set_incident_search_value(driver, search_input, normalized_incident_id)
		WebDriverWait(driver, max(5, timeout)).until(
			lambda current_driver: _normalize_incident_id(
				str(current_driver.execute_script("return String(arguments[0] ? arguments[0].value || '' : '');", search_input))
			)
			== normalized_incident_id
		)

		if not _submit_search_without_keyboard(driver, search_input):
			raise RuntimeError("Unable to trigger the incident search without keyboard input.")

		row = WebDriverWait(driver, max(10, timeout)).until(
			lambda current_driver: _find_matching_result_row(current_driver, normalized_incident_id) or False
		)
		_click_element(driver, row)

		manage_incident_button = _find_manage_incident_button(driver, max(10, timeout))
		_click_element(driver, manage_incident_button)
		_populate_manage_incident_fields(
			driver,
			status=normalized_status,
			classification=normalized_classification,
			comment=normalized_comment,
			timeout=max(10, timeout),
		)

		print(f"Manage Incident opened for incident {normalized_incident_id}.")
		time.sleep(max(0, validation_seconds))
	finally:
		if driver is not None:
			try:
				driver.quit()
			except WebDriverException:
				pass
		if temp_user_data_dir:
			shutil.rmtree(temp_user_data_dir, ignore_errors=True)


def main() -> int:
	args = parse_args()
	try:
		resolve_incident(
			incident_id=args.incident_id,
			profile_directory=args.profile_directory,
			timeout=max(10, args.timeout),
			validation_seconds=max(0, args.validation_seconds),
			status=args.status,
			classification=args.classification,
			comment=args.comment,
		)
	except TimeoutException as exc:
		print(f"Timed out while resolving incident {args.incident_id}: {exc}", file=sys.stderr)
		return 1
	except Exception as exc:  # noqa: BLE001
		print(f"Incident resolve automation failed for {args.incident_id}: {exc}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
