import os
import hashlib
from collections import defaultdict
from pathlib import Path


def get_file_hash(filepath, chunk_size=8192):
    """Calculate MD5 hash of a file by reading it in chunks."""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def find_duplicates(folder_path):
    """Find duplicate files in a folder by comparing content hashes."""
    size_map = defaultdict(list)
    
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            filepath = Path(root) / filename
            try:
                size = filepath.stat().st_size
                size_map[size].append(filepath)
            except (OSError, PermissionError) as e:
                print(f"Could not access: {filepath} - {e}")
    
    hash_map = defaultdict(list)
    
    for size, files in size_map.items():
        if len(files) > 1:
            for filepath in files:
                try:
                    file_hash = get_file_hash(filepath)
                    hash_map[file_hash].append(filepath)
                except (OSError, PermissionError) as e:
                    print(f"Could not hash: {filepath} - {e}")
    
    duplicates = {h: paths for h, paths in hash_map.items() if len(paths) > 1}
    
    return duplicates


def delete_duplicates(duplicates):
    """Delete duplicate files, keeping the first one in each group."""
    total_deleted = 0
    total_bytes_freed = 0
    
    for file_hash, paths in duplicates.items():
        # Keep the first file, delete the rest
        keep = paths[0]
        to_delete = paths[1:]
        
        print(f"Keeping: {keep}")
        
        for filepath in to_delete:
            try:
                size = filepath.stat().st_size
                filepath.unlink()
                total_deleted += 1
                total_bytes_freed += size
                print(f"  Deleted: {filepath}")
            except (OSError, PermissionError) as e:
                print(f"  Could not delete: {filepath} - {e}")
        
        print()
    
    return total_deleted, total_bytes_freed


def main():
    # ============================================
    # SET YOUR FOLDER PATH HERE
    # ============================================
    folder = r"/Users/Shadi/Dropbox/SHARE_Model_LLM/Search Engine/downloads"
    # ============================================
    
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a valid directory")
        return
    
    print(f"Scanning '{folder}' for duplicates...\n")
    
    duplicates = find_duplicates(folder)
    
    if not duplicates:
        print("No duplicate files found.")
        return
    
    # Count total duplicates
    total_dupes = sum(len(paths) - 1 for paths in duplicates.values())
    print(f"Found {len(duplicates)} group(s) with {total_dupes} duplicate file(s).\n")
    
    # Show what will be deleted
    print("=" * 50)
    print("FILES TO BE DELETED:")
    print("=" * 50)
    for file_hash, paths in duplicates.items():
        size = paths[0].stat().st_size
        print(f"\nGroup (size: {size:,} bytes):")
        print(f"  [KEEP]   {paths[0]}")
        for path in paths[1:]:
            print(f"  [DELETE] {path}")
    
    print("\n" + "=" * 50)
    
    # Confirm deletion
    confirm = input("\nType 'DELETE' to confirm deletion: ").strip()
    
    if confirm == "DELETE":
        print("\nDeleting duplicates...\n")
        deleted, freed = delete_duplicates(duplicates)
        print("=" * 50)
        print(f"Done! Deleted {deleted} file(s).")
        print(f"Freed {freed:,} bytes ({freed / (1024*1024):.2f} MB)")
    else:
        print("\nDeletion cancelled.")


if __name__ == "__main__":
    main()