import os
import shutil

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

def cleanup_pycache():
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.pyc'):
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                    print(f"Deleted file: {file_path}")
                except Exception as e:
                    print(f"Error deleting file {file_path}: {e}")
        for dir in dirs:
            if dir == '__pycache__':
                dir_path = os.path.join(root, dir)
                try:
                    shutil.rmtree(dir_path)
                    print(f"Deleted directory: {dir_path}")
                except Exception as e:
                    print(f"Error deleting directory {dir_path}: {e}")

cleanup_pycache()

from main import client
from config import TOKEN

if __name__ == "__main__":
    client.run(TOKEN)
