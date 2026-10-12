"""Create a version commit and one annotated tag; optionally publish atomically."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from application.updates import version_key
from application.version import REPO_SLUG


def git(*args, capture=False):
    result = subprocess.run(["git", *args], cwd=ROOT, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="v1.2.3 or v1.2.3-beta.1")
    parser.add_argument("--publish", action="store_true", help="Push the current branch and this tag to origin")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the plan without changes")
    args = parser.parse_args()
    try:
        key = version_key(args.tag)
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD", capture=True)
        if git("status", "--porcelain", capture=True):
            parser.error("Commit your changes first. A release requires a clean working tree.")
        tags = git("tag", "--list", capture=True).splitlines()
        if args.tag in tags:
            parser.error(f"Tag already exists: {args.tag}")
        previous = []
        for tag in tags:
            try:
                previous.append(version_key(tag))
            except ValueError:
                pass
        if previous and key <= max(previous):
            parser.error("The new version must be greater than previous version tags.")
        if args.publish and not args.dry_run:
            if git("ls-remote", "--tags", "origin", f"refs/tags/{args.tag}", capture=True):
                parser.error(f"Tag already exists on origin: {args.tag}")
        if args.dry_run:
            print(
                f"Version commit → annotated {args.tag} → {branch}"
                + (" + atomic push to origin" if args.publish else " (local only)")
            )
            return
        version_file = ROOT / "application" / "version.py"
        version_file.write_text(
            f'"""Release identity. CI stamps this file from the release tag before packaging."""\n\n'
            f'__version__ = "{args.tag}"\nREPO_SLUG = "{REPO_SLUG}"\n',
            encoding="utf-8",
        )
        git("add", "--", "application/version.py")
        git("commit", "--allow-empty", "-m", f"Release {args.tag}")
        git("tag", "-a", args.tag, "-m", f"Version {args.tag}")
        if args.publish:
            git("push", "--atomic", "origin", f"HEAD:refs/heads/{branch}", f"refs/tags/{args.tag}")
        print(
            f"Created {args.tag}."
            + (
                " GitHub will build Windows x86/x64 and publish the release."
                if args.publish
                else " Tag is local; push it to trigger GitHub Releases."
            )
        )
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release failed: {error}\n")


if __name__ == "__main__":
    main()
