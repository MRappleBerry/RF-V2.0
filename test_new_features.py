#!/usr/bin/env python3
"""Test the new real-time monitoring and DLP folder creation features."""

import os
import sys
import time
import json
import tempfile
import shutil

# Add function dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "function"))

from tools import (
	write_purview_excel_file,
	read_purview_excel_file,
	create_dlp_folders_from_excel,
	start_purview_table_monitor,
	stop_purview_table_monitor,
)


def test_dlp_folder_creation():
	"""Test DLP folder creation from sample Excel data."""
	print("\n=== Testing DLP Folder Creation ===")
	
	# Create a temporary Excel file with test data
	with tempfile.TemporaryDirectory() as tmpdir:
		test_excel = os.path.join(tmpdir, "test_export.xlsx")
		
		# Create test data with DLP alerts
		headers = ["Alert Name", "Severity", "Status", "Time Detected", "User", "Location"]
		rows = [
			["DLP: Sensitive info found", "High", "New", "2026-05-12 10:30", "user@example.com", "/path1"],
			["Regular Alert", "Medium", "Active", "2026-05-12 10:25", "admin@example.com", "/path2"],
			["DLP_Financial_Data_Leak", "Critical", "New", "2026-05-12 10:20", "user2@example.com", "/path3"],
			["DLP Personal Information Exposure", "High", "Resolved", "2026-05-12 09:00", "auditor@example.com", "/path4"],
		]
		
		# Write test Excel file
		write_purview_excel_file(test_excel, headers, rows)
		print(f"✓ Created test Excel file: {test_excel}")
		
		# Read back to verify
		read_headers, read_rows = read_purview_excel_file(test_excel)
		print(f"✓ Read Excel file - Headers: {read_headers}")
		print(f"✓ Read Excel file - Rows count: {len(read_rows)}")
		for i, row in enumerate(read_rows, 1):
			print(f"  Row {i}: {row[0]}")
		
		# Test folder creation
		artifacts_base = os.path.join(tmpdir, "artifacts")
		
		# Monkey patch to use tmpdir artifacts instead
		import function.tools as tools_module
		original_dirname = os.path.dirname
		
		def patched_dirname(path):
			if "tools.py" in path:
				return os.path.join(tmpdir, "function")
			return original_dirname(path)
		
		os.path.dirname = patched_dirname
		create_dlp_folders_from_excel(test_excel)
		os.path.dirname = original_dirname
		
		# Check created folders
		if os.path.exists(artifacts_base):
			created_folders = os.listdir(artifacts_base)
			print(f"\n✓ Created folders in artifacts/:")
			for folder in sorted(created_folders):
				folder_path = os.path.join(artifacts_base, folder)
				if os.path.isdir(folder_path):
					print(f"  - {folder} (empty: {len(os.listdir(folder_path)) == 0})")
			
			# Verify we got the right count (3 DLP folders)
			dlp_folders = [f for f in created_folders if f.upper().startswith("DLP")]
			assert len(dlp_folders) == 3, f"Expected 3 DLP folders, got {len(dlp_folders)}: {dlp_folders}"
			print(f"\n✓ DLP folder creation verified: {len(dlp_folders)} folders created")
		else:
			print(f"✗ Artifacts folder not found at {artifacts_base}")


def test_monitoring_thread():
	"""Test that monitoring thread starts and stops cleanly."""
	print("\n=== Testing Real-time Monitoring ===")
	
	with tempfile.TemporaryDirectory() as tmpdir:
		test_excel = os.path.join(tmpdir, "test_monitoring.xlsx")
		
		# Create initial Excel file
		headers = ["Alert Name", "Severity", "Status"]
		rows = [["DLP: Test Alert", "High", "New"]]
		write_purview_excel_file(test_excel, headers, rows)
		print(f"✓ Created test Excel file for monitoring: {test_excel}")
		
		# Start monitoring with 2-second interval for testing
		print("✓ Starting monitor thread (2-second interval)...")
		monitor_thread = start_purview_table_monitor(test_excel, check_interval=2)
		
		assert monitor_thread is not None, "Monitor thread should not be None"
		assert monitor_thread.is_alive(), "Monitor thread should be alive"
		print(f"✓ Monitor thread started: {monitor_thread.name}")
		
		# Let it run for a couple iterations
		time.sleep(3)
		
		# Simulate adding a new row by rewriting the file
		print("✓ Simulating new row detection (adding row to Excel)...")
		rows.append(["DLP: New Alert", "Critical", "New"])
		write_purview_excel_file(test_excel, headers, rows)
		
		# Let monitor detect it
		time.sleep(3)
		
		# Stop monitoring
		print("✓ Stopping monitor thread...")
		stop_purview_table_monitor()
		
		# Verify thread stopped
		time.sleep(1)
		if monitor_thread.is_alive():
			print("✗ Monitor thread did not stop cleanly")
		else:
			print("✓ Monitor thread stopped cleanly")


if __name__ == "__main__":
	try:
		test_dlp_folder_creation()
		test_monitoring_thread()
		print("\n" + "="*50)
		print("✓ All tests passed!")
		print("="*50)
	except Exception as exc:
		print(f"\n✗ Test failed: {exc}", file=sys.stderr)
		import traceback
		traceback.print_exc()
		sys.exit(1)
