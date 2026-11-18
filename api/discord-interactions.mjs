/**
 * Point d'entrée HTTPS pour les menus déroulants Discord.
 *
 * À déployer comme Vercel Function. Aucun jeton Discord n'est nécessaire :
 * seuls les messages signés par Discord sont acceptés avec DISCORD_PUBLIC_KEY.
 * L'URL sélectionnée provient de l'option présente dans le message signé.
 */
import { createPublicKey, verify } from "node:crypto";

const PREFIX = "streaming_hd_mirrors:v1:";
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export function verifyDiscordSignature(rawBody, signature, timestamp, publicKey) {
  if (!/^[0-9a-f]{128}$/i.test(signature || "") ||
      !/^[0-9a-f]{64}$/i.test(publicKey || "") ||
      !/^\d{10}$/.test(timestamp || "")) {
    return false;
  }
  // Écarter les événements périmés. Discord attend une réponse sous 3 s.
  const delta = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (delta > 300) return false;
  try {
    const key = createPublicKey({
      key: Buffer.concat([ED25519_SPKI_PREFIX, Buffer.from(publicKey, "hex")]),
      type: "spki",
      format: "der",
    });
    return verify(
      null,
      Buffer.concat([Buffer.from(timestamp, "utf8"), Buffer.from(rawBody, "utf8")]),
      key,
      Buffer.from(signature, "hex"),
    );
  } catch {
    return false;
  }
}

function findSelect(components, customId) {
  for (const component of components || []) {
    if (component.type === 3 && component.custom_id === customId) {
      return component;
    }
    const nested = findSelect(component.components, customId);
    if (nested) return nested;
  }
  return null;
}

function publicHttpsUrl(raw) {
  if (typeof raw !== "string" || raw.length > 100) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !url.hostname.includes(".") || url.hostname.endsWith(".local") ||
        url.hostname.endsWith(".internal") || url.hostname.endsWith(".localhost")) {
      return null;
    }
    if (/^(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.\d+\.\d+)$/i.test(url.hostname)) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

export function replyToInteraction(interaction) {
  // PING : vérification initiale de l'endpoint par Discord.
  if (interaction?.type === 1) return { type: 1 };

  if (interaction?.type !== 3 ||
      !String(interaction.data?.custom_id || "").startsWith(PREFIX)) {
    return {
      type: 4,
      data: {
        flags: 64,
        content: "Ce menu n'est pas pris en charge par ce service.",
        allowed_mentions: { parse: [] },
      },
    };
  }
  const selected = interaction.data?.values;
  const menu = findSelect(
    interaction.message?.components,
    interaction.data.custom_id,
  );
  const value = Array.isArray(selected) && selected.length === 1 ? selected[0] : null;
  const option = menu?.options?.find((entry) => entry.value === value);
  const url = option ? publicHttpsUrl(option.value) : null;
  if (!url) {
    return {
      type: 4,
      data: {
        flags: 64,
        content: "Ce miroir n'est plus disponible. Rouvre le menu pour choisir un autre site.",
        allowed_mentions: { parse: [] },
      },
    };
  }
  return {
    type: 4,
    data: {
      flags: 64, // Réponse éphémère : seul le membre qui clique la voit.
      content: "🌐 Voici le lien du miroir sélectionné :",
      allowed_mentions: { parse: [] },
      components: [{
        type: 1,
        components: [{
          type: 2,
          style: 5,
          label: "Ouvrir le site miroir",
          url,
        }],
      }],
    },
  };
}

export async function handleInteraction(request, publicKey) {
  if (request.method !== "POST") return json({ error: "Method Not Allowed" }, 405);

  // La signature porte sur les octets exacts du corps, avant JSON.parse.
  let raw;
  try {
    raw = await request.text();
  } catch {
    return json({ error: "Invalid body" }, 400);
  }
  if (raw.length > 65536) return json({ error: "Payload too large" }, 413);

  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!verifyDiscordSignature(raw, signature, timestamp, publicKey)) {
    return json({ error: "Invalid signature" }, 401);
  }
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "Invalid JSON" }, 400);
  }
  return json(replyToInteraction(body));
}

// Le runtime Vercel attend un export Web Handler nommé (POST/GET).
// Une fonction default qui renvoie Response est interprétée comme l'ancienne
// signature (req, res), et son résultat est ignoré par Vercel.
export async function POST(request) {
  if (!process.env.DISCORD_PUBLIC_KEY) {
    return json({ error: "DISCORD_PUBLIC_KEY is not configured" }, 503);
  }
  return handleInteraction(request, process.env.DISCORD_PUBLIC_KEY);
}

export function GET() {
  return json({ error: "Method Not Allowed" }, 405);
}
