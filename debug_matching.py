import sys
import os
sys.path.insert(0, 'function')
from tools import (
    _find_matching_classifier_row_column_index,
    _normalize_workbook_lookup_value,
    _normalize_digit_only_lookup_value,
    _stringify_workbook_cell_value,
)

# Test data from the classifiers.html
matched_content = "JAMIE CHENELLE"  
surrounding = "er MAELEC Account Accepted 0090689017 CHEN JAMIE CHENELLE WCMASS 28 HARDY ST..."

# Data from the workbook row
row_data = [
    'Dynegy - Dunstable Community Power',
    'MAELEC',
    'Account Accepted',
    '0090689017',
    'CHEN',
    'JAMIE CHENELLE',
    'WCMASS',
    '28 HARDY ST   SOLAR'
]

print(f"Matched content: {matched_content}")
print(f"Surrounding: {surrounding}")
print(f"Row data: {row_data[:8]}")

# Test the matching function
column_index = _find_matching_classifier_row_column_index(
    row_data,
    matched_content,
    surrounding,
)

print(f"\nColumn index found: {column_index}")
if column_index > 0:
    print(f"Matching cell: {row_data[column_index-1]}")
else:
    print("NO MATCH FOUND!")
    
# Debug the normalization
print(f"\n--- Debug Normalization ---")
norm_matched = _normalize_workbook_lookup_value(matched_content)
digit_matched = _normalize_digit_only_lookup_value(matched_content)
norm_surrounding = _normalize_workbook_lookup_value(surrounding)

print(f"Normalized matched: '{norm_matched}'")
print(f"Digit matched: '{digit_matched}'")
print(f"Normalized surrounding: '{norm_surrounding[:50]}...'")

print(f"\n--- Check each cell ---")
for i, cell in enumerate(row_data, start=1):
    norm_cell = _normalize_workbook_lookup_value(cell)
    digit_cell = _normalize_digit_only_lookup_value(cell)
    print(f"Col {i}: '{cell}' -> norm='{norm_cell}', digit='{digit_cell}'")
    if norm_matched and norm_matched == norm_cell:
        print(f"  ^^^ EXACT MATCH!")
