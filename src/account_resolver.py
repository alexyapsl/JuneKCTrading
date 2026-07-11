"""
Account Resolver

Resolves which IG credential file to use based on:
- account_name (e.g. "account1")
- paper_trading (True = demo, False = live)

Credential file naming convention:
    {account_name}.env.demo
    {account_name}.env.live

Example files:
    account1.env.demo
    account1.env.live
    account2.env.demo
    account2.env.live

The resolver searches in two locations (in order):
1. The explicitly provided accounts_dir
2. ./accounts/ (relative to project root)
3. Project root (same folder as .env)

Usage:
    from src.account_resolver import resolve_credentials

    creds = resolve_credentials(account_name="account1", paper_trading=True)
    # Returns: {"username": "...", "password": "...", "api_key": "...", "acc_type": "DEMO"}
"""

from pathlib import Path
from typing import Dict, Optional
from dotenv import dotenv_values
import os


DEFAULT_ACCOUNTS_DIR = Path(__file__).parent.parent / "accounts"


def _find_credential_file(
    account_name: str,
    mode: str,
    accounts_dir: Optional[Path] = None
) -> Optional[Path]:
    """
    Search for the credential file in multiple locations.
    Returns the first match or None.
    """
    filename = f"{account_name}.env.{mode.lower()}"   # demo or live

    search_dirs = []
    if accounts_dir:
        search_dirs.append(Path(accounts_dir))
    search_dirs.append(DEFAULT_ACCOUNTS_DIR)
    search_dirs.append(Path(__file__).parent.parent)   # project root

    for base in search_dirs:
        candidate = base / filename
        if candidate.exists():
            return candidate

    return None


def resolve_credentials(
    account_name: str,
    paper_trading: bool = True,
    accounts_dir: Optional[Path] = None
) -> Dict[str, str]:
    """
    Resolve and load credentials for the given account.

    Parameters
    ----------
    account_name : str
        Name of the account (e.g. "account1", "account2")
    paper_trading : bool
        True  -> load .demo credentials
        False -> load .live credentials
    accounts_dir : Path, optional
        Custom directory to search for credential files.
        If None, searches accounts/ then project root.

    Returns
    -------
    dict
        {
            "username": str,
            "password": str,
            "api_key": str,
            "acc_type": "DEMO" or "LIVE"
        }

    Raises
    ------
    FileNotFoundError
        If the expected credential file cannot be found.
    ValueError
        If required keys are missing from the credential file.
    """
    mode = "demo" if paper_trading else "live"
    cred_file = _find_credential_file(account_name, mode, accounts_dir)

    if cred_file is None:
        expected = f"{account_name}.env.{mode}"
        raise FileNotFoundError(
            f"Credential file not found for account '{account_name}' (mode={mode}).\n"
            f"Expected file: {expected}\n"
            f"Searched in: accounts/ and project root."
        )

    # Load the file (dotenv_values returns a dict, does not pollute os.environ)
    creds = dotenv_values(cred_file)

    # Normalize keys (allow both uppercase and lowercase in the file)
    def get_key(*candidates):
        for k in candidates:
            if k in creds and creds[k]:
                return creds[k]
        return None

    username = get_key("IG_USERNAME", "username")
    password = get_key("IG_PASSWORD", "password")
    api_key  = get_key("IG_API_KEY", "api_key", "IG_APIKEY")
    acc_type = get_key("IG_ACC_TYPE", "acc_type", "IG_ACCTYPE") or ("DEMO" if paper_trading else "LIVE")

    missing = []
    if not username:
        missing.append("IG_USERNAME")
    if not password:
        missing.append("IG_PASSWORD")
    if not api_key:
        missing.append("IG_API_KEY")

    if missing:
        raise ValueError(
            f"Missing required credential(s) in {cred_file.name}: {', '.join(missing)}"
        )

    return {
        "username": username,
        "password": password,
        "api_key": api_key,
        "acc_type": acc_type.upper(),
        "credential_file": str(cred_file)
    }


# Quick manual test
if __name__ == "__main__":
    try:
        creds = resolve_credentials("account1", paper_trading=True)
        print("Resolved demo credentials:")
        print(f"  username : {creds['username']}")
        print(f"  api_key  : {creds['api_key'][:8]}...")
        print(f"  acc_type : {creds['acc_type']}")
        print(f"  source   : {creds['credential_file']}")
    except Exception as e:
        print(f"Error: {e}")