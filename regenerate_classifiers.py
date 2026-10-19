#!/usr/bin/env python3
"""
Regenerate classifiers.html with Data Found At enrichment applied.
This tests the enrichment functionality on existing artifact data.
"""

import os
import sys
sys.path.insert(0, 'function')

from tools import _add_classifier_data_found_at_column

def regenerate_classifier_artifacts():
    """Regenerate all classifiers.html with enrichment applied."""
    artifacts_base = os.path.abspath("artifacts")
    
    # Handle symlinks
    if os.path.islink(artifacts_base):
        artifacts_base = os.path.realpath(artifacts_base)
    
    if not os.path.isdir(artifacts_base):
        print(f"Artifacts directory not found: {artifacts_base}")
        return False
    
    print(f"Scanning artifacts at: {artifacts_base}")
    
    # Find all classifiers.html files
    updated_count = 0
    for root, dirs, files in os.walk(artifacts_base):
        if "classifiers.html" not in files:
            continue
        
        alert_name = os.path.basename(root)
        classifiers_path = os.path.join(root, "classifiers.html")
        
        print(f"\n{'='*70}")
        print(f"Processing: {alert_name}")
        print(f"File: {classifiers_path}")
        
        # Read the existing classifiers.html
        try:
            with open(classifiers_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"  ✗ Failed to read: {e}")
            continue
        
        # Extract the classifier table from the HTML document
        import re
        table_match = re.search(r'<table[^>]*>.*?</table>', content, re.DOTALL | re.IGNORECASE)
        if not table_match:
            print(f"  ✗ No classifier table found")
            continue
        
        classifier_html = table_match.group(0)
        print(f"  Found table ({len(classifier_html)} chars)")
        
        # Apply enrichment
        print(f"  Enriching with Data Found At...")
        enriched_html = _add_classifier_data_found_at_column(alert_name, classifier_html)
        
        # Count populated cells
        import xml.etree.ElementTree as ET
        try:
            root_elem = ET.fromstring(f"<root>{enriched_html}</root>")
            tbody = next((child for child in root_elem.iter() if child.tag.lower() == "tbody"), None)
            if tbody:
                rows = [child for child in tbody if child.tag.lower() == "tr"]
                
                # Find Data Found At column
                thead = next((child for child in root_elem.iter() if child.tag.lower() == "thead"), None)
                if thead:
                    header_row = next((child for child in thead if child.tag.lower() == "tr"), None)
                    if header_row:
                        headers = [cell for cell in header_row if cell.tag.lower() in {"th", "td"}]
                        dfa_index = None
                        for i, header in enumerate(headers):
                            header_text = ET.tostring(header, encoding='unicode')
                            if "data found at" in header_text.lower():
                                dfa_index = i
                                break
                        
                        if dfa_index is not None:
                            populated = 0
                            for row in rows:
                                cells = [cell for cell in row if cell.tag.lower() in {"th", "td"}]
                                if dfa_index < len(cells):
                                    cell_content = ET.tostring(cells[dfa_index], encoding='unicode')
                                    if "artifact-data-found-at-entry" in cell_content or "artifact-row-preview-card" in cell_content:
                                        populated += 1
                            
                            print(f"  Data Found At column populated in {populated}/{len(rows)} rows")
        except Exception as e:
            print(f"  Warning: Could not parse enriched HTML: {e}")
        
        # Replace the classifier table in the original HTML document
        updated_content = content[:table_match.start()] + enriched_html + content[table_match.end():]
        
        # Write back to file
        try:
            with open(classifiers_path, 'w', encoding='utf-8') as f:
                f.write(updated_content)
            print(f"  ✓ Updated classifiers.html")
            updated_count += 1
        except Exception as e:
            print(f"  ✗ Failed to write: {e}")
            continue
    
    print(f"\n{'='*70}")
    print(f"Summary: Updated {updated_count} classifiers.html file(s)")
    return updated_count > 0

if __name__ == "__main__":
    success = regenerate_classifier_artifacts()
    sys.exit(0 if success else 1)
