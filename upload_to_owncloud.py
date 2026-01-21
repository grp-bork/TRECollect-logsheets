#!/usr/bin/env python3
"""
Script to detect changed/new JSON files and images, and upload them to ownCloud.
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple
import requests
from requests.auth import HTTPBasicAuth

# ownCloud WebDAV configuration
OWNCLOUD_BASE_URL = "https://oc.embl.de/public.php/webdav"
CONFIG_FILE = Path(__file__).parent / "owncloud_config.json"


def load_config() -> Dict:
    """Load access token from config file or environment variable."""
    # Try environment variable first (for CI)
    token = os.environ.get("OWNCLOUD_ACCESS_TOKEN")
    if token:
        return {"access_token": token}
    
    # Try config file (for local use)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    
    raise ValueError(
        "No access token found. Set OWNCLOUD_ACCESS_TOKEN environment variable "
        "or create owncloud_config.json with 'access_token' field."
    )


def get_git_changed_files() -> Tuple[List[str], List[str]]:
    """Get changed and new files using git diff."""
    is_ci = os.environ.get("CI") == "true"
    
    if is_ci:
        # Get the current branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        branch_name = result.stdout.strip()
        
        # Get all changed/modified files (including new files added)
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", f"origin/{branch_name}...HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        changed = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        
        # Get files that are new (Added) vs modified (Modified)
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", f"origin/{branch_name}...HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        new = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    else:
        # Get current branch and try to compare against remote tracking branch
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                capture_output=True,
                text=True,
                check=True
            )
            tracking_branch = result.stdout.strip()
            
            # Compare against remote tracking branch (catches committed but unpushed changes)
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{tracking_branch}...HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            changed_committed = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        except subprocess.CalledProcessError:
            # No tracking branch, skip committed changes check
            changed_committed = []
        
        # Check unstaged changes
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        changed_unstaged = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        
        # Check staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True
        )
        changed_staged = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        
        changed = list(set(changed_committed + changed_unstaged + changed_staged))
        
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True
        )
        new = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
    
    return changed, new


def filter_relevant_files(files: List[str]) -> Dict[str, List[str]]:
    """
    Filter files into categories: logsheets, teams, images.
    Returns: {"logsheets": [...], "teams": [...], "images": [...]}
    """
    result = {"logsheets": [], "teams": [], "images": []}
    
    for file_path in files:
        if file_path.startswith("logsheets/") and file_path.endswith(".json"):
            result["logsheets"].append(file_path)
        elif file_path.startswith("teams/") and file_path.endswith(".json"):
            result["teams"].append(file_path)
        elif file_path.startswith("images/"):
            result["images"].append(file_path)
    
    return result


def read_json_file(file_path: str) -> Dict:
    """Read and parse a JSON file."""
    with open(file_path, 'r') as f:
        return json.load(f)


def upload_file_to_owncloud(local_path: str, remote_path: str, access_token: str):
    """Upload a file to ownCloud using WebDAV PUT request."""
    url = f"{OWNCLOUD_BASE_URL}/{remote_path}"
    
    remote_dir = os.path.dirname(remote_path)
    if remote_dir:
        create_directory(remote_dir, access_token)
    
    with open(local_path, 'rb') as f:
        response = requests.put(
            url,
            data=f,
            auth=HTTPBasicAuth(access_token, ''),
            timeout=30
        )
        response.raise_for_status()
        print(f"✓ Uploaded: {local_path} -> {remote_path}")


def create_directory(remote_path: str, access_token: str):
    """Create a directory on ownCloud using WebDAV MKCOL request.
    Creates parent directories recursively if needed."""
    if not remote_path:
        return
    
    # Split path and create each level
    parts = remote_path.strip('/').split('/')
    current_path = ""
    
    for part in parts:
        if not part:
            continue
        current_path = f"{current_path}/{part}" if current_path else part
        url = f"{OWNCLOUD_BASE_URL}/{current_path}"
        
        # Create directory (405 means already exists, which is fine)
        response = requests.request(
            "MKCOL",
            url,
            auth=HTTPBasicAuth(access_token, ''),
            timeout=10
        )
        if response.status_code not in [201, 405]:
            response.raise_for_status()


def process_logsheet(file_path: str, access_token: str):
    """Process a logsheet file: extract ID and version, upload to logsheets/{id}/{version}.json"""
    data = read_json_file(file_path)
    remote_path = f"logsheets/{data['id']}/{data['version']}.json"
    upload_file_to_owncloud(file_path, remote_path, access_token)


def process_team(file_path: str, access_token: str):
    """Process a team file: extract ID and version, upload to teams/{id}/{version}.json"""
    data = read_json_file(file_path)
    remote_path = f"teams/{data['id']}/{data['version']}.json"
    upload_file_to_owncloud(file_path, remote_path, access_token)


def process_image(file_path: str, access_token: str):
    """Process an image file: upload to images/{filename}"""
    filename = os.path.basename(file_path)
    remote_path = f"images/{filename}"
    upload_file_to_owncloud(file_path, remote_path, access_token)


def main():
    """Main function to detect changes and upload to ownCloud."""
    print("Detecting changed files...")
    
    config = load_config()
    access_token = config["access_token"]
    
    changed_files, new_files = get_git_changed_files()
    
    # Logsheets and teams: check both changed and new (for version changes)
    json_files = list(set(changed_files + new_files))
    relevant_json = filter_relevant_files(json_files)
    
    # Images: only check new files (they never change, only new ones are added)
    relevant_images = filter_relevant_files(new_files)
    
    total_files = (
        len(relevant_json["logsheets"]) +
        len(relevant_json["teams"]) +
        len(relevant_images["images"])
    )
    
    if total_files == 0:
        print("No relevant files (logsheets, teams, or images) to upload.")
        return
    
    print(f"\nFound {total_files} file(s) to upload:")
    print(f"  - Logsheets: {len(relevant_json['logsheets'])}")
    print(f"  - Teams: {len(relevant_json['teams'])}")
    print(f"  - Images: {len(relevant_images['images'])}")
    
    # Show which files are new vs changed
    new_json = filter_relevant_files(new_files)
    if new_json["logsheets"] or new_json["teams"]:
        print(f"\nNew files detected:")
        if new_json["logsheets"]:
            print(f"  - New logsheets: {', '.join(new_json['logsheets'])}")
        if new_json["teams"]:
            print(f"  - New teams: {', '.join(new_json['teams'])}")
    print()
    
    for file_path in relevant_json["logsheets"]:
        process_logsheet(file_path, access_token)
    
    for file_path in relevant_json["teams"]:
        process_team(file_path, access_token)
    
    for file_path in relevant_images["images"]:
        process_image(file_path, access_token)
    
    print("\nUpload complete.")


if __name__ == "__main__":
    main()
