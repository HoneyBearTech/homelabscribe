import assert from "node:assert/strict";
import { test } from "node:test";
import worker, { signLicense, validSignature } from "../src/index.js";

const { publicKey, privateKey } = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", privateKey)).toString("base64");
const PEM = `-----BEGIN PRIVATE KEY-----\n${pkcs8.match(/.{1,64}/g).join("\n")}\n-----END PRIVATE KEY-----\n`;
const SECRET = "whsec";

const unb64 = (s) => Buffer.from(s.replaceAll("-", "+").replaceAll("_", "/"), "base64");

async function hmacHex(body) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return Buffer.from(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body))).toString("hex");
}

function makeEnv(extra = {}) {
  const store = new Map();
  return {
    PRODUCT_ID: "42",
    FROM_ADDRESS: "HomelabScribe <licenses@example.com>",
    RESEND_API_KEY: "re_x",
    LICENSE_SIGNING_KEY: PEM,
    LEMONSQUEEZY_WEBHOOK_SECRET: SECRET,
    ORDERS: { get: async (k) => store.get(k) ?? null, put: async (k, v) => void store.set(k, v) },
    store,
    ...extra,
  };
}

const order = (over = {}) => ({
  meta: { event_name: "order_created" },
  data: { id: "1001", attributes: { status: "paid", user_email: "buyer@example.com", test_mode: false, first_order_item: { product_id: 42 }, ...over } },
});

async function post(env, body, sig) {
  const raw = JSON.stringify(body);
  return worker.fetch(
    new Request("https://x/webhook", { method: "POST", body: raw, headers: { "X-Signature": sig ?? (await hmacHex(raw)) } }),
    env,
  );
}

function captureEmail(status = 200) {
  const sent = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url, body: JSON.parse(init.body) });
    return new Response("{}", { status });
  };
  return sent;
}

test("signed key verifies with the public key and has the expected payload", async () => {
  const key = await signLicense("a@example.com", PEM);
  const [prefix, payload, sig] = key.split(".");
  assert.equal(prefix, "LS1");
  assert.ok(await crypto.subtle.verify("Ed25519", publicKey, unb64(sig), unb64(payload)));
  assert.deepEqual(JSON.parse(unb64(payload).toString()), { email: "a@example.com", expires: null, tier: "pro" });
});

test("webhook signature check rejects tampering", async () => {
  const good = await hmacHex("body");
  assert.ok(await validSignature("body", good, SECRET));
  assert.ok(!(await validSignature("bodyx", good, SECRET)));
  assert.ok(!(await validSignature("body", "zz", SECRET)));
  assert.ok(!(await validSignature("body", good, "")));
});

test("a paid order emails one key, once", async () => {
  const env = makeEnv();
  const sent = captureEmail();
  assert.equal((await post(env, order())).status, 200);
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].body.to, ["buyer@example.com"]);
  assert.match(sent[0].body.text, /LS1\.[\w-]+\.[\w-]+/);
  assert.equal(await (await post(env, order())).text(), "Already sent");
  assert.equal(sent.length, 1);
});

test("bad signature, other products, unpaid and test orders send nothing", async () => {
  const env = makeEnv();
  const sent = captureEmail();
  assert.equal((await post(env, order(), "00")).status, 401);
  await post(env, order({ first_order_item: { product_id: 7 } }));
  await post(env, order({ status: "pending" }));
  await post(env, order({ test_mode: true }));
  assert.equal(sent.length, 0);
});

test("test orders are honored only when ACCEPT_TEST_MODE=1", async () => {
  const env = makeEnv({ ACCEPT_TEST_MODE: "1" });
  const sent = captureEmail();
  await post(env, order({ test_mode: true }));
  assert.equal(sent.length, 1);
});

test("a failing email provider returns 500 and is not marked as sent", async () => {
  const env = makeEnv();
  captureEmail(500);
  assert.equal((await post(env, order())).status, 500);
  assert.equal(env.store.size, 0);
});
