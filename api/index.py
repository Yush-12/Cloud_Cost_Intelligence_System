import os
import sys
from pathlib import Path

# Add project root to sys.path so costintel resolves on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from costintel.web import create_app
from costintel.store import SnapshotStore
from costintel.inventory import collect_demo

# The public deployment is a credential-free demo.
db_path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "inventory.sqlite3")
store = SnapshotStore(db_path)
if not store.latest("local", "demo", "demo-account", "ap-south-1"):
    store.save("local", collect_demo("ap-south-1"))

app = create_app(store, "local", "demo", "demo-account", "ap-south-1", deployment="demo")
