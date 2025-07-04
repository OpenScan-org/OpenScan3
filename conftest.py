import sys
import os

# Add the project root directory to the Python path
# This allows pytest to find modules like 'app' and 'settings'
# when run from any directory, especially when tests are in a subfolder.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# You can also add other specific paths if needed, for example:
# sys.path.insert(0, os.path.join(PROJECT_ROOT, 'app'))

print(f"PYTHONPATH extended by conftest.py: {PROJECT_ROOT}")