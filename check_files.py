import os

# Check if we can find the classifiers file
test_paths = [
    r"artifacts\DLP policy match for document 'Master Account List_Dunstable APR 2026.xlsx' in OneDrive\classifiers.html",
    r"C:\Users\jc311806\OneDrive - Vistra Corp\Desktop\dashboard\artifacts\DLP policy match for document 'Master Account List_Dunstable APR 2026.xlsx' in OneDrive\classifiers.html",
]

for path in test_paths:
    exists = os.path.isfile(path)
    print(f"Path: {path}")
    print(f"  Exists: {exists}")
    if exists:
        print(f"  Size: {os.path.getsize(path)} bytes")
        print(f"  Modified: {os.path.getmtime(path)}")
    print()
