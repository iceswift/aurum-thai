"""Send a staging smoke notification to one explicitly supplied device."""

import argparse
import os
from getpass import getpass

import firebase_admin
from firebase_admin import credentials, messaging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Expected isolated Firebase project ID")
    parser.add_argument("--send", action="store_true", help="Deliver to the test device; default is dry run")
    args = parser.parse_args()
    credential = credentials.Certificate(os.environ["FIREBASE_CREDENTIALS_PATH"])
    if credential.project_id != args.project or args.project == "thai-gold-view":
        parser.error("Use credentials from an isolated non-production project")
    token = getpass("FCM registration token from the test device: ").strip()
    if not token:
        parser.error("A test device token is required")
    app = firebase_admin.initialize_app(credential)
    message = messaging.Message(
        token=token,
        data={"type": "gold_snapshot_updated", "revision": "1029", "user_visible": "true",
              "updated_at": "2026-11-13T10:10:00+07:00"},
    )
    messaging.send(message, dry_run=not args.send, app=app)
    print("Accepted by FCM. Verify receipt and refresh on the test device." if args.send else "FCM dry run accepted.")


if __name__ == "__main__":
    main()
