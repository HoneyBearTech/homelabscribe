// Sells HomelabScribe Pro: Lemon Squeezy calls this Worker after a paid order, and it signs a lifetime
// license key (the same LS1 format `homelabscribe.licensing` verifies offline) and emails it to the buyer.
//
// The signing key never leaves Cloudflare: it is the LICENSE_SIGNING_KEY secret. Nothing here is logged
// except order ids. The app itself never talks to this Worker.

const encoder = new TextEncoder();

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromHex(hex) {
  if (!/^([0-9a-f]{2})+$/i.test(hex)) return null;
  return Uint8Array.from(hex.match(/../g), (h) => parseInt(h, 16));
}

async function importSigningKey(pem) {
  const body = pem.replace(/-----[A-Z ]+-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
}

/** A lifetime Pro key: LS1.<payload>.<signature>, byte-compatible with homelabscribe.licensing.sign(). */
export async function signLicense(email, pem) {
  const payload = encoder.encode(JSON.stringify({ email, expires: null, tier: "pro" }));
  const key = await importSigningKey(pem);
  const sig = new Uint8Array(await crypto.subtle.sign("Ed25519", key, payload));
  return `LS1.${b64url(payload)}.${b64url(sig)}`;
}

/** Lemon Squeezy signs the raw body with HMAC-SHA256 (hex) in the X-Signature header. */
export async function validSignature(rawBody, signatureHex, secret) {
  const sig = fromHex(signatureHex || "");
  if (!sig || !secret) return false;
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  return crypto.subtle.verify("HMAC", key, sig, encoder.encode(rawBody));
}

export function emailText(licenseKey) {
  return [
    "Thank you for buying HomelabScribe Pro. This license is for life.",
    "",
    "Your license key:",
    "",
    licenseKey,
    "",
    "To use it, add this line to homelabscribe.yaml:",
    "",
    "  license_key: <the key above>",
    "",
    "or set the HOMELABSCRIBE_LICENSE environment variable. Then run `homelabscribe doctor` to check it.",
    "The key works offline; HomelabScribe never contacts a server to check it.",
    "",
    "Keep this email. If you lose the key, reply to it and I will send it again.",
    "Docs: https://homelabscribe.com",
  ].join("\n");
}

async function sendEmail(env, to, licenseKey) {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: env.FROM_ADDRESS,
      to: [to],
      reply_to: env.REPLY_TO || undefined,
      subject: "Your HomelabScribe Pro license",
      text: emailText(licenseKey),
    }),
  });
  if (!res.ok) throw new Error(`email provider answered ${res.status}`);
}

const reply = (status, text) => new Response(text, { status });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/webhook") return reply(404, "Not found");

    const raw = await request.text();
    if (!(await validSignature(raw, request.headers.get("X-Signature"), env.LEMONSQUEEZY_WEBHOOK_SECRET))) {
      return reply(401, "Bad signature");
    }

    let event;
    try {
      event = JSON.parse(raw);
    } catch {
      return reply(400, "Bad JSON");
    }
    const attrs = event?.data?.attributes ?? {};
    const orderId = String(event?.data?.id ?? "");

    // Only paid orders for our product. Test-mode orders are ignored unless ACCEPT_TEST_MODE=1.
    if (event?.meta?.event_name !== "order_created" || attrs.status !== "paid") return reply(200, "Ignored");
    if (String(attrs.first_order_item?.product_id ?? "") !== String(env.PRODUCT_ID)) return reply(200, "Ignored");
    if (attrs.test_mode && env.ACCEPT_TEST_MODE !== "1") return reply(200, "Ignored test order");

    const email = String(attrs.user_email ?? "").trim();
    if (!orderId || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return reply(400, "No order id or email");

    // Webhooks are retried; never send a second email for the same order.
    if (await env.ORDERS.get(`order:${orderId}`)) return reply(200, "Already sent");

    try {
      const licenseKey = await signLicense(email, env.LICENSE_SIGNING_KEY);
      await sendEmail(env, email, licenseKey);
    } catch (e) {
      console.error(`order ${orderId} failed: ${e.message}`); // message only: never the key or the address
      return reply(500, "Try again"); // Lemon Squeezy retries on a non-2xx answer
    }
    await env.ORDERS.put(`order:${orderId}`, new Date().toISOString());
    console.log(`order ${orderId}: license sent`);
    return reply(200, "Sent");
  },
};
