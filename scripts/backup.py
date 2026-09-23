"""Simple backup helper. SQLite copies the DB. MySQL invokes mysqldump if available."""
import os, re, shutil, subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
BACKUPS.mkdir(exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
url = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'impacto_manager.db'}")
if url.startswith("sqlite:///"):
    src = Path(url.replace("sqlite:///", "", 1))
    dst = BACKUPS / f"impacto_{stamp}.db"
    shutil.copy2(src, dst)
    print(dst)
else:
    parsed = urlparse(url.replace("mysql+pymysql", "mysql"))
    exe = shutil.which("mysqldump")
    if not exe:
        raise SystemExit("mysqldump no está instalado.")
    dst = BACKUPS / f"impacto_{stamp}.sql"
    env = os.environ.copy()
    if parsed.password:
        env["MYSQL_PWD"] = parsed.password
    args = [exe, "-h", parsed.hostname or "localhost", "-P", str(parsed.port or 3306), "-u", parsed.username or "root", parsed.path.lstrip("/")]
    with dst.open("wb") as fh:
        subprocess.run(args, stdout=fh, check=True, env=env)
    print(dst)
