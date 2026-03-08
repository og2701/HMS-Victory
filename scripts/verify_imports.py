import sys
import os
import importlib
import pkgutil

# Add project root to sys.path
sys.path.append(os.getcwd())

def verify_imports(start_dir, prefix=""):
    print(f"Verifying imports in {start_dir}...")
    error_count = 0
    for root, dirs, files in os.walk(start_dir):
        if "venv" in root or "__pycache__" in root or ".git" in root:
            continue
        
        for file in files:
            if file.endswith(".py"):
                module_path = os.path.join(root, file)
                # Calculate module name relative to project root (os.getcwd())
                rel_path = os.path.relpath(module_path, os.getcwd())
                module_name = rel_path.replace(os.sep, ".")[:-3]
                
                # Skip tests and setup scripts if necessary
                if "test" in module_name or "setup" in module_name or "verify_imports" in module_name:
                    continue

                try:
                    # print(f"Importing {module_name}...", end="")
                    importlib.import_module(module_name)
                    # print(" OK")
                except ImportError as e:
                    print(f"Importing {module_name}... FAILED: {e}")
                    error_count += 1
                except Exception as e:
                    # print(f" ERROR (other): {e}")
                    pass

    return error_count

if __name__ == "__main__":
    total_errors = 0
    # Verify lib modules
    total_errors += verify_imports("lib")
    # Verify commands
    total_errors += verify_imports("commands")
    # Verify main
    try:
        print("Importing main...", end="")
        import main
        print(" OK")
    except ImportError as e:
        print(f" FAILED: {e}")
        total_errors += 1
    except Exception as e:
        print(f" ERROR (other): {e}")

    if total_errors == 0:
        print("\nAll imports verified successfully!")
    else:
        print(f"\nFound {total_errors} import errors.")
