# License Worker

Sells HomelabScribe Pro as a **$2.99 lifetime** license. [Lemon Squeezy](https://www.lemonsqueezy.com/)
takes the payment (and handles sales tax as merchant of record) and calls this Cloudflare Worker after each
paid order. The Worker signs a lifetime key (`LS1.<payload>.<signature>`, the format
`homelabscribe.licensing` verifies offline) and emails it to the buyer through [Resend](https://resend.com).

The signing key exists only as a Worker secret. The app never contacts this Worker: keys are checked
offline, so there is no phone-home.

```
buyer -> Lemon Squeezy checkout -> POST /webhook (HMAC-signed) -> Worker -> signs key -> Resend -> buyer
```

What it does and does not do:
- Accepts only a correctly signed `order_created` webhook for a **paid** order of **your product**.
- Ignores test-mode orders unless `ACCEPT_TEST_MODE` is `1` (use that only while testing).
- Remembers each order id in KV, so a retried webhook never sends a second email. If the email fails it
  answers 500 and Lemon Squeezy retries.
- Logs order ids only. Never the key, the address or a secret.
- Keys are offline and cannot be revoked. A refund is handled by refunding in Lemon Squeezy.

## Develop

No dependencies. `npm test` (or `node --test test/worker.test.js`) runs the tests with Node 20 or newer.

## Set up (once, by the owner)

1. **Lemon Squeezy:** create a store and one product, "HomelabScribe Pro (lifetime)", single payment, $2.99.
   Note the product id. Under Settings -> Webhooks add `https://license.homelabscribe.com/webhook`, event
   `order_created`, and a signing secret you invent.
2. **Resend:** add and verify the `homelabscribe.com` domain (DNS records go in Cloudflare), then create an API key
   limited to sending.
3. **Cloudflare**, from this folder (use the version-pinned `wrangler@4` through `npx`):
   ```bash
   npx wrangler@4 kv namespace create ORDERS        # paste the id into wrangler.jsonc
   # edit wrangler.jsonc: PRODUCT_ID, and FROM_ADDRESS / REPLY_TO if you use other addresses
   npx wrangler@4 secret put LEMONSQUEEZY_WEBHOOK_SECRET
   npx wrangler@4 secret put RESEND_API_KEY
   npx wrangler@4 secret put LICENSE_SIGNING_KEY < /path/to/private_key.pem   # the license signing key
   npx wrangler@4 deploy
   ```
4. **Test before selling:** deploy with `npx wrangler@4 deploy --var ACCEPT_TEST_MODE:1`, buy once in Lemon
   Squeezy test mode with a test card, check the email arrives and the key passes `homelabscribe doctor`,
   then deploy again without that variable.
