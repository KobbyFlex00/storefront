# Storefront

A resellable, single-page **fashion & lifestyle storefront** with a phone-friendly admin dashboard.
Customers browse the collection, build a bag and send the order straight to the store's **WhatsApp**, or use an enquiry form.
Store owners change *everything* themselves: name, logo, colours, fonts, hero video, products, FAQ and contact details. No code required.

Built to be deployed once per client: one codebase, many stores.

## What's in it

**Storefront (public)**
- Hero with video or image, brand story, a "Shop the edit" grid with category filters, FAQ, a closing banner, order section and footer
- Product quick view with a photo gallery, size picker and quantity
- **Bag → WhatsApp checkout**: an itemised message with sizes, quantities, total and delivery location
- **Pay online with Paystack**: card and Mobile Money (MTN MoMo, Telecel Cash, AirtelTigo, M-Pesa…) in GHS, NGN, KES, ZAR or USD
- Enquiry form (saved to the dashboard, optionally emailed to the owner)
- SEO title/description, link-preview image, favicon, `sitemap.xml`, `robots.txt`
- Mobile-first, accessible, respects reduced-motion

**Admin (`/admin`)**
- **Look & content**: store name, logo, browser icon, 5 brand colours, 6 font pairings, currency (GHS, NGN, KES, ZAR, USD, GBP, EUR), every piece of text, and show/hide for each section
- **Products**: price, sale price, sizes, category, multiple photos (auto-resized), featured, sold out, hidden, sort order
- **Orders** (paid online, with a "to send" / "sent" workflow), **Categories**, **FAQ**, **Enquiries inbox** (with one-tap WhatsApp reply), **Security log**, **Account** (change password)
- **Support tools** (only for you, the developer): store health, unlock or reset the owner's login, add owner logins

**Security**: hardened against the OWASP Top 10. See [SECURITY.md](SECURITY.md).

## Run it locally

```bash
python -m venv .venv && . .venv/bin/activate
```
```bash
pip install -r requirements-dev.txt
```
```bash
cp .env.example .env   # then edit ADMIN_PASSWORD
```
```bash
flask db upgrade && flask ensure-admin && flask seed-demo && flask run
```

Open http://localhost:5000 for the shop and http://localhost:5000/admin for the dashboard.

Tests and checks:

```bash
pytest -q && ruff check . && pip-audit -r requirements.txt
```

## Your support login (superadmin in every store)

You get one login that works in **every** client store, so you can fix things remotely. Because it opens every store, it's protected by a strong password **and** an authenticator-app code (2FA). Production refuses it without 2FA.

1. Once, on your own computer, run the command below and follow the prompts:
   ```bash
   flask superadmin-setup
   ```
   It prints three values and a setup key for your authenticator app (Google Authenticator, Authy, 1Password…).
2. Paste the same `SUPERADMIN_USERNAME`, `SUPERADMIN_PASSWORD_HASH` and `SUPERADMIN_TOTP_SECRET` into **each** store's Render environment. Only a hash of your password is stored, never the password itself.
3. Sign in at `https://<store>/admin` with your support username, password and the 6-digit code. You'll see **Support tools** in the menu.

Being open with clients: the owner's **Account** page says that support access exists, and every support sign-in and action shows up in their **Security log**. Mention it in your service agreement.
To revoke access to a store, delete the three variables there and redeploy. To change your password everywhere, run `superadmin-setup` again and update each store; old support sessions are signed out.

## Paystack (card + Mobile Money)

1. The client creates a Paystack account (paystack.com) for their business and gives you their **secret key**. Use `sk_test_…` first, then `sk_live_…`.
2. Set `PAYSTACK_SECRET_KEY` in that store's Render environment.
3. In the Paystack dashboard → Settings → API Keys & Webhooks, set the **webhook URL** to `https://<store-domain>/paystack/webhook`.
4. The store currency must be GHS, NGN, KES, ZAR or USD (Admin → Look & content → Brand). The owner can switch online payment off under **Payments**. WhatsApp ordering always stays available.
5. Paid orders appear under **Orders** (and are emailed to the owner if SMTP is set).

Money goes straight from Paystack to the client's bank or MoMo account. The store never sees card details.

## New client in 10 minutes

1. **Database**: in Neon, create a project (or a branch) for the client. Copy the *pooled* and *direct* connection strings.
2. **Deploy**: in Render, go to **New → Blueprint** and pick this repo. When asked, paste:
   - `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` (direct)
   - `ADMIN_USERNAME` and a long `ADMIN_PASSWORD`
   - `ALLOWED_HOSTS`: the client's domain(s) plus the `*.onrender.com` hostname Render gives you
   - Your three `SUPERADMIN_*` values (same in every store)
   - The client's `PAYSTACK_SECRET_KEY` (optional; see above)
   - Optional `SMTP_*` to email enquiries (a Gmail app password works)
3. **Brand it**: sign in at `/admin` → **Look & content**, and set the name, logo, colours, fonts, hero, WhatsApp number and socials.
4. **Products**: add categories, then products with photos. *(Optional: `flask seed-demo` in Render's Shell gives a finished-looking demo for a sales pitch. Delete the demo products before handing over.)*
5. **Domain**: add the client's custom domain in Render → Settings → Custom Domains.
6. Ask the client to change their password under **Account**.

### Reusing a store as a template

```bash
flask export-store template.store.json
```
```bash
flask import-store template.store.json --replace
```

Export copies settings, categories, products, FAQ and photos (never logins or enquiries). Import it into a new client's database to start from a design you've already built.

### Customising further (developers)

- **A new editable field**: add one line to `storefront/settings_schema.py` and use `{{ site.<key> }}` in a template. It appears in the admin automatically, and no migration is needed.
- **Look**: `storefront/static/css/site.css` uses CSS variables (`--primary`, `--accent`, `--bg`, `--surface`, `--text`, `--font-head`, `--font-body`) fed from the admin settings.
- **Font pairs / currencies**: `FONT_PAIRS` and `CURRENCIES` in `settings_schema.py`.

## Stack

Flask · SQLAlchemy/Alembic · Flask-Login · Flask-WTF (CSRF) · Pillow · Postgres (Neon) or SQLite locally · Render.
Uploaded images and videos are stored in the database, so a store has no external file storage to manage. Render's disk is wiped on every deploy.

## Roadmap ideas

- Multiple stores served from one deployment
- Product detail pages for SEO
