import sys
import os
sys.path.insert(0, 'function')

from tools import (
    _add_classifier_data_found_at_column,
    _find_alert_lookup_source_paths,
    _get_alert_workbook_caches,
)

# The alert name from the folder
alert_name = "DLP policy match for document 'Master Account List_Dunstable APR 2026.xlsx' in OneDrive"

print(f"Alert name: {alert_name}\n")

# Check source paths
source_paths = _find_alert_lookup_source_paths(alert_name)
print(f"Found {len(source_paths)} source file(s):")
for path in source_paths:
    print(f"  - {os.path.basename(path)}")

# Check caches
caches = _get_alert_workbook_caches(alert_name)
print(f"\nFound {len(caches)} workbook cache(s):")
for cache in caches:
    print(f"  - {cache.workbook_file_name} (path: {cache.workbook_path})")

# Now test the enrichment
print(f"\nReading classifiers.html...")
classifiers_path = os.path.join(
    "artifacts",
    alert_name,
    "classifiers.html"
)

with open(classifiers_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Extract just the classifier table
import re
table_match = re.search(r'<table[^>]*>.*?</table>', content, re.DOTALL | re.IGNORECASE)
if table_match:
    classifier_html = table_match.group(0)
    print(f"Found classifier table ({len(classifier_html)} chars)")
    
    # Check how many rows before enrichment
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(f"<root>{classifier_html}</root>")
        tbody = next((child for child in root.iter() if child.tag.lower() == "tbody"), None)
        if tbody:
            rows_before = len([child for child in tbody if child.tag.lower() == "tr"])
            print(f"Table has {rows_before} rows before enrichment")
    except:
        pass
    
    # Run enrichment
    print(f"\nRunning enrichment...")
    enriched_html = _add_classifier_data_found_at_column(alert_name, classifier_html)
    
    # Check how many rows after enrichment and if Data Found At has content
    try:
        root = ET.fromstring(f"<root>{enriched_html}</root>")
        
        # Check table headers
        thead = next((child for child in root.iter() if child.tag.lower() == "thead"), None)
        if thead:
            header_row = next((child for child in thead if child.tag.lower() == "tr"), None)
            if header_row:
                headers = [ET.tostring(cell, encoding='unicode').split('>')[1].split('<')[0] for cell in header_row if cell.tag.lower() in {"th", "td"}]
                print(f"\nHeaders: {headers}")
                
                # Find Data Found At index
                dfa_index = None
                for i, header in enumerate(headers):
                    if "data found at" in header.lower():
                        dfa_index = i
                        break
                
                if dfa_index is not None:
                    print(f"Data Found At column index: {dfa_index}")
                    
                    # Check tbody for content in this column
                    tbody = next((child for child in root.iter() if child.tag.lower() == "tbody"), None)
                    if tbody:
                        rows = [child for child in tbody if child.tag.lower() == "tr"]
                        print(f"\nChecking Data Found At column in {len(rows)} rows:")
                        populated = 0
                        for i, row in enumerate(rows[:5]):  # Check first 5 rows
                            cells = [cell for cell in row if cell.tag.lower() in {"th", "td"}]
                            if dfa_index < len(cells):
                                cell_content = ET.tostring(cells[dfa_index], encoding='unicode')
                                has_content = "artifact-data-found-at-entry" in cell_content or "artifact-row-preview-card" in cell_content
                                print(f"  Row {i+1}: {'HAS CONTENT' if has_content else 'EMPTY'} - {cell_content[:100]}...")
                                if has_content:
                                    populated += 1
                        print(f"\nTotal populated: {populated}/{min(5, len(rows))}")
    except Exception as e:
        print(f"Error parsing enriched HTML: {e}")
else:
    print("No classifier table found!")
