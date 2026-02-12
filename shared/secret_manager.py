#!/usr/bin/env python3
"""
Secret Manager Module

Handles fetching secrets from Google Cloud Secret Manager for cloud deployments,
with fallback detection for local development environments.

When running on GCP:
- Credentials are fetched from Secret Manager
- Tokens are persisted back to Secret Manager after refresh

When running locally:
- Returns None, allowing config_loader to use local config.json
- No Secret Manager calls are made
"""

import os
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Secret names in GCP Secret Manager
SECRET_NAMES = {
    "saxo_credentials": "calypso-saxo-credentials",
    "google_sheets_credentials": "calypso-google-sheets-credentials",
    "account_config": "calypso-account-config",
    "email_config": "calypso-email-config",
}


def is_running_on_gcp() -> bool:
    """
    Detect if running on GCP Compute Engine.

    Checks for GCP metadata server availability, which is only
    accessible from within GCP infrastructure.

    Returns:
        bool: True if running on GCP, False otherwise (local dev)
    """
    # Method 1: Check for explicit GCP environment variable
    if os.environ.get("GCP_PROJECT"):
        logger.debug("GCP detected via GCP_PROJECT environment variable")
        return True

    # Method 2: Check for GOOGLE_CLOUD_PROJECT (set by many GCP services)
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        logger.debug("GCP detected via GOOGLE_CLOUD_PROJECT environment variable")
        return True

    # Method 3: Check for metadata server (GCE instances have this)
    try:
        import requests
        response = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/id",
            headers={"Metadata-Flavor": "Google"},
            timeout=1
        )
        if response.status_code == 200:
            logger.debug("GCP detected via metadata server")
            return True
    except Exception:
        pass

    return False


def get_project_id() -> Optional[str]:
    """
    Get the GCP project ID.

    Returns:
        str: Project ID, or None if not on GCP
    """
    # Check environment variable first
    project_id = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id:
        return project_id

    # Try to get from metadata server
    try:
        import requests
        response = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
            timeout=2
        )
        if response.status_code == 200:
            return response.text
    except Exception:
        pass

    return None


def get_secret(secret_name: str, version: str = "latest") -> Optional[str]:
    """
    Fetch a secret from GCP Secret Manager.

    Args:
        secret_name: Name of the secret in Secret Manager
        version: Version of the secret (default: "latest")

    Returns:
        str: Secret value as string, or None if not found/error
    """
    if not is_running_on_gcp():
        logger.debug(f"Not on GCP, skipping Secret Manager fetch for {secret_name}")
        return None

    try:
        from google.cloud import secretmanager

        project_id = get_project_id()
        if not project_id:
            logger.error("Could not determine GCP project ID")
            return None

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/{version}"

        response = client.access_secret_version(request={"name": name}, timeout=10)
        secret_value = response.payload.data.decode("UTF-8")

        logger.info(f"Successfully fetched secret: {secret_name}")
        return secret_value

    except ImportError:
        logger.error("google-cloud-secret-manager not installed. Run: pip install google-cloud-secret-manager")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch secret {secret_name}: {e}")
        return None


def get_saxo_credentials() -> Optional[Dict[str, Any]]:
    """
    Get Saxo API credentials from Secret Manager.

    Returns:
        dict: Saxo credentials with app_key, app_secret, tokens, etc.
              Returns None if not on GCP or if fetch fails.
    """
    secret_value = get_secret(SECRET_NAMES["saxo_credentials"])
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Saxo credentials JSON: {e}")
    return None


def get_google_sheets_credentials() -> Optional[Dict[str, Any]]:
    """
    Get Google Sheets service account credentials from Secret Manager.

    Returns:
        dict: Service account JSON credentials for gspread authentication.
              Returns None if not on GCP or if fetch fails.
    """
    secret_value = get_secret(SECRET_NAMES["google_sheets_credentials"])
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Google Sheets credentials JSON: {e}")
    return None


def get_account_config() -> Optional[Dict[str, Any]]:
    """
    Get account configuration (account_key, client_key) from Secret Manager.

    Returns:
        dict: Account configuration.
              Returns None if not on GCP or if fetch fails.
    """
    secret_value = get_secret(SECRET_NAMES["account_config"])
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse account config JSON: {e}")
    return None


def get_email_config() -> Optional[Dict[str, Any]]:
    """
    Get email alerting configuration from Secret Manager.

    Returns:
        dict: Email config with smtp_server, sender, recipients, etc.
              Returns None if not on GCP or if fetch fails.
    """
    secret_value = get_secret(SECRET_NAMES["email_config"])
    if secret_value:
        try:
            return json.loads(secret_value)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse email config JSON: {e}")
    return None


def update_secret(secret_name: str, secret_value: str) -> bool:
    """
    Update a secret in Secret Manager (for token refresh).

    Creates a new version of the secret with the updated value.
    The old version is automatically disabled.

    Args:
        secret_name: Name of the secret to update
        secret_value: New secret value (will be JSON string for credentials)

    Returns:
        bool: True if successful, False otherwise
    """
    if not is_running_on_gcp():
        logger.debug(f"Not on GCP, skipping Secret Manager update for {secret_name}")
        return False

    try:
        from google.cloud import secretmanager

        project_id = get_project_id()
        if not project_id:
            logger.error("Could not determine GCP project ID")
            return False

        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project_id}/secrets/{secret_name}"

        # Add new version with updated value
        response = client.add_secret_version(
            request={
                "parent": parent,
                "payload": {"data": secret_value.encode("UTF-8")}
            }
        )

        logger.info(f"Secret {secret_name} updated successfully: {response.name}")
        return True

    except ImportError:
        logger.error("google-cloud-secret-manager not installed")
        return False
    except Exception as e:
        logger.error(f"Failed to update secret {secret_name}: {e}")
        return False


def update_saxo_tokens(access_token: str, refresh_token: str, token_expiry: str) -> bool:
    """
    Update Saxo API tokens in Secret Manager.

    This is called after a token refresh to persist the new tokens.

    Args:
        access_token: New access token
        refresh_token: New refresh token
        token_expiry: Token expiry timestamp (ISO format string)

    Returns:
        bool: True if successful, False otherwise
    """
    # Get current credentials
    creds = get_saxo_credentials()
    if not creds:
        logger.error("Could not fetch existing Saxo credentials to update")
        return False

    # Update token fields
    creds["access_token"] = access_token
    creds["refresh_token"] = refresh_token
    creds["token_expiry"] = token_expiry

    # Save back to Secret Manager
    return update_secret(SECRET_NAMES["saxo_credentials"], json.dumps(creds))


# Test function
if __name__ == "__main__":
    print("=" * 60)
    print("SECRET MANAGER TEST")
    print("=" * 60)

    print(f"\nRunning on GCP: {is_running_on_gcp()}")

    if is_running_on_gcp():
        project_id = get_project_id()
        print(f"Project ID: {project_id}")

        print("\nTesting secret fetch...")
        saxo_creds = get_saxo_credentials()
        if saxo_creds:
            print(f"  Saxo credentials: Found (app_key: {saxo_creds.get('app_key', 'N/A')[:8]}...)")
        else:
            print("  Saxo credentials: Not found")

        email_config = get_email_config()
        if email_config:
            print(f"  Email config: Found (enabled: {email_config.get('enabled', False)})")
        else:
            print("  Email config: Not found")
    else:
        print("\nRunning locally - Secret Manager not available")
        print("Use config/config.json for configuration")

    print("\n" + "=" * 60)
