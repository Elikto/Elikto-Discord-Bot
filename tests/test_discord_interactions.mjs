import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import {
  handleInteraction, replyToInteraction, verifyDiscordSignature
} from "../api/discord-interactions.mjs";

const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const publicHex = publicKey.export({format: "der", type: "spki"}).subarray(-32).toString("hex");
const URL = "https://mirror.example.org/";

const message = {
  type: 3,
  data: {
    custom_id: "streaming_hd_mirrors:v1:0",
    values: [URL],
  },
  message: {
    components: [
      {
        type: 17,
        components: [
          {
            type: 1,
            components: [
              {
                type: 3,
                custom_id: "streaming_hd_mirrors:v1:0",
                options: [{ label: "Mirror", value: URL }],
              },
            ],
          },
        ],
      },
    ],
  },
};

function signedRequest(payload) {
  const raw = JSON.stringify(payload);
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = sign(null, Buffer.from(timestamp + raw), privateKey).toString("hex");
  return new Request("https://localhost/api/discord-interactions", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-signature-timestamp": timestamp,
      "x-signature-ed25519": signature,
    },
    body: raw,
  });
}

test("Discord PING is acknowledged with valid signature", async () => {
  const response = await handleInteraction(signedRequest({type: 1}), publicHex);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {type: 1});
});

test("select responds with ephemeral link button", async () => {
  const response = await handleInteraction(signedRequest(message), publicHex);
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.type, 4);
  assert.equal(data.data.flags, 64);
  assert.equal(data.data.components[0].components[0].url, URL);
  assert.equal(data.data.allowed_mentions.parse.length, 0);
});

test("cannot redirect to URL absent from signed menu options", () => {
  const response = replyToInteraction({
    ...message,
    data: {...message.data, values: ["https://evil.example.org/"]},
  });
  assert.equal(response.data.components, undefined);
  assert.match(response.data.content, /plus disponible/);
});

test("blocks private/invalid links even when signed", () => {
  for (const url of [
    "https://127.0.0.1/admin", "http://mirror.example.org/",
    "https://user:pass@example.org/",
  ]) {
    const modified = {
      ...message,
      data: {...message.data, values: [url]},
      message: {
        components: [{
          type: 1, components: [{
            type: 3,
            custom_id: "streaming_hd_mirrors:v1:0",
            options: [{label: "Bad", value: url}],
          }],
        }],
      },
    };
    assert.equal(replyToInteraction(modified).data.components, undefined);
  }
});

test("rejects tampered signature and missing authentication", async () => {
  const bad = signedRequest(message);
  const res = await handleInteraction(bad, "00".repeat(32));
  assert.equal(res.status, 401);
  assert.equal(verifyDiscordSignature("{}", null, "1234567890", publicHex), false);
});
