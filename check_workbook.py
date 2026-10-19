from openpyxl import load_workbook
import os

wb_dir = r"artifacts\DLP policy match for document 'Master Account List_Dunstable APR 2026.xlsx' in OneDrive"
wb_file = r"Master Account List_Dunstable APR 2026.xlsx"
wb_path = os.path.join(wb_dir, wb_file)

print(f"Looking for: {wb_path}")
print(f"Exists: {os.path.exists(wb_path)}")

if os.path.exists(wb_path):
    try:
        wb = load_workbook(wb_path)
        print(f'\nWorkbook loaded: {len(wb.sheetnames)} sheets')
        print(f'Sheets: {wb.sheetnames[:5]}')
        
        ws = wb.active
        print(f'\nActive sheet: {ws.title}')
        print(f'Dimensions: {ws.dimensions}')
        
        # Print first 10 rows and search for JAMIE
        print("\nFirst 10 rows:")
        for i, row in enumerate(ws.iter_rows(max_row=10, values_only=True), 1):
            print(f'Row {i}: {row[:8]}')
            if 'JAMIE' in str(row).upper():
                print(f'  ^^^ FOUND JAMIE')
                
        # Search for JAMIE in all rows
        print("\nSearching for 'JAMIE' in entire workbook...")
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if row and 'JAMIE' in str(row).upper():
                print(f'Row {i}: {row[:8]}')
                break
                
    except Exception as e:
        import traceback
        print(f'Error: {e}')
        traceback.print_exc()
