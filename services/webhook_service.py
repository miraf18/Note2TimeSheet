"""
Webhook Service — invio dati a webhook esterno (n8n).
Disabilitato di default tramite ENABLE_WEBHOOK=false nel .env.
"""

import os
import json
import requests


def is_webhook_enabled():
    return os.getenv("ENABLE_WEBHOOK", "false").strip().lower() == "true"


def send_to_webhook(payload):
    """
    Send payload to configured webhook URL.
    Returns (success: bool, status_code: int|None, error: str|None, response_data: dict|None).
    Returns (False, None, reason, None) immediately if webhook is disabled or not configured.
    """
    if not is_webhook_enabled():
        return False, None, "Webhook disabilitato (ENABLE_WEBHOOK=false)", None

    url = os.getenv("WEBHOOK_URL", "").strip()
    if not url or "your-webhook-url" in url:
        print(f"[WEBHOOK] URL non configurato. Payload: {json.dumps(payload, ensure_ascii=False)[:200]}")
        return False, None, "Webhook non configurato nel .env", None

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        status = resp.status_code
        response_data = None
        try:
            response_data = resp.json()
        except Exception:
            text = resp.text.strip()
            if text:
                response_data = {"message": text}

        if status < 400:
            return True, status, None, response_data
        return False, status, f"Il server ha risposto {status}", response_data

    except requests.exceptions.ConnectionError:
        return False, None, "Impossibile connettersi al webhook", None
    except requests.exceptions.Timeout:
        return False, None, "Timeout - il server non risponde", None
    except Exception as e:
        return False, None, str(e), None
