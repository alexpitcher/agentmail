export interface Env {
  AGENTMAIL_INGEST_URL: string;
  AGENTMAIL_INGEST_TOKEN: string;
  FORWARD_COPY_TO?: string;
}

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    const rawArrayBuffer = await new Response(message.raw).arrayBuffer();

    const response = await fetch(env.AGENTMAIL_INGEST_URL, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "Authorization": `Bearer ${env.AGENTMAIL_INGEST_TOKEN}`,
        "X-AgentMail-Provider": "cloudflare-email-worker",
        "X-AgentMail-Envelope-From": message.from,
        "X-AgentMail-Envelope-To": message.to,
        "X-AgentMail-Raw-Size": String(message.rawSize ?? rawArrayBuffer.byteLength),
      },
      body: rawArrayBuffer,
    });

    if (!response.ok) {
      message.setReject(`AgentMail ingest failed: ${response.status}`);
      return;
    }

    if (env.FORWARD_COPY_TO) {
      await message.forward(env.FORWARD_COPY_TO);
    }
  },
};
