import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

'''
future add some ui maybe?
check winver for support - old versions also?
show actual ver of wats and changelog if asked

//create exe
$ pip install auto-py-to-exe
$ auto-py-to-exe
'''

print("FIXTEC - WHATSAPP CONSOLE UPDATER 2026")
ARGSTART = input("TYPE OF UPDATE `winget` `direct`: ")

STORE_PRODUCT_ID = "9NKSQGP7F2NH"
STORE_URL = f"https://apps.microsoft.com/detail/{STORE_PRODUCT_ID}"

RG_ADGUARD_API = "https://store.rg-adguard.net/api/GetFiles"

# Dependencies commonly required by MSIX/MSStore apps (x64) etc
DEPENDENCY_KEYWORDS = [
    "Microsoft.UI.Xaml",
    "Microsoft.NET.Native.Framework",
    "Microsoft.NET.Native.Runtime",
    "Microsoft.VCLibs",
    "Microsoft.WindowsAppRuntime",
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def log(msg: str):
    print(msg, flush=True)


def run_cmd(cmd: list[str]) -> int:
    log(f"\n>>> Running: {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.stdout.strip():
        print(p.stdout)
    if p.stderr.strip():
        print(p.stderr)
    return p.returncode


def run_powershell(ps_command: str) -> int:
    return run_cmd([
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_command
    ])


def parse_version_from_filename(name: str) -> tuple:
    """
    ex:
      WhatsAppDesktop_2.2587.9.0_neutral_~_cv1g1gvanyjgm.msixbundle
      Microsoft.NET.Native.Framework.2.2_2.2.29512.0_x64__8wekyb3d8bbwe.Appx

    Returns tuple(int,int,int,int) so we can compare versions.
    """
    m = re.search(r"_(\d+(?:\.\d+)+)_", name)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)+)", name)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def post_rg_adguard(product_url: str) -> list[tuple[str, str]]:
    """
    Queries rg-adguard and returns list of (filename, direct_download_url etc .)
    """
    body = urlencode({
        "type": "url",
        "url": product_url,
        "ring": "Retail",
        "lang": "en-US",
    }).encode("utf-8")

    req = Request(
        RG_ADGUARD_API,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    )
    print("requrl: ",req.get_full_url())

    with urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", errors="ignore")

    # Extract: <a href="URL">FILENAME</a>
    links = re.findall(r'<a href="(https?://[^"]+)"[^>]*>([^<]+)</a>', html)
    cleaned = []
    for url, fname in links:
        fname = fname.strip()
        # Ignore weird entries - simple cleaner
        if not fname or fname.lower() in ("click here",):
            continue
        cleaned.append((fname, url))
    return cleaned


def pick_latest_file(files: list[tuple[str, str]], predicate) -> tuple[str, str] | None:
    candidates = [(n, u) for (n, u) in files if predicate(n)]
    if not candidates:
        return None
    candidates.sort(key=lambda x: parse_version_from_filename(x[0]), reverse=True)
    return candidates[0]


def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)

    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=120) as r:
        total = r.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None

        chunk_size = 1024 * 256
        downloaded = 0

        with open(dest, "wb") as f:
            while True:
                chunk = r.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if total:
                    pct = (downloaded / total) * 100
                    print(f"\rDownloading: {dest.name} ({pct:.1f}%)", end="", flush=True)
                else:
                    print(f"\rDownloading: {dest.name} ({downloaded / (1024*1024):.1f} MB)", end="", flush=True)

    print("")  #give a small space

def install_with_winget():
    if not shutil.which("winget"):
        raise RuntimeError("winget was not found. Use --method direct or install App Installer/winget first.")

    log("Installing WhatsApp using winget (Microsoft Store source)...")
    rc = run_cmd([
        "winget", "install",
        "--id", STORE_PRODUCT_ID,
        "--source", "msstore",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--silent"
    ])
    if rc != 0:
        raise RuntimeError(f"winget install failed with exit code {rc}")
    log("WhatsApp installed successfully (winget).")


def install_direct(download_dir: Path):
    log("Getting the latest WhatsApp Desktop package + dependencies list...")
    files = post_rg_adguard(STORE_URL)

    # Main app here (.msixbundle)
    main = pick_latest_file(
        files,
        lambda n: n.lower().endswith(".msixbundle")
        and "whatsappdesktop" in n.lower()
        and ("_neutral_" in n.lower() or "x64" in n.lower())
    )

    if not main:
        raise RuntimeError("Could not find WhatsAppDesktop .msixbundle download link.")

    main_name, main_url = main

    # Dependencies
    deps = []
    for keyword in DEPENDENCY_KEYWORDS:
        dep = pick_latest_file(
            files,
            lambda n, k=keyword: k.lower() in n.lower()
            and (n.lower().endswith(".appx") or n.lower().endswith(".msix"))
            and "x64" in n.lower()
        )
        if dep:
            deps.append(dep)

    depexists = set()
    unique_deps = []
    for n, u in deps:
        if n.lower() not in depexists:
            depexists.add(n.lower())
            unique_deps.append((n, u))

    # dependencies
    dep_paths = []
    for i, (dep_name, dep_url) in enumerate(unique_deps, start=1):
        log(f"\nDownloading dependency {i}: {dep_name}")
        dep_path = download_dir / dep_name
        download_file(dep_url, dep_path)

        log(f"Installing dependency {i}: {dep_name}")
        rc = run_powershell(f'Add-AppxPackage -Path "{dep_path}" -ForceApplicationShutdown')
        if rc == 0:
            log(f"Dependency {i} installed successfully: {dep_name}")
        else:
            log(f"Dependency {i} install returned exit code {rc} (it may already be installed ?).")

        dep_paths.append(str(dep_path))

    #  bundle
    log(f"\nDownloading: {main_name}")
    main_path = download_dir / main_name
    download_file(main_url, main_path)

    # Install main with DependencyPath...
    log("Installing the application...")
    deps_ps_array = "@(" + ",".join([f'"{p}"' for p in dep_paths]) + ")"
    ps = f'Add-AppxPackage -Path "{main_path}" -DependencyPath {deps_ps_array} -ForceApplicationShutdown'
    rc = run_powershell(ps)

    if rc != 0:
        raise RuntimeError(f"WhatsApp install failed with exit code {rc}")

    log(" WhatsApp installed successfully (direct MSIX).")

#MAIN RUN
def main():
    if sys.platform != "win32":
        print("This script is for Windows only.")
        sys.exit(1)

    download_dir = Path(os.environ.get("TEMP", ".")) / "WhatsApp_Install"

    try:
        if os.path.exists(download_dir):
            os.remove(download_dir)
            print("cleaning old folder...")
    except Exception as e:
        log(f"\n ERROR: {e}")

    #curr_dir = os.path.dirname(os.path.abspath(__file__))
    #path = os.path.realpath(curr_dir)

    log(f"Download folder: {download_dir}")

    try:
        if ARGSTART == "direct":
            try:
                install_direct(download_dir)
            except Exception as e:
                log(f"direct method failed... ({e})")

        if ARGSTART == "winget":
            try:
                install_with_winget()
            except Exception as e:
                log(f"winget method failed.. ({e})")

    except Exception as e:
        log(f"\n ERROR: {e}")
        log("\nTip: Try running Terminal/PowerShell as Administrator if installation is blocked.")
        sys.exit(2)


if __name__ == "__main__":
    main()
