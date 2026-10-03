# PIPSGOX Email Delivery

Email delivery is a **server-side deployment setting**. End users never enter SMTP credentials or email-provider API keys in the PIPSGOX Security screen.

## Recommended provider

PIPSGOX supports Resend as the preferred transactional email provider.

Set these variables in the server environment:

```env
PIPSGOX_EMAIL_PROVIDER=resend
PIPSGOX_RESEND_API_KEY=REPLACE_WITH_SERVER_SECRET
PIPSGOX_EMAIL_FROM=PIPSGOX <verified-sender@example.com>
```

The sender address/domain must be accepted by the email provider.

## Generic SMTP fallback

Self-hosted deployments can instead use:

```env
PIPSGOX_EMAIL_PROVIDER=smtp
PIPSGOX_SMTP_HOST=smtp.example.com
PIPSGOX_SMTP_PORT=587
PIPSGOX_SMTP_USERNAME=...
PIPSGOX_SMTP_PASSWORD=...
PIPSGOX_SMTP_SECURITY=starttls
PIPSGOX_EMAIL_FROM=PIPSGOX <noreply@example.com>
```

With `auto`, PIPSGOX uses Resend when `PIPSGOX_RESEND_API_KEY` is present and otherwise falls back to SMTP.

## Security behavior

- Provider credentials are read only by the backend.
- Provider credentials are never returned by API endpoints.
- Browser users receive a generic delivery error if the provider is unavailable.
- Verification tokens are stored only as SHA-256 hashes.
- Verification links expire after 24 hours and are single-use.
- Provider-specific response bodies are not exposed to users.

For local development, configure the provider once in the server environment. A normal PIPSGOX user only enters their own email address.
