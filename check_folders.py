import os
import sys
sys.path.insert(0, 'function')
from tools import sanitize_alert_name_for_path

# Get existing artifact folders
artifacts_dir = os.path.abspath("artifacts")
folders = [d for d in os.listdir(artifacts_dir) if os.path.isdir(os.path.join(artifacts_dir, d))]

print(f"Artifact folders ({len(folders)} total):")
for folder in sorted(folders):
    print(f"  - {folder}")
    
# Now test if the first one would be sanitized correctly
if folders:
    test_folder = folders[0]
    print(f"\nTest: Does '{test_folder}' match sanitized version?")
    
    # Try to reverse-sanitize by replacing underscores with characters that would have been invalid
    # This is just to understand the mapping
    print(f"  Raw folder name: {test_folder}")
    
    # Check what files are in this folder
    folder_path = os.path.join(artifacts_dir, test_folder)
    files = os.listdir(folder_path)
    print(f"  Files in folder:")
    for f in sorted(files):
        print(f"    - {f}")
